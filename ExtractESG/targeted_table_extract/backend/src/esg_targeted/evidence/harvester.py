from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from esg_targeted.contracts import EvidenceSpan, LiteralCandidate
from esg_targeted.evidence.quality import is_navigation_span
from esg_targeted.evidence.units import (
    GHG_FORMULA_RE,
    MEASUREMENT_UNIT_PATTERN,
    UNIT_RE,
    canonical_unit_id,
)
from esg_targeted.ids import stable_id


NUMBER = r"[-+]?\d{1,3}(?:[,，]\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?"
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?=\s*年|\b)")
DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?")
PERCENT_RE = re.compile(rf"(?P<value>{NUMBER})\s*(?P<unit>%|％|百分比)")
QUANTITY_RE = re.compile(
    rf"(?P<comparator>约|大约|近|超过|不少于|不低于|低于|少于|≤|≥|<|>)?\s*"
    rf"(?P<value>{NUMBER})\s*"
    rf"(?P<unit>{MEASUREMENT_UNIT_PATTERN})",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(NUMBER)
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
MARKDOWN_IMAGE_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
LATEX_CHEMICAL_RE = re.compile(
    r"\$[^$]*(?:_\{?\s*(?:\d+|[A-Za-z])\s*\}?)[^$]*\$|"
    r"\b[A-Z][A-Za-z]?_\{?\s*(?:\d+|[A-Za-z])\s*\}?",
    re.IGNORECASE,
)

@dataclass
class HarvestReport:
    harvested_span_count: int = 0
    candidate_count: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    def stats(self) -> dict[str, int]:
        values = {
            "harvest_span_count": self.harvested_span_count,
            "harvest_candidate_count": self.candidate_count,
            "harvest_skipped_span_count": sum(self.skipped.values()),
        }
        values.update({f"harvest_skipped_{key}": count for key, count in self.skipped.items()})
        return dict(sorted(values.items()))


def _normalize_number(raw: str) -> str | None:
    try:
        value = Decimal(raw.replace(",", "").replace("，", ""))
    except InvalidOperation:
        return None
    return format(value, "f")


def _comparator(raw: str | None) -> str:
    if raw in {"约", "大约", "近"}:
        return "about"
    if raw in {"超过", "不少于", "不低于", "≥", ">"}:
        return "greater_than"
    if raw in {"低于", "少于", "≤", "<"}:
        return "less_than"
    return "exact"


def _mask_match(text: str, pattern: re.Pattern[str]) -> str:
    characters = list(text)
    for match in pattern.finditer(text):
        for index in range(match.start(), match.end()):
            if not characters[index].isspace():
                characters[index] = " "
    return "".join(characters)


def _mask_latex_chemicals_except_ghg(text: str) -> str:
    characters = list(text)
    for match in LATEX_CHEMICAL_RE.finditer(text):
        if GHG_FORMULA_RE.search(match.group(0)):
            continue
        for index in range(match.start(), match.end()):
            if not characters[index].isspace():
                characters[index] = " "
    return "".join(characters)


def mask_markup_preserving_offsets(text: str) -> str:
    """Hide markup without changing offsets used by evidence locators."""

    return _mask_latex_chemicals_except_ghg(
        _mask_match(_mask_match(text, HTML_TAG_RE), MARKDOWN_IMAGE_RE)
    )


class LiteralHarvester:
    def __init__(self, *, max_span_chars: int = 6000, max_numeric_tokens: int = 250) -> None:
        self.max_span_chars = max_span_chars
        self.max_numeric_tokens = max_numeric_tokens

    def harvest(self, spans: list[EvidenceSpan]) -> list[LiteralCandidate]:
        candidates, _ = self.harvest_with_report(spans)
        return candidates

    def harvest_with_report(
        self, spans: list[EvidenceSpan]
    ) -> tuple[list[LiteralCandidate], HarvestReport]:
        candidates: list[LiteralCandidate] = []
        report = HarvestReport()
        sentence_groups = {
            span.context_group_id for span in spans if span.span_type == "sentence"
        }

        for span in spans:
            reason = self._skip_reason(span, sentence_groups)
            if reason:
                report.skipped[reason] += 1
                continue
            masked = mask_markup_preserving_offsets(span.text)
            reason = self._pathology_reason(masked)
            if reason:
                report.skipped[reason] += 1
                continue
            harvested = self._harvest_span(span, masked)
            candidates.extend(harvested)
            report.harvested_span_count += 1
            report.candidate_count += len(harvested)
        return candidates, report

    @staticmethod
    def _skip_reason(span: EvidenceSpan, sentence_groups: set[str]) -> str | None:
        if is_navigation_span(span):
            return "navigation_span"
        if span.span_type == "table_row":
            return "table_row_context_only"
        if span.span_type == "page_text" and "fallback_page_text" not in span.quality_flags:
            return "aggregate_page_text"
        if span.span_type == "block":
            if span.has_table_structure:
                return "structured_table_block"
            if span.context_group_id in sentence_groups:
                return "sentence_source_preferred"
        if span.span_type == "sentence" and span.has_table_structure:
            return "structured_table_sentence"
        return None

    def _pathology_reason(self, text: str) -> str | None:
        if len(text) > self.max_span_chars:
            return "oversized_span"
        numbers = list(NUMBER_RE.finditer(text))
        if len(numbers) > self.max_numeric_tokens:
            return "numeric_flood"
        non_space = max(1, sum(not character.isspace() for character in text))
        numeric_chars = sum(match.end() - match.start() for match in numbers)
        if len(numbers) >= 50 and numeric_chars / non_space > 0.55:
            return "numeric_density"
        if len(numbers) >= 40:
            normalized = [match.group(0).replace(",", "").replace("，", "") for match in numbers]
            most_common = Counter(normalized).most_common(1)[0][1]
            if most_common / len(normalized) >= 0.8:
                return "repeated_numeric_token"
        return None

    def _harvest_span(self, span: EvidenceSpan, searchable_text: str) -> list[LiteralCandidate]:
        matches: list[tuple[int, int, str, str, str | None, str | None, str]] = []
        occupied: list[tuple[int, int]] = []

        def add(
            start: int,
            end: int,
            kind: str,
            raw: str,
            normalized: str | None,
            unit: str | None = None,
            comparator: str = "exact",
        ) -> None:
            matches.append((start, end, kind, raw, normalized, unit, comparator))
            occupied.append((start, end))

        for match in DATE_RE.finditer(searchable_text):
            normalized = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
            add(match.start(), match.end(), "date", span.text[match.start() : match.end()], normalized)

        for match in PERCENT_RE.finditer(searchable_text):
            normalized = _normalize_number(match.group("value"))
            add(
                match.start(),
                match.end(),
                "percentage",
                span.text[match.start() : match.end()],
                normalized,
                match.group("unit"),
            )

        for match in QUANTITY_RE.finditer(searchable_text):
            normalized = _normalize_number(match.group("value"))
            add(
                match.start(),
                match.end(),
                "quantity",
                span.text[match.start() : match.end()],
                normalized,
                match.group("unit"),
                _comparator(match.group("comparator")),
            )

        for match in YEAR_RE.finditer(searchable_text):
            if self._overlaps(match.start(), match.end(), occupied):
                continue
            add(
                match.start(),
                match.end(),
                "year",
                span.text[match.start() : match.end()],
                match.group(1),
            )

        for match in UNIT_RE.finditer(searchable_text):
            if self._overlaps(match.start(), match.end(), occupied):
                continue
            unit_raw = span.text[match.start() : match.end()]
            add(match.start(), match.end(), "unit", unit_raw, unit_raw, unit_raw)

        for match in NUMBER_RE.finditer(searchable_text):
            if self._overlaps(match.start(), match.end(), occupied):
                continue
            if self._is_structural_number(searchable_text, match.start(), match.end()):
                continue
            raw = span.text[match.start() : match.end()]
            add(match.start(), match.end(), "number", raw, _normalize_number(raw))

        results: list[LiteralCandidate] = []
        for start, end, kind, raw, normalized, unit, comparator in sorted(matches):
            results.append(
                LiteralCandidate(
                    candidate_id=stable_id(
                        "cand", span.span_id, start, end, kind, raw, normalized, unit
                    ),
                    span_id=span.span_id,
                    candidate_type=kind,
                    raw_value=raw,
                    normalized_value=normalized,
                    char_start=start,
                    char_end=end,
                    unit_raw=unit,
                    unit_id=canonical_unit_id(unit),
                    comparator=comparator,
                    context_group_id=span.context_group_id,
                )
            )
        return results

    @staticmethod
    def _overlaps(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
        return any(start < existing_end and end > existing_start for existing_start, existing_end in occupied)

    @staticmethod
    def _is_structural_number(text: str, start: int, end: int) -> bool:
        """Reject list ordinals and digits embedded in labels or standard codes."""

        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before == "(" and after == ")":
            prefix = text[: start - 1].rstrip()
            if not prefix or prefix[-1] in {"|", ";", "；", ":", "："}:
                return True
        if before and (before.isalpha() or before in {"_", "-"}):
            return True
        if before == "." and start >= 2 and text[start - 2].isascii() and text[start - 2].isalnum():
            return True
        if after and (after.isalpha() or after in {"_", "."}):
            return True
        # Superscript-like footnote markers at the end of a textual table label
        # are structural annotations, not measured values.
        prefix = text[:start].rstrip()
        suffix = text[end:].strip()
        if not suffix and prefix and any(character.isalpha() or "\u4e00" <= character <= "\u9fff" for character in prefix):
            if len(text[start:end].replace(",", "").replace("，", "")) <= 2:
                return True
        return False
