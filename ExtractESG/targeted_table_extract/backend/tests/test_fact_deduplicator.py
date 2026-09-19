from __future__ import annotations

from esg_targeted.contracts import SemanticAssignment, SemanticFactGroup
from esg_targeted.results.deduplicator import ExactFactDeduplicator


def _group(group_id: str, pollutant: str, amount: str, confidence: float):
    return SemanticFactGroup(
        group_ref_id=group_id,
        assignments=[
            SemanticAssignment(
                element_id="metric.element.pollutant",
                source_ref_id=f"{group_id}-pollutant",
                evidence_span_ids=[f"{group_id}-span"],
                value_raw=pollutant,
                source_mode="visual",
                confidence=confidence,
            ),
            SemanticAssignment(
                element_id="metric.element.amount",
                source_ref_id=f"{group_id}-amount",
                evidence_span_ids=[f"{group_id}-span"],
                value_raw=amount,
                source_mode="visual",
                confidence=confidence,
            ),
        ],
    )


def test_exact_fact_deduplication_merges_same_physical_semantic_group() -> None:
    lower = _group("cell-a", "二氧化硫", "0.52", 0.8)
    better = _group("cell-a", "二氧化硫", "0.52", 0.95)
    result = ExactFactDeduplicator().merge([lower, better])

    assert result.input_count == 2
    assert result.output_count == 1
    assert result.removed_count == 1
    assert result.groups[0].group_ref_id == "cell-a"


def test_equal_payload_from_distinct_physical_cells_is_not_merged() -> None:
    member = _group("cell-member", "二氧化硫", "0.52", 0.9)
    total = _group("cell-total", "二氧化硫", "0.52", 0.9)
    result = ExactFactDeduplicator().merge([member, total])
    assert result.output_count == 2


def test_equal_numbers_with_different_dimensions_are_not_merged() -> None:
    sulfur = _group("region-a", "二氧化硫", "0.52", 0.9)
    nitrogen = _group("region-b", "氮氧化物", "0.52", 0.9)
    result = ExactFactDeduplicator().merge([sulfur, nitrogen])

    assert result.output_count == 2
