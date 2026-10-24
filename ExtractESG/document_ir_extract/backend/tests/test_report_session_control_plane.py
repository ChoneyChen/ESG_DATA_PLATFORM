"""No-model regression tests for the report facade and durable queue contracts."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from fastapi import UploadFile
from fastapi.testclient import TestClient
import pytest

from esg_v2.contracts import OcrJobState
from esg_v2.control_plane.contracts import PipelineTaskType
from esg_v2.control_plane.hooks import PlatformTaskHooks
from esg_v2.control_plane.plans import PipelinePlanCoordinator, PipelinePlanRequest
from esg_v2.control_plane.plan_lifecycle import PlanLifecycle
from esg_v2.control_plane.shared_contracts import ExtractionRequest, ReportExtractionRequest
from esg_v2.control_plane.secrets import RuntimeSecretVault
from esg_v2.control_plane.scheduler import PipelineScheduler
from esg_v2.control_plane.store import PipelineQueueStore
from esg_v2.document.contracts import DocumentIrBuildRequest, DocumentIrJobState
from esg_v2.storage.job_store import JobStore


def prepare_api(tmp_path, monkeypatch):
    from esg_v2 import config
    # First import initializes API stores; direct test selection must never
    # initialize them under the user's configured production paths.
    fixture_settings = config.Settings(output_root=tmp_path / "bootstrap" / "ocr",
        document_ir_output_root=tmp_path / "bootstrap" / "ir", upload_root=tmp_path / "bootstrap" / "uploads",
        report_asset_root=tmp_path / "bootstrap" / "assets", pipeline_queue_db=tmp_path / "bootstrap" / "queue.sqlite3",
        pipeline_task_root=tmp_path / "bootstrap" / "tasks", ocr_job_state_root=tmp_path / "bootstrap" / "jobs" / "ocr",
        document_ir_job_state_root=tmp_path / "bootstrap" / "jobs" / "ir", storage_cleanup_root=tmp_path / "bootstrap" / "cleanup")
    monkeypatch.setattr(config, "get_settings", lambda: fixture_settings)
    from esg_v2.api import main as api
    settings = SimpleNamespace(output_root=tmp_path / "ocr", document_ir_output_root=tmp_path / "ir",
        upload_root=tmp_path / "upload", pipeline_task_root=tmp_path / "tasks", qiniu_api_key=None,
        default_review_provider="local_qwen", targeted_app_root=tmp_path / "targeted")
    for path in (settings.output_root, settings.document_ir_output_root, settings.upload_root):
        path.mkdir()
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "pipeline_store", queue)
    monkeypatch.setattr(api, "ocr_store", JobStore(tmp_path / "ocr_states", OcrJobState))
    monkeypatch.setattr(api, "ir_store", JobStore(tmp_path / "ir_states", DocumentIrJobState))
    monkeypatch.setattr(api, "pipeline_scheduler", SimpleNamespace(wake=lambda: None))
    monkeypatch.setattr(api, "report_assets", SimpleNamespace(refresh=lambda: {"assets": []}))
    remote = []
    state = {"session_id": "report-" + "a" * 32, "execution_status": "queued", "result_status": "not_evaluated", "revision": 1,
             "job_ids": ["tx-one", "tx-two"], "jobs": [], "stages": [], "available_actions": []}
    def proxy(method, path, payload=None):
        remote.append((method, path, payload))
        if path.endswith("/preview"):
            return {"roles": payload["role_config"], "internal_dependencies": [], "reused_inputs": [], "budget": {}}
        if "/resume" in path:
            state.update(execution_status="queued", result_status="partial")
        return dict(state)
    monkeypatch.setattr(api, "_targeted_report_request", proxy)
    engine = PipelinePlanCoordinator(queue, api._dispatch_plan_step, api._plan_ir_manifest, api._cancel_plan_child)
    monkeypatch.setattr(api, "pipeline_plans", engine)
    monkeypatch.setattr(api, 'plan_lifecycle', PlanLifecycle(engine, proxy))
    monkeypatch.setattr(api, 'pipeline_compatibility', lambda: {'ready': True, 'blockers': []})
    return api, queue, engine, remote, state


def test_incompatible_runtime_rejects_new_report_without_queue_mutation(tmp_path, monkeypatch):
    from fastapi import HTTPException
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, 'pipeline_compatibility', lambda: {'ready': False, 'blockers': ['运行版本已过期']})
    with pytest.raises(HTTPException) as caught:
        api.create_report_session(request())
    assert caught.value.status_code == 503
    assert queue.list() == [] and engine.list() == [] and remote == []


def request(ir_run_id="ir-existing"):
    return ReportExtractionRequest(input={"kind": "document_ir", "ir_run_id": ir_run_id}, standards=[
        {"package_id": p, "package_version": "2.0.0", "metric_ids": [p + ".metric"]}
        for p in ("esrs.e1-5", "esrs.e1-6")])


def test_multiple_standards_create_one_fixed_queue_session(tmp_path, monkeypatch):
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / "ir-existing"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"run_id": "ir-existing", "ocr_run_id": "ocr-existing", "can_build_evidence": True}))
    result = api.create_report_session(request())
    tasks = queue.list()
    assert len(tasks) == 1
    assert tasks[0].operation == "report_session"
    assert len(tasks[0].payload["standards"]) == 2
    assert tasks[0].payload["role_config"]["semantic_brain"]["provider"] == "local_qwen"
    assert result["native_session_id"] == state["session_id"]
    assert result["job_ids"] == ["tx-one", "tx-two"]
    assert queue.pinned_ir_references() == {"ir-existing"}
    engine.tick()
    assert len(queue.list()) == 1
    assert sum(path.startswith("/api/report-sessions?enqueue=false&idempotency_key=") for _, path, _ in remote) == 1


def test_partial_session_resume_keeps_the_native_session_and_ir(tmp_path, monkeypatch):
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / "ir-existing"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"can_build_evidence": True, "ocr_run_id": "ocr-existing"}))
    result = api.create_report_session(request())
    queue.update(queue.list()[0].task_id, status="partial")
    queue.record_outcome(queue.list()[0].task_id, {"status": "partial"})
    state.update(execution_status="finished", result_status="partial", jobs=[{"job_id": "tx-one", "status": "partial"},
        {"job_id": "tx-two", "status": "completed"}])
    engine.tick()
    assert engine.list()[0]["status"] == "partial"
    resumed = api.resume_report_session(result["session_id"], None)
    assert resumed["native_session_id"] == state["session_id"]
    assert {t.native_job_id for t in queue.list()} == {state["session_id"]}
    assert [t.operation for t in queue.list()].count("report_session_resume") == 1
    assert any("/resume?enqueue=false" in path for _, path, _ in remote)


def test_ir_partial_advances_and_is_not_rebuilt_on_resume(tmp_path):
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    names = []
    def dispatch(name, config, document, secrets):
        names.append(name)
        return queue.create(task_id=f"pt-{len(names)}", task_type=PipelineTaskType.DOCUMENT_IR if name == "ir" else PipelineTaskType.TARGETED_EXTRACTION,
            operation="build" if name == "ir" else "run", title=name, payload={}, native_job_id=f"run-{len(names)}")
    engine = PipelinePlanCoordinator(queue, dispatch, lambda run: {"run_id": run, "can_build_limited_evidence": True}, queue.request_cancel)
    req = PipelinePlanRequest(asset_ids=["asset"], standards=[{"package_id": "esrs.e1-6", "package_version": "2.0.0", "metric_ids": ["m"]}])
    plan = engine.create(req, [{"asset_id": "asset", "file_name": "fixture", "ocr_run_id": "ocr-existing"}])
    engine.tick()
    queue.update("pt-1", status="partial")
    engine.tick()
    assert names == ["ir", "targeted:esrs.e1-6"]
    queue.update("pt-2", status="partial")
    engine.tick()
    assert engine.list()[0]["status"] == "partial"
    engine.action(plan["plan_id"], "resume")
    engine.tick()
    assert names.count("ir") == 1


def test_old_ir_api_only_queues_without_workflow_execution(tmp_path, monkeypatch):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    root = api.settings.output_root / "ocr-existing"
    root.mkdir()
    (root / "manifest.json").write_text('{}')
    monkeypatch.setattr(api.ir_workflow, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("API must not execute workflow")))
    state = api.create_document_ir_job(DocumentIrBuildRequest(ocr_run_id="ocr-existing"))
    assert state.status == "queued"
    assert queue.list()[0].native_job_id == state.run_id


def test_history_clear_preserves_frozen_ir_pins(tmp_path):
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = queue.create(task_id="pt-source", task_type=PipelineTaskType.TARGETED_EXTRACTION, operation="report_session",
        title="report", payload={"input": {"kind": "document_ir", "ir_run_id": "ir-pinned"}}, native_job_id="report-one")
    queue.update(task.task_id, status="completed")
    assert queue.clear_history() == [task.task_id]
    assert PipelineQueueStore(queue.path).pinned_ir_references() == {"ir-pinned"}


def test_queue_deadline_uses_requested_total_budget(tmp_path):
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = queue.create(task_id="pt-deadline", task_type=PipelineTaskType.TARGETED_EXTRACTION, operation="report_session",
        title="report", payload={"budget": {"total_timeout_seconds": 30}}, native_job_id="report-one")
    task = queue.update(task.task_id, started_at=(datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat())
    assert PipelineScheduler._deadline_expired(task)


def test_native_evidence_transition_queues_same_session_without_duplicate_resume(tmp_path, monkeypatch):
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    for run_id in ("ir-existing", "ir-repaired"):
        root = api.settings.document_ir_output_root / run_id
        root.mkdir()
        (root / "manifest.json").write_text(json.dumps({"run_id": run_id, "ocr_run_id": "ocr-existing", "can_build_evidence": True}))
    result = api.create_report_session(request())
    queue.update(queue.list()[0].task_id, status="completed")
    engine.tick()
    transitioned = api.transition_report_evidence(result["session_id"], {"new_ir_run_id": "ir-repaired", "affected_object_ids": ["table-one"],
        "reason": "Repair unreliable header", "requested_by": "test-user"})
    assert transitioned["native_session_id"] == state["session_id"]
    assert len(queue.list()) == 2
    assert {t.native_job_id for t in queue.list()} == {state["session_id"]}
    assert queue.pinned_ir_references() == {"ir-existing", "ir-repaired"}
    assert any(path.endswith("/evidence-transition?enqueue=false") for _, path, _ in remote)
    assert not any("/resume?enqueue=false" in path for _, path, _ in remote)


def test_evidence_transition_restores_runtime_credentials_without_snapshot_persistence(tmp_path, monkeypatch):
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    vault = RuntimeSecretVault()
    monkeypatch.setattr(api, "pipeline_secrets", vault)
    for run_id in ("ir-existing", "ir-repaired"):
        root = api.settings.document_ir_output_root / run_id
        root.mkdir()
        (root / "manifest.json").write_text(json.dumps({"run_id": run_id, "ocr_run_id": "ocr-existing", "can_build_evidence": True}))
    cloud_request = ReportExtractionRequest.model_validate({**request().model_dump(mode="json"),
        "role_config": {"semantic_brain": {"provider": "qiniu_vlm"}, "cloud_authorization": {"allowed": True}},
        "qiniu_api_key": "initial-runtime-key"})
    result = api.create_report_session(cloud_request)
    queue.update(queue.list()[0].task_id, status="completed")
    vault.discard(queue.list()[0].task_id)
    engine.tick()
    engine._secrets.clear()  # API restart discarded the old, process-only handoff.
    transitioned = api.transition_report_evidence(result["session_id"], {"new_ir_run_id": "ir-repaired",
        "affected_object_ids": ["table-one"], "reason": "Adopt repaired header", "requested_by": "test-user",
        "qiniu_api_key": "transition-runtime-key", "ocr_token": "transition-runtime-ocr"})
    assert transitioned["native_session_id"] == state["session_id"]
    task = next(task for task in queue.list() if task.status.value == "queued")
    assert vault.pop(task.task_id) == {"QINIU_API_KEY": "transition-runtime-key", "PADDLEOCR_VL_API_TOKEN": "transition-runtime-ocr"}
    payload = next(payload for _, path, payload in remote if "/evidence-transition?enqueue=false" in path)
    assert "qiniu_api_key" not in payload and "ocr_token" not in payload
    persisted = json.dumps(engine.list()) + json.dumps([t.model_dump(mode="json") for t in queue.list()])
    assert "transition-runtime-key" not in persisted and "transition-runtime-ocr" not in persisted
    database = queue.path.read_bytes().decode("utf-8", errors="ignore")
    assert "transition-runtime-key" not in database and "transition-runtime-ocr" not in database


def test_signed_source_url_remains_in_runtime_vault_only(tmp_path, monkeypatch):
    from esg_v2.control_plane.secrets import RuntimeSecretVault
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    vault = RuntimeSecretVault()
    monkeypatch.setattr(api, "pipeline_secrets", vault)
    signed = "https://reports.example/report.pdf?token=private-fixture-token"
    task = api._queue_task(task_type=PipelineTaskType.OCR, operation="ocr", title="source",
        payload={"file_url": signed}, native_job_id="ocr-source")
    assert "private-fixture-token" not in json.dumps(task.model_dump(mode="json"))
    assert task.payload["file_url"] == "https://reports.example/report.pdf"
    assert task.requires_runtime_secret
    assert vault.pop(task.task_id)["ESG_OCR_SOURCE_URL"] == signed


def test_cancelled_execution_keeps_explicit_partial_result_axis(tmp_path):
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = queue.create(task_id="pt-cancelled", task_type=PipelineTaskType.TARGETED_EXTRACTION, operation="report_session",
        title="report", payload={"input": {"ir_run_id": "ir-fixed"}}, native_job_id="report-one")
    queue.update(task.task_id, status="cancelled")
    task = queue.record_outcome(task.task_id, {"result_status": "partial"})
    assert task.outcome.execution_status == "cancelled"
    assert task.outcome.result_status == "partial"
    assert task.outcome.artifacts[0]["run_id"] == "report-one"
    assert any(action.action == "resume" for action in task.available_actions)


@pytest.mark.parametrize("status", ["interrupted", "cancelled", "failed"])
def test_report_facade_preserves_stopped_execution_candidates_and_underlying_error(tmp_path, monkeypatch, status):
    api, queue, engine, _, state = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / "ir-existing"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"can_build_evidence": True}))
    result = api.create_report_session(request())
    cause = "GenerationTimeout: call reached its 600 second wall deadline"
    counts = {"selected_metrics": 3, "processed_metrics": 1, "processing_metrics": 0,
              "unfinished_metrics": 1, "not_executed_metrics": 1, "candidate_source_facts": 2, "core_facts": 0}
    state.update(execution_status=status, result_status="needs_confirmation", maturity="source_candidates",
        error="RuntimeError: projection stopped", errors=["earlier protocol error", cause], counts=counts,
        jobs=[{"job_id": "tx-one", "status": status, "error": cause}, {"job_id": "tx-two", "status": "queued"}],
        source_facts=[{"source_fact_id": "candidate-one"}, {"source_fact_id": "candidate-two"}])
    task = queue.list()[0]
    queue.update(task.task_id, status=status)
    queue.record_outcome(task.task_id, state)
    engine.tick()
    plan = engine.list()[0]
    assert plan["status"] == ("needs_attention" if status == "failed" else status)
    assert plan["documents"][0]["status"] == status
    assert plan["documents"][0]["error"] == cause
    detail = api.get_report_session(result["session_id"])
    assert detail["execution_status"] == status
    assert detail["result_status"] == "needs_confirmation"
    assert detail["maturity"] == "source_candidates"
    assert detail["counts"] == counts
    assert detail["error"] == cause
    assert "RuntimeError: projection stopped" in detail["errors"]
    assert "earlier protocol error" in detail["errors"]
    assert f"report_session: {status}" in detail["platform_errors"]
    assert any(action["action"] == "resume" for action in detail["available_actions"])
    assert len(api.get_report_session_facts(result["session_id"])["source_facts"]) == 2
    engine.tick()
    assert len(queue.list()) == 1  # Terminal observation cannot dispatch again.


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_report_facade_uses_current_queue_execution_state(tmp_path, monkeypatch, status):
    api, queue, engine, _, state = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / "ir-existing"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"can_build_evidence": True}))
    result = api.create_report_session(request())
    queue.update(queue.list()[0].task_id, status=status)
    state.update(execution_status=status)
    engine.tick()
    assert api.get_report_session(result["session_id"])["execution_status"] == status


@pytest.mark.parametrize("queue_error", [None, "Worker start failed: fixture executable unavailable"])
def test_report_facade_has_explicit_fallback_when_native_errors_are_absent(tmp_path, monkeypatch, queue_error):
    api, queue, engine, _, state = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / "ir-existing"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"can_build_evidence": True}))
    result = api.create_report_session(request())
    state.update(execution_status="interrupted", jobs=[{"job_id": "tx-one", "status": "interrupted"}])
    task = queue.list()[0]
    queue.update(task.task_id, status="interrupted", error=queue_error)
    queue.record_outcome(task.task_id, state)
    engine.tick()
    detail = api.get_report_session(result["session_id"])
    assert detail["execution_status"] == "interrupted"
    assert detail["error"] == (queue_error or "报告执行已中断；已保存的成果与检查点保留")
    assert detail["error"] in detail["errors"]
    assert detail["platform_errors"] == [f"report_session: {queue_error or 'interrupted'}"]


def test_legacy_failed_plan_uses_durable_interruption_without_losing_generic_metadata(tmp_path, monkeypatch):
    api, queue, engine, _, state = prepare_api(tmp_path, monkeypatch)
    plan = engine.create_report(request(), {"ir_run_id": "ir-existing"})
    plan["status"] = "needs_attention"
    plan["documents"][0].update(status="failed", native_session_id=state["session_id"], error="report_session: interrupted")
    plan["documents"][0]["steps"]["report_session"] = {"status": "interrupted",
        "outcome": {"execution_status": "interrupted", "errors": ["GenerationTimeout: hard call deadline"]}}
    engine.save(plan)
    state.update(execution_status="interrupted", result_status="needs_confirmation", jobs=[{"job_id": "tx-one", "status": "interrupted"}])
    detail = api.get_report_session(plan["plan_id"])
    assert detail["execution_status"] == "interrupted"
    assert detail["error"] == "GenerationTimeout: hard call deadline"
    assert detail["platform_errors"] == ["report_session: interrupted"]
    assert queue.list() == []


def test_report_started_execution_budget_is_an_interruption_with_saved_results(tmp_path, monkeypatch):
    api, queue, engine, _, state = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / "ir-existing"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"can_build_evidence": True}))
    result = api.create_report_session(request())
    state.update(errors=["Earlier source protocol error"], result_status="needs_confirmation")
    plan = engine.list()[0]
    plan["budget_started_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    engine.save(plan)
    queue.update(queue.list()[0].task_id, status='running',
        started_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    engine.tick()
    detail = api.get_report_session(result["session_id"])
    assert engine.list()[0]["status"] == "interrupted"
    assert detail["execution_status"] == "interrupted"
    assert detail["error"] == "报告已达到总执行时限；已保存的成果与检查点保留"
    assert any("total time budget" in error for error in detail["platform_errors"])
    assert "Earlier source protocol error" in detail["errors"]
    assert queue.list()[0].cancel_requested


def existing_job(status="queued", **overrides):
    native_request = ExtractionRequest(ir_run_id="ir-existing", package_id="esrs.e1-6",
        package_version="2.0.0", metric_ids=["metric-one"])
    return {"job_id": "tx-existing", "status": status,
            "request": {**native_request.model_dump(mode="json"), **overrides}}


@pytest.mark.parametrize("native_status", ["queued", "interrupted", "failed", "cancelled", "partial"])
def test_existing_job_endpoint_preserves_native_snapshot_and_identity(tmp_path, monkeypatch, native_status):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    record = existing_job(native_status)
    monkeypatch.setattr(api, "_targeted_report_request", lambda *a, **k: record)
    response = TestClient(api.app).post("/api/pipeline/tasks/targeted-extraction/tx-existing/run-existing")
    assert response.status_code == 202
    queued = queue.get(response.json()["task_id"])
    assert queued.native_job_id == "tx-existing"
    assert queued.operation == "targeted_existing"
    assert queued.payload == record["request"]
    assert queue.pinned_ir_references() == {"ir-existing"}


@pytest.mark.parametrize("native_status", ["running", "completed", "unknown"])
def test_existing_job_endpoint_rejects_illegal_native_states(tmp_path, monkeypatch, native_status):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "_targeted_report_request", lambda *a, **k: existing_job(native_status))
    response = TestClient(api.app).post("/api/pipeline/tasks/targeted-extraction/tx-existing/run-existing")
    assert response.status_code == 409
    assert queue.list() == []


def test_existing_projection_job_must_execute_through_parent_report(tmp_path, monkeypatch):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "_targeted_report_request", lambda *a, **k: existing_job(report_session_id="report-" + "a" * 32))
    response = TestClient(api.app).post("/api/pipeline/tasks/targeted-extraction/tx-existing/run-existing")
    assert response.status_code == 409
    assert queue.list() == []


@pytest.mark.parametrize("child_status,operation", [("queued", "report_session"), ("partial", "report_session_resume")])
def test_existing_report_endpoint_deduplicates_and_preserves_runtime_credentials(tmp_path, monkeypatch, child_status, operation):
    api, queue, _, remote, state = prepare_api(tmp_path, monkeypatch)
    state.update(request=request().model_dump(mode="json"), jobs=[{"status": child_status}])
    vault = RuntimeSecretVault()
    monkeypatch.setattr(api, "pipeline_secrets", vault)
    client = TestClient(api.app)
    endpoint = f"/api/pipeline/tasks/report-session/{state['session_id']}/run-existing"
    first = client.post(endpoint, json={"qiniu_api_key": "key-first", "ocr_token": "token-runtime"})
    second = client.post(endpoint, json={"qiniu_api_key": "key-refreshed"})
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert len(queue.list()) == 1
    task = queue.list()[0]
    assert task.native_job_id == state["session_id"] and task.operation == operation
    assert task.payload["budget"]["agent_max_steps"] == 8
    assert len(remote) == 1
    assert "key-first" not in json.dumps(task.model_dump(mode="json"))
    assert "token-runtime" not in queue.path.read_bytes().decode("utf-8", errors="ignore")
    assert vault.pop(task.task_id) == {"QINIU_API_KEY": "key-refreshed", "PADDLEOCR_VL_API_TOKEN": "token-runtime"}
    queue.update(task.task_id, status="running")
    third = client.post(endpoint, json={"qiniu_api_key": "ignored-running-key"})
    assert third.status_code == 202 and third.json()["task_id"] == task.task_id
    assert vault.pop(task.task_id) == {}
    assert len(remote) == 1


@pytest.mark.parametrize("native_status", ["running", "finished", "interrupted", "failed", "cancelled"])
def test_existing_report_endpoint_requires_native_queued_transition(tmp_path, monkeypatch, native_status):
    api, queue, _, _, state = prepare_api(tmp_path, monkeypatch)
    state.update(execution_status=native_status, request=request().model_dump(mode="json"))
    response = TestClient(api.app).post(f"/api/pipeline/tasks/report-session/{state['session_id']}/run-existing")
    assert response.status_code == 409
    assert queue.list() == []


def test_existing_native_snapshot_identity_and_contract_are_validated(tmp_path, monkeypatch):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    client = TestClient(api.app)
    record = existing_job()
    monkeypatch.setattr(api, "_targeted_report_request", lambda *a, **k: record)
    endpoint = "/api/pipeline/tasks/targeted-extraction/tx-existing/run-existing"
    record["job_id"] = "tx-another"
    assert client.post(endpoint).status_code == 502
    record["job_id"] = "tx-existing"
    record["request"] = {"invalid": True}
    assert client.post(endpoint).status_code == 502
    assert queue.list() == []


def test_legacy_resume_is_an_idempotent_existing_task_adapter(tmp_path, monkeypatch):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    reads = []
    def proxy(*args):
        reads.append(args)
        return existing_job("interrupted")
    monkeypatch.setattr(api, "_targeted_report_request", proxy)
    client = TestClient(api.app)
    first = client.post("/api/pipeline/tasks/targeted-extraction/tx-existing/resume")
    second = client.post("/api/pipeline/tasks/targeted-extraction/tx-existing/run-existing")
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert queue.list()[0].operation == "targeted_existing"
    assert len(queue.list()) == len(reads) == 1


def test_limited_completed_report_only_offers_legal_evidence_repair(tmp_path, monkeypatch):
    api, queue, engine, _, state = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / "ir-existing"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"run_id": "ir-existing", "schema_version": "document-ir-v0.4",
        "can_build_limited_evidence": True, "ocr_run_id": "ocr-existing"}))
    result = api.create_report_session(request())
    queue.update(queue.list()[0].task_id, status="partial")
    queue.record_outcome(queue.list()[0].task_id, {"status": "partial"})
    state.update(execution_status="finished", result_status="partial",
        jobs=[{"job_id": "tx-one", "status": "completed"}, {"job_id": "tx-two", "status": "completed"}],
        gaps=[{"kind": "column_header", "object_ids": ["table-affected"]}])
    engine.tick()
    detail = api.get_report_session(result["session_id"])
    actions = detail["available_actions"]
    assert not any(a["action"] == "resume" for a in actions)
    repair = next(a for a in actions if a["action"] == "local_repair")
    assert repair["payload"]["target_ids"] == ["table-affected"]
    response = TestClient(api.app).post(f"/api/report-sessions/{result['session_id']}/resume")
    assert response.status_code == 409
    assert len(queue.list()) == 1


@pytest.mark.parametrize("initial_status,result_status", [("queued", "cancelled"), ("running", "running")])
def test_native_cancel_uses_unified_queue_and_discards_ephemeral_credentials(tmp_path, monkeypatch, initial_status, result_status):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    callbacks = []
    monkeypatch.setattr(api, "pipeline_hooks", SimpleNamespace(cancelled=lambda task: callbacks.append(task.task_id)))
    vault = RuntimeSecretVault()
    monkeypatch.setattr(api, "pipeline_secrets", vault)
    task = queue.create(task_id="pt-native-cancel", task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="report_session", title="native report", payload={}, native_job_id="report-existing")
    queue.update(task.task_id, status=initial_status)
    vault.put(task.task_id, {"QINIU_API_KEY": "discard-runtime-key"})
    response = TestClient(api.app).post("/api/pipeline/tasks/native/report-existing/cancel")
    assert response.status_code == 200
    result = response.json()
    assert result["task_id"] == task.task_id and result["native_job_id"] == "report-existing"
    assert result["status"] == result_status and result["cancel_requested"]
    assert queue.get(task.task_id).cancel_requested
    assert vault.pop(task.task_id) == {}
    assert callbacks == ([task.task_id] if initial_status == "queued" else [])


def test_native_cancel_without_active_task_is_a_safe_noop(tmp_path, monkeypatch):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    response = TestClient(api.app).post("/api/pipeline/tasks/native/tx-missing/cancel")
    assert response.status_code == 200
    assert response.json() == {"native_job_id": "tx-missing", "task_id": None, "status": "not_active",
        "cancel_requested": False, "task": None}
    assert queue.list() == []


def test_new_queue_task_hands_credentials_to_worker_claimed_during_commit(tmp_path, monkeypatch):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    vault = RuntimeSecretVault()
    monkeypatch.setattr(api, "pipeline_secrets", vault)
    original_create = queue.create_many
    started, finished = threading.Event(), threading.Event()
    consumed, workers = [], []
    def immediately_claim(specifications):
        tasks = original_create(specifications)
        queue.update(tasks[0].task_id, status="running")
        def consume():
            started.set()
            consumed.append(vault.pop(tasks[0].task_id))
            finished.set()
        worker = threading.Thread(target=consume)
        workers.append(worker)
        worker.start()
        assert started.wait(1)
        assert not finished.wait(0.01)
        return [queue.get(tasks[0].task_id)]
    monkeypatch.setattr(queue, "create_many", immediately_claim)
    task = api._queue_task(task_type=PipelineTaskType.TARGETED_EXTRACTION, operation="targeted_existing",
        title="credential race", native_job_id="tx-existing", payload={}, secrets={"QINIU_API_KEY": "runtime-handoff"})
    for worker in workers:
        worker.join(1)
        assert not worker.is_alive()
    assert task.status.value == "running"
    assert consumed == [{"QINIU_API_KEY": "runtime-handoff"}]


def test_existing_cloud_job_requires_ephemeral_key_then_queues_without_persisting_it(tmp_path, monkeypatch):
    api, queue, _, _, _ = prepare_api(tmp_path, monkeypatch)
    vault = RuntimeSecretVault()
    monkeypatch.setattr(api, "pipeline_secrets", vault)
    monkeypatch.setattr(api, "_targeted_report_request", lambda *a, **k: existing_job(semantic_provider="qiniu_vlm"))
    client = TestClient(api.app)
    endpoint = "/api/pipeline/tasks/targeted-extraction/tx-existing/run-existing"
    assert client.post(endpoint).status_code == 400
    assert queue.list() == []
    response = client.post(endpoint, json={"qiniu_api_key": "runtime-qiniu", "ocr_token": "runtime-ocr"})
    assert response.status_code == 202
    task = queue.list()[0]
    assert task.requires_runtime_secret
    assert "runtime-qiniu" not in json.dumps(task.model_dump(mode="json"))
    assert vault.pop(task.task_id) == {"QINIU_API_KEY": "runtime-qiniu", "PADDLEOCR_VL_API_TOKEN": "runtime-ocr"}


def test_concurrent_existing_submissions_atomically_deduplicate_native_identity(tmp_path):
    path = tmp_path / "queue.sqlite3"
    stores = [PipelineQueueStore(path) for _ in range(8)]
    def submit(index):
        return stores[index].create_many([{"task_id": f"pt-race-{index}", "task_type": PipelineTaskType.TARGETED_EXTRACTION,
            "operation": "targeted_existing", "title": "same native job", "payload": {"ir_run_id": "ir-existing"},
            "native_job_id": "tx-existing", "deduplicate_native": True}])[0]
    with ThreadPoolExecutor(max_workers=8) as executor:
        tasks = list(executor.map(submit, range(8)))
    assert len({task.task_id for task in tasks}) == 1
    assert len(stores[0].list()) == 1
    assert len(stores[0].events(tasks[0].task_id)) == 1
    assert stores[0].pinned_ir_references() == {"ir-existing"}


@pytest.mark.parametrize("operation,command", [("targeted_existing", "existing"), ("report_session", "report-session"),
    ("report_session_resume", "report-session"), ("resume", "resume")])
def test_existing_task_hooks_execute_inside_inherited_queue_worker(tmp_path, operation, command):
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = queue.create(task_id="pt-existing", task_type=PipelineTaskType.TARGETED_EXTRACTION, operation=operation,
        title="existing native task", payload={}, native_job_id="native-existing")
    hooks = object.__new__(PlatformTaskHooks)
    hooks.settings = SimpleNamespace(targeted_runtime_python=Path(sys.executable), targeted_app_root=tmp_path / "targeted")
    worker = hooks.command(task, {"QINIU_API_KEY": "runtime-only"})
    assert worker.argv[3] == command
    assert "--worker" in worker.argv and "native-existing" in worker.argv
    assert worker.environment["QINIU_API_KEY"] == "runtime-only"
    assert "runtime-only" not in " ".join(worker.argv)


def test_report_facade_preserves_extension_only_selection(tmp_path, monkeypatch):
    api, queue, engine, remote, _ = prepare_api(tmp_path, monkeypatch)
    root = api.settings.document_ir_output_root / 'ir-existing'
    root.mkdir()
    (root / 'manifest.json').write_text(json.dumps({'run_id':'ir-existing','can_build_evidence':True}))
    selected = {'extension_id':'project.esrs.2023-set1.e1-6.extension.scope1-scope2-total',
        'definition_version':'1.0.0','release_digest':'a'*64,'selection_kind':'custom_extension',
        'value_origins':['reported'],'derivation_rule_ids':[]}
    frozen = {'esrs.2023-set1.e1-6':{'parent_package_id':'esrs.2023-set1.e1-6',
        'parent_package_version':'2.1.1','parent_source_digest':'b'*64,'runtime_digest':'c'*64,
        'selections':[selected],'extension_releases':[{'release_digest':'a'*64,'fixture':'approved server response'}]}}
    original_proxy = api._targeted_report_request
    def freeze_proxy(method,path,payload=None):
        result = original_proxy(method,path,payload)
        if path.endswith('/preview'):
            result['runtime_standard_snapshots'] = frozen
        return result
    monkeypatch.setattr(api,'_targeted_report_request',freeze_proxy)
    parsed = ReportExtractionRequest(input={'kind':'document_ir','ir_run_id':'ir-existing'},
        standards=[], extension_selections=[selected])
    api.create_report_session(parsed)
    plan = engine.list()[0]
    assert plan['config']['standards'] == []
    assert plan['report_request']['extension_selections'] == [selected]
    assert plan['report_request']['frozen_runtime_standard_snapshots'] == frozen
    assert remote[0][2]['extension_selections'] == [selected]
    queued = queue.list()[0]
    assert queued.payload['extension_selections'] == [selected]
    assert queued.payload['frozen_runtime_standard_snapshots'] == frozen
    assert queued.operation == 'report_session'


@pytest.mark.parametrize('failure',['missing','changed'])
def test_pdf_report_freezes_overlay_before_preparation_and_blocks_changed_native_contract(tmp_path, monkeypatch, failure):
    from copy import deepcopy
    api, queue, engine, remote, _ = prepare_api(tmp_path, monkeypatch)
    selected = {'extension_id':'project.esrs.2023-set1.e1-6.extension.scope1-scope2-total',
        'definition_version':'1.0.0','release_digest':'a'*64,'selection_kind':'custom_extension',
        'value_origins':['reported'],'derivation_rule_ids':[]}
    frozen = {'esrs.2023-set1.e1-6':{'parent_package_id':'esrs.2023-set1.e1-6',
        'parent_package_version':'2.1.1','parent_source_digest':'b'*64,'runtime_digest':'c'*64,
        'selections':[selected],'extension_releases':[{'release_digest':'a'*64,'fixture':'full approved server response'}]}}
    calls = []
    def proxy(method,path,payload=None):
        calls.append((method,path,payload))
        return {'runtime_standard_snapshots':deepcopy(frozen)}
    monkeypatch.setattr(api,'_targeted_report_request',proxy)
    monkeypatch.setattr(api,'_report_document',lambda request:{'asset_id':'pdf-fixture','file_name':'Fixture.pdf'})
    monkeypatch.setattr(api,'_report_plan_detail',lambda plan:plan)
    monkeypatch.setattr(engine,'tick',lambda:None)  # Only plan creation; no OCR/model worker.
    parsed = ReportExtractionRequest(input={'kind':'pdf','asset_id':'pdf-fixture'},standards=[],extension_selections=[selected])
    created = api.create_report_session(parsed)
    saved = engine.list()[0]
    assert saved['report_request']['frozen_runtime_standard_snapshots'] == frozen
    config = deepcopy(saved['config'])
    if failure == 'missing':
        config['report_request'].pop('frozen_runtime_standard_snapshots')
    else:
        changed = deepcopy(frozen); changed['esrs.2023-set1.e1-6']['parent_source_digest'] = 'd'*64
        monkeypatch.setattr(api,'_targeted_report_request',lambda method,path,payload=None:{'runtime_standard_snapshots':changed})
    with pytest.raises(ValueError,match='冻结|快照'):
        api._dispatch_plan_step('report_session',config,{'ir_run_id':'ir-prepared','file_name':'Fixture.pdf'}, {})
    assert engine.list()[0]['report_request']['frozen_runtime_standard_snapshots'] == frozen
    assert queue.list() == []
    assert not any('/api/report-sessions?enqueue=false' in path for _,path,_ in calls)


def test_a_second_dispatch_of_one_plan_step_adopts_the_first_queue_row(tmp_path):
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    base = {"task_type": PipelineTaskType.TARGETED_EXTRACTION, "operation": "report_session",
            "title": "report", "payload": {"_plan_step_key": "plan-1:0:report_session:0", "ir_run_id": "ir-1"}}
    first = queue.create_many([{**base, "task_id": "pt-one", "native_job_id": "report-a"}])[0]
    second = queue.create_many([{**base, "task_id": "pt-two", "native_job_id": "report-b"}])[0]
    assert first.task_id == second.task_id == "pt-one"
    assert len(queue.list()) == 1
