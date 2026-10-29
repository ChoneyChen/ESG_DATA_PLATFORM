"""Deterministic unit families and scale factors for cross-source agreement."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from .normalize import fold

_UNIT_RULES: tuple[tuple[re.Pattern, str, Decimal], ...] = tuple(
    (re.compile(pattern), family, Decimal(scale))
    for pattern, family, scale in (
        (r"(?:百万|million|mt|mn)t?(?:onnes?)?co2e|百万吨二氧化碳当量|mtco2", "ghg", "1e6"),
        (r"万t?(?:吨)?co2e|万吨二氧化碳当量", "ghg", "1e4"),
        (r"(?:千|kt|thousand)t?(?:onnes?)?co2e|千吨二氧化碳当量|ktco2", "ghg", "1e3"),
        (r"kgco2e|千克二氧化碳当量|公斤二氧化碳当量", "ghg", "1e-3"),
        (r"(?:^|[^a-z])t(?:onnes?|ons?)?co2e|吨二氧化碳当量|^tco2|tonnesco2", "ghg", "1"),
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
        (r"万吨标准煤|万tce", "energy_tce", "1e4"),
        (r"吨标准煤|tce|标煤", "energy_tce", "1"),
        (r"百万立方米|百万m3|millionm3|mm3|百萬立方米", "volume", "1e6"),
        (r"万立方米|万m3|萬立方米", "volume", "1e4"),
        (r"千立方米|千m3|thousandm3|megalit|^ml$|兆升", "volume", "1e3"),
        (r"立方米|m3|m³|cubicmet", "volume", "1"),
        (r"平方公里|km2|km²|squarekilomet", "area", "100"),
        (r"公顷|hectares?|^ha$", "area", "1"),
        (r"平方米|squaremet|m2|m²|sqm", "area", "1e-4"),
        (r"百万吨|百萬噸|milliontonnes?|^mt$", "mass", "1e6"),
        (r"万吨|萬噸", "mass", "1e4"),
        (r"千吨|kt$|^kt|thousandtonnes?", "mass", "1e3"),
        (r"千克|公斤|kg", "mass", "1e-3"),
        (r"公吨|吨|噸|tonnes?|^t$|\(t\)|metricton", "mass", "1"),
        (r"%|％|百分比|percent", "ratio", "1"),
    )
)


def _normalize(raw: str) -> str:
    text = fold(unicodedata.normalize("NFKC", raw))
    text = re.sub(r"\\(?:mathrm|text|operatorname)", "", text)
    text = text.replace("₂", "2").replace("co_2", "co2").replace("co_{2}", "co2").replace("co_{{2}}", "co2")
    text = re.sub(r"[\s$\\{}_^]+", "", text)
    text = text.replace("co2eq", "co2e").replace("co2equivalent", "co2e").replace("co2-e", "co2e")
    return text


def unit_scale(raw: str | None) -> tuple[str, Decimal] | None:
    if not raw:
        return None
    text = _normalize(raw)
    if "/" in text or "每" in text or "per" in text:
        return None
    for pattern, family, scale in _UNIT_RULES:
        if pattern.search(text):
            return family, scale
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


def agreement(a: dict, b: dict, *, tolerance: Decimal = Decimal("0.011")) -> bool | None:
    """True/False when both quantities are in comparable units, else None."""
    qa, qb = comparable(a.get("value"), a.get("unit_raw")), comparable(b.get("value"), b.get("unit_raw"))
    if qa is None or qb is None:
        if a.get("value") is not None and a.get("value") == b.get("value") and (a.get("unit_raw") or "") == (b.get("unit_raw") or ""):
            return True
        return None
    if qa[0] != qb[0] and frozenset({qa[0], qb[0]}) not in COMPATIBLE:
        return None
    big = max(abs(qa[1]), abs(qb[1]))
    return big == 0 or abs(qa[1] - qb[1]) <= big * tolerance
