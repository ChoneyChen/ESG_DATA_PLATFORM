from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from esg_targeted.contracts import EvidenceSpan, LiteralCandidate, MetricQuery


_TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "範": "范",
        "圍": "围",
        "疇": "畴",
        "溫": "温",
        "氣": "气",
        "體": "体",
        "總": "总",
        "量": "量",
        "噸": "吨",
        "當": "当",
        "電": "电",
        "購": "购",
        "產": "产",
        "類": "类",
        "別": "别",
        "營": "营",
        "業": "业",
        "報": "报",
        "應": "应",
        "邊": "边",
        "計": "计",
        "與": "与",
        "為": "为",
        "數": "数",
        "據": "据",
        "強": "强",
        "度": "度",
    }
)
_SCOPE_NUMBER_RE = re.compile(
    r"(?P<prefix>范围|范畴|scope)\s*(?P<number>iii(?![a-z])|ii(?![a-z])|i(?![a-z])|[一二三壹贰叁123])",
    re.IGNORECASE,
)
_SCOPE_NUMBERS = {
    "一": "1",
    "壹": "1",
    "1": "1",
    "Ⅰ": "1",
    "ⅰ": "1",
    "i": "1",
    "二": "2",
    "贰": "2",
    "2": "2",
    "Ⅱ": "2",
    "ⅱ": "2",
    "ii": "2",
    "三": "3",
    "叁": "3",
    "3": "3",
    "Ⅲ": "3",
    "ⅲ": "3",
    "iii": "3",
}


@lru_cache(maxsize=65_536)
def normalize_match_text(value: str) -> str:
    """Normalize common disclosure typography without changing its semantics.

    This deliberately handles presentation variants only: Unicode width,
    traditional characters used in the tested reports, Scope numerals and
    lightweight LaTeX around formulae. It does not decide whether evidence is
    applicable to a metric; that remains the semantic model's job.
    """

    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = normalized.translate(_TRADITIONAL_TO_SIMPLIFIED)
    normalized = normalized.replace("₂", "2").replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\\(?:mathrm|text|operatorname)\s*", "", normalized)
    normalized = re.sub(r"[$\\{}_^]", "", normalized)

    def replace_scope(match: re.Match[str]) -> str:
        prefix = match.group("prefix").casefold()
        if prefix == "范畴":
            prefix = "范围"
        return prefix + _SCOPE_NUMBERS[match.group("number")]

    normalized = _SCOPE_NUMBER_RE.sub(replace_scope, normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


@lru_cache(maxsize=32_768)
def _match_text_variants(text: str) -> tuple[str, ...]:
    normalized = normalize_match_text(text)
    # A common table label inserts "温室气体" between the scope and "排放".
    # Keep both the literal form and a retrieval-only compact form so a package
    # alias such as "范围1排放" can retrieve it without treating the alias as a
    # final semantic qualification rule.
    compact_scope = re.sub(
        r"(范围[123])\s*(?:温室气体|ghg|greenhouse gas)\s*(排放)",
        r"\1\2",
        normalized,
        flags=re.IGNORECASE,
    )
    expanded_composite_scope = re.sub(
        r"(?P<prefix>范围|scope)(?P<first>[123])\s*\+\s*(?P<second>[123])",
        lambda match: (
            f"{match.group('prefix')}{match.group('first')} "
            f"{match.group('prefix')}{match.group('second')}"
        ),
        normalized,
        flags=re.IGNORECASE,
    )
    return tuple(
        dict.fromkeys((normalized, compact_scope, expanded_composite_scope))
    )


def contains_term(text: str, term: str) -> bool:
    haystacks = _match_text_variants(text)
    needle = normalize_match_text(term)
    if not needle:
        return False
    if needle.isascii() and re.fullmatch(r"[a-z0-9 .+/_-]+", needle):
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])")
        return any(pattern.search(haystack) is not None for haystack in haystacks)
    return any(needle in haystack for haystack in haystacks)


