from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
from typing import Any

from esg_targeted.contracts import JobRecord
from esg_targeted.ids import safe_filename, stable_id
from esg_targeted.evidence.units import UNIT_ONLY_RE
from esg_targeted.inspection.contracts import (
    InspectionEvidence,
    InspectionElementCell,
    InspectionElementColumn,
    InspectionFact,
    InspectionIssue,
    InspectionMetric,
    InspectionOverview,
    InspectionValue,
    ResultInspection,
)
from esg_targeted.io import read_json
from esg_targeted.results.organization import FactOrganizer, element_order
from esg_targeted.results.spec import (
    EXPORT_FILES,
    INSPECTION_SCHEMA_VERSION,
    RECORD_LAYOUT,
)
from esg_targeted.storage.artifacts import ArtifactStore


TERMINAL_JOB_STATUSES = {"completed", "partial", "failed", "cancelled", "interrupted"}


def _looks_numeric(value: str) -> bool:
    return bool(re.fullmatch(r"[+\-\d,，.()%％\s]+", value))


class ResultInspectionService:
    """Build a read-only UI model from immutable extraction artifacts."""

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store

    def build(self, job: JobRecord) -> ResultInspection:
        job_id = job.job_id
        manifest = self._read_optional(job_id, "manifest.json") or job.summary or {}
        standard = self._read_optional(job_id, "input/standard-package.json") or {}
        package = self._read_optional(job_id, EXPORT_FILES["result_package"]) or {}
        records = self._record_collections(package.get("records", {}))
        records, organization = FactOrganizer().organize(records, standard)
        metrics = self._build_metrics(job, manifest, standard, records)
        for metric in metrics:
            for fact in metric.facts:
                fact.organization = organization["facts"].get(fact.fact_id, {})
            metric.logical_measurement_count = len({f.organization.get("logical_measurement_id", f.fact_id) for f in metric.facts})
        issues = self._integrity_issues(job, manifest, package, records)

        status_counts = Counter(item.status for item in metrics)
        all_facts = [fact for metric in metrics for fact in metric.facts]
        overview = InspectionOverview(
            metric_count=len(metrics),
            found_count=status_counts["found"],
            partial_count=status_counts["partial"],
            not_found_count=status_counts["not_found"],
            ambiguous_count=status_counts["ambiguous"],
            pending_count=status_counts["pending"],
            guard_accepted_count=sum(item.guard_accepted is True for item in metrics),
            human_review_count=sum(item.review_status == "human_required" for item in metrics),
            quantitative_fact_count=sum(
                item.fact_type == "quantitative_observation" for item in all_facts
            ),
            qualitative_fact_count=sum(
                item.fact_type == "qualitative_assertion" for item in all_facts
            ),
            evidence_count=sum(len(item.evidence) for item in all_facts),
        )

        terminal = job.status.value in TERMINAL_JOB_STATUSES
        has_results = bool(package)
        if has_results:
            availability = "ready"
            message = "成果包已生成；本页面只读展示，不会修改任何抽取产物。"
        elif terminal:
            availability = "unavailable"
            message = "该任务没有生成完整成果包，请检查失败信息和阶段产物。"
        else:
            availability = "building"
            message = "任务仍在运行，成果检查会在物化与导出完成后自动出现。"

        if not terminal and not issues:
            integrity_status = "pending"
        elif any(item.severity == "error" for item in issues):
            integrity_status = "failed"
        elif issues:
            integrity_status = "warning"
        else:
            integrity_status = "passed"

        return ResultInspection(
            schema_version=INSPECTION_SCHEMA_VERSION,
            result_schema_version=manifest.get("schema_version"),
            availability=availability,
            message=message,
            job={
                "job_id": job.job_id,
                "status": job.status.value,
                "stage": job.stage,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "error": job.error,
            },
            model_execution=manifest.get(
                "model_execution",
                {
                    "requested_provider": job.request.semantic_provider,
                    "requested_model": job.request.semantic_model,
                    "actual_providers": [],
                    "actual_models": [],
                    "provider_match": None,
                },
            ),
            document=manifest.get("document", {}),
            standard=manifest.get(
                "standard",
                {
                    "package_id": job.request.package_id,
                    "package_version": job.request.package_version,
                },
            ),
            overview=overview,
            integrity_status=integrity_status,
            integrity_issues=issues,
            metrics=metrics,
            downloads={
                key: path
                for key, path in EXPORT_FILES.items()
                if self._exists(job_id, path)
            },
        )

    def _build_metrics(
        self,
        job: JobRecord,
        manifest: dict[str, Any],
        standard: dict[str, Any],
        records: dict[str, list[dict[str, Any]]],
    ) -> list[InspectionMetric]:
        metric_definitions = {item["metric_id"]: item for item in standard.get("metrics", [])}
        element_definitions = {
            item["element_id"]: item for item in standard.get("elements", [])
        }
        selected_ids = job.request.metric_ids or list(metric_definitions)
        outcomes = {item["metric_id"]: item for item in manifest.get("outcomes", [])}
        tasks = {item["metric_id"]: item for item in records["reporting_tasks"]}
        facts_by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records["quantitative_observations"]:
            facts_by_metric[record["metric_id"]].append(record)
        for record in records["qualitative_assertions"]:
            facts_by_metric[record["metric_id"]].append(record)

        attributes_by_parent = self._group(records["attribute_values"], "parent_record_id")
        dimensions_by_parent = self._group(records["dimension_values"], "parent_record_id")
        evidence_by_source = self._group(records["evidence_references"], "source_record_id")

        result: list[InspectionMetric] = []
        inventory_spans = self._inventory_spans(job.job_id)
        for metric_id in selected_ids:
            metric = metric_definitions.get(metric_id, {})
            metric_elements = [
                item
                for item in element_definitions.values()
                if item.get("metric_id") == metric_id
            ]
            outcome = outcomes.get(metric_id, {})
            task = tasks.get(metric_id, {})
            task_id = outcome.get("task_id") or task.get("task_id")
            slug = safe_filename(metric_id)
            packet = self._read_optional(job.job_id, f"packets/{slug}.json") or {}
            guard = self._read_optional(job.job_id, f"guards/{slug}.json")
            decision_artifact = self._read_optional(job.job_id, f"decisions/{slug}.json")
            retrieval_artifact = self._read_optional(job.job_id, f"retrieval/{slug}.json")
            packet_spans = packet.get("spans", [])
            provenance_by_fact = self._decision_provenance(decision_artifact)

            facts = [
                self._fact_view(
                    item,
                    attributes_by_parent,
                    dimensions_by_parent,
                    evidence_by_source,
                    element_definitions,
                    packet_spans,
                    metric_elements,
                    provenance_by_fact.get(
                        item.get("observation_id") or item.get("assertion_id"), {}
                    ),
                )
                for item in facts_by_metric.get(metric_id, [])
            ]
            paths = {
                "retrieval": f"retrieval/{slug}.json",
                "evidence_selection": f"packets/{slug}.json",
                "evidence_regions": f"packets/{slug}.regions.json",
                "decision": f"decisions/{slug}.json",
                "guard": f"guards/{slug}.json",
            }
            result.append(
                InspectionMetric(
                    task_id=task_id,
                    metric_id=metric_id,
                    source_datapoint_id=metric.get("source_datapoint_id", metric_id),
                    labels=metric.get("labels", {"zh": metric_id, "en": metric_id}),
                    data_class=metric.get("data_class", "unknown"),
                    description=metric.get("description", ""),
                    status=outcome.get("status", "pending"),
                    found_status=outcome.get("found_status", task.get("found_status", "pending")),
                    review_status=outcome.get(
                        "review_status", task.get("review_status", "pending")
                    ),
                    guard_accepted=outcome.get("guard_accepted"),
                    attempts=int(outcome.get("attempts", 0)),
                    uncertainty_reason=outcome.get("uncertainty_reason"),
                    contract_validation_status=outcome.get(
                        "contract_validation_status"
                    ),
                    contract_error=outcome.get("contract_error"),
                    result_counts=outcome.get("result_counts", {}),
                    element_columns=self._element_columns(metric_elements),
                    facts=facts,
                    unmapped_measurements=[g for g in (decision_artifact or {}).get("accepted_decision", {}).get("fact_groups", []) if g.get("metric_match", "match") != "match"],
                    task_attributes=self._value_views(
                        attributes_by_parent.get(task_id, []), element_definitions
                    ),
                    task_dimensions=self._value_views(
                        dimensions_by_parent.get(task_id, []), element_definitions
                    ),
                    guard=guard,
                    decision=self._decision_view(decision_artifact),
                    retrieval=self._retrieval_view(retrieval_artifact, packet, inventory_spans),
                    artifact_paths={
                        key: path for key, path in paths.items() if self._exists(job.job_id, path)
                    },
                )
            )
        return result

    def _fact_view(
        self,
        record: dict[str, Any],
        attributes_by_parent: dict[str, list[dict[str, Any]]],
        dimensions_by_parent: dict[str, list[dict[str, Any]]],
        evidence_by_source: dict[str, list[dict[str, Any]]],
        elements: dict[str, dict[str, Any]],
        packet_spans: list[dict[str, Any]],
        metric_elements: list[dict[str, Any]],
        provenance_by_element: dict[str, str],
    ) -> InspectionFact:
        quantitative = "observation_id" in record
        fact_id = record.get("observation_id") or record["assertion_id"]
        raw_value = record.get("value_raw") if quantitative else None
        statement = record.get("statement_raw") if not quantitative else None
        display_value = raw_value or statement or ""
        fact_attributes = attributes_by_parent.get(fact_id, [])
        fact_dimensions = dimensions_by_parent.get(fact_id, [])
        element_cells = self._element_cells(
            record,
            metric_elements,
            fact_attributes,
            fact_dimensions,
            provenance_by_element,
        )
        evidence_records = evidence_by_source.get(fact_id, [])
        evidence_views = [
            self._evidence_view(item, packet_spans) for item in evidence_records
        ]
        provenance_modes = sorted(set(provenance_by_element.values()))
        source_kind = (
            "visual_table"
            if "visual" in provenance_modes
            else self._source_kind(evidence_records, packet_spans)
        )
        return InspectionFact(
            fact_id=fact_id,
            fact_type="quantitative_observation" if quantitative else "qualitative_assertion",
            metric_id=record["metric_id"],
            display_value=str(display_value),
            value_raw=raw_value,
            value_numeric=record.get("value_numeric"),
            comparator=record.get("comparator"),
            unit_raw=record.get("unit_raw"),
            unit_id=record.get("unit_id"),
            statement_raw=statement,
            statement_summary=record.get("statement_summary"),
            reporting_period_raw=record.get("reporting_period_raw"),
            reporting_year=record.get("reporting_year"),
            period_start=record.get("period_start"),
            period_end=record.get("period_end"),
            reporting_boundary=record.get("reporting_boundary"),
            confidence=record.get("confidence"),
            review_status=record["review_status"],
            source_kind=source_kind,
            provenance_modes=provenance_modes,
            quality_issues=self._fact_quality_issues(
                fact_id=fact_id,
                quantitative=quantitative,
                element_cells=element_cells,
            ),
            attributes=self._value_views(fact_attributes, elements),
            dimensions=self._value_views(fact_dimensions, elements),
            element_cells=element_cells,
            evidence=evidence_views,
            raw_record=record,
        )

    @staticmethod
    def _source_kind(
        evidence_records: list[dict[str, Any]],
        packet_spans: list[dict[str, Any]],
    ) -> str:
        matched = []
        for record in evidence_records:
            for span in packet_spans:
                if any(
                    locator.get("ir_object_id") == record.get("ir_object_id")
                    and locator.get("pdf_page_index") == record.get("pdf_page_index")
                    for locator in span.get("locators", [])
                ):
                    matched.append(span)
        if any(
            "visual_model_transcription" in span.get("quality_flags", [])
            or str(span.get("structural_context", {}).get("object_type", "")).startswith(
                "visual_"
            )
            for span in matched
        ):
            return "visual_table"
        if any(
            span.get("has_table_structure")
            or str(span.get("structural_context", {}).get("object_type", "")).startswith(
                "table_"
            )
            for span in matched
        ):
            return "ir_table"
        if matched:
            return "text"
        return "unknown"

    @staticmethod
    def _fact_quality_issues(
        *,
        fact_id: str,
        quantitative: bool,
        element_cells: list[InspectionElementCell],
    ) -> list[InspectionIssue]:
        value_cells = [
            item
            for item in element_cells
            if item.binding_target == "value_raw" or item.semantic_role == "value"
        ]
        unit_cells = [
            item
            for item in element_cells
            if item.binding_target in {"unit_raw", "unit_id"}
            or item.semantic_role == "unit"
        ]
        period_cells = [
            item
            for item in element_cells
            if item.binding_target
            in {"reporting_period_raw", "reporting_year", "period_start", "period_end"}
            or item.semantic_role == "period"
        ]
        issues = []
        has_value = any(item.value is not None and item.value != "" for item in value_cells)
        if quantitative and has_value:
            if unit_cells and not any(
                item.value is not None and item.value != "" for item in unit_cells
            ):
                issues.append(
                    InspectionIssue(
                        code="value_without_unit",
                        severity="warning",
                        message="量化值缺少同一事实单元内可追溯的单位。",
                        record_id=fact_id,
                    )
                )
            if period_cells and not any(
                item.value is not None and item.value != "" for item in period_cells
            ):
                issues.append(
                    InspectionIssue(
                        code="value_without_period",
                        severity="warning",
                        message="量化值缺少同一事实单元内可追溯的报告期。",
                        record_id=fact_id,
                    )
                )
        for cell in element_cells:
            if cell.semantic_role not in {"dimension", "subject", "category"}:
                continue
            if cell.source == "fixed":
                continue
            value = str(cell.value or "").strip()
            if value and (UNIT_ONLY_RE.fullmatch(value) or _looks_numeric(value)):
                issues.append(
                    InspectionIssue(
                        code="invalid_dimension_label",
                        severity="error",
                        message=f"维度字段“{cell.label.get('zh') or cell.element_code}”看起来是数值或单位。",
                        record_id=fact_id,
                    )
                )
        return issues

    @staticmethod
    def _element_columns(
        metric_elements: list[dict[str, Any]],
    ) -> list[InspectionElementColumn]:
        return [
            InspectionElementColumn(
                element_id=item["element_id"],
                element_code=item["element_code"],
                label=item.get("labels", {}),
                value_type=item.get("value_contract", {}).get("primary_type", "string"),
                required=item.get("requirement_level") == "required",
                nullable=bool(item.get("null_allowed", True)),
                binding_target=item.get("binding", {}).get("target", ""),
                semantic_role=item.get("semantic_role", "context"),
                unit_dimension=item.get("value_contract", {}).get("unit_dimension"),
                fixed_value=item.get("value_contract", {}).get("fixed_value"),
            )
            for item in sorted(metric_elements, key=element_order)
        ]

    @classmethod
    def _element_cells(
        cls,
        fact: dict[str, Any],
        metric_elements: list[dict[str, Any]],
        attributes: list[dict[str, Any]],
        dimensions: list[dict[str, Any]],
        provenance_by_element: dict[str, str] | None = None,
    ) -> list[InspectionElementCell]:
        values_by_element: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in [*attributes, *dimensions]:
            values_by_element[str(row.get("element_id"))].append(row)
        value_fields = (
            "value_raw",
            "value_text",
            "value_numeric",
            "value_boolean",
            "value_date",
            "value_code",
        )
        cells = []
        for item in metric_elements:
            contract = item.get("value_contract", {})
            binding = item.get("binding", {})
            fixed = contract.get("fixed_value")
            linked = values_by_element.get(item["element_id"], [])
            if fixed is not None:
                value, source = fixed, "fixed"
            elif binding.get("storage") == "core_field":
                value = fact.get(binding.get("target"))
                source = (
                    "core_field"
                    if value is not None and value != ""
                    else "missing"
                )
            elif linked:
                linked_values = [
                    next(
                        (row.get(key) for key in value_fields if row.get(key) is not None),
                        None,
                    )
                    for row in linked
                ]
                linked_values = [value for value in linked_values if value is not None]
                value = linked_values[0] if len(linked_values) == 1 else linked_values
                source = binding.get("storage", "attribute")
            else:
                value, source = None, "missing"
            column = cls._element_columns([item])[0]
            cells.append(
                InspectionElementCell(
                    **column.model_dump(mode="python"),
                    value=value,
                    source=source,
                    provenance_mode=(provenance_by_element or {}).get(
                        item["element_id"]
                    ),
                )
            )
        return cells

    @staticmethod
    def _decision_provenance(
        artifact: dict[str, Any] | None,
    ) -> dict[str, dict[str, str]]:
        if not artifact:
            return {}
        decision = artifact.get("accepted_decision") or {}
        task_id = decision.get("task_id")
        if not task_id:
            return {}
        result: dict[str, dict[str, str]] = {}
        for index, group in enumerate(decision.get("fact_groups", []), 1):
            group_id = group.get("group_ref_id")
            if not group_id:
                continue
            modes = {
                str(item.get("element_id")): str(item.get("source_mode"))
                for item in group.get("assignments", [])
                if item.get("element_id")
                and item.get("source_mode") in {"candidate", "span", "visual"}
            }
            result[stable_id("obs", task_id, group_id, index)] = modes
            result[stable_id("assert", task_id, group_id, index)] = modes
        return result

    @staticmethod
    def _value_views(
        rows: list[dict[str, Any]], elements: dict[str, dict[str, Any]]
    ) -> list[InspectionValue]:
        result = []
        value_fields = (
            "value_raw",
            "value_text",
            "value_numeric",
            "value_boolean",
            "value_date",
            "value_code",
        )
        for row in rows:
            value = next((row.get(key) for key in value_fields if row.get(key) is not None), None)
            element = elements.get(row.get("element_id"), {})
            result.append(
                InspectionValue(
                    element_id=row.get("element_id", "unknown"),
                    label=element.get("labels", {}),
                    value=value,
                    value_type=row.get("value_type"),
                    unit_id=row.get("unit_id"),
                    raw_record=row,
                )
            )
        return result

    @staticmethod
    def _evidence_view(
        record: dict[str, Any], packet_spans: list[dict[str, Any]]
    ) -> InspectionEvidence:
        matching_span = None
        for span in packet_spans:
            if any(
                locator.get("ir_object_id") == record.get("ir_object_id")
                and locator.get("pdf_page_index") == record.get("pdf_page_index")
                for locator in span.get("locators", [])
            ):
                matching_span = span
                break
        bbox_values = {
            key: record.get(f"bbox_{key}") for key in ("x0", "y0", "x1", "y1")
        }
        bbox = None if all(value is None for value in bbox_values.values()) else bbox_values
        page_index = int(record["pdf_page_index"])
        return InspectionEvidence(
            evidence_id=record["evidence_id"],
            evidence_set_id=record["evidence_set_id"],
            evidence_role=record["evidence_role"],
            excerpt=record["evidence_excerpt"],
            document_id=record["document_id"],
            ir_run_id=record["ir_run_id"],
            ir_revision=int(record["ir_revision"]),
            ir_object_type=record["ir_object_type"],
            ir_object_id=record["ir_object_id"],
            pdf_page_index=page_index,
            pdf_page_number=page_index + 1,
            printed_page_label=record.get("printed_page_label"),
            section_id=matching_span.get("section_id") if matching_span else None,
            section_title=matching_span.get("section_title") if matching_span else None,
            bbox=bbox,
            quote_sha256=record["quote_sha256"],
            review_status=record["review_status"],
        )

    @staticmethod
    def _decision_view(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
        if not artifact:
            return None
        attempts = artifact.get("attempts", [])
        return {
            "accepted_decision": artifact.get("accepted_decision"),
            "attempt_count": len(attempts),
            "routes": [item.get("route", "unknown") for item in attempts],
            "visual_used": any(item.get("visual_used") for item in attempts),
            "image_count": sum(int(item.get("image_count", 0) or 0) for item in attempts),
            "generation_tokens": sum(
                int(item.get("generation_tokens", 0) or 0) for item in attempts
            ),
            "elapsed_seconds": round(
                sum(float(item.get("elapsed_seconds", 0) or 0) for item in attempts), 3
            ),
            "prompt_tokens": sum(int(item.get("prompt_tokens", 0) or 0) for item in attempts),
            "input_chars": sum(int(item.get("input_chars", 0) or 0) for item in attempts),
            "attempts": [
                {
                    key: item.get(key)
                    for key in (
                        "attempt",
                        "route",
                        "failure_category",
                        "visual_used",
                        "image_count",
                        "input_chars",
                        "prompt_tokens",
                        "generation_tokens",
                        "output_token_budget",
                        "finish_reason",
                        "timings",
                        "elapsed_seconds",
                        "error",
                        "provider",
                        "model_id",
                        "region",
                        "region_index",
                        "region_count",
                        "region_packet_id",
                        "input_group_count",
                        "input_span_count",
                        "input_candidate_count",
                        "output_row_count",
                    )
                    if key in item
                }
                for item in attempts
            ],
        }

    @staticmethod
    def _retrieval_view(
        artifact: dict[str, Any] | None, packet: dict[str, Any], inventory_spans=None
    ) -> dict[str, Any] | None:
        if not artifact:
            return None
        spans = {item["span_id"]: item for item in [*(inventory_spans or []), *packet.get("spans", [])]}
        top_hits = []
        for hit in artifact.get("hits", [])[:10]:
            span = spans.get(hit.get("span_id"), {})
            top_hits.append(
                {
                    **hit,
                    "text": span.get("text"),
                    "page_index": span.get("page_index"),
                    "printed_page_label": span.get("printed_page_label"),
                    "section_title": span.get("section_title"),
                }
            )
        return {
            "query": artifact.get("query"),
            "sufficiency": artifact.get("sufficiency"),
            "hit_count": len(artifact.get("hits", [])),
            "packet_id": packet.get("packet_id"),
            "packet_span_count": len(packet.get("spans", [])),
            "packet_candidate_count": len(packet.get("candidates", [])),
            "packet_image_count": len(packet.get("page_image_paths", [])),
            "packet_truncated": packet.get("truncated"),
            "packet_budget": packet.get("budget", {}),
            "top_hits": top_hits,
        }

    def _inventory_spans(self, job_id):
        import json
        path = self.artifact_store.job_dir(job_id) / "inventory/spans.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _integrity_issues(
        self,
        job: JobRecord,
        manifest: dict[str, Any],
        package: dict[str, Any],
        records: dict[str, list[dict[str, Any]]],
    ) -> list[InspectionIssue]:
        issues: list[InspectionIssue] = []
        terminal = job.status.value in TERMINAL_JOB_STATUSES
        if terminal and job.status.value in {"completed", "partial"} and not package:
            issues.append(
                InspectionIssue(
                    code="result_package_missing",
                    severity="error",
                    message="任务已结束，但 exports/extraction-result.json 不存在。",
                    artifact_path=EXPORT_FILES["result_package"],
                )
            )
        expected_counts = manifest.get("record_counts", {})
        for collection in RECORD_LAYOUT:
            expected = expected_counts.get(collection)
            actual = len(records[collection])
            if expected is not None and expected != actual:
                issues.append(
                    InspectionIssue(
                        code="record_count_mismatch",
                        severity="error",
                        message=f"{collection} 清单为 {expected} 条，成果包实际为 {actual} 条。",
                        artifact_path=EXPORT_FILES["result_package"],
                    )
                )
        validation = manifest.get("contract_validation")
        if package and not validation:
            issues.append(
                InspectionIssue(
                    code="contract_validation_missing",
                    severity="warning",
                    message="成果包缺少输出合同校验记录，可能来自旧版本链路。",
                    artifact_path="manifest.json",
                )
            )
        return issues

    @staticmethod
    def _record_collections(raw: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        return {key: list(raw.get(key, [])) for key in RECORD_LAYOUT}

    @staticmethod
    def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row.get(key)].append(row)
        return grouped

    def _read_optional(self, job_id: str, relative: str) -> dict[str, Any] | None:
        try:
            return read_json(self.artifact_store.resolve(job_id, relative))
        except FileNotFoundError:
            return None

    def _exists(self, job_id: str, relative: str) -> bool:
        try:
            self.artifact_store.resolve(job_id, relative)
            return True
        except FileNotFoundError:
            return False
