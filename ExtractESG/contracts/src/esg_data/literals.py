"""Literal parsing is physical data normalization, independent of clauses and workflows."""
from __future__ import annotations
import re
from decimal import Decimal,InvalidOperation

PERIOD_RE = re.compile(r"(?<!\d)(?:FY\s?)?((?:19|20)\d{2})(?:\s*[/／-]\s*(\d{4}|\d{2}))?(?!\d)", re.I)


NUMBER_RE = re.compile(
    r"^[<>≤≥~约約]?\s*[\(（]?\s*[-−–+]?\s*(?:\d{1,3}(?:[,，\s]\d{3})+|\d+)(?:\.\d+)?\s*[\)）]?\s*[%％]?\s*[*#¹²³⁴⁵⁶⁷⁸⁹⁰]*$"
)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\\n", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


SUPERSCRIPT_MARK_RE = re.compile(r"\$?\s*\^\s*\{+\s*\(?\s*[\dA-Za-z]{1,3}\s*\)?\s*\}+\s*\$?"
                                 r"|[⁽]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[,，、][⁰¹²³⁴⁵⁶⁷⁸⁹]+)*[⁾]?")


TRAILING_SUPERSCRIPT_RE = re.compile(r"(?<=[\d%％)）])\s*[¹²³⁴⁵⁶⁷⁸⁹⁰]+(?:[,，、][¹²³⁴⁵⁶⁷⁸⁹⁰]+)*$")


TRAILING_FOOTNOTE_RE = re.compile(r"^(.*?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+))\s+\(?\d{1,2}\)?$")


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
    negative = bool(re.search(r"[\(（].*[\)）]", raw)) or bool(re.match(r"^[<>≤≥~约約]?\s*[\(（]?\s*[-−–]", raw))
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits or digits.count(".") > 1:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


_RANGE_RE = re.compile(r"^\s*([-+]?\d[\d,，.]*)\s*(?:-|–|—|~|～|至|到)\s*([-+]?\d[\d,，.]*)")


def parse_measure(text: str | None) -> dict | None:
    """A number together with how the report qualifies it.

    Open bounds and ranges stay typed: the printed value is preserved, while
    the platform receives a comparator and lower/upper bounds instead of a
    silently truncated exact number.
    """
    if not text:
        return None
    raw = number_text(text)
    if not raw:
        return None
    match = _RANGE_RE.match(raw.replace(" ", ""))
    if match and not re.search(r"\d", raw.replace(" ", "")[match.end():]):
        low, high = parse_number(match.group(1)), parse_number(match.group(2))
        if low is not None and high is not None and low <= high:
            return {"value": None, "comparator": "range", "lower": low, "upper": high}
    comparator = "exact"
    if re.search(r"≥|>=|不少于|不低于|至少", raw):
        comparator = "at_least"
    elif re.search(r"≤|<=|不超过|不高于|至多", raw):
        comparator = "at_most"
    elif re.search(r">|大于|高于|多于", raw):
        comparator = "greater_than"
    elif re.search(r"<|小于|低于|少于", raw):
        comparator = "less_than"
    elif re.search(r"约|大約|大约|約|approximately|approx\.?|~|～|≈", raw, re.I):
        comparator = "approx"
    cleaned = re.sub(r"不少于|不低于|不超过|不高于|至少|至多|大于|高于|多于|小于|低于|少于|大約|大约|approximately|approx\.?|约|約|≥|>=|≤|<=|>|<|~|～|≈",
                     "", raw, flags=re.I).strip()
    value = parse_number(cleaned)
    if value is None:
        return None
    return {"value": value, "comparator": comparator,
            "lower": value if comparator in {"greater_than","at_least"} else None,
            "upper": value if comparator in {"less_than","at_most"} else None}


CN_DIGITS = {"零": "0", "〇": "0", "○": "0", "Ｏ": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
             "六": "6", "七": "7", "八": "8", "九": "9"}


CN_YEAR_RE = re.compile(r"([零〇○Ｏ一二三四五六七八九]{4})(?=\s*年)")


def arabic_years(text: str | None) -> str:
    """二零二五年 -> 2025年; other characters unchanged."""
    return CN_YEAR_RE.sub(lambda m: "".join(CN_DIGITS[ch] for ch in m.group(1)), text or "")


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
