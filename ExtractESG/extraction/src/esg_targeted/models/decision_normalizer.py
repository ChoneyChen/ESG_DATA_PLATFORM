from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from esg_targeted.contracts import (
    EvidencePacket,
    SemanticAssignment,
    SemanticDecision,
    SemanticFactGroup,
)


@dataclass(frozen=True)
class DecisionNormalizationResult:
    decision: SemanticDecision
    actions: tuple[str, ...]


class SemanticDecisionNormalizer:
    """Apply only lossless normalization; semantic choices remain model-owned."""

    def normalize(
        self, decision: SemanticDecision, packet: EvidencePacket
    ) -> DecisionNormalizationResult:
        spans = {item.span_id for item in packet.spans}
        candidates = {item.candidate_id: item for item in packet.candidates}
        actions: Counter[str] = Counter()
        groups: list[SemanticFactGroup] = []
        seen_rows: set[tuple] = set()

        for group in decision.fact_groups:
            assignments: list[SemanticAssignment] = []
            seen_assignments: set[tuple] = set()
            for item in group.assignments:
                evidence_ids = []
                for evidence_id in item.evidence_span_ids:
                    if evidence_id in spans:
                        evidence_ids.append(evidence_id)
                    elif evidence_id in candidates:
                        evidence_ids.append(candidates[evidence_id].span_id)
                if item.source_mode == "candidate" and item.source_ref_id in candidates:
                    evidence_ids.append(candidates[item.source_ref_id].span_id)
                elif item.source_ref_id in spans:
                    evidence_ids.append(item.source_ref_id)
                evidence_ids = list(dict.fromkeys(evidence_ids))
                key = (
                    item.element_id,
                    item.source_ref_id,
                    item.source_mode,
                    item.value_raw,
                    tuple(evidence_ids),
                )
                if key in seen_assignments:
                    actions["deduplicate_assignment"] += 1
                    continue
                seen_assignments.add(key)
                assignments.append(
                    item.model_copy(update={"evidence_span_ids": evidence_ids})
                )

            if not assignments:
                actions["drop_empty_row"] += 1
                continue
            row_key = (
                group.group_ref_id,
                tuple(
                    sorted(
                        (
                            item.element_id,
                            item.value_raw,
                            item.source_mode,
                            item.source_ref_id,
                        )
                        for item in assignments
                    )
                ),
            )
            if row_key in seen_rows:
                actions["deduplicate_row"] += 1
                continue
            seen_rows.add(row_key)
            groups.append(
                SemanticFactGroup(
                    group_ref_id=group.group_ref_id,
                    assignments=assignments,
                )
            )

        selected = sorted(
            {
                evidence_id
                for group in groups
                for assignment in group.assignments
                for evidence_id in assignment.evidence_span_ids
                if evidence_id in spans
            }
        )
        status = decision.status
        uncertainty = decision.uncertainty_code
        if status in {"found", "partial"} and not groups:
            status = "ambiguous"
            uncertainty = "model_output_invalid"
            actions["downgrade_empty_positive_decision"] += 1
        normalized = SemanticDecision(
            task_id=packet.task_id,
            status=status,
            fact_groups=groups,
            selected_evidence_span_ids=selected,
            uncertainty_code=uncertainty,
        )
        return DecisionNormalizationResult(
            normalized,
            tuple(f"{name}:{count}" for name, count in sorted(actions.items())),
        )
