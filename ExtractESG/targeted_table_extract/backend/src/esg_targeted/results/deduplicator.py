from __future__ import annotations

from dataclasses import dataclass

from esg_targeted.contracts import SemanticFactGroup


@dataclass(frozen=True)
class FactDeduplicationResult:
    groups: list[SemanticFactGroup]
    input_count: int
    output_count: int

    @property
    def removed_count(self) -> int:
        return self.input_count - self.output_count


class ExactFactDeduplicator:
    """Remove only complete semantic duplicates after every region has run.

    Provenance IDs and region/group IDs deliberately do not participate in the
    signature. Field identity and exact visible values do, so equal numbers from
    different pollutants, periods or boundaries are never collapsed.
    """

    def merge(self, groups: list[SemanticFactGroup]) -> FactDeduplicationResult:
        selected: dict[tuple[tuple[str, str], ...], tuple[int, SemanticFactGroup]] = {}
        order: list[tuple[tuple[str, str], ...]] = []
        for index, group in enumerate(groups):
            signature = self._signature(group)
            existing = selected.get(signature)
            if existing is None:
                selected[signature] = (index, group)
                order.append(signature)
                continue
            if self._quality(group) > self._quality(existing[1]):
                best, other = group, existing[1]
            else:
                best, other = existing[1], group
            by_element = {a.element_id: a for a in other.assignments}
            merged = best.model_copy(update={
                "assignments": [a.model_copy(update={"evidence_span_ids": list(dict.fromkeys([
                    *a.evidence_span_ids,
                    *(by_element[a.element_id].evidence_span_ids if a.element_id in by_element else []),
                ]))}) for a in best.assignments],
                "context_span_ids": list(dict.fromkeys([*best.context_span_ids, *other.context_span_ids])),
            })
            selected[signature] = (existing[0], merged)
        result = [selected[signature][1] for signature in order]
        return FactDeduplicationResult(
            groups=result,
            input_count=len(groups),
            output_count=len(result),
        )

    @staticmethod
    def _signature(group: SemanticFactGroup) -> tuple[tuple[str, str], ...]:
        return (("__metric_match", group.metric_match),) + tuple(
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
