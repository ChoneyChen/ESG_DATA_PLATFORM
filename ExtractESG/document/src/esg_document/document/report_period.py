"""Reporting-period evidence from the report itself, never from the largest year."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from esg_data.text_search import sentence_ranges


RANGE_PATTERNS = (
    re.compile(
        r"((?:19|20)\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*(?:至|到|－|—|-|~|～)\s*((?:19|20)\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    ),
    re.compile(
        r"\b\d{1,2}\s+[A-Z][a-z]+\s+(?:((?:19|20)\d{2})\s*)?(?:to|until|through|-|–)\s*\d{1,2}\s+[A-Z][a-z]+\s+((?:19|20)\d{2})",
        re.I,
    ),
    re.compile(
        r"\b[A-Z][a-z]+\s+\d{1,2},?\s+(?:((?:19|20)\d{2})\s*)?(?:to|until|through|-|–)\s*[A-Z][a-z]+\s+\d{1,2},?\s+((?:19|20)\d{2})",
        re.I,
    ),
)
PERIOD_STATEMENT_RE = re.compile(
    r"(?:报告(?:时间)?(?:期|范围|周期|期间)|本报告(?:涵盖|覆盖)|时间范围|reporting\s+period|report(?:ing)?\s+covers|"
    r"covers\s+the\s+period|financial\s+year|fiscal\s+year)[^。.;；]{0,60}?((?:19|20)\d{2})",
    re.I,
)
PERIOD_LABEL_RE = re.compile(
    r"报告(?:期|时间|范围|周期)|本报告(?:涵盖|覆盖)|时间范围|reporting\s+period|"
    r"report\s+cover(?:s|ing)\b|fiscal\s+year|financial\s+year", re.I,
)
OTHER_PERIOD_RE = re.compile(
    r"\b(?:compar(?:ison|ative|ed|ing)|against|targets?|goals?|objectives?)\b|"
    r"同比|对比|對比|比较|比較|目标|目標", re.I,
)
LABEL_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
TITLE_YEAR_RES = (
    re.compile(
        r"((?:19|20)\d{2})\s*(?:年度?)?\s*(?:[^\s\d]{0,12}?)(?:可持续发展|社会责任|ESG|环境、社会及管治|环境、社会和治理|"
        r"sustainability|corporate\s+responsibility|environmental,\s+social)[^。.]{0,20}?(?:报告|報告|report)",
        re.I,
    ),
    re.compile(
        r"(?:sustainability|esg|corporate\s+responsibility)\s+report\s+((?:19|20)\d{2})",
        re.I,
    ),
)


@dataclass(frozen=True)
class ReportingPeriod:
    year: int | None
    basis: str
    evidence: str | None
    source_id: str | None


STANDARD_CODE_PREFIX_RE = re.compile(
    r"(?:iso|iec|gb\s*/?\s*t?|esrs|ifrs|sasb|gri|astm|en|bs|jis)\s*$", re.I
)


def _year_is_standard_code(text: str, start: int) -> bool:
    """`ISO 14067:2018` names a standard, not the reporting period."""
    prefix = text[max(0, start - 16) : start]
    if re.search(r"[:：/／]\s*$", prefix):
        return True
    return bool(STANDARD_CODE_PREFIX_RE.search(prefix))


def _range_is_reporting_statement(text: str, match: re.Match) -> bool:
    """A report-period label cannot authorize a comparison in another clause."""
    for start, end in sentence_ranges(text):
        if not start <= match.start() < match.end() <= end:
            continue
        labels = list(PERIOD_LABEL_RE.finditer(text, start, end))
        preceding = [label for label in labels if label.end() <= match.start()]
        if preceding:
            return not OTHER_PERIOD_RE.search(text, preceding[-1].end(), match.start())
        following = [label for label in labels if label.start() >= match.end()]
        if following:
            # A following parenthetical can explicitly name this date range.
            label = following[0]
            clause_start = max(start, text.rfind(",", start, match.start()) + 1,
                text.rfind(";", start, match.start()) + 1)
            return (label.start() - match.end() <= 80
                and not OTHER_PERIOD_RE.search(text, clause_start, label.end()))
        subject = re.search(r"\b(?:the|this)\s+report\b", text[start:match.start()], re.I)
        period = re.search(r"\b(?:in|for|during)\s+(?:the\s+)?period\s+of\s*$",
            text[start:match.start()], re.I)
        if subject and period:
            return not OTHER_PERIOD_RE.search(text, start + subject.end(), match.start())
    return False


def detect_reporting_period(
    manifest: dict,
    pages: list[dict],
    *,
    header_years: list[int] | None = None,
    scan_pages: int = 12,
) -> ReportingPeriod:
    for payload in pages[:scan_pages]:
        for block in payload.get("blocks") or []:
            text = str(block.get("text") or "")
            if not text:
                continue
            for pattern in RANGE_PATTERNS:
                for match in pattern.finditer(text):
                    if not _range_is_reporting_statement(text, match):
                        continue
                    end = int(match.group(2))
                    start = int(match.group(1)) if match.group(1) else end
                    if 0 <= end - start <= 1:
                        return ReportingPeriod(
                            end,
                            "reporting_period_statement",
                            match.group(0)[:160],
                            block["block_id"],
                        )
    for payload in pages[:scan_pages]:
        for block in payload.get("blocks") or []:
            text = str(block.get("text") or "")
            for match in PERIOD_STATEMENT_RE.finditer(text or ""):
                if _year_is_standard_code(text or "", match.start(1)):
                    continue
                if re.search(r"\b(?:compar(?:ison|ative|ed|ing)|against)\b|同比|对比|對比|比较|比較",
                    text[match.start():match.start(1)], re.I):
                    continue
                return ReportingPeriod(
                    int(match.group(1)),
                    "reporting_period_statement",
                    match.group(0)[:160],
                    block["block_id"],
                )
    title_years: Counter = Counter()
    title_evidence: dict[int, tuple[str, str]] = {}
    for payload in pages[:8]:
        for block in payload.get("blocks") or []:
            text = str(block.get("text") or "")
            for pattern in TITLE_YEAR_RES:
                for match in pattern.finditer(text or ""):
                    year = int(match.group(1))
                    title_years[year] += 1
                    title_evidence.setdefault(
                        year, (match.group(0)[:160], block["block_id"])
                    )
    if title_years:
        year, _ = max(title_years.items(), key=lambda item: (item[1], item[0]))
        evidence, block_id = title_evidence[year]
        return ReportingPeriod(year, "report_title_in_document", evidence, block_id)
    label = str(manifest.get("document_label") or "")
    years = sorted({int(y) for y in LABEL_YEAR_RE.findall(label)})
    if len(years) == 1:
        return ReportingPeriod(years[0], "report_file_label", label, None)
    if header_years:
        counts = Counter(header_years)
        year, _ = max(counts.items(), key=lambda item: (item[1], item[0]))
        return ReportingPeriod(year, "most_common_table_header_year", None, None)
    if years:
        return ReportingPeriod(None, "ambiguous_title_years", label, None)
    return ReportingPeriod(None, "unknown", None, None)
