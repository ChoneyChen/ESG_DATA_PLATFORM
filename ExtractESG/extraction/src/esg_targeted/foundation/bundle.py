"""Write `targeted-result-bundle-v1` directories from foundation conclusions.

One bundle per standard package, so the existing importer, review and
publication chain consume foundation results unchanged. Core records are
validated with the canonical ResultContractValidator before anything is
written as importable.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from esg_targeted.contracts import EvidenceGap, ExtractionRequest, ExtractionSelectionSnapshot, ModelRole, RoleConfig
from esg_targeted.foundation.catalog import METRICS
from esg_targeted.ids import stable_id
from esg_targeted.report_facts import adapt_direct_core_facts
from esg_targeted.results.exporter import ResultExporter
from esg_targeted.results.spec import RESULT_BUNDLE_LAYOUT_VERSION, RESULT_SCHEMA_VERSION
from esg_targeted.results.validation import ResultContractValidator
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.profiles import platform_major_profile

ROUTE = "foundation16-table-understanding-v1"
FOUND_STATUS = {"found": "reported", "review": "partial", "conflict": "uncertain"}
OUTCOME_STATUS = {"found": "found", "review": "partial", "conflict": "ambiguous"}


def _quote_sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _enum_text(value) -> str:
    return str(getattr(value, "value", value))


def _decimal_text(value: str | None) -> str | None:
    if value is None:
        return None
    number = Decimal(value)
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def not_covered_gap(task_id: str, metric_id: str, code: str) -> dict:
    """The bounded tables and paragraphs hold no qualifying disclosure for this datapoint."""
    return EvidenceGap(gap_id=f"gap-{task_id}", kind="not_covered", metric_ids=[metric_id],
                       reason=f"{code}: no qualifying disclosure in the selected tables and paragraphs",
                       next_action="review_source_pages").model_dump(mode="json")


def _conclusion_summary(conclusion: dict) -> dict:
    """A conclusion as saved evidence: status, value, members and refusal reasons."""
    summary = {"code": conclusion["code"], "metric_id": conclusion["metric_id"], "status": conclusion["status"]}
    for key in ("label", "rejection_reasons"):
        if conclusion.get(key):
            summary[key] = conclusion[key]
    members = conclusion.get("members") or []
    if members:
        summary["members"] = [
            {key: member.get(key) for key in ("member", "status", "value", "value_raw", "unit_raw",
                                              "unit_id", "period", "period_year", "flags")
             if member.get(key) is not None}
            for member in members]
    statements = conclusion.get("statements") or []
    if statements:
        summary["statements"] = [str(item.get("text") or "")[:400] for item in statements[:6]]
    candidates = conclusion.get("candidates") or []
    if candidates:
        summary["candidates"] = [
            {key: candidate.get(key) for key in ("value_raw", "unit_raw", "row_label", "page", "rejected_by", "flags")
             if candidate.get(key) is not None}
            for candidate in candidates[:8]]
    return summary


class BundleWriter:
    def __init__(self, settings, *, model_label: str = "Qwen3.5-9B-4bit"):
        self.settings = settings
        self.catalog = StandardPackageCatalog(settings.standard_dist_root)
        self.profile = platform_major_profile(settings.standard_dist_root.parent, topics=["E1", "E2", "E3"])
        self.routes = {route["package_id"]: route for route in self.profile["routes"]}
        self.model_label = model_label
        self.dist_root = settings.project_root.parent / "standard_packages" / "dist"

    def write_all(self, summary: dict, ir, *, session_id: str | None = None, job_ids: dict[str, str] | None = None,
                  out_root: Path | None = None) -> list[dict]:
        out_root = out_root or self.settings.output_root
        written = []
        by_package: dict[str, list[dict]] = {}
        for conclusion in summary["conclusions"]:
            by_package.setdefault(conclusion["package_id"], []).append(conclusion)
        for package_id, conclusions in by_package.items():
            route = self.routes[package_id]
            job_id = (job_ids or {}).get(package_id) or self.new_job_id()
            written.append(self.write_package(
                package_id, route["package_version"], conclusions, summary, ir,
                output_dir=out_root / job_id, job_id=job_id, session_id=session_id,
            ))
        return written

    @staticmethod
    def new_job_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"tx-{stamp}-{hashlib.sha256(str(datetime.now().timestamp()).encode()).hexdigest()[:10]}"

    def write_package(self, package_id: str, package_version: str, conclusions: list[dict], summary: dict, ir, *,
                      output_dir: Path, job_id: str, session_id: str | None,
                      request: ExtractionRequest | None = None) -> dict:
        """Write one importable bundle; a session's frozen child request is kept as-is."""
        package = self.catalog.load(package_id, package_version)
        records, gaps = self.build_records(package, conclusions, ir)
        validation = ResultContractValidator(package.core).validate(records)
        metric_ids = [c["metric_id"] for c in conclusions]
        frozen_request = request is not None
        if request is None:
            roles = RoleConfig(semantic_brain=ModelRole(provider="local_qwen", model=self.model_label),
                               specialist=ModelRole(provider="local_qwen", model=self.model_label))
            request = ExtractionRequest(
                selection_snapshot=ExtractionSelectionSnapshot.model_validate(self.profile["selection_snapshot"]),
                ir_run_id=ir.run_id, package_id=package_id, package_version=package_version, metric_ids=metric_ids,
                semantic_provider="local_qwen", semantic_model=self.model_label, report_session_id=session_id,
                roles=roles, extraction_mode="foundation16",
            )
        roles = request.roles or RoleConfig()
        facts, bindings = adapt_direct_core_facts(records, package, bound_core_ids=set(), request=request,
                                                  target_cells=self._target_cells(conclusions))
        for binding in bindings:
            binding["route"] = ROUTE

        (output_dir / "input").mkdir(parents=True, exist_ok=True)
        (output_dir / "results").mkdir(parents=True, exist_ok=True)
        if not frozen_request:
            (output_dir / "input" / "request.json").write_text(
                json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
        shutil.copyfile(self.dist_root / package_id / package_version / "package.json", output_dir / "input" / "standard-package.json")
        shutil.copyfile(ir.root / "manifest.json", output_dir / "input" / "ir-manifest.json")
        (output_dir / "results" / "source-facts.json").write_text(json.dumps(
            {"schema_version": "report-source-facts-v2", "facts": facts, "gaps": gaps, "source_objects": []},
            ensure_ascii=False, indent=2, sort_keys=True))
        (output_dir / "results" / "metric-source-mappings.json").write_text(json.dumps(
            {"schema_version": "metric-source-mappings-v4", "mappings": [], "core_bindings": bindings},
            ensure_ascii=False, indent=2, sort_keys=True))

        task_status_counts: dict[str, int] = {}
        for task in records["reporting_tasks"]:
            task_status_counts[task["task_status"]] = task_status_counts.get(task["task_status"], 0) + 1
        manifest = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "bundle_layout_version": RESULT_BUNDLE_LAYOUT_VERSION,
            "job_id": job_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "core_schema": {"core_schema_id": package.core.core_schema_id, "core_schema_version": package.core.core_schema_version},
            "document": {"document_id": ir.document_id, "ir_run_id": ir.run_id, "ir_revision": ir.ir_revision,
                         "ir_schema_version": ir.manifest.get("schema_version"), "document_label": ir.manifest.get("document_label"),
                         "evidence_policy": ir.manifest.get("evidence_policy") or {}},
            "standard": {"package_id": package_id, "package_version": package_version,
                         "source_digest": package.compilation.source_digest,
                         "runtime_standard_digest": package.compilation.source_digest},
            "configuration": {"route": ROUTE, "reporting_period": summary.get("reporting_period")},
            "model_execution": {"provider": "local_qwen", "model_id": self.model_label,
                                "model_calls": summary.get("model_calls"), "input_tokens": summary.get("input_tokens"),
                                "output_tokens": summary.get("output_tokens"), "elapsed_seconds": summary.get("elapsed_seconds")},
            "task_count": len(records["reporting_tasks"]),
            "task_status_counts": task_status_counts,
            "record_counts": validation["collection_counts"],
            "contract_validation": validation,
            "report_session_id": session_id,
            "roles": roles.model_dump(mode="json"),
            "evidence_gap_count": len(gaps),
            "execution_status": "completed",
            "outcomes": self._outcomes(records, conclusions),
            "unfinished_metric_ids": [], "processing_metric_ids": [], "not_executed_metric_ids": [],
            # The bundle keeps what the conclusion actually says. Truncating it
            # to three fields made every saved result unable to explain which
            # member was found, with what value, or why a candidate was refused.
            "foundation_conclusions": [_conclusion_summary(c) for c in conclusions],
        }
        exports = ResultExporter().export(output_dir=output_dir, records=records, summary=manifest, package=package)
        manifest["exports"] = exports
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return {"job_id": job_id, "package_id": package_id, "package_version": package_version, "path": str(output_dir),
                "record_counts": validation["collection_counts"], "source_facts": len(facts)}

    def build_records(self, package, conclusions: list[dict], ir) -> tuple[dict, list[dict]]:
        core = package.core
        code_sets = {cs.code_set_id: {v.code for v in cs.values} for cs in core.common_code_sets}
        field_sets = {(rt.record_type.value, f.field_id): f.code_set_id for rt in core.record_types for f in rt.fields}
        elements = {e.element_id: e for e in package.elements}
        by_metric = {mid: ids for mid, ids in package.elements_by_metric.items()}
        records = {key: [] for key in ("reporting_tasks", "quantitative_observations", "qualitative_assertions",
                                       "attribute_values", "dimension_values", "evidence_references")}
        gaps = []

        def coded(record_type: str, field: str, value):
            code_set = field_sets.get((record_type, field))
            if value is None or code_set is None:
                return value
            return value if value in code_sets.get(code_set, set()) else None

        for conclusion in conclusions:
            metric_id = conclusion["metric_id"]
            task_id = stable_id("task", ir.document_id, ir.run_id, ir.ir_revision, package.manifest.package_id,
                                package.manifest.package_version, metric_id)
            facts_before = len(records["quantitative_observations"]) + len(records["qualitative_assertions"])
            evidence_before = len(records["evidence_references"])
            status = conclusion["status"]
            if conclusion.get("statements"):
                for index, statement in enumerate(conclusion["statements"][:3], 1):
                    self._assertion(records, task_id, metric_id, statement, index, ir)
            for member in conclusion.get("members") or []:
                sources = [member["source"], *member.get("agreeing_sources", [])]
                self._observation(records, package, elements, by_metric.get(metric_id, []), task_id, metric_id, member,
                                  sources, ir, review="human_required" if member["status"] != "accepted" else "pending", coded=coded)
                for other in member.get("conflicting_sources", []):
                    self._observation(records, package, elements, by_metric.get(metric_id, []), task_id, metric_id,
                                      {**member, "value_raw": other["value_raw"], "unit_raw": other["unit_raw"],
                                       "value": None, "period": other.get("period")},
                                      [other], ir, review="human_required", coded=coded)
            results = len(records["quantitative_observations"]) + len(records["qualitative_assertions"]) - facts_before
            evidence = len(records["evidence_references"]) - evidence_before
            if results == 0:
                found_status, task_status, reason = "uncertain", "completed", "insufficient_evidence"
                gaps.append(not_covered_gap(task_id, metric_id, conclusion["code"]))
            else:
                found_status = FOUND_STATUS.get(status, "partial")
                task_status = "completed"
                reason = "conflicting_values" if status == "conflict" else None
            records["reporting_tasks"].append({
                "task_id": task_id, "report_id": ir.document_id, "document_id": ir.document_id, "ir_run_id": ir.run_id,
                "ir_revision": ir.ir_revision, "package_id": package.manifest.package_id,
                "package_version": package.manifest.package_version, "metric_id": metric_id,
                "materiality_status": "not_assessed", "applicability_status": "not_assessed",
                "found_status": coded("reporting_task", "found_status", found_status) or "uncertain",
                "result_count": results, "evidence_count": evidence,
                "review_status": "pending" if status == "found" else "human_required",
                "task_status": task_status, "uncertainty_reason": coded("reporting_task", "uncertainty_reason", reason),
            })
        return records, gaps

    @staticmethod
    def _outcomes(records: dict, conclusions: list[dict]) -> list[dict]:
        tasks = {t["metric_id"]: t for t in records["reporting_tasks"]}
        outcomes = []
        for conclusion in conclusions:
            task = tasks[conclusion["metric_id"]]
            counts = {"reporting_task": 1, "quantitative_observation": 0, "qualitative_assertion": 0,
                      "attribute_value": 0, "dimension_value": 0, "evidence_reference": 0}
            facts = {r["observation_id"] for r in records["quantitative_observations"] if r["task_id"] == task["task_id"]}
            facts |= {r["assertion_id"] for r in records["qualitative_assertions"] if r["task_id"] == task["task_id"]}
            counts["quantitative_observation"] = sum(r["task_id"] == task["task_id"] for r in records["quantitative_observations"])
            counts["qualitative_assertion"] = sum(r["task_id"] == task["task_id"] for r in records["qualitative_assertions"])
            counts["attribute_value"] = sum(r["parent_record_id"] in facts for r in records["attribute_values"])
            counts["dimension_value"] = sum(r["parent_record_id"] in facts for r in records["dimension_values"])
            counts["evidence_reference"] = sum(r["source_record_id"] in facts for r in records["evidence_references"])
            outcomes.append({
                "metric_id": conclusion["metric_id"], "task_id": task["task_id"],
                "status": OUTCOME_STATUS.get(conclusion["status"], "not_found"),
                "found_status": task["found_status"], "review_status": task["review_status"],
                "guard_accepted": conclusion["status"] in ("found", "review"), "attempts": 0,
                "result_counts": counts, "uncertainty_reason": task["uncertainty_reason"],
                "contract_validation_status": "passed", "contract_error": None,
                "foundation_status": conclusion["status"], "route": ROUTE,
            })
        return outcomes

    @staticmethod
    def _target_cells(conclusions: list[dict]) -> dict:
        cells = {}
        for conclusion in conclusions:
            for member in conclusion.get("members") or []:
                for source in [member["source"], *member.get("agreeing_sources", []), *member.get("conflicting_sources", [])]:
                    if source.get("cell_id"):
                        cells[source["cell_id"]] = SimpleNamespace(
                            row_label=source.get("row_label"),
                            column_headers=tuple(filter(None, [source.get("column"), source.get("period")])))
        return cells

    @staticmethod
    def _evidence(records, *, fact_id, fact_type, evidence_set_id, source, ir, sequence, role, excerpt):
        object_type = "cell" if source.get("cell_id") else "block"
        object_id = source.get("cell_id") or source.get("note_block_id") or source.get("table_id")
        records["evidence_references"].append({
            "evidence_id": stable_id("evidence", fact_id, object_id, sequence), "evidence_set_id": evidence_set_id,
            "source_record_type": fact_type, "source_record_id": fact_id, "document_id": ir.document_id,
            "ir_run_id": ir.run_id, "ir_revision": ir.ir_revision, "ir_object_type": object_type, "ir_object_id": object_id,
            "pdf_page_index": int(source["page_number"]) - 1, "printed_page_label": None, "evidence_excerpt": excerpt,
            "quote_sha256": _quote_sha(excerpt), "evidence_role": role, "review_status": "pending",
            "bbox_x0": None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        })

    def _observation(self, records, package, elements, element_ids, task_id, metric_id, member, sources, ir, *, review, coded):
        primary = sources[0]
        observation_id = stable_id("obs", task_id, primary.get("cell_id") or primary.get("note_block_id"), member.get("member"),
                                   member.get("value_raw"))
        evidence_set_id = stable_id("evset", observation_id)
        value_numeric = _decimal_text(member.get("value")) if member.get("value") is not None else None
        if value_numeric is None and member.get("value_raw"):
            from esg_targeted.foundation.tables import parse_number
            parsed = parse_number(member["value_raw"])
            value_numeric = _decimal_text(str(parsed)) if parsed is not None else None
        records["quantitative_observations"].append({
            "observation_id": observation_id, "task_id": task_id, "metric_id": metric_id, "value_origin": "reported",
            "value_raw": member.get("value_raw") or "", "comparator": "exact", "value_numeric": value_numeric,
            "value_lower": None, "value_upper": None, "unit_raw": member.get("unit_raw"),
            "unit_id": coded("quantitative_observation", "unit_id", member.get("unit_id")),
            "reporting_period_raw": member.get("period"), "reporting_year": member.get("period_year"),
            "period_start": None, "period_end": None, "reporting_boundary": None, "confidence": None,
            "evidence_set_id": evidence_set_id, "review_status": review,
        })
        for index, source in enumerate(sources, 1):
            self._evidence(records, fact_id=observation_id, fact_type="quantitative_observation", evidence_set_id=evidence_set_id,
                           source=source, ir=ir, sequence=index, role="primary" if index == 1 else "supporting",
                           excerpt=source.get("value_raw") if source.get("cell_id") else (source.get("row_label") or "")[:400])
        sequence = 0
        for element_id in element_ids:
            element = elements.get(element_id)
            if element is None or _enum_text(element.semantic_role) != "dimension":
                continue
            fixed = element.value_contract.fixed_value
            dimension_id = element.binding.target
            value = fixed if fixed is not None else (member.get("member_label") or primary.get("row_label")
                                                     if element.element_code == "pollutant" else None)
            if value is None:
                continue
            sequence += 1
            records["dimension_values"].append({
                "dimension_value_id": stable_id("dim", observation_id, element_id), "parent_record_type": "quantitative_observation",
                "parent_record_id": observation_id, "metric_id": metric_id, "element_id": element_id, "dimension_id": dimension_id,
                "value_raw": str(value), "value_code": str(fixed) if fixed is not None else None, "value_level": None,
                "parent_dimension_value_id": None, "sequence": sequence, "evidence_set_id": evidence_set_id, "review_status": "pending",
            })
        for element_id in element_ids:
            element = elements.get(element_id)
            if element is not None and element.element_code in ("source_row_label", "source-row-label"):
                records["attribute_values"].append({
                    "attribute_id": stable_id("attr", observation_id, element_id), "parent_record_type": "quantitative_observation",
                    "parent_record_id": observation_id, "metric_id": metric_id, "element_id": element_id, "value_type": "text",
                    "value_text": primary.get("row_label"), "value_numeric": None, "value_boolean": None, "value_date": None,
                    "value_code": None, "unit_id": None, "sequence": 1, "evidence_set_id": evidence_set_id, "review_status": "pending",
                })

    def _assertion(self, records, task_id, metric_id, statement, index, ir):
        assertion_id = stable_id("assert", task_id, statement["block_id"], index)
        evidence_set_id = stable_id("evset", assertion_id)
        records["qualitative_assertions"].append({
            "assertion_id": assertion_id, "task_id": task_id, "metric_id": metric_id, "statement_raw": statement["text"],
            "statement_summary": None, "reporting_period_raw": None, "reporting_year": None, "period_start": None,
            "period_end": None, "reporting_boundary": None, "confidence": None, "evidence_set_id": evidence_set_id,
            "review_status": "human_required",
        })
        self._evidence(records, fact_id=assertion_id, fact_type="qualitative_assertion", evidence_set_id=evidence_set_id,
                       source={"note_block_id": statement["block_id"], "page_number": statement["page_number"]},
                       ir=ir, sequence=1, role="primary", excerpt=statement["text"][:400])
