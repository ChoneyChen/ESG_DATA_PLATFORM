from __future__ import annotations

import re
import unicodedata


# Standard packages declare the expected unit dimension. This catalogue is
# deliberately clause-agnostic and only recognises units visible in evidence.
CO2E_TOKEN = (
    r"(?:CO\s*(?:2|₂|[_^]\s*\{*\s*(?:2|₂)\s*\}*)\s*(?:e|eq)|"
    r"CO2[- ]?eq(?:uivalent)?)"
)
GHG_FORMULA_RE = re.compile(CO2E_TOKEN, re.IGNORECASE)
GHG_UNIT = (
    rf"(?:(?:Mt|kt|t|kg|g|吨|噸|千克|公斤|克)\s*\$?\s*{CO2E_TOKEN}\s*\$?|"
    rf"(?:百万|百萬|千)\s*(?:吨|噸)?\s*\$?\s*t?\s*{CO2E_TOKEN}\s*\$?|"
    r"(?:百万吨|百萬噸|千吨|千噸|吨|噸|千克|公斤|克)\s*二氧化碳(?:当量|當量))"
)
ENERGY_UNIT = (
    r"(?:TWh|GWh|MWh|kWh|Wh|TJ|GJ|MJ|kJ|J|MMBtu|MBtu|Btu|tce|toe|"
    r"太瓦时|吉瓦时|兆瓦时|千瓦时|瓦时|"
    r"太焦|吉焦|兆焦|千焦|焦耳|吨标准煤|吨油当量)"
)
MASS_UNIT = (
    r"(?:万吨|千克|公斤|毫克|微克|吨|噸|克|"
    r"(?<![A-Za-z])(?:kg|mg|μg|ug|kt|t)(?![A-Za-z]))"
)
CURRENCY_UNIT = (
    r"(?:(?:千|万|十万|百万|千万|亿|十亿|thousand|million|billion)\s*)?"
    r"(?:人民币|美元|欧元|英镑|日元|港元|元|CNY|RMB|USD|EUR|GBP|JPY|HKD)"
    r"|(?:CNY|RMB|USD|EUR|GBP|JPY|HKD)\s*(?:thousand|million|billion)"
)
DENOMINATOR = (
    r"(?:收入|营收|營收|净收入|人民币|美元|欧元|英镑|日元|港元|元|"
    r"CNY|RMB|USD|EUR|GBP|JPY|HKD|monetary\s+unit|net\s+revenue)"
)
PHYSICAL_DENOMINATOR = (
    rf"(?:{ENERGY_UNIT}|{MASS_UNIT}|"
    r"m(?:²|³|2|3|\^\s*\{?[23]\}?)|平方米|平米|立方米|"
    r"square\s+met(?:er|re)s?|人|员工|員工|件|台)"
)
MEASUREMENT_UNIT_PATTERN = (
    rf"(?:{GHG_UNIT}|{ENERGY_UNIT}|{MASS_UNIT}|{CURRENCY_UNIT})"
    rf"(?:\s*(?:/|／|per|每)\s*(?:{CURRENCY_UNIT}|{DENOMINATOR}|{PHYSICAL_DENOMINATOR}))?"
)
UNIT_PATTERN = rf"(?:{MEASUREMENT_UNIT_PATTERN}|%|％|百分比)"
UNIT_RE = re.compile(UNIT_PATTERN, re.IGNORECASE)
UNIT_ONLY_RE = re.compile(rf"^(?:{UNIT_PATTERN})$", re.IGNORECASE)


