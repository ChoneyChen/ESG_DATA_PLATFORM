"""Reported time, independent of publication year and clause identity."""
from __future__ import annotations
from datetime import date
from calendar import month_name, month_abbr
import re
import unicodedata
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .literals import arabic_years

YEAR = r"(?<!\d)((?:19|20|21)\d{2})(?!\d)"
MONTHS = {name.casefold(): number for number in range(1, 13) for name in (month_name[number], month_abbr[number])}
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))


class ReportedPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["annual", "fiscal_year", "quarter", "instant", "range", "unknown"]
    raw: str = Field(min_length=1)
    year: int | None = Field(default=None, ge=1900, le=2199)
    quarter: int | None = Field(default=None, ge=1, le=4)
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("Reported period starts after it ends")
        if self.kind == "quarter" and self.quarter is None:
            raise ValueError("A quarter needs its reported quarter number")
        return self


def parse_period(raw: str | None) -> ReportedPeriod | None:
    if not raw or not raw.strip():
        return None
    text = arabic_years(unicodedata.normalize("NFKC", raw)).strip()
    if re.search(r"\b(?:ISO|IEC|GRI|ESRS|IFRS|GB/T)\s*\d", text, re.I):
        return None
    years = list(dict.fromkeys(int(y) for y in re.findall(YEAR, text)))
    if not years:
        quarter = re.search(r"(?:Q\s*([1-4])|第?([一二三四1-4])季度)", text, re.I)
        if quarter:
            token=quarter.group(1) or quarter.group(2)
            number=int(token) if token.isdigit() else "一二三四".index(token)+1
            return ReportedPeriod(kind="quarter",raw=raw,quarter=number)
        if re.search(r"财年|財年|fiscal year|\bFY\b",text,re.I):
            return ReportedPeriod(kind="fiscal_year",raw=raw)
        return None
    positions = []
    for match in re.finditer(r"((?:19|20|21)\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?", text):
        try:
            positions.append((match.start(), date(*(int(x) for x in match.groups()))))
        except ValueError:
            return ReportedPeriod(kind="unknown", raw=raw)
    patterns = [(rf"\b(\d{{1,2}})\s+({MONTH_PATTERN})\s+((?:19|20|21)\d{{2}})\b", False),
                (rf"\b({MONTH_PATTERN})\s+(\d{{1,2}}),?\s+((?:19|20|21)\d{{2}})\b", True)]
    for pattern, month_first in patterns:
        for match in re.finditer(pattern, text, re.I):
            a, b, year = match.groups()
            try:
                positions.append((match.start(), date(int(year), MONTHS[(a if month_first else b).casefold()], int(b if month_first else a))))
            except ValueError:
                return ReportedPeriod(kind="unknown", raw=raw)
    dates = [value for _, value in sorted(set(positions))]
    if len(dates) >= 2:
        if dates[0] > dates[-1]:
            return ReportedPeriod(kind="unknown", raw=raw)
        calendar_year = dates[0].year == dates[-1].year and (dates[0].month,dates[0].day)==(1,1) and (dates[-1].month,dates[-1].day)==(12,31)
        return ReportedPeriod(kind="annual" if calendar_year else "range", raw=raw,
            year=dates[0].year if calendar_year else None, start=dates[0], end=dates[-1])
    if len(dates) == 1:
        if re.search(r"累计|累計|cumulative|\bYTD\b|year.to.date", text, re.I):
            return ReportedPeriod(kind="range", raw=raw, year=dates[0].year, end=dates[0])
        return ReportedPeriod(kind="instant", raw=raw, year=dates[0].year, start=dates[0], end=dates[0])
    if len(years) > 1:
        if re.search(r"财年|財年|fiscal|\bFY\b", text, re.I):
            return ReportedPeriod(kind="fiscal_year", raw=raw)
        interval = bool(re.search(r"\d{4}\s*年?\s*(?:至|到|[-–—~～]|to|through)\s*\d{4}", text, re.I))
        return ReportedPeriod(kind="range" if interval else "unknown", raw=raw)
    year = years[0]
    if re.search(r"\d{4}\s*[/／-]\s*\d{2}(?!\d)", text):
        return ReportedPeriod(kind="fiscal_year" if re.search(r"财年|財年|fiscal|\bFY", text, re.I) else "range", raw=raw)
    quarter = re.search(r"(?:Q\s*([1-4])|第?([一二三四1-4])季度)", text, re.I)
    if quarter:
        token = quarter.group(1) or quarter.group(2)
        number = int(token) if token.isdigit() else "一二三四".index(token) + 1
        return ReportedPeriod(kind="quarter", raw=raw, year=year, quarter=number)
    if re.search(r"财年|財年|fiscal|\bFY\s*\d", text, re.I):
        return ReportedPeriod(kind="fiscal_year", raw=raw, year=year)
    if re.search(r"上半年|下半年|H[12]|半年度|累计|累計|cumulative|year.to.date|\bYTD\b|月|至|到|截至", text, re.I):
        return ReportedPeriod(kind="range", raw=raw, year=year)
    return ReportedPeriod(kind="annual", raw=raw, year=year)


def normalize_period(value) -> ReportedPeriod | None:
    if isinstance(value, (str, int)):
        return parse_period(str(value))
    if isinstance(value, dict):
        body = dict(value)
        if body.get("kind") in {"specific_period", "multi_year"}:
            body["kind"] = "range"
        body.setdefault("raw", str(body.get("year") or body))
        try:
            return ReportedPeriod.model_validate(body)
        except ValueError:
            return None
    return None


def period_payload(period: ReportedPeriod):
    # Annual strings are the frozen shorthand; richer periods are explicit.
    if period.kind == "annual" and period.year is not None:
        return str(period.year)
    return period.model_dump(mode="json", exclude_none=True)


def period_label(value) -> str:
    period = normalize_period(value)
    if not period:
        return "期间未知"
    if period.kind == "annual":
        return str(period.year)
    if period.kind == "quarter" and period.year is not None:
        return f"{period.year} Q{period.quarter}"
    return period.raw
