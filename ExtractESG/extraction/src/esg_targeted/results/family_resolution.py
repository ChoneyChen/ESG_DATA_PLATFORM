from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from esg_targeted.models.family_adjudication import build_family_packet


class FamilyConflictResolver:
    """Apply one model judgement across each conflicting metric family.

    The original per-metric decision artifacts remain untouched. This stage only
    changes the aggregate result view after a joint semantic decision succeeds.
    """

    def resolve(
        self,
        *,
        conflicts: list[dict[str, Any]],
        records: dict[str, list[dict[str, Any]]],
        outcomes: list[dict[str, Any]],
        package,
        model,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        adjudicate = getattr(model, "readjudicate_family", None)
        if not conflicts or not callable(adjudicate):
            return {
                "schema_version": "family-adjudication-v1",
                "attempted": False,
                "reason": "no_conflicts" if not conflicts else "model_not_supported",
                "families": [],
                "resolved_case_ids": [],
                "rejected_fact_ids": [],
            }

        metric_family = {
            metric.metric_id: metric.metric_family for metric in package.metrics
        }
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        unsupported = []
        for conflict in conflicts:
            families = {
                metric_family.get(metric_id)
                for metric_id in conflict.get("metric_ids", [])
            }
            families.discard(None)
            if len(families) != 1:
                unsupported.append(conflict)
                continue
            grouped[next(iter(families))].append(conflict)

        family_reports = []
        resolved_case_ids = []
        rejected_fact_ids = []
        for family_id, family_conflicts in sorted(grouped.items()):
            packet = build_family_packet(
                family_id=family_id,
                conflicts=family_conflicts,
                records=records,
                package=package,
            )
            if not packet.cases:
                continue
            try:
                result = adjudicate(packet, cancel_check=cancel_check)
            except Exception as exc:
                family_reports.append(
                    {
                        "family_id": family_id,
                        "packet": packet.model_dump(mode="json"),
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            cases_by_id = {item.case_id: item for item in packet.cases}
            family_rejected = []
            decided_cases = []
            for decision in result.decision.cases:
                if decision.status != "decided":
                    continue
                case = cases_by_id[decision.case_id]
                applicable = set(decision.applicable_metric_ids)
                rejected = [
                    fact.fact_id
                    for fact in case.facts
                    if fact.metric_id not in applicable
                ]
                family_rejected.extend(rejected)
                rejected_fact_ids.extend(rejected)
                resolved_case_ids.append(decision.case_id)
                decided_cases.append(decision.case_id)

            self._remove_rejected_facts(records, family_rejected)
            family_reports.append(
                {
                    "family_id": family_id,
                    "packet": packet.model_dump(mode="json"),
                    "status": "completed",
                    "decision": result.decision.model_dump(mode="json"),
                    "raw_output": result.raw_output,
                    "cleaned_output": result.cleaned_output,
                    "provider": result.provider,
                    "model_id": result.model_id,
                    "prompt_tokens": result.prompt_tokens,
                    "generation_tokens": result.generation_tokens,
                    "finish_reason": result.finish_reason,
                    "timings": result.timings,
                    "decided_case_ids": decided_cases,
                    "rejected_fact_ids": family_rejected,
                }
            )

        self._refresh_metric_states(
            records=records,
            outcomes=outcomes,
            affected_metric_ids={
                metric_id
                for conflict in conflicts
                for metric_id in conflict.get("metric_ids", [])
            },
        )
        return {
            "schema_version": "family-adjudication-v1",
            "attempted": bool(grouped),
            "families": family_reports,
            "resolved_case_ids": sorted(set(resolved_case_ids)),
            "rejected_fact_ids": sorted(set(rejected_fact_ids)),
            "unsupported_conflicts": unsupported,
        }

    @staticmethod
    def _remove_rejected_facts(
        records: dict[str, list[dict[str, Any]]],
        fact_ids: list[str],
    ) -> None:
        rejected = set(fact_ids)
        if not rejected:
            return
        records["quantitative_observations"] = [
            item
            for item in records.get("quantitative_observations", [])
            if item.get("observation_id") not in rejected
        ]
        records["attribute_values"] = [
            item
            for item in records.get("attribute_values", [])
            if item.get("parent_record_id") not in rejected
        ]
        records["dimension_values"] = [
            item
            for item in records.get("dimension_values", [])
            if item.get("parent_record_id") not in rejected
        ]
        records["evidence_references"] = [
            item
            for item in records.get("evidence_references", [])
            if item.get("source_record_id") not in rejected
        ]

    @staticmethod
    def _refresh_metric_states(
        *,
        records: dict[str, list[dict[str, Any]]],
        outcomes: list[dict[str, Any]],
        affected_metric_ids: set[str],
    ) -> None:
        facts_by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for collection in ("quantitative_observations", "qualitative_assertions"):
            for item in records.get(collection, []):
                facts_by_metric[item.get("metric_id")].append(item)
        evidence_by_metric: dict[str, int] = defaultdict(int)
        attribute_by_metric: dict[str, int] = defaultdict(int)
        dimension_by_metric: dict[str, int] = defaultdict(int)
        fact_metric = {
            item.get("observation_id") or item.get("assertion_id"): item.get("metric_id")
            for collection in ("quantitative_observations", "qualitative_assertions")
            for item in records.get(collection, [])
        }
        for item in records.get("evidence_references", []):
            metric_id = fact_metric.get(item.get("source_record_id"))
            if metric_id:
                evidence_by_metric[metric_id] += 1
        for item in records.get("attribute_values", []):
            attribute_by_metric[item.get("metric_id")] += 1
        for item in records.get("dimension_values", []):
            dimension_by_metric[item.get("metric_id")] += 1

        for task in records.get("reporting_tasks", []):
            metric_id = task.get("metric_id")
            if metric_id not in affected_metric_ids:
                continue
            fact_count = len(facts_by_metric.get(metric_id, []))
            task["result_count"] = fact_count
            task["evidence_count"] = evidence_by_metric.get(metric_id, 0)
            if fact_count:
                task.update(
                    task_status="completed",
                    found_status="reported",
                    review_status="pending",
                    uncertainty_reason=None,
                )
            else:
                task.update(
                    task_status="partial",
                    found_status="uncertain",
                    review_status="human_required",
                    uncertainty_reason=(
                        "Joint family adjudication rejected the conflicting source; "
                        "no alternative supported fact remains."
                    ),
                )

        for outcome in outcomes:
            metric_id = outcome.get("metric_id")
            if metric_id not in affected_metric_ids:
                continue
            facts = facts_by_metric.get(metric_id, [])
            quantitative = sum("observation_id" in item for item in facts)
            qualitative = sum("assertion_id" in item for item in facts)
            outcome["result_counts"]["quantitative_observation"] = quantitative
            outcome["result_counts"]["qualitative_assertion"] = qualitative
            outcome["result_counts"]["attribute_value"] = attribute_by_metric.get(
                metric_id, 0
            )
            outcome["result_counts"]["dimension_value"] = dimension_by_metric.get(
                metric_id, 0
            )
            outcome["result_counts"]["evidence_reference"] = evidence_by_metric.get(
                metric_id, 0
            )
            if facts:
                outcome.update(
                    status="found",
                    found_status="reported",
                    review_status="pending",
                    semantic_decision_status="decided",
                    display_readiness_status="ready",
                    uncertainty_reason=None,
                )
            else:
                outcome.update(
                    status="partial",
                    found_status="uncertain",
                    review_status="human_required",
                    semantic_decision_status="unresolved",
                    display_readiness_status="not_applicable",
                    uncertainty_reason=(
                        "Joint family adjudication rejected the conflicting source; "
                        "no alternative supported fact remains."
                    ),
                )
