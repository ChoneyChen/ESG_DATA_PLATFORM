"""Text-layer page selection for page-targeted OCR.

Every original PDF page is scored from its embedded text layer only (no
rendering, no OCR) against the source groups of the 16 ESRS foundation
datapoints, so the OCR provider receives just the pages that can carry them.
Scanned PDFs, unreadable text layers and short documents fall back to
whole-document OCR instead of guessing.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import pypdfium2 as pdfium


SELECTOR_NAME = "foundation16"
SELECTOR_VERSION = "foundation16-text-layer-v4"


@dataclass(frozen=True)
class KeywordGroup:
    key: str
    strong: tuple[str, ...]
    weak: tuple[str, ...]


# Mirrors the foundation-16 source groups of the downstream extractor. Terms are
# matched against simplified, case-folded text; ASCII terms need word edges.
GROUPS: tuple[KeywordGroup, ...] = (
    KeywordGroup(
        "energy",
        ("能源消耗", "能源消费", "能耗", "能源耗", "综合能源", "能源使用", "耗能", "用能", "用电", "可再生能源",
         "清洁能源", "非化石能源", "energy"),
        ("gwh", "mwh", "kwh", "千瓦时", "兆瓦时", "吨标准煤", "标准煤", "tce", "吉焦", "gj", "tj", "电力", "天然气",
         "柴油", "汽油", "煤炭", "electricity", "natural gas", "diesel", "fuel"),
    ),
    KeywordGroup(
        "ghg",
        ("范围一", "范围1", "范围二", "范围2", "范围三", "范围3", "范畴一", "范畴二", "范畴三", "scope 1", "scope 2",
         "scope 3", "scope1", "scope2", "scope3", "温室气体", "greenhouse gas", "ghg", "直接排放", "间接排放"),
        ("co2e", "tco2e", "co2eq", "二氧化碳当量", "碳排放", "emissions", "排放总量"),
    ),
    KeywordGroup(
        "air",
        ("氮氧化物", "二氧化硫", "硫氧化物", "颗粒物", "烟尘", "粉尘", "挥发性有机", "vocs", "voc", "nox", "sox", "so2",
         "particulate", "废气", "大气污染物", "air emissions", "air pollutant"),
        ("排放量", "排放", "吨", "tonnes", "emissions"),
    ),
    KeywordGroup(
        "water_pollutants",
        ("化学需氧量", "cod", "氨氮", "总氮", "总磷", "生化需氧量", "bod", "水污染物", "废水污染物", "重金属", "总铜",
         "总锌", "总铅", "总镉", "总汞", "总砷", "ammonia nitrogen", "chemical oxygen demand"),
        ("废水", "排放量", "吨", "wastewater", "discharge"),
    ),
    KeywordGroup(
        "water",
        ("取水", "耗水", "用水", "排水", "水资源", "循环水", "循环利用", "重复利用", "回用", "新鲜水", "新水", "淡水",
         "水风险", "缺水", "水压力", "water withdrawal", "water consumption", "water discharge", "water recycled",
         "recycled water", "water use", "water withdrawn", "water stress", "water management", "water resources",
         "water intake", "water sources", "total water", "ground water", "groundwater", "surface water"),
        ("立方米", "m3", "万吨", "百万吨", "吨", "megalitre", "ml", "地下水", "地表水", "市政", "groundwater",
         "surface water", "municipal"),
    ),
    KeywordGroup(
        "substances",
        ("关注物质", "高度关注物质", "高关注物质", "svhc", "substances of concern", "substance of concern",
         "有毒有害物质", "有害物质", "危险化学品", "hazardous substances"),
        ("化学品", "chemicals", "使用量", "采购量"),
    ),
    KeywordGroup(
        'ghg_intensity',
        ('温室气体排放强度', '排放强度', '碳强度', 'ghg intensity', 'emission intensity', 'carbon intensity', '每百万元营收', 'per million revenue', 'per revenue'),
        ('tco2e', 'co2e', '吨二氧化碳当量', '万元', '百万元'),
    ),
    KeywordGroup(
        'waste',
        ('废弃物', '废物', '固废', '危险废物', '有害废弃物', 'waste', 'solid waste', 'hazardous waste'),
        ('产生量', '总量', '处置', '回收', '有害', '无害', '吨', 'tonnes', 'generated', 'disposed', 'diverted'),
    ),
    KeywordGroup(
        'materials',
        ('原材料', '资源投入', '物料使用', '材料使用', 'materials used', 'resource inflow', 'material inflow'),
        ('吨', '重量', '采购量', 'tonnes', 'weight'),
    ),
    KeywordGroup(
        'workforce',
        ('员工总数', '员工总人数', '员工人数', '雇员人数', '在职员工', '总人数', '员工流失率', '离职率', 'employee turnover', 'number of employees', 'headcount', 'head count'),
        ('人', '人数', '期末', '平均', '%', 'turnover', 'employees'),
    ),
    KeywordGroup(
        'workforce_training',
        ('培训', '受训', '培训时长', '培训小时', '人均受训', 'training', 'training hours', 'trained'),
        ('小时', '学时', '人均', 'hours', 'per employee'),
    ),
    KeywordGroup(
        'workforce_safety',
        ('工伤', '事故', '可记录', 'accident', 'injury', 'ltifr', 'trifr', 'recordable'),
        ('率', '每百万工时', '人数', '起', 'rate'),
    ),
    KeywordGroup(
        'workforce_pay',
        ('薪酬差距', '薪酬差异', '男女薪酬', 'gender pay gap', 'pay gap', 'remuneration gap'),
        ('%', '比率', 'ratio'),
    ),
    KeywordGroup(
        'workforce_incidents',
        ('歧视', '骚扰', 'discrimination', 'harassment'),
        ('事件', '投诉', '件', 'incident', 'complaint'),
    ),
    KeywordGroup(
        'workforce_gender',
        ('高管', '管理层', '女性管理者', '性别比例', 'top management', 'senior management', 'gender distribution'),
        ('%', '女性', '男性', 'female', 'male'),
    ),
    KeywordGroup(
        'governance',
        ('董事会', '治理机构', '女性董事', '董事会多元化', '腐败', '贿赂', '定罪', '付款天数', '账期', 'board', 'corruption', 'bribery', 'conviction', 'payment days'),
        ('%', '件', '天', '人', '女性', 'days', 'cases'),
    ),
)
# Measurement-method wording (E2-4_09) only matters beside pollutant figures.
METHOD_TERMS = ("监测", "检测", "在线", "测量", "测算", "估算", "核算", "排放因子", "物料衡算", "monitoring",
                "measured", "estimated", "calculated", "emission factor", "mass balance", "methodology")
POLLUTANT_GROUPS = ("air", "water_pollutants")

_PERIOD_RANGE = re.compile(
    r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*(?:至|到|－|—|-|~|～)\s*(?:19|20)\d{2}\s*年"
)
_PERIOD_STATEMENT = re.compile(
    r"(?:报告(?:时间)?(?:期|范围|周期|期间)|本报告(?:涵盖|覆盖)|时间范围|reporting\s+period|report(?:ing)?\s+covers|"
    r"covers\s+the\s+period)[^。.;；]{0,60}?(?:19|20)\d{2}",
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WHITESPACE = re.compile(r"\s+")

# Traditional→simplified folding for the characters the terms above use.
_FOLD_PAIRS = (
    "範范 圍围 疇畴 溫温 氣气 體体 顆颗 揮挥 發发 機机 廢废 總总 學学 銅铜 鋅锌 鉛铅 鎘镉 鉻铬 鎳镍 錳锰 鋁铝 "
    "屬属 資资 環环 復复 險险 壓压 關关 質质 煙烟 塵尘 標标 準准 噸吨 萬万 億亿 電电 熱热 綜综 費费 測测 監监 "
    "檢检 線线 計计 數数 據据 報报 時时 間间 週周 當当 際际 統统 購购 處处 類类 別别 價价 鏈链 項项 運运 輸输 "
    "營营 產产 業业 務务 區区 濃浓 劑剂 礦矿 鋼钢 鐵铁 燒烧 鍋锅 爐炉 煉炼 氫氢 懸悬 淨净 潔洁 節节 減减 廠厂 "
    "設设 備备 車车 輛辆 綠绿 證证 參参 額额 會会 層层 現现 規规 劃划 儲储 裝装 組组 號号 開开 動动 態态 頁页 "
    "東东 華华 國国 團团 職职 責责 審审 認认 識识 議议 記记 導导 經经 濟济 長长 變变 點点 種种 級级 確确 損损 "
    "權权 結结 構构 園园 場场 農农 養养 醫医 藥药 療疗 預预 應应 響响 風风 陽阳 鹽盐 汙污 濾滤 滲渗 雜杂 迴回 "
    "蓋盖 錄录 頭头 兩两 實实 續续 辦办 擁拥 為为 與与 從从 這这"
)
_FOLD = {
    ord(pair[0]): pair[1]
    for pair in _FOLD_PAIRS.split()
    if len(pair) == 2 and pair[0] != pair[1]
}
_FOLD.update(str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789"))


def fold_text(text: str) -> str:
    """Simplified, case-folded text with sub/superscript digits made plain."""
    return text.translate(_FOLD).casefold()


def _matcher(term: str) -> tuple[str, Any]:
    term = fold_text(term)
    if term.isascii():
        body = r"\s?".join(re.escape(part) for part in term.split(" "))
        return term, re.compile(rf"(?<![a-z]){body}(?![a-z])")
    return term, term


_GROUP_MATCHERS = {
    group.key: ([_matcher(term) for term in group.strong], [_matcher(term) for term in group.weak])
    for group in GROUPS
}
_METHOD_MATCHERS = [_matcher(term) for term in METHOD_TERMS]


def _hits(matchers: list[tuple[str, Any]], compact: str, spaced: str) -> list[str]:
    found = []
    for term, matcher in matchers:
        # CJK terms ignore the text layer's arbitrary inter-glyph whitespace.
        hit = matcher.search(spaced) if isinstance(matcher, re.Pattern) else matcher in compact
        if hit:
            found.append(term)
    return found


@dataclass(frozen=True)
class PageScore:
    page_index: int
    text_chars: int
    numeric_tokens: int
    group_scores: dict[str, float]
    group_hits: dict[str, list[str]]
    method_hits: int
    density_factor: float
    score: float
    reporting_period: bool

    def to_dict(self, *, with_hits: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "page_index": self.page_index,
            "page_number": self.page_index + 1,
            "score": self.score,
            "group_scores": self.group_scores,
            "numeric_tokens": self.numeric_tokens,
            "text_chars": self.text_chars,
        }
        if with_hits:
            payload["group_hits"] = self.group_hits
            payload["method_hits"] = self.method_hits
            payload["density_factor"] = self.density_factor
            payload["reporting_period"] = self.reporting_period
        return payload


@dataclass(frozen=True)
class PageSelection:
    """Resolved targets; ``page_indices`` is None when every page must be OCR'd."""

    source_page_count: int
    page_indices: tuple[int, ...] | None
    reason: str
    inclusion_reasons: dict[int, list[str]] = field(default_factory=dict)
    scores: tuple[PageScore, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    text_source: str = "text_layer"  # or "vision_prescan" for scans

    @property
    def targeted(self) -> bool:
        return self.page_indices is not None

    def provenance(self) -> dict[str, Any]:
        """Compact selector record kept in the OCR manifest."""
        by_index = {item.page_index: item for item in self.scores}
        return {
            "name": SELECTOR_NAME,
            "version": SELECTOR_VERSION,
            "reason": self.reason,
            "text_source": self.text_source,
            "parameters": self.parameters,
            "elapsed_seconds": self.elapsed_seconds,
            "selected_pages": [
                {
                    **(by_index[index].to_dict() if index in by_index else {"page_index": index, "page_number": index + 1}),
                    "included_by": self.inclusion_reasons.get(index, []),
                }
                for index in (self.page_indices or ())
            ],
        }

    def report(self) -> dict[str, Any]:
        """Full per-page score table kept beside the OCR source snapshot."""
        return {
            "schema_version": "ocr-page-selection-v1",
            "selector": SELECTOR_NAME,
            "selector_version": SELECTOR_VERSION,
            "reason": self.reason,
            "text_source": self.text_source,
            "source_page_count": self.source_page_count,
            "page_indices": list(self.page_indices) if self.page_indices is not None else None,
            "inclusion_reasons": {str(key): value for key, value in sorted(self.inclusion_reasons.items())},
            "parameters": self.parameters,
            "elapsed_seconds": self.elapsed_seconds,
            "pages": [item.to_dict(with_hits=True) for item in self.scores],
        }


class Foundation16PageSelector:
    """Ranks pages by foundation keyword evidence weighted by numeric density."""

    def __init__(
        self,
        *,
        top_n: int = 18,
        per_group_pages: int = 2,
        front_pages: int = 3,
        continuation_pages: int = 6,
        continuation_min_numbers: int = 12,
        period_scan_pages: int = 12,
        min_text_chars: int = 40,
        min_text_page_ratio: float = 0.5,
        max_coverage_ratio: float = 0.8,
    ):
        self.top_n = max(1, int(top_n))
        self.per_group_pages = max(0, int(per_group_pages))
        self.front_pages = max(0, int(front_pages))
        self.continuation_pages = max(0, int(continuation_pages))
        self.continuation_min_numbers = max(0, int(continuation_min_numbers))
        self.period_scan_pages = max(0, int(period_scan_pages))
        self.min_text_chars = max(1, int(min_text_chars))
        self.min_text_page_ratio = float(min_text_page_ratio)
        self.max_coverage_ratio = float(max_coverage_ratio)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "top_n": self.top_n,
            "per_group_pages": self.per_group_pages,
            "front_pages": self.front_pages,
            "continuation_pages": self.continuation_pages,
            "continuation_min_numbers": self.continuation_min_numbers,
            "period_scan_pages": self.period_scan_pages,
            "min_text_chars": self.min_text_chars,
            "min_text_page_ratio": self.min_text_page_ratio,
            "max_coverage_ratio": self.max_coverage_ratio,
        }

    def select(
        self,
        pdf_path: str | Path,
        *,
        prescan: Callable[[Path, int], list[str]] | None = None,
    ) -> PageSelection:
        """Score pages from the text layer. A PDF without a usable text layer
        (a scan) is scored from ``prescan`` page texts when given, else every
        page goes to OCR."""
        started = time.perf_counter()
        try:
            texts = self._page_texts(Path(pdf_path))
        except Exception as exc:
            return self._result(0, None, f"text_layer_unreadable:{type(exc).__name__}", started)
        selection = self.select_texts(texts, started=started)
        if selection.reason != "insufficient_text_layer" or prescan is None:
            return selection
        try:
            scanned = prescan(Path(pdf_path), len(texts))
        except Exception as exc:
            return replace(selection, reason=f"insufficient_text_layer;prescan_failed:{type(exc).__name__}",
                           elapsed_seconds=round(time.perf_counter() - started, 3))
        return self.select_texts(scanned, started=started, source="vision_prescan")

    def select_texts(self, texts: list[str], *, started: float | None = None, source: str = "text_layer") -> PageSelection:
        started = time.perf_counter() if started is None else started
        scores = tuple(self.score_page(index, text) for index, text in enumerate(texts))
        page_count = len(scores)
        text_pages = sum(1 for item in scores if item.text_chars >= self.min_text_chars)
        if not page_count or text_pages < page_count * self.min_text_page_ratio:
            reason = "insufficient_text_layer" if source == "text_layer" else f"insufficient_{source}_text"
            return self._result(page_count, None, reason, started, scores=scores, source=source)
        ranked = sorted((item for item in scores if item.score > 0), key=lambda item: (-item.score, item.page_index))
        if len(ranked) < 2:
            return self._result(page_count, None, "no_foundation_keyword_pages", started, scores=scores, source=source)

        reasons: dict[int, list[str]] = {}

        def include(page_index: int, reason: str) -> None:
            if 0 <= page_index < page_count:
                reasons.setdefault(page_index, []).append(reason)

        for page_index in range(min(self.front_pages, page_count)):
            include(page_index, "front_matter")
        period_page = next(
            (item.page_index for item in scores[: self.period_scan_pages] if item.reporting_period),
            None,
        )
        if period_page is not None:
            include(period_page, "reporting_period_statement")
        # Each family gets its best pages before the global ranking fills the
        # rest: a fixed top-N lets one family crowd the others out as the
        # metric set grows.
        for group in GROUPS:
            family = sorted((item for item in ranked if item.group_scores.get(group.key, 0) > 0),
                            key=lambda item: (-item.group_scores[group.key], -item.numeric_tokens, item.page_index))
            for item in family[: self.per_group_pages]:
                include(item.page_index, f"family_{group.key}")
        for rank, item in enumerate(ranked[: self.top_n], start=1):
            include(item.page_index, f"score_rank_{rank}")
        # KPI tables run across consecutive pages whose rows repeat no keyword, so
        # numeric-dense neighbours of the best pages come along.
        for item in ranked[: self.continuation_pages]:
            for neighbour, label in ((item.page_index + 1, "continuation_of"), (item.page_index - 1, "lead_in_to")):
                if 0 <= neighbour < page_count and scores[neighbour].numeric_tokens >= self.continuation_min_numbers:
                    include(neighbour, f"{label}_page_{item.page_index + 1}")

        selected = tuple(sorted(reasons))
        if len(selected) >= page_count * self.max_coverage_ratio:
            return self._result(page_count, None, "selection_covers_most_pages", started, scores=scores, source=source)
        return self._result(page_count, selected, f"{source}_scored", started, scores=scores, reasons=reasons, source=source)

    def score_page(self, page_index: int, text: str) -> PageScore:
        folded = fold_text(text)
        spaced = _WHITESPACE.sub(" ", folded).strip()
        compact = _WHITESPACE.sub("", folded)
        group_scores: dict[str, float] = {}
        group_hits: dict[str, list[str]] = {}
        for key, (strong_matchers, weak_matchers) in _GROUP_MATCHERS.items():
            strong = _hits(strong_matchers, compact, spaced)
            if not strong:
                continue
            weak = _hits(weak_matchers, compact, spaced)
            group_scores[key] = float(3 * len(strong) + min(4, len(weak)))
            group_hits[key] = strong + weak
        method_hits = len(_hits(_METHOD_MATCHERS, compact, spaced)) if any(
            key in group_scores for key in POLLUTANT_GROUPS
        ) else 0
        numeric_tokens = len(_NUMBER.findall(spaced))
        # Many figures mark data tables; keyword-rich narrative pages weigh less.
        density_factor = round(0.4 + min(1.6, numeric_tokens / 25), 4)
        base = sum(group_scores.values()) + min(3, method_hits)
        return PageScore(
            page_index=page_index,
            text_chars=len(compact),
            numeric_tokens=numeric_tokens,
            group_scores=group_scores,
            group_hits=group_hits,
            method_hits=method_hits,
            density_factor=density_factor,
            score=round(base * density_factor, 2) if group_scores else 0.0,
            reporting_period=bool(_PERIOD_RANGE.search(spaced) or _PERIOD_STATEMENT.search(spaced)),
        )

    def _result(
        self,
        page_count: int,
        page_indices: tuple[int, ...] | None,
        reason: str,
        started: float,
        *,
        scores: tuple[PageScore, ...] = (),
        reasons: dict[int, list[str]] | None = None,
        source: str = "text_layer",
    ) -> PageSelection:
        return PageSelection(
            source_page_count=page_count,
            page_indices=page_indices,
            reason=reason,
            inclusion_reasons=reasons or {},
            scores=scores,
            parameters=self.parameters,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            text_source=source,
        )

    @staticmethod
    def _page_texts(pdf_path: Path) -> list[str]:
        document = pdfium.PdfDocument(str(pdf_path))
        texts: list[str] = []
        try:
            for page_index in range(len(document)):
                page = document[page_index]
                text_page = None
                try:
                    text_page = page.get_textpage()
                    texts.append(text_page.get_text_range() or "")
                except Exception:
                    # One damaged page must not hide the others' evidence.
                    texts.append("")
                finally:
                    if text_page is not None:
                        text_page.close()
                    page.close()
        finally:
            document.close()
        return texts
