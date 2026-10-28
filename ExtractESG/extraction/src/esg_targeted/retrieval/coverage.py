"""Explicit coverage decisions, distinct from semantic approval."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CoverageDecision:
    metric_id: str
    state: str
    evidence_object_ids: tuple[str, ...]
    reason: str


def coverage_decision(metric_id: str, *, evidence_object_ids: list[str], candidate_count: int,
                      accepted_count: int, evidence_sufficient: bool) -> dict:
    if not evidence_object_ids:
        return asdict(CoverageDecision(metric_id, "not_covered", (), "No selected source object covers this clause"))
    if not candidate_count:
        return asdict(CoverageDecision(metric_id, "unmapped", tuple(evidence_object_ids),
                                      "Source objects exist but no row was mapped to the clause"))
    if not accepted_count and not evidence_sufficient:
        return asdict(CoverageDecision(metric_id, "insufficient_evidence", tuple(evidence_object_ids),
                                      "Mapped candidates lack sufficient value, period, unit, boundary or context evidence"))
    if not accepted_count and evidence_sufficient:
        return asdict(CoverageDecision(metric_id, "confirmed_not_disclosed", tuple(evidence_object_ids),
                                      "Sufficient source coverage establishes no disclosure under the clause definition"))
    return asdict(CoverageDecision(metric_id, "covered", tuple(evidence_object_ids), "Source facts support the clause observation"))
