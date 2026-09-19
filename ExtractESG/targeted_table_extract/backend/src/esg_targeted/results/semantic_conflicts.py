"""Cross-metric semantic conflicts derived from package-fixed dimensions.

This is an audit layer, not a semantic Guard.  It never deletes a model result.
It identifies the narrow deterministic case where the exact same physical source
cell was labelled with incompatible fixed package dimensions by sibling metrics.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class SemanticConflictAuditor:
    def audit(self, records: dict[str, list[dict[str, Any]]], package) -> list[dict[str, Any]]:
        fixed_by_metric: dict[str, dict[str, str]] = defaultdict(dict)
        for element in package.elements:
            fixed = element.value_contract.fixed_value
            if (
                fixed is None
                or element.binding.storage.value != "dimension"
                or element.binding.record_type.value != "quantitative_observation"
            ):
                continue
            fixed_by_metric[element.metric_id][element.binding.target] = str(fixed)

        evidence_by_fact: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in records.get("evidence_references", []):
            evidence_by_fact[item.get("source_record_id")].append(item)

        observations_by_source: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for fact in records.get("quantitative_observations", []):
            fact_id = fact.get("observation_id")
            primary = next(
                (
                    item
                    for item in evidence_by_fact.get(fact_id, [])
                    if item.get("evidence_role") == "primary"
                ),
                None,
            )
            if primary is None:
                continue
            source_key = (
                primary.get("document_id"),
                primary.get("ir_run_id"),
                primary.get("ir_revision"),
                primary.get("pdf_page_index"),
                primary.get("ir_object_id"),
                str(fact.get("value_raw") or "").strip(),
                str(fact.get("unit_raw") or fact.get("unit_id") or "").strip(),
            )
            observations_by_source[source_key].append(fact)

        conflicts: list[dict[str, Any]] = []
        for source_key, facts in observations_by_source.items():
            if len({item.get("metric_id") for item in facts}) < 2:
                continue
            for left_index, left in enumerate(facts):
                left_fixed = fixed_by_metric.get(left.get("metric_id"), {})
                for right in facts[left_index + 1 :]:
                    if left.get("metric_id") == right.get("metric_id"):
                        continue
                    right_fixed = fixed_by_metric.get(right.get("metric_id"), {})
                    incompatible = {
                        dimension_id: [left_fixed[dimension_id], right_fixed[dimension_id]]
                        for dimension_id in set(left_fixed) & set(right_fixed)
                        if left_fixed[dimension_id] != right_fixed[dimension_id]
                    }
                    if not incompatible:
                        continue
                    conflicts.append(
                        {
                            "code": "same_source_incompatible_fixed_dimensions",
                            "severity": "warning",
                            "metric_ids": [left.get("metric_id"), right.get("metric_id")],
                            "fact_ids": [left.get("observation_id"), right.get("observation_id")],
                            "source": {
                                "document_id": source_key[0],
                                "ir_run_id": source_key[1],
                                "ir_revision": source_key[2],
                                "pdf_page_index": source_key[3],
                                "ir_object_id": source_key[4],
                                "value_raw": source_key[5],
                                "unit": source_key[6],
                            },
                            "incompatible_dimensions": incompatible,
                            "requires_joint_readjudication": True,
                            "message": (
                                "The same physical source cell was assigned to sibling metrics "
                                "with incompatible package-fixed dimensions. Preserve both model "
                                "decisions for audit and jointly re-adjudicate their applicability."
                            ),
                        }
                    )
        return conflicts