def candidate_types_compatible(
    expected: Iterable[str], candidates: Iterable[LiteralCandidate]
) -> bool:
    expected_types = set(expected)
    candidate_list = list(candidates)
    if not expected_types:
        return True
    actual = {candidate.candidate_type for candidate in candidate_list}
    if "quantity" in expected_types and "quantity" in actual:
        return True
    if "number" in expected_types and "number" in actual:
        if "unit" in actual or any(candidate.unit_raw for candidate in candidate_list):
            return True
    if "percentage" in expected_types and "number" in actual:
        if any(
            candidate.candidate_type == "unit"
            and str(candidate.unit_raw or candidate.raw_value).strip() in {"%", "％", "百分比"}
            for candidate in candidate_list
        ):
            return True
    return bool(expected_types & actual)


def candidate_allowed_in_packet(candidate_type: str, query: MetricQuery) -> bool:
    expected = set(query.intent.expected_candidate_types)
    if not expected:
        return candidate_type in {
            "quantity",
            "percentage",
            "date",
            "date_range",
            "year",
            "unit",
            "number",
        }
    allowed = {*expected, "date", "date_range", "year", "unit"}
    if "quantity" in expected:
        allowed.add("number")
    if "percentage" in expected:
        # Structured tables commonly split `0` and `%` into adjacent cells.  The
        # group-level FactPattern recombines them without inventing a percentage.
        allowed.add("number")
    return candidate_type in allowed


@dataclass(frozen=True)
class FactPatternMatch:
    topic_coverage: float
    role_coverage: float
    required_coverage: float
    topic_complete: bool
    role_complete: bool
    compatible_candidate: bool
    full_pattern: bool
    matched_topic_terms: list[list[str]]
    matched_role_terms: list[list[str]]
    conflict_terms: list[str]


def evaluate_fact_pattern(
    query: MetricQuery,
    spans: Iterable[EvidenceSpan],
    candidates: Iterable[LiteralCandidate],
) -> FactPatternMatch:
    span_list = list(spans)
    candidate_list = list(candidates)
    text = "\n".join(span.context_text for span in span_list)
    topic_matches = _matched_groups(text, query.intent.topic_term_groups)
    role_matches = _matched_groups(text, query.intent.role_term_groups)
    topic_count = sum(bool(items) for items in topic_matches)
    role_count = sum(bool(items) for items in role_matches)
    topic_coverage = (
        topic_count / len(query.intent.topic_term_groups)
        if query.intent.topic_term_groups
        else 0.0
    )
    role_coverage = (
        role_count / len(query.intent.role_term_groups)
        if query.intent.role_term_groups
        else 0.0
    )
    required_count = len(query.intent.topic_term_groups) + len(query.intent.role_term_groups)
    required_coverage = (
        (topic_count + role_count) / required_count if required_count else 0.0
    )
    conflict_terms = [
        term for term in query.intent.must_not_terms if contains_term(text, term)
    ]
    topic_complete = bool(query.intent.topic_term_groups) and topic_coverage == 1.0
    role_complete = not query.intent.role_term_groups or role_coverage == 1.0
    compatible = candidate_types_compatible(
        query.intent.expected_candidate_types, candidate_list
    )
    full_pattern = bool(
        topic_complete and role_complete and compatible and not conflict_terms
    )
    return FactPatternMatch(
        topic_coverage=topic_coverage,
        role_coverage=role_coverage,
        required_coverage=required_coverage,
        topic_complete=topic_complete,
        role_complete=role_complete,
        compatible_candidate=compatible,
        full_pattern=full_pattern,
        matched_topic_terms=topic_matches,
        matched_role_terms=role_matches,
        conflict_terms=conflict_terms,
    )


def _matched_groups(text: str, groups: list[list[str]]) -> list[list[str]]:
    return [[term for term in group if contains_term(text, term)] for group in groups]
