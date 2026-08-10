from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from esg_v2.targeted.contracts import DisclosureFeatures


INDEX_SECTION_MARKERS = (
    "指標索引",
    "指标索引",
    "準則索引",
    "准则索引",
    "standards index",
    "content index",
)

UNIT_PATTERNS: dict[str, tuple[str, ...]] = {
    "count_person": (
        r"\bpersons?\b", r"\bpeople\b", r"人數", r"人数", r"\d\s*人(?:\b|次)?", r"\d\s*名",
        r"(?:>|\|)\s*(?:人|人數|人数|名)\s*(?:<|\|)",
    ),
    "count_incident": (
        r"incidents?", r"cases?", r"complaints?", r"事件", r"案件", r"投訴", r"投诉",
        r"\d\s*[起件宗]", r"(?:>|\|)\s*(?:起|件|宗)\s*(?:<|\|)",
    ),
    "percent": (r"%", r"百分比", r"percentage", r"percent", r"比例", r"比率"),
    "hours": (r"小時", r"小时", r"hours?", r"(?:>|\|)\s*(?:小時|小时)\s*(?:<|\|)"),
    "days": (r"\bdays?\b", r"天數", r"天数", r"工作日"),
    "emissions": (r"噸\s*二氧化碳", r"吨\s*二氧化碳", r"t\s*co2e", r"tonnes?\s+co2", r"metric tons?\s+co2"),
    "energy": (r"\bkwh\b", r"\bmwh\b", r"\bgj\b", r"\bmj\b", r"千瓦時", r"千瓦时", r"兆瓦時", r"兆瓦时", r"吉焦"),
    "mass": (r"\bkg\b", r"kilograms?", r"\btonnes?\b", r"\btons?\b", r"千克", r"公斤", r"噸", r"吨"),
    "volume": (r"m[³3]", r"立方米", r"cubic metres?", r"cubic meters?", r"\blitres?\b", r"\bliters?\b", r"公升"),
    "monetary": (r"rmb", r"cny", r"hkd", r"usd", r"eur", r"人民幣", r"人民币", r"港元", r"美元", r"歐元", r"欧元", r"元/", r"元每"),
    "area": (r"m[²2]", r"平方(?:米|公里)", r"hectares?", r"公頃", r"公顷", r"\bha\b"),
    "distance": (r"\bkm\b", r"kilomet(?:re|er)s?", r"公里"),
}

STATEMENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "policy": (r"政策", r"方針", r"方针", r"制度", r"policy", r"policies"),
    "action": (r"措施", r"行動", r"行动", r"實施", r"实施", r"開展", r"开展", r"actions?"),
    "target": (r"目標", r"目标", r"承諾", r"承诺", r"targets?"),
    "absence": (r"未.{0,18}(制定|採取|采取|設定|设定|建立)", r"尚未", r"has not", r"no\s+(policy|action|target)"),
}

EXPLICIT_ZERO_PATTERNS = (
    r"未.{0,18}(發現|发现|發生|发生).{0,24}(事件|案件)",
    r"(事件|案件).{0,10}(為|为)?\s*0\s*[起件宗]?",
    r"0\s*[起件宗].{0,18}(事件|案件|貪污|贪污|腐敗|腐败|歧視|歧视)",
    r"no\s+(?:reported\s+)?(?:incidents?|cases?)",
)


def normalized_match_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    replacements = {
        "範圍一": "scope1", "范围一": "scope1", "範圍二": "scope2", "范围二": "scope2",
        "範圍三": "scope3", "范围三": "scope3", "範圍 1": "scope1", "范围 1": "scope1",
        "範圍 2": "scope2", "范围 2": "scope2", "範圍 3": "scope3", "范围 3": "scope3",
        "scope 1": "scope1", "scope 2": "scope2", "scope 3": "scope3",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"[\s\-_/:：,，;；()（）\[\]【】]+", " ", text).strip()


def matched_aliases(text: str, aliases: Iterable[str]) -> list[str]:
    normalized = normalized_match_text(text)
    compact = normalized.replace(" ", "")
    matched = []
    for alias in aliases:
        normalized_alias = normalized_match_text(alias)
        if normalized_alias and (
            normalized_alias in normalized or normalized_alias.replace(" ", "") in compact
        ):
            matched.append(alias)
    return list(dict.fromkeys(matched))


class DisclosureFeatureExtractor:
    """Deterministic, model-free parser for retrieval and verification features."""

    @classmethod
    def extract(
        cls,
        source_text: str,
        *,
        section_path: list[str],
        group_type: str,
    ) -> DisclosureFeatures:
        years = cls.years(source_text)
        numeric_values = cls.numeric_values(source_text, years=years)
        unit_families = [
            family
            for family, patterns in UNIT_PATTERNS.items()
            if any(re.search(pattern, source_text, flags=re.IGNORECASE) for pattern in patterns)
        ]
        statement_types = [
            kind
            for kind, patterns in STATEMENT_PATTERNS.items()
            if any(re.search(pattern, source_text, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)
        ]
        explicit_zero = any(
            re.search(pattern, source_text, flags=re.IGNORECASE | re.DOTALL)
            for pattern in EXPLICIT_ZERO_PATTERNS
        )
        section_text = " > ".join(section_path).casefold()
        index_like = any(marker.casefold() in section_text for marker in INDEX_SECTION_MARKERS)
        index_like = index_like or (
            "披露回" in source_text
            and any(marker in source_text for marker in ("指標", "指标", "GRI", "SASB"))
            and not numeric_values
        )
        return DisclosureFeatures(
            numeric_values=numeric_values,
            years=years,
            unit_families=unit_families,
            statement_types=statement_types,
            explicit_zero=explicit_zero,
            index_like=index_like,
            table_like=group_type in {"table", "logical_table"},
        )

    @staticmethod
    def years(text: str) -> list[int]:
        return sorted(
            {
                int(match)
                for match in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text)
                if 1900 <= int(match) <= 2100
            }
        )

    @staticmethod
    def numeric_values(text: str, *, years: list[int]) -> list[str]:
        year_strings = {str(year) for year in years}
        output = []
        for token in re.findall(r"(?<![A-Za-z0-9.])[-+]?\d[\d,]*(?:\.\d+)?%?", text):
            plain = token.replace(",", "").rstrip("%")
            if plain in year_strings:
                continue
            try:
                float(plain)
            except ValueError:
                continue
            output.append(token)
        return output
