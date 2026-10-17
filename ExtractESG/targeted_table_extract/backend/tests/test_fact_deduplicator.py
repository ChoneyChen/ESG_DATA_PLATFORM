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


def test_duplicate_reading_keeps_complete_best_fragment_proof_and_source_index() -> None:
    from esg_targeted.grounding.guard import GroundingGuard
    from tests.test_source_reading import decision_with, guard_packet
    packet = guard_packet()
    note = packet.spans[1].model_copy(update={"span_id": "s-note-second"})
    packet.spans.append(note)
    packet.context_only_span_ids.append(note.span_id)
    statement = SemanticAssignment(element_id="statement", source_ref_id="s-body", evidence_span_ids=["s-body"],
        value_raw="采用实际测量。", confidence=0.8)
    def group(ref, confidence):
        return SemanticFactGroup(group_ref_id=packet.allowed_group_ids[0], assignments=[statement.model_copy(update={"confidence": confidence}),
            SemanticAssignment(element_id="period", source_ref_id=ref, evidence_span_ids=[ref], value_raw="2024", confidence=confidence,
                support_axis="reporting_period", support_fragments=[{"span_id": ref, "text": "2024"}])], context_span_ids=[ref])
    low, high = group("s-note", 0.7), group("s-note-second", 0.9)
    assert GroundingGuard().validate(packet, decision_with(packet, high.assignments)).accepted
    result = ExactFactDeduplicator().merge([low, high])
    assert result.groups == [high] and result.source_indices == [1]
    assert GroundingGuard().validate(packet, decision_with(packet, result.groups[0].assignments)).accepted
    assert result.groups[0].assignments[1].evidence_span_ids == ["s-note-second"]


def test_agent_identity_keys_preserve_distinct_physical_anchors_and_interpretations() -> None:
    groups = [_group("shared-group", "二氧化硫", "0.52", 0.8), _group("shared-group", "二氧化硫", "0.52", 0.9)]
    result = ExactFactDeduplicator().merge(groups, identity_keys=[
        {"source_anchor_id": "occurrence-a", "identity_raw": {"reporting_period": "2024"}},
        {"source_anchor_id": "occurrence-b", "identity_raw": {"reporting_period": "2024"}}])
    assert result.output_count == 2 and result.source_indices == [0, 1]
    result = ExactFactDeduplicator().merge(groups, identity_keys=[
        {"source_anchor_id": "occurrence-a", "identity_raw": {"reporting_period": "2023"}},
        {"source_anchor_id": "occurrence-a", "identity_raw": {"reporting_period": "2024"}}])
    assert result.output_count == 2
