"""Deterministic unit families and scale factors for cross-source agreement."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from .normalize import fold

_SCALE_WORDS = {
    "tenthousand": Decimal("1e4"),
    "hundred": Decimal("1e2"),
    "thousand": Decimal("1e3"),
    "million": Decimal("1e6"),
    "billion": Decimal("1e9"),
    "十亿": Decimal("1e9"),
    "亿": Decimal("1e8"),
    "千万": Decimal("1e7"),
    "百万": Decimal("1e6"),
    "十万": Decimal("1e5"),
    "万": Decimal("1e4"),
    "千": Decimal("1e3"),
}
_SCALE_WORD = re.compile("^(" + "|".join(sorted(_SCALE_WORDS,key=len,reverse=True)) + ")")

_UNIT_RULES: tuple[tuple[re.Pattern, str, Decimal], ...] = tuple(
    (re.compile(pattern), family, Decimal(scale))
    for pattern, family, scale in (
        (
            r"(?:百万|million|mt|mn)(?:metric)?t?(?:onnes?|ons?)?co2e|百万吨二氧化碳当量|mtco2",
            "ghg",
            "1e6",
        ),
        (r"万t?(?:吨)?co2e|万吨二氧化碳当量", "ghg", "1e4"),
        (r"(?:千|kt|thousand)t?(?:onnes?)?co2e|千吨二氧化碳当量|ktco2", "ghg", "1e3"),
        (r"kgco2e|千克二氧化碳当量|公斤二氧化碳当量", "ghg", "1e-3"),
        (
            r"(?:^|[^a-z])(?:metric)?t(?:onnes?|ons?)?co2e?|吨(?:二氧化碳当量|co2e?)|^tco2|tonnesco2",
            "ghg",
            "1",
        ),
        (r"twh|太瓦时", "energy", "1e6"),
        (r"亿千瓦时|亿度", "energy", "1e5"),
        (r"gwh|吉瓦时|百万千瓦时", "energy", "1e3"),
        (r"万千瓦时|万度", "energy", "10"),
        (r"mwh|兆瓦时", "energy", "1"),
        (r"千度", "energy", "1"),
        (r"kwh|千瓦时|度电|^度$", "energy", "1e-3"),
        (r"pj|拍焦", "energy", "277777.778"),
        (r"tj|太焦", "energy", "277.777778"),
        (r"gj|吉焦|十亿焦", "energy", "0.277777778"),
        (r"mj|兆焦", "energy", "0.000277777778"),
        (r"kj|千焦", "energy", "0.000000277777778"),
        (r"万吨标准煤|万tce", "energy_tce", "1e4"),
        (r"吨标准煤|tce|标煤", "energy_tce", "1"),
        (r"百万立方米|百万m3|millionm3|mm3|百萬立方米", "volume", "1e6"),
        (r"万立方米|万m3|萬立方米|tenthousand(?:m3|cubicmet)", "volume", "1e4"),
        (r"(?:million|百万)lit(?:er|re)s?", "volume", "1e3"),
        (r"(?:thousand|千)lit(?:er|re)s?", "volume", "1"),
        (r"千立方米|千m3|thousandm3|megalit|^ml$|兆升", "volume", "1e3"),
        (r"立方米|m3|m³|cubicmet", "volume", "1"),
        (r"lit(?:er|re)s?|升|^l$", "volume", "1e-3"),
        (r"平方公里|km2|km²|squarekilomet", "area", "100"),
        (r"公顷|hectares?|^ha$", "area", "1"),
        (r"平方米|squaremet|m2|m²|sqm", "area", "1e-4"),
        (r"百万吨|百萬噸|milliontonnes?|^mt$", "mass", "1e6"),
        (r"万吨|萬噸|tenthousand(?:tonnes?|tons?)", "mass", "1e4"),
        (r"千吨|kt$|^kt|thousandtonnes?", "mass", "1e3"),
        (r"千克|公斤|kg", "mass", "1e-3"),
        (r"公吨|吨|噸|tonnes?|^t$|\(t\)|metricton", "mass", "1"),
        (r"%|％|百分比|percent", "ratio", "1"),
    )
)


def _normalize(raw: str) -> str:
    text = fold(unicodedata.normalize("NFKC", raw))
    text = re.sub(r"carbon\s+dioxide\s+equivalent", "co2e", text)
    text = re.sub(r"\\(?:mathrm|text|operatorname)", "", text)
    text = (
        text.replace("₂", "2")
        .replace("co_2", "co2")
        .replace("co_{2}", "co2")
        .replace("co_{{2}}", "co2")
    )
    text = re.sub(r"[\s$\\{}_^]+", "", text)
    text = (
        text.replace("co2equivalent", "co2e")
        .replace("co2-e", "co2e")
        .replace("co2eq", "co2e")
    )
    text = re.sub(r"((?:metric)?(?:tonnes?|tons?|kilograms?|grams?))of(?=co2e)", r"\1", text)
    text = re.sub(r"^(co2e?)\(([^()]+)\)$",r"\2\1",text)
    return text


def bilingual_unit_parts(text: str) -> tuple[str, str] | None:
    first = re.sub(r"[一-鿿]", "", text).strip()
    second = "".join(re.findall(r"[一-鿿]", text))
    return (first,second) if first and second else None


def unit_scale(raw: str | None) -> tuple[str, Decimal] | None:
    if not raw:
        return None
    printed = unicodedata.normalize("NFKC", raw).strip()
    parts = bilingual_unit_parts(printed)
    if parts:
        first,second = (unit_scale(part) for part in parts)
        if first is not None and second is not None:
            return first if first == second else None
    explicit_scale = re.match(r"^(10{3,9}|(?:1|10|100)(?:[,，]000)+)\s*(?=[A-Za-z一-鿿])", printed)
    multiplier = Decimal(explicit_scale[1].replace(",", "").replace("，", "")) if explicit_scale else Decimal(1)
    text = _normalize(printed[explicit_scale.end():] if explicit_scale else printed)
    if "/" in text or "每" in text or "per" in text:
        return None
    candidates = [(text,multiplier)]
    word_scale = _SCALE_WORD.match(text)
    if word_scale:
        candidates.insert(0,(text[word_scale.end():],multiplier*_SCALE_WORDS[word_scale[1]]))
    for candidate,factor in candidates:
        for pattern, family, scale in _UNIT_RULES:
            if pattern.search(candidate):
                return family, scale * factor
    return None


# Exact research conversions. The agreement heuristics above also accept
# approximate conversions and cannot establish an analytical unit identity.
_STANDARD_SCALES = {
    "tonne": ("tonne", "1"),
    "kilogram": ("tonne", "0.001"),
    "gram": ("tonne", "0.000001"),
    "milligram": ("tonne", "0.000000001"),
    "kilogram-co2e": ("tonne-co2e", "0.001"),
    "tonne-co2e": ("tonne-co2e", "1"),
    "kilotonne-co2e": ("tonne-co2e", "1e3"),
    "megatonne-co2e": ("tonne-co2e", "1e6"),
    "watt-hour": ("megajoule", "0.0036"),
    "kilowatt-hour": ("megajoule", "3.6"),
    "megawatt-hour": ("megajoule", "3600"),
    "gigawatt-hour": ("megajoule", "3600000"),
    "terawatt-hour": ("megajoule", "3600000000"),
    "megajoule": ("megajoule", "1"),
    "gigajoule": ("megajoule", "1000"),
    "terajoule": ("megajoule", "1000000"),
    "tonne-coal-equivalent": ("tonne-coal-equivalent", "1"),
}
_STANDARD_ALIASES = tuple(
    (re.compile(pattern), unit, Decimal(factor))
    for pattern, unit, factor in (
        (r"(?:metric)?tonnes?|metrictons?", "tonne", "1"),
        (r"kt|kilotonnes?", "tonne", "1000"),
        (r"kilograms?", "tonne", "0.001"),
        (r"grams?", "tonne", "0.000001"),
        (r"(?:t|(?:metric)?tonnes?|metrictons?)(?:of)?co2e", "tonne-co2e", "1"),
        (r"(?:t|(?:metric)?tonnes?|metrictons?)(?:of)?co2", "tonne-co2", "1"),
        (r"kgco2", "tonne-co2", "0.001"),
        (r"m3|立方米|cubicmet(?:er|re)s?", "cubic-metre", "1"),
        (r"megalit(?:er|re)s?|兆升", "cubic-metre", "1000"),
        (r"l|升|lit(?:er|re)s?", "cubic-metre", "0.001"),
        (r"m2|sqm|平方米|squaremet(?:er|re)s?", "square-metre", "1"),
        (r"ha|公顷|hectares?", "square-metre", "10000"),
        (r"km2|平方公里", "square-metre", "1000000"),
        (r"%|percent(?:age)?|百分比", "percent", "1"),
        (r"percentagepoints?|百分点", "percentage-point", "1"),
        (r"persons?|people|employees?|headcount|人|名", "person", "1"),
        (r"hours?|hrs?|小时", "hour", "1"),
        (r"days?|天", "day", "1"),
        (r"w|watts?|瓦", "watt", "1"),
        (r"kw|kilowatts?|千瓦", "watt", "1000"),
        (r"mw|megawatts?|兆瓦", "watt", "1000000"),
        (r"gw|gigawatts?|吉瓦", "watt", "1000000000"),
        (r"wp", "watt-peak", "1"),
        (r"kwp", "watt-peak", "1000"),
        (r"mwp", "watt-peak", "1000000"),
        (r"gwp", "watt-peak", "1000000000"),
        (r"kj|kilojoules?|千焦", "megajoule", "0.001"),
        (r"j|joules?|焦耳", "megajoule", "0.000001"),
        (r"pj|petajoules?|拍焦", "megajoule", "1000000000"),
    )
)
_UNCHANGED_UNITS = {"cny", "usd", "eur", "gbp", "jpy", "hkd", "percent"}
PRINTED_MAGNITUDE = re.compile(
    r"^(?P<coefficient>[1-9]\d{0,2})?\s*(?P<word>ten\s*thousand|hundred|thousands?|"
    r"millions?|billions?|十亿|[亿億]|千万|千萬|百万|百萬|十万|十萬|[万萬]|千)\s*", re.I
)
EXPLICIT_SCALE = re.compile(r"^(10{3,9}|(?:1|10|100)(?:[,，]000)+)\s*(?=[A-Za-z一-鿿])")


def printed_magnitude(match: re.Match) -> Decimal:
    word = re.sub(r"\s+", "", fold(match["word"])).rstrip("s")
    return Decimal(match["coefficient"] or 1) * _SCALE_WORDS[word]


def standard_unit(raw: str | None) -> tuple[str, Decimal] | None:
    """Return a known unit and its exact multiplier for the printed measurement."""
    if not raw:
        return None
    from .units import canonical_unit_id

    printed = unicodedata.normalize("NFKC", raw).strip()
    parts = re.split(r"\s*(?:/|\bper\b|每)\s*", printed, flags=re.I)
    if len(parts) == 2:
        numerator, denominator = map(standard_unit, parts)
        if numerator and denominator:
            unit = f"{numerator[0]}/{denominator[0]}"
            if numerator[1] == denominator[1]:
                return unit, Decimal(1)
            _, digits, exponent = denominator[1].normalize().as_tuple()
            if digits == (1,):
                return unit, numerator[1].scaleb(-exponent)
        return None
    if len(parts) != 1:
        return None
    if printed == "ML":
        return "cubic-metre", Decimal(1000)
    if printed == "mL":
        return "cubic-metre", Decimal("0.000001")
    bilingual = bilingual_unit_parts(printed)
    if bilingual:
        first, second = map(standard_unit, bilingual)
        if first and second:
            return first if first == second else None

    body = printed
    explicit_scale = EXPLICIT_SCALE.match(body)
    multiplier = Decimal(explicit_scale[1].replace(",", "")) if explicit_scale else Decimal(1)
    body = body[explicit_scale.end():] if explicit_scale else body
    currency_with_scale = re.fullmatch(r"([A-Za-z]{3})\s*(10{3,9}|(?:1|10|100)(?:,000)+)", body)
    if currency_with_scale and (currency := canonical_unit_id(currency_with_scale[1])) in _UNCHANGED_UNITS:
        return currency, multiplier * Decimal(currency_with_scale[2].replace(",", ""))
    if word_scale := PRINTED_MAGNITUDE.match(body):
        base = standard_unit(body[word_scale.end():])
        if base:
            return base[0], base[1] * multiplier * printed_magnitude(word_scale)
    if body == "mWh":
        return "megajoule", Decimal("0.0000036") * multiplier
    if body == "mW":
        return "watt", Decimal("0.001") * multiplier
    normalized = _normalize(body)
    if normalized == "mtco2e" and not body.startswith("Mt"):
        return None
    unit = canonical_unit_id(body)
    if unit in _UNCHANGED_UNITS:
        return unit, multiplier
    if unit in _STANDARD_SCALES:
        target, factor = _STANDARD_SCALES[unit]
        return target, multiplier * Decimal(factor)
    for pattern, target, factor in _STANDARD_ALIASES:
        if pattern.fullmatch(normalized):
            return target, multiplier * factor
    if alias := re.fullmatch(r"([^()]+)\(([^()]+)\)", body):
        main, symbol = map(standard_unit, alias.groups())
        if main and main == symbol:
            return main[0], multiplier * main[1]

    return None


def comparable(value: str | None, unit_raw: str | None) -> tuple[str, Decimal] | None:
    if value is None:
        return None
    scaled = unit_scale(unit_raw)
    if scaled is None:
        return None
    family, scale = scaled
    if family == "mass":
        family = "mass_or_water"
    return family, Decimal(value) * scale


COMPATIBLE = {frozenset({"volume", "mass_or_water"})}


def agreement(
    a: dict, b: dict, *, tolerance: Decimal = Decimal("0.011")
) -> bool | None:
    """True/False when both quantities are in comparable units, else None."""
    qa, qb = (
        comparable(a.get("value"), a.get("unit_raw")),
        comparable(b.get("value"), b.get("unit_raw")),
    )
    if qa is None or qb is None:
        if (
            a.get("value") is not None
            and a.get("value") == b.get("value")
            and (a.get("unit_raw") or "") == (b.get("unit_raw") or "")
        ):
            return True
        return None
    if qa[0] != qb[0] and frozenset({qa[0], qb[0]}) not in COMPATIBLE:
        return None
    big = max(abs(qa[1]), abs(qb[1]))
    return big == 0 or abs(qa[1] - qb[1]) <= big * tolerance
