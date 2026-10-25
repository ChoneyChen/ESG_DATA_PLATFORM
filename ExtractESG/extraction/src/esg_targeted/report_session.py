"""One durable report task, with immutable standard projections and shared reading.

The session ledger contains execution metadata, never model credentials or a
second fact database. Sources live in its artifact directory; Core projections
remain normal, independently readable Result Bundles.
"""
from __future__ import annotations

import time

import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from esg_targeted.contracts import ExtractionRequest, JobStatus, ReportExtractionRequest, ModelCallBudget
from esg_targeted.results.stages import collect_stage
from esg_targeted.standards.runtime_snapshot import (
    RuntimeStandardView, freeze_runtime_snapshot, load_extension_selections, resolve_report_parents,
    selections_for_parent, load_runtime_package,
)
from esg_targeted.ids import new_job_id
from esg_targeted.ids import safe_filename
from esg_targeted.ir.catalog import DocumentIrCatalog
from esg_targeted.ir.loader import DocumentIrLoader
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore
from esg_targeted.models.model_selection import freeze_report_role_models, report_role_assignments


def _now():
    return datetime.now(UTC).isoformat()


def _revision_fingerprint(root):
    """Freeze the physical package, including canonical records and images."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


class ReportSessionService:
    def __init__(self, settings, jobs=None, artifacts=None, *, workflow_factory=None):
        self.settings = settings
        self.jobs = jobs or JobStore(settings.state_db)
        self.artifacts = artifacts or ArtifactStore(settings.output_root)
        self.ir_catalog = DocumentIrCatalog(settings.ir_roots)
        self.standards = StandardPackageCatalog(settings.standard_dist_root)
        self.workflow_factory = workflow_factory
        with self.jobs._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS report_sessions (session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)")

    @staticmethod
    def dependencies(package, selected):
        metrics = {m.metric_id: m for m in package.metrics}
        unknown = set(selected) - metrics.keys()
        if unknown:
            raise ValueError(f"Unknown requirements: {sorted(unknown)}")
        # Only declared, data-processing formula dependencies are executed.
        # Presentation references and independent extensions do not activate work.
        visited, pending = set(selected), list(selected)
        while pending:
            metric = metrics[pending.pop()]
            if metric.derivation_policy != "reported_then_derived_when_dependencies_complete":
                continue
            for dependency in metric.depends_on:
                if dependency not in metrics:
                    raise ValueError(f"Unknown dependency {dependency}")
                if dependency not in visited:
                    visited.add(dependency)
                    pending.append(dependency)
        return sorted(visited - set(selected))

    def preview(self, request: ReportExtractionRequest):
        request = freeze_report_role_models(request, self.settings)
        model_tasks = report_role_assignments(request, self.settings)
        request = resolve_report_parents(request, self.settings.standard_dist_root.parent, self.standards)
        packages, dependencies, loaded_selections = [], [], []
        snapshots, matched_extensions = {}, set()
        selected_outputs, context_requirements, identity_requirements, formula_operands, exclusions = [], [], [], [], []
        for selected in request.standards:
            base = self.standards.load(selected.package_id, selected.package_version)
            loaded_selections.append((base, selected.metric_ids))
            extensions = selections_for_parent(self.settings.standard_dist_root.parent, base, request.extension_selections)
            if not selected.metric_ids and not extensions:
                raise ValueError('A selected parent package requires an original or extension output')
            package, extension_dependencies = base, set()
            if extensions:
                releases = load_extension_selections(self.settings.standard_dist_root.parent, base, extensions)
                frozen = freeze_runtime_snapshot(base, extensions, releases)
                snapshots[selected.package_id] = frozen.model_dump(mode='json')
                package = RuntimeStandardView(base, frozen)
                matched_extensions.update(s.extension_id for s in extensions)
                extension_dependencies.update(mid for recipe in package.extension_computations for mid in recipe['internal_operand_metric_ids'])
            output_ids = [*selected.metric_ids, *(s.extension_id for s in extensions)]
            internal = sorted((set(self.dependencies(base, [*selected.metric_ids, *extension_dependencies])) | extension_dependencies) - set(selected.metric_ids))
            dependencies.append({"package_id": selected.package_id, "metric_ids": internal})
            packages.append({"package_id": selected.package_id, "package_version": selected.package_version,
                             "digest": base.compilation.source_digest, "metric_ids": selected.metric_ids,
                             "extension_ids": [s.extension_id for s in extensions],
                             "runtime_digest": package.runtime_digest if extensions else base.compilation.source_digest})
            descriptions = self._selection_description(package, output_ids, internal)
            selected_outputs.extend(descriptions["selected_outputs"])
            context_requirements.extend(descriptions["context_requirements"])
            identity_requirements.extend(descriptions["identity_requirements"])
            formula_operands.extend(descriptions["formula_operands"])
            formula_operands.extend({**recipe, 'package_id': selected.package_id,
                'operand_role': 'internal_dependency', 'output_role': 'selected_extension'}
                for recipe in getattr(package, 'extension_computations', []))
            exclusions.extend(descriptions["exclusions"])
        if matched_extensions != {s.extension_id for s in request.extension_selections}:
            raise ValueError('Every selected extension must have its exact parent package in the plan')
        if request.frozen_runtime_standard_snapshots and request.frozen_runtime_standard_snapshots != snapshots:
            raise ValueError('冻结的延伸父包、批准成果或组合快照不一致；任务被阻塞，不会重建或换新版本。')
        from esg_targeted.standards.profiles import validate_selection_snapshot
        validate_selection_snapshot(self.settings.standard_dist_root.parent, request.selection_snapshot, loaded_selections)
        limitations, reused, input_identity = [], [], {}
        if request.input.kind == "document_ir":
            path = self.ir_catalog.resolve(request.input.ir_run_id)
            ir = DocumentIrLoader().load(path, require_ready=True)
            limited = bool(ir.manifest.get("evidence_policy", {}).get("mode") == "limited")
            readiness = ir.manifest.get("readiness") or ir.manifest.get("validation", {}).get("readiness")
            if limited and not request.allow_limited_evidence:
                raise ValueError("This revision requires explicit limited-evidence consumption")
            input_identity = {"ir_run_id": request.input.ir_run_id, "document_id": ir.document_id,
                              "manifest_sha256": hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
                              "package_sha256": _revision_fingerprint(path)}
            reused = [{"kind": "document_ir", "run_id": request.input.ir_run_id}]
            if limited or not ir.manifest.get("can_build_evidence", True):
                limitations.append({"readiness": readiness, "policy": ir.manifest.get("evidence_policy", {}),
                                    "message": "Only declared usable evidence can be consumed; coverage remains partial."})
        else:
            limitations.append({"message": "Document capabilities will be determined after OCR and basic IR preparation."})
        return {"schema_version": "report-plan-v1", "selection_snapshot": request.selection_snapshot.model_dump(mode="json") if request.selection_snapshot else None,
                "roles": request.role_config.model_dump(mode="json"), "model_task_assignments": model_tasks,
                "model_plan": request.model_plan.model_dump(mode="json") if request.model_plan else None,
                "standards": packages, "internal_dependencies": dependencies, "reused_inputs": reused,
                "selected_outputs": selected_outputs, "output_count": len(selected_outputs),
                "original_output_count": sum(o.get('clause_kind') != 'project_extension' for o in selected_outputs),
                "extension_output_count": sum(o.get('clause_kind') == 'project_extension' for o in selected_outputs),
                "internal_operand_count": sum(len(row['metric_ids']) for row in dependencies),
                "runtime_standard_snapshots": snapshots,
                "context_requirements": context_requirements, "identity_requirements": identity_requirements,
                "formula_operands": formula_operands, "exclusions": exclusions,
                "output_policy": "only_explicitly_selected_requirements",
                "input_identity": input_identity, "budget": request.effective_budget().model_dump(mode="json"),
                "model_budget_policy": {"scope": "standard_package", "policy": "independent-standard-package-v1",
                    "shared_report_allowance": False, "allocation": "full_configured_allowance_per_package"},
                "evidence_limitations": limitations,
                "stages": ["准备文档", "理解披露", "补充证据", "整理事实", "形成结果"]}

    def _selection_description(self, package, selected_ids, internal_ids):
        """Describe declared contracts without expanding the output denominator.

        Cross-package references are pinned and checked, not activated as output.
        A formula operand without a source metric retains its described evidence
        role; no synthetic ESRS number or execution dependency is manufactured.
        """
        from esg_standard_packages.scope import resolve_context_requirements, resolve_shared_requirements
        metrics = {m.metric_id: m for m in package.metrics}
        dimensions = {d.dimension_id: d for d in [*package.core.common_dimensions, *package.dimensions]}
        base = {"package_id": package.manifest.package_id, "package_version": package.manifest.package_version,
                "source_digest": package.compilation.source_digest}
        def clause(metric):
            return {"metric_id": metric.metric_id, "source_datapoint_id": metric.source_datapoint_id,
                    "label": metric.labels.get("zh") or metric.labels.get("en") or metric.metric_id,
                    "clause_kind": getattr(metric, 'clause_kind', 'original_datapoint'),
                    "extension_id": getattr(metric, 'extension_id', None),
                    "related_source_datapoint_ids": getattr(metric, 'related_source_datapoint_ids', [])}
        selected = [metrics[mid] for mid in selected_ids]
        original_ids = list(dict.fromkeys([m.source_datapoint_id for m in selected if m.source_datapoint_id] +
                           [original for m in selected for original in getattr(m, 'related_source_datapoint_ids', [])]))
        selected_outputs = [{**base, **clause(m),
            "package_core": getattr(m, 'package_core', m.source_datapoint_id in package.manifest.core_source_datapoint_ids),
            "output_policy": "selected_output"} for m in selected]
        identity = [{**base, **clause(m), "identity_axes": m.identity_axes,
            "required_semantic_discriminators": m.required_semantic_discriminators,
            "required_dimensions": [{"dimension_id": did, "label": dimensions[did].labels.get("zh") or dimensions[did].labels.get("en") or did}
                                    for did in m.required_dimension_ids],
            "comparison_requirements": m.measurement_contract.comparison_requirements if m.measurement_contract else []}
            for m in selected]
        excluded = [{**base, **clause(m),
            "measurement_exclusions": m.measurement_contract.exclusions if m.measurement_contract else [],
            "confusable_metric_ids": m.confusable_metric_ids,
            "non_output_policy": "context_and_operands_never_increase_selected_output_count"}
            for m in selected]
        targets, registry = {package.manifest.package_id: package}, {}
        references = list(package.manifest.context_requirements) + list(package.manifest.shared_requirements)
        if package.manifest.shared_requirements:
            registry = json.loads((self.settings.standard_dist_root.parent / "catalogs/esrs/2023-set1/registry.json").read_text())
            from esg_standard_packages.contracts import SharedRequirementReference
            topics = {ref.topic_binding for ref in package.manifest.shared_requirements}
            purposes = {ref.purpose.split("_", 1)[1] for ref in package.manifest.shared_requirements}
            references += [SharedRequirementReference.model_validate(ref) for ref in registry["topic_shared_requirements"]
                           if ref["topic_binding"] in topics and ref["purpose"].split("_", 1)[1] in purposes]
        for ref in references:
            if hasattr(ref, "applies_to_source_datapoint_ids") and not set(original_ids).intersection(ref.applies_to_source_datapoint_ids):
                continue
            targets[ref.target_package_id] = self.standards.load(ref.target_package_id, ref.target_package_version)
        context = resolve_context_requirements(package, original_ids, targets) if package.manifest.context_requirements else []
        if package.manifest.shared_requirements:
            context += resolve_shared_requirements(package, registry, targets)
        for ref in context:
            target = targets[ref["target_package_id"]]
            lookup = {m.source_datapoint_id: m for m in target.metrics}
            ref.update(requesting_package_id=package.manifest.package_id,
                clauses=[clause(lookup[original]) for original in ref["target_source_datapoint_ids"]],
                output_policy="context_only")
        formulas = []
        execution = set(selected_ids) | set(internal_ids)
        for rule in package.derivations:
            if rule.target_metric_id not in execution:
                continue
            formulas.append({**base, "rule_id": rule.rule_id, "target": clause(metrics[rule.target_metric_id]),
                "operation": rule.operation, "reported_value_priority": rule.reported_value_priority,
                "required_match_fields": rule.required_match_fields, "required_match_dimensions": rule.required_match_dimensions,
                "guards": [guard.model_dump(mode="json") for guard in rule.guards],
                "unit_policy": rule.unit_policy, "dependency_activation": "declared_dependencies_only",
                "operands": [{"operand_id": operand.operand_id, "role": operand.role,
                    "description": operand.description,
                    "source_clause": clause(metrics[operand.source_metric_id]) if operand.source_metric_id else None,
                    "scheduled_as_internal_dependency": operand.source_metric_id in internal_ids,
                    "already_selected_output": operand.source_metric_id in selected_ids}
                    for operand in rule.operands], "output_policy": "internal_operand_only"})
        return {"selected_outputs": selected_outputs, "context_requirements": context,
                "identity_requirements": identity, "formula_operands": formulas, "exclusions": excluded}

    def create(self, request: ReportExtractionRequest, *, idempotency_key=None):
        request = freeze_report_role_models(request, self.settings)
        if request.input.kind != "document_ir":
            raise ValueError("PDF preparation is owned by the platform report-session entry point")
        session_id = "report-" + (hashlib.sha256(idempotency_key.encode()).hexdigest()[:32] if idempotency_key else uuid4().hex)
        if idempotency_key:
            try:
                existing = self._state(session_id)
            except KeyError:
                pass
            else:
                if existing["request"] != request.model_dump(mode="json"):
                    raise ValueError("Report creation idempotency key was already used with different inputs")
                return self.detail(session_id)
        plan = self.preview(request)
        now = _now()
        budget = request.effective_budget()
        projections = []
        prepared = []
        from esg_targeted.contracts import RequirementSelection
        resolved_standards = [RequirementSelection(package_id=p['package_id'], package_version=p['package_version'],
                                                   metric_ids=p['metric_ids']) for p in plan['standards']]
        for selected, dependency in zip(resolved_standards, plan["internal_dependencies"]):
            job_id = new_job_id()
            frozen = plan['runtime_standard_snapshots'].get(selected.package_id)
            extension_selections = [s for s in request.extension_selections if s.extension_id in
                                    (set(f['extension_id'] for f in frozen['selections']) if frozen else set())]
            output_ids = [*selected.metric_ids, *(s.extension_id for s in extension_selections)]
            child = ExtractionRequest(ir_run_id=request.input.ir_run_id, package_id=selected.package_id,
                selection_snapshot=request.selection_snapshot,
                extension_selections=extension_selections, runtime_standard_digest=frozen['runtime_digest'] if frozen else None,
                package_version=selected.package_version, metric_ids=output_ids,
                semantic_search=request.semantic_search, semantic_provider=request.role_config.semantic_brain.provider,
                semantic_model=request.role_config.semantic_brain.model, roles=request.role_config,
                report_session_id=session_id, internal_metric_ids=dependency["metric_ids"],
                retrieval_object_top_n=budget.retrieval_object_top_n, shared_object_budget=budget.shared_object_budget,
                agent_max_steps=budget.agent_max_steps, call_timeout_seconds=budget.call_timeout_seconds,
                extraction_mode="foundation16" if request.extraction_route == "foundation16" else "auto",
                call_budget=ModelCallBudget.model_validate(budget.model_dump(include=set(ModelCallBudget.model_fields))))
            if request.extraction_route == "foundation16":
                from esg_targeted.foundation.session import foundation_metric_ids
                allowed = foundation_metric_ids(request.metric_profile)
                outside = sorted(set(selected.metric_ids) - allowed)
                if outside or extension_selections:
                    raise ValueError(f"foundation16 route covers only the 16 foundation datapoints; not supported: {outside}")
            package = self.standards.load(selected.package_id, selected.package_version)
            self.artifacts.write_json(job_id, "input/standard-package.json", package.model_dump(mode="json"))
            if frozen:
                self.artifacts.write_json(job_id, 'input/runtime-standard-snapshot.json', frozen)
            self.artifacts.write_json(job_id, "input/request.json", child.model_dump(mode="json"))
            prepared.append((job_id, child, str(self.artifacts.job_dir(job_id))))
            projections.append({"job_id": job_id, "package_id": selected.package_id,
                "model_call_budget": child.call_budget.model_dump(mode="json"),
                "package_version": selected.package_version, "selected_metric_ids": output_ids,
                "original_selected_metric_ids": selected.metric_ids,
                "extension_ids": [s.extension_id for s in extension_selections],
                "runtime_digest": frozen['runtime_digest'] if frozen else package.compilation.source_digest,
                "internal_metric_ids": dependency["metric_ids"], "digest": package.compilation.source_digest})
        state = {"schema_version": "report-session-v1", "session_id": session_id, "name": request.name,
            "request": request.model_dump(mode="json"), "plan": plan, "projections": projections,
            "execution_status": "queued", "result_status": "not_evaluated", "revision": 1,
            "created_at": now, "updated_at": now, "cancel_requested": False,
            "owner_pid": None, "owner_started": None, "evidence_transitions": []}
        # Register the master and all immutable projections in one transaction.
        with self.jobs._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT state_json FROM report_sessions WHERE session_id=?", (session_id,)).fetchone()
            if existing:
                previous = json.loads(existing[0])
                for job_id, _, _ in prepared:
                    self.artifacts.delete_job(job_id)
                if previous["request"] != state["request"]:
                    raise ValueError("Report creation idempotency key was already used with different inputs")
            else:
                for job_id, child, output_dir in prepared:
                    connection.execute("INSERT INTO jobs(job_id,status,stage,progress_current,progress_total,created_at,updated_at,request_json,output_dir) VALUES(?,?,?,?,?,?,?,?,?)",
                        (job_id, "queued", "queued", 0, len(child.metric_ids), now, now, child.model_dump_json(), output_dir))
                connection.execute("INSERT INTO report_sessions VALUES(?,?)", (session_id, json.dumps(state, ensure_ascii=False)))
        if existing:
            return self.detail(session_id)
        root = self.artifacts.report_session_dir(session_id)
        self._write_snapshot(root, "request.json", state["request"])
        self._write_snapshot(root, "plan.json", plan)
        ir_path = self.ir_catalog.resolve(request.input.ir_run_id)
        pins = ir_path.parent / ".pins"
        pins.mkdir(exist_ok=True)
        self._write_snapshot(pins, f"{session_id}.json", {"run_id": request.input.ir_run_id, "owner": session_id, "kind": "report_session"})
        return self.detail(session_id)

    @staticmethod
    def _write_snapshot(root, name, data):
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _state(self, session_id):
        with self.jobs._connect() as connection:
            row = connection.execute("SELECT state_json FROM report_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return json.loads(row[0])

    def _mutate(self, session_id, change):
        with self.jobs._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state_json FROM report_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            state = json.loads(row[0])
            change(state)
            state.update(revision=state["revision"] + 1, updated_at=_now())
            connection.execute("UPDATE report_sessions SET state_json=? WHERE session_id=?", (json.dumps(state, ensure_ascii=False), session_id))
        getattr(self, "_list_cache", {}).pop(session_id, None)
        return state

    # Building one list row reads every projection's stage files. The UI polls
    # this list, so a short cache keeps polling from re-reading unchanged work
    # while still showing a running job within seconds.
    LIST_TTL_SECONDS = 5.0

    def list(self, limit=100, *, include_archived=False):
        with self.jobs._connect() as connection:
            ids = [r[0] for r in connection.execute("SELECT session_id FROM report_sessions "
                + ("" if include_archived else "WHERE COALESCE(json_extract(state_json,'$.lifecycle.state'),'active')='active' ")
                + "ORDER BY rowid DESC LIMIT ?", (limit,))]
        cache = getattr(self, "_list_cache", None)
        if cache is None:
            cache = self._list_cache = {}
        now = time.monotonic()
        rows = []
        for session_id in ids:
            cached = cache.get(session_id)
            if cached and now - cached[0] < self.LIST_TTL_SECONDS:
                rows.append(cached[1])
                continue
            value = self.detail(session_id)
            cache[session_id] = (now, value)
            rows.append(value)
        return rows

    def detail(self, session_id):
        state = self._state(session_id)
        if state.get("lifecycle", {}).get("state") == "discarded":
            state.update(jobs=[],stages=[],available_actions=[],database_status="not_imported",artifacts_available=False)
            return state
        children = [self.jobs.get(p["job_id"]) for p in state["projections"]]
        if state["execution_status"] == "running" and not JobStore._worker_alive(str(state.get("owner_pid") or ""), state.get("owner_started")):
            if not any(self.jobs.has_live_worker(job.job_id) for job in children):
                state = self._mutate(session_id, lambda s: s.update(execution_status="cancelled" if s.get('cancel_requested') else "interrupted", owner_pid=None, owner_started=None))
        snapshots = [collect_stage(self.artifacts, job.job_id) for job in children]
        recorded_statuses = {job.job_id: job.status.value for job in children}
        if state['execution_status'] not in {'queued', 'running'}:
            # Historical ledgers may leave never-entered children queued. This
            # read projection states the actual scope without rewriting them.
            children = [job.model_copy(update={'status': JobStatus.INTERRUPTED, 'stage': 'not_executed',
                'error': '报告任务已结束；此标准投影尚未执行'})
                if job.status == JobStatus.QUEUED and not self.jobs.has_live_worker(job.job_id) else job
                for job in children]
        for job, snapshot in zip(children, snapshots):
            if job.status != JobStatus.RUNNING:
                counts = snapshot["summary"]["counts"]
                entered = set(snapshot["summary"].get("unfinished_metric_ids", [])) | set(snapshot["summary"].get("processing_metric_ids", []))
                snapshot["summary"]["unfinished_metric_ids"] = sorted(entered)
                snapshot["summary"]["processing_metric_ids"] = []
                counts["unfinished_metrics"] = len(entered)
                counts["processing_metrics"] = 0
        stage_counts = [s["summary"]["counts"] for s in snapshots]
        counts = {key: sum(c.get(key, 0) for c in stage_counts) for key in (
            "selected_metrics", "original_output_count", "extension_output_count", "processed_metrics", "processing_metrics", "unfinished_metrics", "not_executed_metrics", "core_facts", "core_bindings", "evidence_references",
            'internal_dependency_metrics','internal_processed_metrics','internal_core_facts','internal_core_bindings','internal_evidence_references')}
        counts['awaiting_reviewed_operands'] = sum(o.get('status') == 'awaiting_reviewed_operands'
            for snapshot in snapshots for o in snapshot['summary'].get('outcomes', []))
        # Shared sources must be counted once, independent of projection count.
        facts_payload = self.facts(session_id)
        counts["candidate_source_facts"] = len(facts_payload["facts"])
        counts["source_objects"] = len(facts_payload["source_objects"])
        facts = bool(counts["core_facts"])
        candidates = bool(counts["candidate_source_facts"])
        statuses = {job.status for job in children}
        if statuses == {JobStatus.COMPLETED}:
            covered = counts["selected_metrics"] == counts["processed_metrics"]
            state["result_status"] = "usable" if facts and covered else "partial" if facts else "needs_confirmation" if candidates else "no_evidence_in_bound" if covered else "not_evaluated"
        elif facts:
            state["result_status"] = "partial"
        else:
            state["result_status"] = "needs_confirmation" if candidates or counts['awaiting_reviewed_operands'] else "not_evaluated"
        if any(item.get("policy", {}).get("mode") == "limited" for item in state["plan"].get("evidence_limitations", [])) and facts:
            state["result_status"] = "partial"
        if state["execution_status"] == "finished" and JobStatus.FAILED in statuses:
            state["result_status"] = "partial" if facts else "needs_confirmation"
        state["job_ids"] = [j.job_id for j in children]
        state["jobs"] = [{**j.model_dump(mode="json"), 'recorded_status': recorded_statuses[j.job_id]} for j in children]
        state["stages"] = [{"job_id": j.job_id, "stage": j.stage, "status": j.status.value,
                            "progress_current": j.progress_current, "progress_total": j.progress_total,
                            "result_status": snapshot["summary"]["result_status"], "counts": snapshot["summary"]["counts"]}
                           for j, snapshot in zip(children, snapshots)]
        state["counts"] = counts
        state["maturity"] = ("core_validated" if state["result_status"] == "usable" else "core_partial") if facts else "source_candidates" if candidates else "no_facts"
        from esg_targeted.budget_scope import report_model_usage
        state["runtime_budget"] = report_model_usage(self.artifacts, session_id, state["projections"])
        state["errors"] = list(dict.fromkeys([*(snapshot_error for snapshot in snapshots for snapshot_error in snapshot["summary"].get("errors", [])),
                                             *(j.error for j in children if j.error)]))
        from esg_runtime.progress import classify_error
        state["error_details"] = [{"job_id": job.job_id, **classify_error(job.error,
            task_type="targeted_extraction", producer_error=job.summary or {}).model_dump(mode="json")}
            for job in children if job.error and job.status in {JobStatus.FAILED, JobStatus.INTERRUPTED}]
        state["error_detail"] = next(iter(state["error_details"]), None)
        state["gaps"] = [gap for job in children for gap in (job.summary or {}).get("agent", {}).get("gaps", [])]
        # Gaps are authoritative artifacts when a process stopped before its
        # summary reached the ledger; do not infer them from error text.
        for job in children:
            agent_path = self.artifacts.job_dir(job.job_id) / "agent/state.json"
            if agent_path.is_file():
                agent = json.loads(agent_path.read_text(encoding="utf-8"))
                state["gaps"].extend(g for g in agent.get("gaps", []) if g not in state["gaps"])
            source_path = self.artifacts.job_dir(job.job_id) / "results/source-facts.json"
            if source_path.is_file():
                source_result = json.loads(source_path.read_text(encoding="utf-8"))
                state["gaps"].extend(g for g in source_result.get("gaps", []) if g not in state["gaps"])
        state["gaps"].extend(g for snapshot in snapshots for g in snapshot["summary"].get("gaps", []) if g not in state["gaps"])
        state["gaps"] = [g for g in state["gaps"] if g.get("status", "open") != "resolved"]
        state["revision"] += sum(int(j.updated_at.timestamp() * 1000) for j in children)
        state["available_actions"] = []
        pending_projections = [job.job_id for job, snapshot in zip(children, snapshots) if job.status != JobStatus.COMPLETED or snapshot["summary"].get("stale_projection_metric_ids")]
        if state.get('cancel_requested') and state['execution_status'] == 'running':
            state['cancellation_status'] = 'stopping'
        elif state["execution_status"] in {"queued", "running"}:
            state["available_actions"].append({"action": "cancel", "label": "取消任务", "endpoint": f"/api/report-sessions/{session_id}/cancel",
                "scope": {"session_id": session_id}, "calls_model": False, "incurs_cost": False})
        elif pending_projections and (state["execution_status"] in {"finished", "interrupted", "failed", "cancelled"} or state["result_status"] in {"partial", "needs_confirmation"}):
            state["available_actions"].append({"action": "resume", "label": "恢复未完成部分", "endpoint": f"/api/report-sessions/{session_id}/resume",
                "scope": {"session_id": session_id, "job_ids": pending_projections},
                "calls_model": True, "incurs_cost": state["request"]["role_config"]["semantic_brain"]["provider"] == "qiniu_vlm"})
        state["database_status"] = "not_imported"
        from esg_targeted.results.terminal_bundle import delivery_artifacts, saved_delivery_counts
        deliveries = [delivery_artifacts(self.artifacts.existing_job_dir(job.job_id)) for job in children]
        state['delivery_artifacts'] = [item for batch in deliveries for item in batch['items']]
        state['delivery_artifact_blockers'] = [item for batch in deliveries for item in batch['blocked']]
        state['delivery_counts'] = saved_delivery_counts(self.artifacts.output_root, deliveries)
        terminal = state['execution_status'] not in {'queued', 'running'}
        retained_jobs = [job.job_id for job, snapshot in zip(children, snapshots)
            if snapshot['summary']['counts'].get('candidate_source_facts') or snapshot['summary']['counts'].get('core_facts')
            or snapshot['summary']['counts'].get('internal_core_facts')]
        if terminal and retained_jobs:
            state['available_actions'].append({'action': 'materialize', 'label': '保存可交付阶段成果',
                'endpoint': f'/api/report-sessions/{session_id}/materialize', 'scope': {'job_ids': retained_jobs},
                'payload': {'job_ids': retained_jobs}, 'requires_reason': True, 'accepts_revision': False,
                'calls_model': False, 'incurs_cost': False, 'requires_operator': True})
        if terminal and state['delivery_artifacts']:
            state['available_actions'].append({'action': 'deliver', 'label': '交付已保存来源进入审核',
                'endpoint': f'/api/console/report-sessions/{session_id}/deliver', 'scope': {'session_id': session_id},
                'calls_model': False, 'incurs_cost': False, 'requires_operator': True})
        return state

    def materialize(self, session_id, *, reason, requested_by, job_ids=None):
        """Explicit artifact revision; never mutate old manifest/checkpoint/state."""
        state = self._state(session_id)
        if state.get('lifecycle', {}).get('state', 'active') != 'active':
            raise ValueError('Restore the report session before materialization')
        if state['execution_status'] in {'queued', 'running'} or JobStore._worker_alive(
                str(state.get('owner_pid') or ''), state.get('owner_started')):
            raise ValueError('Materialization requires a stopped terminal report session')
        children = {p['job_id']: self.jobs.get(p['job_id']) for p in state['projections']}
        selected = list(children) if job_ids is None else job_ids
        if not selected or len(selected) != len(set(selected)) or set(selected) - children.keys():
            raise ValueError('Materialization scope must contain original session jobs exactly once')
        if any(self.jobs.has_live_worker(job_id) for job_id in children):
            raise ValueError('A worker still owns report fragments')
        from esg_targeted.results.terminal_bundle import materialize_terminal_bundle
        results = []
        for job_id in selected:
            child = children[job_id]
            status = child.status.value if child.status != JobStatus.QUEUED else 'interrupted'
            artifact = materialize_terminal_bundle(child.output_dir, job_id, status, child.error or state.get('error'),
                reason=reason, requested_by=requested_by)
            results.append(artifact or {'job_id': job_id, 'status': 'no_retained_facts', 'source_fact_count': 0,
                'core_fact_count': 0, 'publication_status': 'not_published'})
        return {'session_id': session_id, 'materializations': results, 'calls_model': False,
            'approval_or_publication_performed': False, 'session': self.detail(session_id)}

    def _finalize_terminal_projections(self, session_id):
        state = self._state(session_id)
        if state['execution_status'] in {'queued', 'running'}:
            return
        for projection in state['projections']:
            job_id = projection['job_id']
            job = self.jobs.get(job_id)
            if self.jobs.has_live_worker(job_id):
                continue
            if job.status == JobStatus.QUEUED:
                self.jobs.update(job_id, status=JobStatus.INTERRUPTED, stage='not_executed',
                    error='报告任务已结束；此标准投影尚未执行',
                    summary={**(job.summary or {}), 'not_executed_reason': 'report_terminal_before_projection'})
            if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
                              JobStatus.INTERRUPTED, JobStatus.PARTIAL}:
                # A finished extraction is already the deliverable one; saving
                # its immutable bundle here is content-addressed and idempotent,
                # so the handoff never waits for an operator click.
                from esg_targeted.results.terminal_bundle import materialize_terminal_bundle
                try:
                    materialize_terminal_bundle(job.output_dir, job_id, job.status.value, job.error,
                        reason='Retain report terminal fragments', requested_by='report-terminal-finalizer')
                except Exception as exc:
                    self.jobs.add_event(job_id, 'stage_materialization_failed', 'error', str(exc))

    def cancel(self, session_id):
        def request_cancel(state):
            owner_live = JobStore._worker_alive(str(state.get('owner_pid') or ''), state.get('owner_started'))
            child_live = any(self.jobs.has_live_worker(child['job_id']) for child in state['projections'])
            state.update(cancel_requested=True, execution_status='running' if owner_live or child_live else 'cancelled')
        state = self._mutate(session_id, request_cancel)
        for child in state["projections"]:
            if self.jobs.get(child["job_id"]).status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                self.jobs.request_cancel(child["job_id"])
                if self.jobs.get(child["job_id"]).status == JobStatus.QUEUED:
                    self.jobs.update(child["job_id"], status=JobStatus.CANCELLED, stage="cancelled")
        return self.detail(session_id)

    def resume(self, session_id):
        def update(state):
            if state.get("lifecycle", {}).get("state", "active") != "active":
                raise ValueError("归档或丢弃的报告会话应先恢复。")
            if JobStore._worker_alive(str(state.get("owner_pid") or ""), state.get("owner_started")):
                raise ValueError("A live worker still owns this report session")
            if state["execution_status"] == "queued":
                raise ValueError("Report session is already queued")
            if all(self.jobs.get(p["job_id"]).status == JobStatus.COMPLETED and not collect_stage(self.artifacts, p["job_id"])["summary"].get("stale_projection_metric_ids") for p in state["projections"]):
                raise ValueError("Report session already has complete projections")
            state.update(cancel_requested=False, execution_status="queued", owner_pid=None, owner_started=None, error=None)
        self._mutate(session_id, update)
        return self.detail(session_id)

    def run(self, session_id):
        from esg_targeted.storage.power import idle_sleep_assertion
        with idle_sleep_assertion():
            return self._run(session_id)

    def _run(self, session_id):
        def claim(state):
            if state.get("lifecycle", {}).get("state", "active") != "active":
                raise ValueError("归档或丢弃的报告会话应先恢复。")
            if state["cancel_requested"]:
                raise ValueError("Cancelled session must be resumed explicitly")
            if state["execution_status"] == "running" and JobStore._worker_alive(str(state.get("owner_pid") or ""), state.get("owner_started")):
                raise ValueError("Report session already has a live worker")
            state.update(execution_status="running", owner_pid=os.getpid(), owner_started=JobStore.process_started(os.getpid()))
        state = self._mutate(session_id, claim)
        try:
            ir_path = self.ir_catalog.resolve(state.get("current_ir_run_id") or state["request"]["input"]["ir_run_id"])
            digest = hashlib.sha256((ir_path / "manifest.json").read_bytes()).hexdigest()
            if digest != state["plan"]["input_identity"]["manifest_sha256"]:
                raise ValueError("Frozen input revision changed; create an explicit evidence transition instead")
            frozen_package = state["plan"]["input_identity"].get("package_sha256")
            if frozen_package and _revision_fingerprint(ir_path) != frozen_package:
                raise ValueError("Frozen input package changed; create an explicit evidence transition instead")
            started = time.monotonic()
            if state["request"].get("extraction_route") == "foundation16":
                from esg_targeted.foundation.session import FoundationSessionExecutor
                FoundationSessionExecutor(self).run(session_id, state, ir_path)
            deferred_budget_failure = None
            for projection in ([] if state["request"].get("extraction_route") == "foundation16" else state["projections"]):
                if self._state(session_id)["cancel_requested"]:
                    break
                if time.monotonic() - started >= state["plan"]["budget"]["total_timeout_seconds"]:
                    raise TimeoutError("Report session total execution budget exhausted")
                child = self.jobs.get(projection["job_id"])
                stale = collect_stage(self.artifacts, child.job_id)["summary"].get("stale_projection_metric_ids", [])
                if child.status == JobStatus.COMPLETED and not stale:
                    continue
                if stale and child.status == JobStatus.COMPLETED:
                    self.jobs.update(child.job_id, status=JobStatus.PARTIAL, stage="source_reprojection_pending")
                if child.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    self.jobs.prepare_resume(child.job_id)
                if self.workflow_factory:
                    workflow = self.workflow_factory(self.settings, self.jobs, self.artifacts)
                else:
                    from esg_targeted.workflow import TargetedExtractionWorkflow
                    workflow = TargetedExtractionWorkflow(self.settings, self.jobs, self.artifacts)
                workflow.run(child.job_id)
                finished_child = self.jobs.get(child.job_id)
                finished_summary = finished_child.summary or {}
                # A report-wide budget is shared across projections; package
                # quotas stay independent and only fail their own projection.
                # A package that stops before processing its selected metrics
                # still makes the session incomplete, so it is recorded and
                # raised after sibling projections have had their own allowance.
                if finished_summary.get("failure_category") == "budget":
                    boundary = finished_summary.get("budget_boundary", "report")
                    if boundary == "report":
                        from esg_targeted.runtime_budget import BudgetExceeded
                        raise BudgetExceeded(
                            "Report runtime budget exhausted; later standard projections remain unexecuted",
                            {}, scope=session_id, boundary="report")
                    if boundary == "package" and finished_summary.get("not_executed_metric_ids"):
                        deferred_budget_failure = deferred_budget_failure or finished_child.job_id
                if finished_child.status == JobStatus.INTERRUPTED and finished_summary.get("failure_category") != "budget":
                    raise RuntimeError(self.jobs.get(child.job_id).error or "Projection interrupted")
            if deferred_budget_failure:
                from esg_targeted.runtime_budget import BudgetExceeded
                raise BudgetExceeded(
                    "Package runtime budget exhausted; later metrics in this projection remain unexecuted",
                    {}, scope=deferred_budget_failure, boundary="package")
            self._mutate(session_id, lambda s: s.update(execution_status="cancelled" if s["cancel_requested"] else "finished", owner_pid=None, owner_started=None))
        except Exception as exc:
            self._mutate(session_id, lambda s: s.update(execution_status="interrupted" if isinstance(exc, TimeoutError) else "failed", error=f"{type(exc).__name__}: {exc}", owner_pid=None, owner_started=None))
            raise
        finally:
            self._finalize_terminal_projections(session_id)
        return self.detail(session_id)

    def transition(self, session_id, request):
        state = self._state(session_id)
        if state["execution_status"] in {"queued", "running"} or JobStore._worker_alive(str(state.get("owner_pid") or ""), state.get("owner_started")):
            raise ValueError("Stop the report session before adopting a repaired revision")
        new_path = self.ir_catalog.resolve(request.new_ir_run_id)
        ir = DocumentIrLoader().load(new_path, require_ready=True)
        if ir.manifest.get("evidence_policy", {}).get("mode") == "limited" and not state["request"]["allow_limited_evidence"]:
            raise ValueError("Original report request does not authorize limited-evidence revisions")
        previous_run = state.get("current_ir_run_id") or state["request"]["input"]["ir_run_id"]
        if previous_run == request.new_ir_run_id:
            raise ValueError("Evidence transition requires a new immutable revision")
        if ir.document_id != state["plan"]["input_identity"]["document_id"]:
            raise ValueError("Repaired revision belongs to another document")
        selected = {m for p in state["projections"] for m in p["selected_metric_ids"]}
        if set(request.metric_ids) - selected:
            raise ValueError("Affected requirements must be in the original selection")
        affected, prepared, replacements = set(request.metric_ids), [], []
        targets = set(request.affected_object_ids)
        for projection in state["projections"]:
            old_id = projection["job_id"]
            for checkpoint in (self.artifacts.job_dir(old_id) / "checkpoints").glob("*.json"):
                data = json.loads(checkpoint.read_text(encoding="utf-8"))
                if any(e.get("ir_object_id") in targets for e in data.get("records", {}).get("evidence_references", [])):
                    affected.add(data["metric_id"])
            for gap in (self.jobs.get(old_id).summary or {}).get("agent", {}).get("gaps", []):
                if targets.intersection(gap.get("object_ids", [])) or gap.get("object_id") in targets:
                    affected.update(gap.get("metric_ids", []))
        if not affected:
            raise ValueError("No requirement is bound to the affected objects; provide the exact affected metric_ids")
        # A changed operand also invalidates the projections of its declared
        # dependants, even when those had no direct evidence before the repair.
        for projection in state["projections"]:
            package = self.standards.compiler.validate_compiled(self.artifacts.resolve(projection["job_id"], "input/standard-package.json"))
            package = load_runtime_package(package, artifact_root=self.artifacts.job_dir(projection['job_id']),
                                           request=self.jobs.get(projection['job_id']).request)
            while True:
                dependants = {m.metric_id for m in package.metrics if set(m.depends_on) & affected}
                dependants.update(recipe['extension_id'] for recipe in getattr(package, 'extension_computations', [])
                                  if set(recipe['internal_operand_metric_ids']) & affected)
                if dependants <= affected:
                    break
                affected.update(dependants)
        for projection in state["projections"]:
            changed = set(projection["selected_metric_ids"] + projection["internal_metric_ids"]) & affected
            if not changed:
                replacements.append(projection)
                continue
            old = self.jobs.get(projection["job_id"])
            job_id = new_job_id()
            child = old.request.model_copy(update={"ir_run_id": request.new_ir_run_id})
            root = self.artifacts.job_dir(job_id)
            self.artifacts.write_json(job_id, "input/request.json", child.model_dump(mode="json"))
            shutil.copy2(self.artifacts.resolve(old.job_id, "input/standard-package.json"), root / "input/standard-package.json")
            if self.artifacts.exists(old.job_id, 'input/runtime-standard-snapshot.json'):
                shutil.copy2(self.artifacts.resolve(old.job_id, 'input/runtime-standard-snapshot.json'),
                             root / 'input/runtime-standard-snapshot.json')
            checkpoint_root = root / "checkpoints"
            checkpoint_root.mkdir(exist_ok=True)
            retained = []
            for checkpoint in (self.artifacts.job_dir(old.job_id) / "checkpoints").glob("*.json"):
                data = json.loads(checkpoint.read_text(encoding="utf-8"))
                if data.get("metric_id") not in affected:
                    shutil.copy2(checkpoint, checkpoint_root / checkpoint.name)
                    retained.append(data.get("metric_id"))
            for relative in ("results/source-facts.json", "results/metric-source-mappings.json"):
                if self.artifacts.exists(old.job_id, relative):
                    destination = root / relative
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copy2(self.artifacts.resolve(old.job_id, relative), destination)
            self.artifacts.write_json(job_id, "input/evidence-transition.json", {
                "from_job_id": old.job_id, "from_ir_run_id": previous_run, "to_ir_run_id": request.new_ir_run_id,
                "affected_object_ids": request.affected_object_ids, "invalidated_metric_ids": sorted(changed),
                "retained_metric_ids": retained, "reason": request.reason, "requested_by": request.requested_by})
            prepared.append((job_id, child, str(root)))
            replacements.append({**projection, "job_id": job_id, "previous_job_id": old.job_id})
        if not prepared:
            raise ValueError("No active projection is affected by this transition")
        transition = {"from_ir_run_id": previous_run, "to_ir_run_id": request.new_ir_run_id,
            "affected_object_ids": request.affected_object_ids, "affected_metric_ids": sorted(affected),
            "reason": request.reason, "requested_by": request.requested_by, "created_at": _now()}
        now = _now()
        with self.jobs._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = json.loads(connection.execute("SELECT state_json FROM report_sessions WHERE session_id=?", (session_id,)).fetchone()[0])
            if current["revision"] != state["revision"]:
                raise ValueError("Report session changed while preparing the evidence transition")
            for job_id, child, output_dir in prepared:
                connection.execute("INSERT INTO jobs(job_id,status,stage,progress_current,progress_total,created_at,updated_at,request_json,output_dir) VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, "queued", "queued", 0, len(child.metric_ids), now, now, child.model_dump_json(), output_dir))
            state.setdefault("projection_history", []).append({"projections": state["projections"], "transition": transition})
            state["projections"] = replacements
            state["evidence_transitions"].append(transition)
            state.update(current_ir_run_id=request.new_ir_run_id, execution_status="queued", cancel_requested=False,
                revision=state["revision"] + 1, updated_at=now)
            state["plan"]["input_identity"] = {"ir_run_id": request.new_ir_run_id, "document_id": ir.document_id,
                "manifest_sha256": hashlib.sha256((new_path / "manifest.json").read_bytes()).hexdigest(),
                "package_sha256": _revision_fingerprint(new_path)}
            state["plan"]["evidence_limitations"] = ([{"policy": ir.manifest.get("evidence_policy", {}),
                "readiness": ir.manifest.get("readiness"), "message": "Only declared usable evidence can be consumed; coverage remains partial."}]
                if ir.manifest.get("evidence_policy", {}).get("mode") == "limited" else [])
            connection.execute("UPDATE report_sessions SET state_json=? WHERE session_id=?", (json.dumps(state, ensure_ascii=False), session_id))
        pins = new_path.parent / ".pins"
        self._write_snapshot(pins, f"{session_id}-{len(state['evidence_transitions'])}.json", {
            "run_id": request.new_ir_run_id, "owner": session_id, "kind": "report_session"})
        self._write_snapshot(self.artifacts.report_session_dir(session_id), "evidence-transitions.json", state["evidence_transitions"])
        return self.detail(session_id)

    def facts(self, session_id):
        state = self._state(session_id)
        objects, facts, conflicts, mappings, evidence = [], {}, {}, [], {}
        superseded, active_sources, active_core = {}, {}, set()
        def collect(items, origin):
            for fact in items:
                identity = fact.get("source_fact_id") or fact.get("fact_id")
                if not identity:
                    continue
                immutable = {k: v for k, v in fact.items() if k != "review_status"}
                previous = facts.get(identity)
                if previous and {k: v for k, v in previous.items() if k != "review_status"} != immutable:
                    variants = conflicts.setdefault(identity, [previous])
                    if not any({k: v for k, v in item.items() if k not in {"review_status", "artifact_origin"}} == immutable for item in variants):
                        variants.append({**fact, "artifact_origin": origin})
                else:
                    facts[identity] = fact
        for path in sorted((self.artifacts.report_session_path(session_id) / "sources").glob("*.json")):
            source = json.loads(path.read_text(encoding="utf-8"))
            objects.append(source)
            collect(source.get("facts", []), path.name)
        for projection in state["projections"]:
            root = self.artifacts.job_dir(projection["job_id"])
            exported = root / "results/source-facts.json"
            if exported.is_file():
                payload = json.loads(exported.read_text(encoding="utf-8"))
                collect(payload.get("facts", []), projection["job_id"])
            mapped = root / "results/metric-source-mappings.json"
            if mapped.is_file():
                payload = json.loads(mapped.read_text(encoding="utf-8"))
                mappings.extend({**item, "job_id": projection["job_id"]} for item in payload.get("core_bindings", []))
            snapshot = collect_stage(self.artifacts, projection["job_id"])
            active_core.update((projection["job_id"], row.get("observation_id") or row.get("assertion_id"))
                for collection in ("quantitative_observations", "qualitative_assertions")
                for row in snapshot["records"].get(collection, []))
            collect(snapshot["source_facts"].get("facts", []), projection["job_id"])
            objects.extend(snapshot["source_facts"].get("objects", []))
            superseded.update({f["source_fact_id"]: f for f in snapshot["source_facts"].get("historical_facts", [])})
            for obj in snapshot["source_facts"].get("objects", []):
                obj = obj.get("interpretation", obj)
                identity = obj.get("source_identity", {})
                key = (identity.get("document_id"), identity.get("ir_run_id"), identity.get("ir_revision"), obj.get("object_id"))
                active_sources.setdefault(key, set()).add(obj.get("source_key"))
            mappings.extend({**item, "job_id": projection["job_id"]} for item in snapshot["metric_source_mappings"].get("core_bindings", []))
            for item in snapshot["records"].get("evidence_references", []):
                evidence[(projection["job_id"], item.get("evidence_id"))] = {**item, "job_id": projection["job_id"]}
            internal = snapshot.get('internal_dependency_scope',{})
            active_core.update((projection['job_id'],row.get('observation_id') or row.get('assertion_id'))
                for collection in ('quantitative_observations','qualitative_assertions')
                for row in internal.get('records',{}).get(collection,[]))
            collect(internal.get('source_facts',[]),projection['job_id'])
            mappings.extend({**item,'job_id':projection['job_id'],'is_internal_dependency':True,
                'scope_role':'internal_dependency'} for item in internal.get('core_bindings',[]))
            for item in internal.get('evidence_references',[]):
                evidence[(projection['job_id'],item.get('evidence_id'))] = {**item,'job_id':projection['job_id'],
                    'is_internal_dependency':True,'scope_role':'internal_dependency'}
            # The stage already selects current validated Core and evidence.
            # Old exports may predate family adjudication or a source revision;
            # their references must never overwrite this active projection.
        current, historical = [], []
        for identity, fact in facts.items():
            if identity in conflicts:
                continue
            key = (fact.get("document_id"), fact.get("ir_run_id"), fact.get("ir_revision"), fact.get("source_object_id"))
            if identity in superseded or (key in active_sources and fact.get("source_key") not in active_sources[key]):
                historical.append({**fact, "interpretation_status": "superseded"})
                continue
            invalidated = []
            for transition in state["evidence_transitions"]:
                targets = set(transition["affected_object_ids"])
                physical = {location.get("ir_object_id") for location in fact.get("physical_sources", [])}
                if fact.get("ir_run_id") == transition["from_ir_run_id"] and (
                    fact.get("source_object_id") in targets or fact.get("source_row_id") in targets or targets & physical):
                    invalidated.append(transition)
            if invalidated:
                historical.append({**fact, "interpretation_status": "superseded", "invalidated_by": invalidated})
            else:
                current.append(fact)
        historical.extend(f for fid, f in superseded.items() if fid not in {h.get("source_fact_id") for h in historical})
        gaps = [{"gap_id": f"source-collision-{identity}", "kind": "contract", "reason": "One source fact ID has conflicting immutable payloads; only this fact is quarantined",
                 "object_ids": sorted({f.get("source_object_id") for f in variants if f.get("source_object_id")}),
                 "metric_ids": sorted({m.get("metric_id") for m in mappings if m.get("source_fact_id") == identity and m.get("metric_id")}),
                 "next_action": "review_source_identity"} for identity, variants in conflicts.items()]
        objects = [obj.get("interpretation", obj) for obj in objects]
        objects = list({obj.get("source_key") or obj.get("source_object_id") or obj.get("object_id") or json.dumps(obj, sort_keys=True): obj for obj in objects}.values())
        objects = [obj for obj in objects if (key := (obj.get("source_identity", {}).get("document_id"), obj.get("source_identity", {}).get("ir_run_id"),
                   obj.get("source_identity", {}).get("ir_revision"), obj.get("object_id"))) not in active_sources or obj.get("source_key") in active_sources[key]]
        source_gaps = list({g["gap_id"]: g for obj in objects for g in obj.get("gaps", obj.get("interpretation", {}).get("gaps", []))
                            if g.get("status", "open") != "resolved"}.values())
        effective_ids = {f.get("source_fact_id") or f.get("fact_id") for f in current}
        mappings = [m for m in mappings if m.get("source_fact_id") in effective_ids
                    and (m.get("job_id"), m.get("core_record_id")) in active_core]
        mappings = list({json.dumps(m, ensure_ascii=False, sort_keys=True): m for m in mappings}.values())
        return {"schema_version": "report-facts-v2", "session_id": session_id, "source_objects": objects,
                "facts": current, "historical_facts": historical, "projections": state["projections"],
                "core_bindings": mappings, "evidence_references": list(evidence.values()), "gaps": [*gaps, *source_gaps],
                "quarantined_facts": [{"source_fact_id": identity, "variants": variants} for identity, variants in conflicts.items()],
                "evidence_transitions": state["evidence_transitions"]}