_EXACT_UNIT_IDS = {
    "吨": "extractesg.core.unit.tonne",
    "噸": "extractesg.core.unit.tonne",
    "t": "extractesg.core.unit.tonne",
    "千克": "extractesg.core.unit.kilogram",
    "公斤": "extractesg.core.unit.kilogram",
    "kg": "extractesg.core.unit.kilogram",
    "克": "extractesg.core.unit.gram",
    "g": "extractesg.core.unit.gram",
    "毫克": "extractesg.core.unit.milligram",
    "mg": "extractesg.core.unit.milligram",
    "%": "extractesg.core.unit.percent",
    "％": "extractesg.core.unit.percent",
    "百分比": "extractesg.core.unit.percent",
    "wh": "extractesg.core.unit.watt-hour",
    "kwh": "extractesg.core.unit.kilowatt-hour",
    "mwh": "extractesg.core.unit.megawatt-hour",
    "gwh": "extractesg.core.unit.gigawatt-hour",
    "twh": "extractesg.core.unit.terawatt-hour",
    "mj": "extractesg.core.unit.megajoule",
    "gj": "extractesg.core.unit.gigajoule",
    "tj": "extractesg.core.unit.terajoule",
    "tce": "extractesg.core.unit.tonne-coal-equivalent",
    "瓦时": "extractesg.core.unit.watt-hour",
    "千瓦时": "extractesg.core.unit.kilowatt-hour",
    "兆瓦时": "extractesg.core.unit.megawatt-hour",
    "吉瓦时": "extractesg.core.unit.gigawatt-hour",
    "太瓦时": "extractesg.core.unit.terawatt-hour",
    "兆焦": "extractesg.core.unit.megajoule",
    "吉焦": "extractesg.core.unit.gigajoule",
    "太焦": "extractesg.core.unit.terajoule",
    "吨标准煤": "extractesg.core.unit.tonne-coal-equivalent",
    "kgco2e": "extractesg.core.unit.kilogram-co2e",
    "tco2e": "extractesg.core.unit.tonne-co2e",
    "ktco2e": "extractesg.core.unit.kilotonne-co2e",
    "mtco2e": "extractesg.core.unit.megatonne-co2e",
    "千克二氧化碳当量": "extractesg.core.unit.kilogram-co2e",
    "公斤二氧化碳当量": "extractesg.core.unit.kilogram-co2e",
    "吨二氧化碳当量": "extractesg.core.unit.tonne-co2e",
    "千吨二氧化碳当量": "extractesg.core.unit.kilotonne-co2e",
    "百万吨二氧化碳当量": "extractesg.core.unit.megatonne-co2e",
    "cny": "extractesg.core.unit.cny",
    "rmb": "extractesg.core.unit.cny",
    "人民币": "extractesg.core.unit.cny",
    "元": "extractesg.core.unit.cny",
    "usd": "extractesg.core.unit.usd",
    "美元": "extractesg.core.unit.usd",
    "eur": "extractesg.core.unit.eur",
    "欧元": "extractesg.core.unit.eur",
    "gbp": "extractesg.core.unit.gbp",
    "英镑": "extractesg.core.unit.gbp",
    "jpy": "extractesg.core.unit.jpy",
    "日元": "extractesg.core.unit.jpy",
    "hkd": "extractesg.core.unit.hkd",
    "港元": "extractesg.core.unit.hkd",
}


UNIT_DIMENSION_TERMS = {
    "mass": ("吨", "噸", "千克", "公斤", "毫克", "微克", "kg", "mg", "μg", "ug", "tonne", "tons"),
    "volume": ("立方米", "m3", "m³", "升", "litre", "liter"),
    "energy": ("瓦时", "千瓦时", "兆瓦时", "吉瓦时", "wh", "kwh", "mwh", "gwh", "twh", "mj", "gj", "tj", "btu", "tce", "toe"),
    "ghg-emissions": ("co2e", "co2eq", "co2equivalent", "二氧化碳当量"),
    "currency": ("人民币", "美元", "欧元", "英镑", "日元", "港元", "元", "cny", "rmb", "usd", "eur", "gbp", "jpy", "hkd"),
    "ratio": ("%", "百分比", "比率"),
    "energy-intensity": ("mwh/", "kwh/", "gj/", "能源强度", "energy intensity"),
    "ghg-intensity": ("co2e/", "co₂e/", "温室气体强度", "ghg intensity"),
}


def normalize_unit_text(raw: str | None) -> str:
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKC", raw).casefold()
    normalized = normalized.replace("噸", "吨").replace("當", "当").replace("萬", "万").replace("₂", "2")
    normalized = re.sub(r"\\(?:mathrm|text|operatorname)\s*", "", normalized)
    normalized = re.sub(r"[$\\{}_^\s.\-]+", "", normalized)
    normalized = normalized.replace("co2equivalent", "co2e").replace("co2eq", "co2e")
    # Normalize the numerator without dropping an intensity denominator.
    normalized = re.sub(r"^百万(?:t|吨)co2e", "mtco2e", normalized)
    normalized = re.sub(r"^千(?:t|吨)co2e", "ktco2e", normalized)
    return normalized


def canonical_unit_id(raw: str | None) -> str | None:
    if not raw:
        return None
    normalized = normalize_unit_text(raw)
    return _EXACT_UNIT_IDS.get(normalized) or _EXACT_UNIT_IDS.get(raw.strip().casefold())
