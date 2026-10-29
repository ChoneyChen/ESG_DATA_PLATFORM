from __future__ import annotations

import re
import unicodedata


# Clause contracts declare the expected unit dimension. This catalogue is
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
    "吨": "tonne",
    "噸": "tonne",
    "t": "tonne",
    "千克": "kilogram",
    "公斤": "kilogram",
    "kg": "kilogram",
    "克": "gram",
    "g": "gram",
    "毫克": "milligram",
    "mg": "milligram",
    "%": "percent",
    "％": "percent",
    "百分比": "percent",
    "wh": "watt-hour",
    "kwh": "kilowatt-hour",
    "mwh": "megawatt-hour",
    "gwh": "gigawatt-hour",
    "twh": "terawatt-hour",
    "mj": "megajoule",
    "gj": "gigajoule",
    "tj": "terajoule",
    "tce": "tonne-coal-equivalent",
    "瓦时": "watt-hour",
    "千瓦时": "kilowatt-hour",
    "兆瓦时": "megawatt-hour",
    "吉瓦时": "gigawatt-hour",
    "太瓦时": "terawatt-hour",
    "兆焦": "megajoule",
    "吉焦": "gigajoule",
    "太焦": "terajoule",
    "吨标准煤": "tonne-coal-equivalent",
    "kgco2e": "kilogram-co2e",
    "tco2e": "tonne-co2e",
    "ktco2e": "kilotonne-co2e",
    "mtco2e": "megatonne-co2e",
    "千克二氧化碳当量": "kilogram-co2e",
    "公斤二氧化碳当量": "kilogram-co2e",
    "吨二氧化碳当量": "tonne-co2e",
    "千吨二氧化碳当量": "kilotonne-co2e",
    "百万吨二氧化碳当量": "megatonne-co2e",
    "cny": "cny",
    "rmb": "cny",
    "人民币": "cny",
    "元": "cny",
    "usd": "usd",
    "美元": "usd",
    "eur": "eur",
    "欧元": "eur",
    "gbp": "gbp",
    "英镑": "gbp",
    "jpy": "jpy",
    "日元": "jpy",
    "hkd": "hkd",
    "港元": "hkd",
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


FAMILY_DIMENSION = {"ghg": "ghg-emissions", "energy": "energy", "energy_tce": "energy", "volume": "volume",
                    "mass": "mass", "ratio": "ratio", "area": "area"}
FALLBACK_DIMENSIONS = (
    ("ghg-emissions", ("co2", "二氧化碳", "碳当量")),
    ("ratio", ("%", "％", "百分比")),
    ("energy", ("wh", "千瓦时", "瓦时", "焦", "tj", "gj", "mj", "tce", "toe", "标准煤", "标煤", "btu")),
    ("volume", ("m3", "m³", "立方米", "升", "megalit", "litre", "liter")),
    ("area", ("公顷", "hectare", "ha", "平方米", "m2", "m²", "平方公里", "km2")),
    ("currency", ("元", "usd", "rmb", "cny", "hkd", "eur", "dollar")),
    ("mass", ("吨", "噸", "kg", "千克", "公斤", "tonne")),
    ("count", ("件", "次", "个", "家", "项", "起", "台", "辆", "宗", "人", "number", "cases", "no.")),
)

def unit_dimension(unit: str | None) -> str | None:
    from .quantities import unit_scale
    if not unit:
        return None
    raw_unit = unit.casefold()
    # "m3 (m3 per employee)" prints a conversion note after the unit; the base
    # unit is still m3. Only a denominator in the unit itself is an intensity.
    base_unit = raw_unit.split("(")[0].split("（")[0].strip()
    if base_unit and base_unit != raw_unit:
        base_dimension = unit_dimension(base_unit)
        if base_dimension:
            return base_dimension
    # A unit with a denominator is an intensity of its numerator, not the
    # numerator's absolute dimension ("吨CO2e/万元" is GHG intensity).
    if re.search(r"[/／]|每|per\s", raw_unit):
        if any(token in raw_unit for token in ("co2", "co₂", "二氧化碳", "碳当量")):
            return "ghg-intensity"
        if any(token in raw_unit for token in ("wh", "千瓦时", "瓦时", "焦", "tce", "toe", "标煤", "标准煤")):
            return "energy-intensity"
        if any(token in raw_unit for token in ("m3", "m³", "立方米", "升", "litre", "liter")):
            return "water-intensity"
        return "intensity"
    scaled = unit_scale(unit)
    if scaled:
        return FAMILY_DIMENSION.get(scaled[0])
    text, raw = normalize_unit_text(unit), unit.casefold()
    for dimension, tokens in FALLBACK_DIMENSIONS:
        if any(token in text or token in raw for token in tokens):
            return dimension
    return None
