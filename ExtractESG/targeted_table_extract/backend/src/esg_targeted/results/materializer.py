from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from esg_standard_packages.contracts import CompiledStandardPackage, MetricDefinition

from esg_targeted.contracts import (
    EvidencePacket,
    GuardResult,
    MaterializedTaskResult,
    SemanticDecision,
    TaskOutcome,
)
from esg_targeted.ids import stable_id


class CoreResultMaterializer:
    materializer_version = "core-result-materializer-v3"

    def materialize(
        self,
        *,
        package: CompiledStandardPackage,
        metric: MetricDefinition,
        packet: EvidencePacket,
        decision: SemanticDecision,
        guard: GuardResult,
        attempts: int,
    ) -> MaterializedTaskResult:
        elements_by_id = {item.element_id: item for item in package.elements}
        code_sets = {
            item.code_set_id: item for item in [*package.core.common_code_sets, *package.code_sets]
        }
        spans = {item.span_id: item for item in packet.spans}
        candidates = {item.candidate_id: item for item in packet.candidates}

        quantitative: list[dict[str, Any]] = []
        qualitative: list[dict[str, Any]] = []
        attributes: list[dict[str, Any]] = []
        dimensions: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        # Grounding proves source/shape consistency only.  It does not constitute
        # business approval of the model's ESG interpretation.  Keep model-created
        # records pending and expose the independent validation stages on the
        # TaskOutcome instead of overloading one optimistic review label.
        review_status = "pending" if guard.accepted else "human_required"
        has_matched_group = any(
            group.metric_match == "match" for group in decision.fact_groups
        )
        # Sequence is scoped to the actual parent and element.  A reporting-task
        # element can be emitted by several model fact groups, so resetting an
        # enumerate() inside each group would create duplicate primary keys.
        occurrence_counts: dict[tuple[str, str], int] = defaultdict(int)

        for group_index, group in enumerate(decision.fact_groups, 1):
            if group.metric_match != "match":
                # The model's report observation is kept in decisions and the
                # inspection view, not converted into a falsely labelled metric.
                continue
            resolved: list[dict[str, Any]] = []
            for assignment in group.assignments:
                element = elements_by_id[assignment.element_id]
                source = candidates.get(assignment.source_ref_id) or spans.get(
                    assignment.source_ref_id
                )
                resolved.append(
                    {
                        "element": element,
                        "assignment": assignment,
                        "value": self._resolve_value(source, assignment.value_raw),
                    }
                )

            for element_id in package.elements_by_metric[metric.metric_id]:
                element = elements_by_id[element_id]
                if element.value_contract.fixed_value is None:
                    continue
                # Task-scoped constants describe the task once.  Observation-
                # and assertion-scoped constants still belong to each fact.
                if element.binding.record_type.value == "reporting_task":
                    continue
                resolved.append(
                    {
                        "element": element,
                        "assignment": None,
                        "value": {
                            "raw": str(element.value_contract.fixed_value),
                            "normalized": element.value_contract.fixed_value,
                            "candidate": None,
                            "span": None,
                        },
                    }
                )

            record_types = {item["element"].binding.record_type.value for item in resolved}
            primary_evidence = [
                sid for item in resolved
                if item["assignment"] is not None
                and item["element"].binding.target in {"value_raw", "statement_raw"}
                for sid in item["assignment"].evidence_span_ids if sid in spans
            ]
            evidence_span_ids = list(dict.fromkeys([
                *primary_evidence,
                *sorted({
                    span_id
                    for item in resolved
                    if item["assignment"] is not None
                    for span_id in item["assignment"].evidence_span_ids
                    if span_id in spans
                }),
                *(sid for sid in group.context_span_ids if sid in spans),
            ]))
            confidence_values = [
                item["assignment"].confidence
                for item in resolved
                if item["assignment"] is not None
                and item["assignment"].confidence is not None
            ]
            confidence = min(confidence_values) if confidence_values else None

            observation_id = None
            assertion_id = None
            parent_evidence_sets: dict[str, str] = {}
            if "quantitative_observation" in record_types and any(
                item["element"].binding.record_type.value == "quantitative_observation"
                and item["element"].binding.storage.value == "core_field"
                and item["element"].binding.target == "value_raw"
                and item["assignment"] is not None
                for item in resolved
            ):
                observation_id = stable_id(
                    "obs", packet.task_id, group.group_ref_id, group_index
                )
                evidence_set_id = stable_id("evset", observation_id)
                observation = self._base_observation(
                    observation_id,
                    packet.task_id,
                    metric.metric_id,
                    evidence_set_id,
                    confidence,
                    review_status,
                )
                self._apply_core_fields(
                    observation, resolved, "quantitative_observation"
                )
                quantitative.append(observation)
                parent_evidence_sets[observation_id] = evidence_set_id
                evidence.extend(
                    self._evidence_records(
                        record_type="quantitative_observation",
                        record_id=observation_id,
                        evidence_set_id=evidence_set_id,
                        packet=packet,
                        span_ids=evidence_span_ids,
                        review_status=review_status,
                    )
                )

            has_explicit_statement = any(
                item["element"].binding.record_type.value == "qualitative_assertion"
                and item["element"].binding.storage.value == "core_field"
                and item["element"].binding.target == "statement_raw"
                and item["assignment"] is not None
                for item in resolved
            )
            if "qualitative_assertion" in record_types and has_explicit_statement:
                assertion_id = stable_id(
                    "assert", packet.task_id, group.group_ref_id, group_index
                )
                evidence_set_id = stable_id("evset", assertion_id)
                assertion = self._base_assertion(
                    assertion_id,
                    packet.task_id,
                    metric.metric_id,
                    evidence_set_id,
                    confidence,
                    review_status,
                )
                self._apply_core_fields(assertion, resolved, "qualitative_assertion")
                qualitative.append(assertion)
                parent_evidence_sets[assertion_id] = evidence_set_id
                evidence.extend(
                    self._evidence_records(
                        record_type="qualitative_assertion",
                        record_id=assertion_id,
                        evidence_set_id=evidence_set_id,
                        packet=packet,
                        span_ids=evidence_span_ids,
                        review_status=review_status,
                    )
                )

            parent_ids = {
                "reporting_task": packet.task_id,
                "quantitative_observation": observation_id,
                "qualitative_assertion": assertion_id,
            }
            for item in resolved:
                element = item["element"]
                binding = element.binding
                if binding.storage.value == "core_field":
                    continue
                parent_id = parent_ids.get(binding.record_type.value)
                if parent_id is None:
                    continue
                assignment = item["assignment"]
                evidence_set_id = parent_evidence_sets.get(parent_id)
                counter_key = (parent_id, element.element_id)
                occurrence_counts[counter_key] += 1
                sequence = occurrence_counts[counter_key]
                identity_token = self._occurrence_identity(
                    parent_type=binding.record_type.value,
                    group_ref_id=group.group_ref_id,
                    assignment=assignment,
                    sequence=sequence,
                )
                if binding.storage.value == "attribute":
                    attributes.append(
                        self._attribute_record(
                            packet,
                            metric,
                            element,
                            item["value"],
                            parent_id,
                            sequence,
                            evidence_set_id,
                            review_status,
                            identity_token,
                        )
                    )
                elif binding.storage.value == "dimension":
                    dimensions.append(
                        self._dimension_record(
                            packet,
                            metric,
                            element,
                            item["value"],
                            code_sets,
                            parent_id,
                            sequence,
                            evidence_set_id,
                            review_status,
                            identity_token,
                        )
                    )

        # Materialize fixed reporting-task elements exactly once after all model
        # groups.  They are request-scope metadata, not one value per row.
        for element_id in package.elements_by_metric[metric.metric_id]:
            element = elements_by_id[element_id]
            binding = element.binding
            if (
                not has_matched_group
                or
                element.value_contract.fixed_value is None
                or binding.record_type.value != "reporting_task"
                or binding.storage.value == "core_field"
            ):
                continue
            value = {
                "raw": str(element.value_contract.fixed_value),
                "normalized": element.value_contract.fixed_value,
                "candidate": None,
                "span": None,
            }
            counter_key = (packet.task_id, element.element_id)
            occurrence_counts[counter_key] += 1
            sequence = occurrence_counts[counter_key]
            if binding.storage.value == "attribute":
                attributes.append(
                    self._attribute_record(
                        packet,
                        metric,
                        element,
                        value,
                        packet.task_id,
                        sequence,
                        None,
                        review_status,
                        "task-fixed",
                    )
                )
            elif binding.storage.value == "dimension":
                dimensions.append(
                    self._dimension_record(
                        packet,
                        metric,
                        element,
                        value,
                        code_sets,
                        packet.task_id,
                        sequence,
                        None,
                        review_status,
                        "task-fixed",
                    )
                )

        has_unmapped = any(g.metric_match != "match" for g in decision.fact_groups)
        incomplete_qualitative_groups = sum(
            group.metric_match == "match"
            and any(
                elements_by_id[item.element_id].binding.record_type.value
                == "qualitative_assertion"
                for item in group.assignments
            )
            and not any(
                elements_by_id[item.element_id].binding.record_type.value
                == "qualitative_assertion"
                and elements_by_id[item.element_id].binding.storage.value == "core_field"
                and elements_by_id[item.element_id].binding.target == "statement_raw"
                for item in group.assignments
            )
            for group in decision.fact_groups
        )
        effective_status = (
            "partial"
            if has_unmapped or incomplete_qualitative_groups
            else decision.status
        )
        found_status = {
            "found": "reported",
            "partial": "partial",
            "not_found": "not_found_after_complete_search",
            "ambiguous": "uncertain",
        }[effective_status]
        task_status = {
            "found": "completed",
            "not_found": "completed",
            "partial": "partial",
            "ambiguous": "partial",
        }[effective_status]
        uncertainty = None if decision.uncertainty_code == "none" else decision.uncertainty_code
        if has_unmapped:
            uncertainty = "Reported measurements retained; metric scope/method remains unconfirmed or different."
        elif incomplete_qualitative_groups:
            uncertainty = (
                "Qualitative rows lacked an explicit evidence-grounded statement or list "
                "member and were not converted from numeric/period evidence."
            )
        display_readiness = self._display_readiness(
            quantitative=quantitative,
            qualitative=qualitative,
            dimensions=dimensions,
            fixed_element_ids={
                element_id
                for element_id in package.elements_by_metric[metric.metric_id]
                if elements_by_id[element_id].value_contract.fixed_value is not None
            },
        )
        semantic_status = (
            "unresolved"
            if effective_status in {"partial", "ambiguous"}
            else "decided"
            if decision.status in {"found", "not_found"}
            else "not_run"
        )
        task_record = {
            "task_id": packet.task_id,
            "report_id": packet.spans[0].document_id if packet.spans else packet.task_id,
            "document_id": packet.spans[0].document_id if packet.spans else "unknown",
            "ir_run_id": packet.spans[0].ir_run_id if packet.spans else "unknown",
            "ir_revision": packet.spans[0].ir_revision if packet.spans else 0,
            "package_id": package.manifest.package_id,
            "package_version": package.manifest.package_version,
            "metric_id": metric.metric_id,
            "materiality_status": "not_assessed",
            "applicability_status": "not_assessed",
            "found_status": found_status,
            "result_count": len(quantitative) + len(qualitative),
            "evidence_count": len(evidence),
            "uncertainty_reason": uncertainty,
            "review_status": review_status if decision.status != "ambiguous" else "human_required",
            "task_status": task_status,
        }
        reporting_tasks = [task_record]
        outcome = TaskOutcome(
            task_id=packet.task_id,
            metric_id=metric.metric_id,
            status=effective_status,
            found_status=found_status,
            review_status=task_record["review_status"],
            guard_accepted=guard.accepted,
            attempts=attempts,
            result_counts={
                "reporting_task": 1,
                "quantitative_observation": len(quantitative),
                "qualitative_assertion": len(qualitative),
                "attribute_value": len(attributes),
                "dimension_value": len(dimensions),
                "evidence_reference": len(evidence),
            },
            uncertainty_reason=uncertainty,
            source_validation_status="passed" if guard.accepted else "failed",
            semantic_decision_status=semantic_status,
            display_readiness_status=display_readiness,
        )
        return MaterializedTaskResult(
            outcome=outcome,
            reporting_tasks=reporting_tasks,
            quantitative_observations=quantitative,
            qualitative_assertions=qualitative,
            attribute_values=attributes,
            dimension_values=dimensions,
            evidence_references=evidence,
        )

    @staticmethod
    def _display_readiness(
        *, quantitative, qualitative, dimensions, fixed_element_ids=None
    ) -> str:
        facts = [*quantitative, *qualitative]
        if not facts:
            return "not_applicable"
        if len(facts) == 1:
            return "ready"
        fixed_element_ids = set(fixed_element_ids or ())
        dimensions_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in dimensions:
            dimensions_by_parent[item["parent_record_id"]].append(item)

        identities = []
        for fact in facts:
            fact_id = fact.get("observation_id") or fact.get("assertion_id")
            typed_dimensions = tuple(sorted(
                (
                    item.get("element_id"),
                    str(item.get("value_code") or item.get("value_raw") or "").strip(),
                )
                for item in dimensions_by_parent.get(fact_id, [])
                if item.get("element_id") not in fixed_element_ids
                if item.get("value_code") or str(item.get("value_raw") or "").strip()
            ))
            identities.append((
                str(fact.get("reporting_period_raw") or fact.get("reporting_year") or "").strip(),
                str(fact.get("reporting_boundary") or "").strip(),
                typed_dimensions,
            ))
        periods = [identity[0] for identity in identities]
        distinct_periods = len(set(periods)) == len(periods) and all(periods)
        # This is a display warning, not a semantic veto.  A package-fixed scope
        # does not distinguish sibling rows.  Repeated identities are allowed,
        # however, because two source columns may be equivalent unit
        # presentations of one measurement (for example GWh and TJ).  The
        # organizer links those losslessly after materialization.
        sufficiently_identified = all(
            period and (typed_dimensions or boundary or distinct_periods)
            for period, boundary, typed_dimensions in identities
        )
        return "ready" if sufficiently_identified else "needs_semantic_completion"

    @staticmethod
    def _resolve_value(source: Any, value_raw: str | None = None) -> dict[str, Any]:
        if source is None:
            return {"raw": None, "normalized": None, "candidate": None, "span": None}
        if hasattr(source, "candidate_id"):
            return {
                "raw": value_raw if value_raw is not None else source.raw_value,
                "normalized": source.normalized_value,
                "candidate": source,
                "span": None,
            }
        raw = value_raw if value_raw is not None else source.text
        return {"raw": raw, "normalized": raw, "candidate": None, "span": source}

    @staticmethod
    def _base_observation(
        observation_id: str,
        task_id: str,
        metric_id: str,
        evidence_set_id: str,
        confidence: float | None,
        review_status: str,
    ) -> dict[str, Any]:
        return {
            "observation_id": observation_id,
            "task_id": task_id,
            "metric_id": metric_id,
            "value_origin": "reported",
            "value_raw": "",
            "comparator": "exact",
            "value_numeric": None,
            "value_lower": None,
            "value_upper": None,
            "unit_raw": None,
            "unit_id": None,
            "reporting_period_raw": None,
            "reporting_year": None,
            "period_start": None,
            "period_end": None,
            "reporting_boundary": None,
            "evidence_set_id": evidence_set_id,
            "confidence": confidence,
            "review_status": review_status,
        }

    @staticmethod
    def _base_assertion(
        assertion_id: str,
        task_id: str,
        metric_id: str,
        evidence_set_id: str,
        confidence: float | None,
        review_status: str,
    ) -> dict[str, Any]:
        return {
            "assertion_id": assertion_id,
            "task_id": task_id,
            "metric_id": metric_id,
            "statement_raw": "",
            "statement_summary": None,
            "reporting_period_raw": None,
            "reporting_year": None,
            "period_start": None,
            "period_end": None,
            "reporting_boundary": None,
            "evidence_set_id": evidence_set_id,
            "confidence": confidence,
            "review_status": review_status,
        }

    def _apply_core_fields(
        self,
        record: dict[str, Any],
        resolved: list[dict[str, Any]],
        record_type: str,
    ) -> None:
        for item in resolved:
            element = item["element"]
            binding = element.binding
            if binding.record_type.value != record_type or binding.storage.value != "core_field":
                continue
            target = binding.target
            value = item["value"]
            candidate = value["candidate"]
            raw = value["raw"]
            if target == "value_raw":
                record["value_raw"] = raw or ""
                if candidate is not None:
                    record["value_numeric"] = candidate.normalized_value
                    record["comparator"] = candidate.comparator
                    record["unit_raw"] = candidate.unit_raw or record.get("unit_raw")
                    record["unit_id"] = candidate.unit_id or record.get("unit_id")
                else:
                    record["value_numeric"] = self._numeric_from_visible(raw)
            elif target == "unit_raw":
                record["unit_raw"] = candidate.unit_raw if candidate and candidate.unit_raw else raw
                if candidate is not None:
                    record["unit_id"] = candidate.unit_id
            elif target == "reporting_period_raw":
                record[target] = raw
                if candidate is not None and candidate.candidate_type == "year":
                    record["reporting_year"] = int(candidate.normalized_value)
                else:
                    year = re.search(r"(?:19|20)\d{2}", str(raw or ""))
                    if year:
                        record["reporting_year"] = int(year.group(0))
            elif target in record:
                record[target] = raw

    @staticmethod
    def _numeric_from_visible(raw: Any) -> str | None:
        match = re.search(r"[-+]?\d[\d,，]*(?:\.\d+)?", str(raw or ""))
        if not match:
            return None
        try:
            return format(
                Decimal(match.group(0).replace(",", "").replace("，", "")),
                "f",
            )
        except InvalidOperation:
            return None

    def _attribute_record(
        self,
        packet,
        metric,
        element,
        value,
        parent_id,
        sequence,
        evidence_set_id,
        review_status,
        identity_token,
    ) -> dict[str, Any]:
        value_type, field, typed_value = self._typed_attribute_value(
            element.value_contract.primary_type.value, value["normalized"]
        )
        record = {
            "attribute_id": stable_id(
                "attr", parent_id, element.element_id, identity_token
            ),
            "parent_record_type": element.binding.record_type.value,
            "parent_record_id": parent_id,
            "metric_id": metric.metric_id,
            "element_id": element.element_id,
            "value_type": value_type,
            "value_text": None,
            "value_numeric": None,
            "value_boolean": None,
            "value_date": None,
            "value_code": None,
            "unit_id": value["candidate"].unit_id if value["candidate"] else None,
            "sequence": sequence,
            "evidence_set_id": evidence_set_id,
            "review_status": review_status,
        }
        record[field] = typed_value
        return record

    def _dimension_record(
        self,
        packet,
        metric,
        element,
        value,
        code_sets,
        parent_id,
        sequence,
        evidence_set_id,
        review_status,
        identity_token,
    ) -> dict[str, Any]:
        raw = str(value["raw"] or "")
        code = None
        code_set_id = element.value_contract.code_set_id
        if element.value_contract.fixed_value is not None:
            code = str(element.value_contract.fixed_value)
        elif code_set_id in code_sets:
            code = self._match_code(raw, code_sets[code_set_id])
        return {
            "dimension_value_id": stable_id(
                "dim", parent_id, element.element_id, identity_token
            ),
            "parent_record_type": element.binding.record_type.value,
            "parent_record_id": parent_id,
            "metric_id": metric.metric_id,
            "element_id": element.element_id,
            "dimension_id": element.binding.target,
            "value_raw": raw,
            "value_code": code,
            "value_level": None,
            "parent_dimension_value_id": None,
            "sequence": sequence,
            "evidence_set_id": evidence_set_id,
            "review_status": review_status,
        }

    @staticmethod
    def _occurrence_identity(
        *,
        parent_type: str,
        group_ref_id: str,
        assignment: Any,
        sequence: int,
    ) -> str:
        if parent_type == "reporting_task":
            source_ref_id = assignment.source_ref_id if assignment is not None else "fixed"
            return f"{group_ref_id}:{source_ref_id}:{sequence}"
        return str(sequence)

    @staticmethod
    def _typed_attribute_value(primary_type: str, value: Any) -> tuple[str, str, Any]:
        if primary_type in {"decimal", "decimal_range", "integer"}:
            try:
                numeric = str(Decimal(str(value)))
            except (InvalidOperation, ValueError):
                numeric = None
            return "numeric", "value_numeric", numeric
        if primary_type == "boolean":
            normalized = str(value).strip().lower()
            boolean = normalized in {"true", "1", "yes", "是", "有"}
            return "boolean", "value_boolean", boolean
        if primary_type == "date":
            return "date", "value_date", value
        if primary_type == "enum":
            return "code", "value_code", value
        return "text", "value_text", value

    @staticmethod
    def _match_code(raw: str, code_set: Any) -> str | None:
        normalized = re.sub(r"\s+", "", raw).lower()
        for item in code_set.values:
            candidates = [item.code, *item.labels.values()]
            for candidate in candidates:
                token = re.sub(r"\s+", "", candidate).lower()
                if token and (normalized == token or token in normalized):
                    return item.code
        return None

    @staticmethod
    def _evidence_records(
        *,
        record_type: str,
        record_id: str,
        evidence_set_id: str,
        packet: EvidencePacket,
        span_ids: list[str],
        review_status: str,
    ) -> list[dict[str, Any]]:
        spans = {item.span_id: item for item in packet.spans}
        records: list[dict[str, Any]] = []
        for sequence, span_id in enumerate(span_ids, 1):
            span = spans[span_id]
            locator = span.locators[0]
            bbox = locator.bbox or {}
            excerpt = span.text[:2000]
            normalized_excerpt = re.sub(r"\s+", " ", excerpt).strip()
            records.append(
                {
                    "evidence_id": stable_id("evidence", record_id, span_id, sequence),
                    "evidence_set_id": evidence_set_id,
                    "source_record_type": record_type,
                    "source_record_id": record_id,
                    "document_id": span.document_id,
                    "ir_run_id": span.ir_run_id,
                    "ir_revision": span.ir_revision,
                    "ir_object_type": locator.ir_object_type,
                    "ir_object_id": locator.ir_object_id,
                    "pdf_page_index": locator.pdf_page_index,
                    "printed_page_label": locator.printed_page_label,
                    "bbox_x0": bbox.get("x0"),
                    "bbox_y0": bbox.get("y0"),
                    "bbox_x1": bbox.get("x1"),
                    "bbox_y1": bbox.get("y1"),
                    "evidence_excerpt": excerpt,
                    "quote_sha256": hashlib.sha256(
                        normalized_excerpt.encode("utf-8")
                    ).hexdigest(),
                    "evidence_role": "primary" if sequence == 1 else "supporting",
                    "review_status": review_status,
                }
            )
        return records
