from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from esg_targeted.contracts import SemanticFactGroup


@dataclass(frozen=True)
class FactDeduplicationResult:
    groups: list[SemanticFactGroup]
    input_count: int
    output_count: int
    source_indices: list[int] = field(default_factory=list)

    @property
    def removed_count(self) -> int:
        return self.input_count - self.output_count


class ExactFactDeduplicator:
    """Remove only complete semantic duplicates after every region has run.

    The physical semantic group participates in the signature.  The same cell
    encountered in overlapping regions still merges because its immutable cell or
    row id is stable, while equal-valued sibling entity/period cells stay distinct.
    """

    def merge(self, groups: list[SemanticFactGroup], *, identity_keys: list[Any] | None = None) -> FactDeduplicationResult:
        if identity_keys is not None and len(identity_keys) != len(groups):
            raise ValueError("Every semantic group requires its own physical source identity")
        selected: dict[tuple[tuple[str, str], ...], tuple[int, SemanticFactGroup]] = {}
        order: list[tuple[tuple[str, str], ...]] = []
        for index, group in enumerate(groups):
            signature = self._signature(group)
            if identity_keys is not None:
                signature += (("__source_identity", json.dumps(identity_keys[index], ensure_ascii=False, sort_keys=True, separators=(",", ":"))),)
            existing = selected.get(signature)
            if existing is None:
                selected[signature] = (index, group)
                order.append(signature)
                continue
            if self._quality(group) > self._quality(existing[1]):
                best_index, best = index, group
            else:
                best_index, best = existing
            # A proof is an indivisible binding. Combining evidence IDs from a
            # different reading can detach support_fragments/source_ref_id and
            # its SourceFact identity from the chosen value. Keep the complete
            # best reading and expose its input position for Core binding.
            selected[signature] = (best_index, best)
        result = [selected[signature][1] for signature in order]
        return FactDeduplicationResult(
            groups=result,
            input_count=len(groups),
            output_count=len(result),
            source_indices=[selected[signature][0] for signature in order],
        )

    @staticmethod
    def _signature(group: SemanticFactGroup) -> tuple[tuple[str, str], ...]:
        return (
            ("__metric_match", group.metric_match),
            ("__semantic_group", group.group_ref_id),
        ) + tuple(
            sorted(
                (
                    assignment.element_id,
                    " ".join(str(assignment.value_raw or "").split()),
                )
                for assignment in group.assignments
            )
        )

    @staticmethod
    def _quality(group: SemanticFactGroup) -> tuple[float, int, int]:
        confidences = [
            item.confidence for item in group.assignments if item.confidence is not None
        ]
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        exact_sources = sum(
            item.source_mode in {"candidate", "span"} for item in group.assignments
        )
        return confidence, exact_sources, len(group.assignments)
