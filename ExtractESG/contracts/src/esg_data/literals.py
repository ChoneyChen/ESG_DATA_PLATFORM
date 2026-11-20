"""Literal parsing is physical data normalization, independent of clauses and workflows."""

from __future__ import annotations
import re
import unicodedata
from decimal import Decimal, InvalidOperation

PERIOD_RE = re.compile(
    r"(?<!\d)(?:FY\s?)?((?:19|20)\d{2})(?:\s*[/／-]\s*(\d{4}|\d{2}))?(?!\d)", re.I
)


NUMBER_RE = re.compile(
    r"^[<>≤≥~约約]?\s*[\(（]?\s*[-−–+]?\s*(?:\d{1,3}(?:(?:[,，]\s*|\s+)\d{3})+|\d+)(?:\.\d+)?\s*[\)）]?\s*[%％]?\s*[*#¹²³⁴⁵⁶⁷⁸⁹⁰]*$"
)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\\n", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


SUPERSCRIPT_MARK_RE = re.compile(
    r"\$?\s*\^\s*\{+\s*\(?\s*[\dA-Za-z]{1,3}\s*\)?\s*\}+\s*\$?"
    r"|[⁽]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[,，、][⁰¹²³⁴⁵⁶⁷⁸⁹]+)*[⁾]?"
)


TRAILING_SUPERSCRIPT_RE = re.compile(
    r"(?<=[\d%％)）])\s*[¹²³⁴⁵⁶⁷⁸⁹⁰]+(?:[,，、][¹²³⁴⁵⁶⁷⁸⁹⁰]+)*$"
)


TRAILING_FOOTNOTE_RE = re.compile(
    r"^(.*?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+))\s+\(?\d{1,2}\)?$"
)


def number_text(text: str | None) -> str:
    """Cell text without footnote marks printed after the figure ("8,432,549 4", "2,557 $^{(7)}$")."""
    raw = TRAILING_SUPERSCRIPT_RE.sub("", (text or "").strip()).strip()
    if NUMBER_RE.match(raw):
        return raw
    stripped = SUPERSCRIPT_MARK_RE.sub("", raw).strip()
    match = TRAILING_FOOTNOTE_RE.match(stripped)
    if match:
        stripped = match.group(1).strip()
    return stripped if NUMBER_RE.match(stripped) else raw


def parse_number(text: str | None) -> Decimal | None:
    if not text:
        return None
    raw = number_text(text)
    if not NUMBER_RE.match(raw):
        return None
    negative = bool(re.search(r"[\(（].*[\)）]", raw)) or bool(
        re.match(r"^[<>≤≥~约約]?\s*[\(（]?\s*[-−–]", raw)
    )
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits or digits.count(".") > 1:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


_POINT = r"[-−+]?\s*(?:\d{1,3}(?:(?:[,，]\s*|\s+)\d{3})+|\d+)(?:\.\d+)?"
_RANGE_RE = re.compile(rf"^({_POINT})\s*(?:-|–|—|~|～|至|到|\bto\b)\s*({_POINT})$", re.I)
_BETWEEN_RE = re.compile(rf"^between\s+({_POINT})\s+and\s+({_POINT})$", re.I)
_RANGE_QUALIFIER = re.compile(r"\s+(?:or\s+more|and\s+above|or\s+less|and\s+below)$|(?<=\d)\s*\+$", re.I)
_APPROXIMATION = r"大约|大約|约|約|\b(?:approximately|about|around|circa)\b|\bapprox\b\.?|~|～|≈"
_QUALIFIERS = (
    ("at_least", r"≥|>=|不少于|不低于|至少|at least|no less than"),
    ("at_most", r"≤|<=|不超过|不高于|至多|at most|no more than|up to"),
    ("greater_than", r">|大于|高于|多于|more than|greater than|over"),
    ("less_than", r"<|小于|低于|少于|less than|under|below"),
    ("approx", _APPROXIMATION),
)
_SCIENTIFIC_RE = re.compile(rf"^({_POINT})\s*(?:[Ee]|[×x]\s*10\s*\^)\s*([+-]?\d{{1,2}})$")
_SUPER_EXPONENT = re.compile(r"([×x]\s*10)\s*([⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,2})$")
_PERCENT = re.compile(r"(?<=\d)\s*([%％])(?=\s*\D|$)")
_UNIT_BOUNDARY = re.compile(r"(?<=[\d+%％)）*¹²³⁴⁵⁶⁷⁸⁹⁰])(?=\s*[^\W\d_]|\s*[%％])")


def parse_measure(text: str | None) -> dict | None:
    """A number together with how the report qualifies it.

    Open bounds and ranges stay typed: the printed value is preserved, while
    the platform receives a comparator and lower/upper bounds instead of a
    silently truncated exact number.
    """
    return parse_printed_measure(text)[0]


def _parse_measure_literal(text: str) -> dict | None:
    raw = number_text(_SUPER_EXPONENT.sub(
        lambda match: match[1] + "^" + unicodedata.normalize("NFKC", match[2]), text
    ))
    match = _RANGE_RE.fullmatch(raw) or _BETWEEN_RE.fullmatch(raw)
    if match:
        low, high = (parse_number(part.strip()) for part in match.groups())
        if low is not None and high is not None and low <= high:
            return {"value": None, "comparator": "range", "lower": low, "upper": high}
    comparator = "exact"
    if boundary := _RANGE_QUALIFIER.search(raw):
        comparator = "at_least" if boundary[0].strip().casefold() in {"or more", "and above", "+"} else "at_most"
        raw = raw[:boundary.start()]
    for kind, pattern in _QUALIFIERS:
        prefix = re.match(rf"^(?:{pattern})\s*(?=[−+\-\d(（])", raw, re.I)
        if prefix:
            comparator, raw = kind, raw[prefix.end():]
            break
    value = parse_number(raw)
    if value is None and (scientific := _SCIENTIFIC_RE.fullmatch(unicodedata.normalize("NFKC", raw))):
        mantissa = parse_number(scientific[1])
        if mantissa is not None:
            sign, digits, exponent = mantissa.as_tuple()
            value = Decimal((sign, digits, exponent + int(scientific[2])))
    if value is None:
        return None
    return {
        "value": value,
        "comparator": comparator,
        "lower": value if comparator in {"greater_than", "at_least"} else None,
        "upper": value if comparator in {"less_than", "at_most"} else None,
    }


def parse_printed_measure(text: str | None, unit_raw: str | None = None) -> tuple[dict | None, str | None]:
    """Parse a complete printed expression and separate a unit printed with it."""
    if not text:
        return None, unit_raw
    from .quantities import EXPLICIT_SCALE, PRINTED_MAGNITUDE, printed_magnitude, standard_unit
    from .units import is_unit_expression

    raw = text.strip()
    literal = _parse_measure_literal(raw)
    if literal is not None:
        percent = _PERCENT.search(raw)
        return literal, unit_raw or (percent[1] if percent else None)
    percentages = list(_PERCENT.finditer(raw))
    if 1 <= len(percentages) <= 2 and not raw[percentages[-1].end():].strip():
        literal = _parse_measure_literal(_PERCENT.sub("", raw))
        if literal is not None and literal["comparator"] == "range":
            return literal, unit_raw or percentages[-1][1]
    for boundary in _UNIT_BOUNDARY.finditer(raw):
        amount, unit = raw[:boundary.start()].rstrip(), raw[boundary.start():].strip()
        # Visual extraction may split the printed multiplier from its unit.
        if unit_raw and (magnitude := PRINTED_MAGNITUDE.fullmatch(unit)):
            printed = unicodedata.normalize("NFKC", unit_raw).strip()
            printed_scale = None
            if scale := EXPLICIT_SCALE.match(printed):
                printed_scale = Decimal(scale[1].replace(",", ""))
            elif scale := PRINTED_MAGNITUDE.match(printed):
                printed_scale = printed_magnitude(scale)
            if printed_scale is not None:
                # The unit may already contain the same multiplier, but two printed
                # multipliers that disagree cannot establish the quantity.
                if printed_magnitude(magnitude) != printed_scale:
                    continue
                unit = unit_raw.strip()
            else:
                unit = f"{unit} {unit_raw.strip()}"
        if not (is_unit_expression(unit) or standard_unit(unit)):
            continue
        literal = _parse_measure_literal(amount)
        if literal is not None:
            return literal, unit
    return None, unit_raw


def has_reported_approximation(label: str | None, raw: str, page_text: str) -> bool:
    """Confirm a qualifier that was transcribed with the label instead of the value."""
    if not label or not page_text:
        return False
    amount = number_text(raw)
    if parse_number(amount) is None:
        return False
    literal = r"\s+".join(re.escape(part) for part in amount.split())
    return bool(re.search(_APPROXIMATION, label, re.I) and
        re.search(rf"(?:{_APPROXIMATION})[\s.:：]*{literal}(?![\d.,，])", page_text, re.I))


CN_DIGITS = {
    "零": "0",
    "〇": "0",
    "○": "0",
    "Ｏ": "0",
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}


CN_YEAR_RE = re.compile(r"([零〇○Ｏ一二三四五六七八九]{4})(?=\s*年)")


def arabic_years(text: str | None) -> str:
    """二零二五年 -> 2025年; other characters unchanged."""
    return CN_YEAR_RE.sub(
        lambda m: "".join(CN_DIGITS[ch] for ch in m.group(1)), text or ""
    )


def period_years(text: str) -> list[int]:
    years = []
    for match in PERIOD_RE.finditer(arabic_years(text)):
        start = int(match.group(1))
        end = match.group(2)
        if end:
            end_year = int(end) if len(end) == 4 else (start // 100) * 100 + int(end)
            if start < end_year <= start + 1:
                years.append(end_year)
                continue
        years.append(start)
    return years


def strip_text_footnotes(text: str | None) -> str:
    """Remove word-end superscript note marks for matching, preserving unit powers."""
    return re.sub(r"([A-Za-z]{3,}|[一-鿿]{2,})[⁰¹²³⁴⁵⁶⁷⁸⁹]+(?=\W|$)", r"\1", text or "")
