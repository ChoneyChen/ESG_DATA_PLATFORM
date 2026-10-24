"""Contract, dependency and recovery tests with isolated fake stage workers."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from esg_v2.api.report_processing import processing_router
from esg_v2.assets.report_catalog import ReportAssetCatalog
from esg_v2.control_plane.contracts import PipelineTaskStatus, PipelineTaskType
from esg_v2.control_plane.plans import dispatch_key
from esg_v2.control_plane.report_processing import (
    EvidenceTransitionRequest, ProcessingActionRequest, ProcessingConfig, ProcessingRequest,
    ReportMetadata, ReportMetadataPatch, ReportProcessingCoordinator,
)
from esg_v2.control_plane.store import PipelineQueueStore


def fixture(tmp_path):
    assets = ReportAssetCatalog(tmp_path / "pdf", tmp_path / "ocr")
    (assets.root / "report.pdf").write_bytes(b"%PDF isolated test report")
    asset = assets.refresh()["assets"][0]
    store = PipelineQueueStore(tmp_path / "queue.sqlite")
    calls = []

    def dispatch(stage, config, state, secrets):
        calls.append(stage)
        number = len(calls)
        return store.create(task_id=f"task-{number}", task_type=PipelineTaskType.OCR if stage == "ocr" else PipelineTaskType.DOCUMENT_IR,
            operation="ocr" if stage == "ocr" else "build", title=stage,
            payload={"_plan_step_key": dispatch_key.get(), "asset_id": state["asset_id"]}, native_job_id=f"{stage}-fixture-{number}")

    def integrity(stage, run_id):
        if (tmp_path / stage / run_id / "payload.txt").read_text() != "valid fixture artifact":
            raise ValueError("Artifact integrity mismatch")

    engine = ReportProcessingCoordinator(store, assets, dispatch, store.request_cancel,
        tmp_path / "ocr", tmp_path / "ir", engine_versions={"ocr": "ocr-engine-v1", "ir": "ir-engine-v1"}, integrity_check=integrity)
    app = FastAPI()
    app.include_router(processing_router(engine))
    return engine, store, asset, calls, TestClient(app)


def finish(engine, store, stage, *, full=True, limited=False, fail=False):
    task = next(t for t in store.list() if t.task_type.value == ("ocr" if stage == "ocr" else "document_ir") and t.status.value == "queued")
    if fail:
        store.update(task.task_id, status=PipelineTaskStatus.FAILED, error="fixture IR failure")
        return task
    state = next(s for s in engine.list() if any(step.get("task_id") == task.task_id for step in s["steps"].values()))
    target = engine.roots[stage] / task.native_job_id
    target.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": task.native_job_id, "page_count": 2,
        "source": {"sha256": state["source_sha256"]} if stage == "ocr" else {"pdf_sha256": state["source_sha256"], "ocr_run_id": state["ocr_run_id"]}}
    if stage == "ir":
        manifest.update(schema_version="document-ir-v0.12", ocr_run_id=state["ocr_run_id"],
            can_build_evidence=full, can_build_limited_evidence=limited,
            evidence_policy={"mode": "full" if full else "limited", "excluded_target_ids": [] if full else ["table-problem"]})
    else:
        config = state["request"]["config"]
        manifest["provider_route"] = {"requested": config["ocr_provider"], "requested_model": config["ocr_model"], "allow_api_fallback": config["allow_cloud_ocr"]}
        # A real targeted run records its selector; a fixture that omitted it
        # would silently claim full-document coverage.
        if config.get("page_selection", "all") != "all":
            manifest["page_selection"] = {"mode": "targeted", "page_indices": [0], "selector": {"name": config["page_selection"]}}
    (target / "manifest.json").write_text(json.dumps(manifest))
    (target / "payload.txt").write_text("valid fixture artifact")
    store.update(task.task_id, status=PipelineTaskStatus.COMPLETED)
    return task


def complete(engine, store, asset):
    state = engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    engine.tick()
    finish(engine, store, "ocr")
    engine.tick()
    finish(engine, store, "ir")
    engine.tick()
    return engine.get(state["processing_id"])


def test_required_review_policy_changes_ir_reuse_without_repeating_ocr(tmp_path):
    engine,store,asset,calls,_=fixture(tmp_path)
    first=engine.create(ProcessingRequest(asset_id=asset["asset_id"],mode="auto"))
    engine.tick();finish(engine,store,"ocr");engine.tick();finish(engine,store,"ir");engine.tick()
    first=engine.get(first["processing_id"])
    assert first["status"]=="ready"
    revised=engine.create(ProcessingRequest(asset_id=asset["asset_id"],config=ProcessingConfig(execute_required_reviews=False)))
    engine.tick()
    revised=engine.get(revised["processing_id"])
    assert revised["ocr_run_id"]==first["ocr_run_id"]
    assert revised.get("ir_run_id") is None
    assert calls==["ocr","ir","ir"]


def test_required_cloud_review_requires_credentials_before_queue_mutation(tmp_path):
    engine,store,asset,_,_=fixture(tmp_path)
    with pytest.raises(ValueError,match="云证据复核"):
        engine.create(ProcessingRequest(asset_id=asset["asset_id"],config=ProcessingConfig(
            review_provider="qiniu",execute_optional_reviews=False,execute_required_reviews=True)))
    assert store.list()==[]


def test_standalone_processing_needs_no_standard_and_parent_occupies_no_worker(tmp_path):
    engine, store, asset, calls, client = fixture(tmp_path)
    response = client.post("/api/report-processing/runs", json={"asset_id": asset["asset_id"], "submission_key": "once"})
    assert response.status_code == 202
    state = response.json()
    assert state["report"]["metadata"]["publisher_name"] is None
    assert state["report"]["metadata"]["reporting_year"] is None
    assert "standards" not in state["request"]
    assert store.list() == []
    engine.tick()
    assert calls == ["ocr"]
    assert len(store.list()) == 1
    finish(engine, store, "ocr")
    engine.tick()
    assert calls == ["ocr", "ir"]
    finish(engine, store, "ir")
    engine.tick()
    state = client.get(f"/api/report-processing/runs/{state['processing_id']}").json()
    assert state["status"] == "ready"
    assert state["evidence_status"] == "full"
    assert {a["action"] for a in state["available_actions"]} == {"inspect_evidence", "select_evidence_version", "start_extraction"}
    assert [s["label"] for s in state["stages"]] == ["文档识别", "结构与证据构建", "证据质量检查"]


def test_submission_idempotency_concurrency_and_conflict(tmp_path):
    engine, store, asset, calls, client = fixture(tmp_path)
    request = ProcessingRequest(asset_id=asset["asset_id"], submission_key="stable-key")
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: engine.create(request), range(3)))
    assert len({r["processing_id"] for r in results}) == 1
    engine.tick()
    assert calls == ["ocr"]
    conflict = client.post("/api/report-processing/runs", json={"asset_id": asset["asset_id"], "submission_key": "stable-key", "mode": "reprocess"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["artifacts_retained"] is True


def test_restart_recovers_enqueue_checkpoint_gap_and_failed_ir_only(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    state = engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    engine.tick()
    # Simulate a crash after durable enqueue but before parent checkpoint.
    state["steps"] = {}
    engine._save(state, force=True)
    restarted = ReportProcessingCoordinator(store, engine.assets, engine.dispatch, engine.cancel,
        engine.roots["ocr"], engine.roots["ir"], engine_versions=engine.engine_versions, integrity_check=engine.integrity_check)
    restarted.tick()
    assert calls == ["ocr"]
    finish(restarted, store, "ocr")
    restarted.tick()
    original_ocr = restarted.get(state["processing_id"])["ocr_run_id"]
    finish(restarted, store, "ir", fail=True)
    restarted.tick()
    stopped = restarted.get(state["processing_id"])
    assert stopped["status"] == "failed"
    restarted.action(state["processing_id"], "resume", ProcessingActionRequest(expected_revision=stopped["revision"]))
    restarted.tick()
    assert calls == ["ocr", "ir", "ir"]
    assert restarted.get(state["processing_id"])["ocr_run_id"] == original_ocr
    finish(restarted, store, "ir")
    restarted.tick()
    assert restarted.get(state["processing_id"])["status"] == "ready"


def test_compatible_reuse_all_modes_and_immutable_source(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    original = complete(engine, store, asset)
    reused = engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    assert reused["status"] == "ready"
    assert reused["ir_run_id"] == original["ir_run_id"]
    assert calls == ["ocr", "ir"]
    rebuilt = engine.create(ProcessingRequest(asset_id=asset["asset_id"], mode="rebuild_ir"))
    engine.tick()
    assert rebuilt["ocr_run_id"] == original["ocr_run_id"]
    assert calls == ["ocr", "ir", "ir"]
    fresh = engine.create(ProcessingRequest(asset_id=asset["asset_id"], mode="reprocess"))
    engine.tick()
    assert "ocr_run_id" not in fresh
    assert calls[-1] == "ocr"
    assert (engine.roots["ir"] / original["ir_run_id"] / "manifest.json").is_file()
    with pytest.raises(ValueError, match="内容已变化"):
        engine.create(ProcessingRequest(asset_id=asset["asset_id"], source_sha256="0" * 64))


def test_config_builder_and_integrity_mismatch_do_not_reuse(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    original = complete(engine, store, asset)
    # A file corruption cannot be masked by a valid manifest.
    (engine.roots["ir"] / original["ir_run_id"] / "payload.txt").write_text("corrupt")
    changed = engine.create(ProcessingRequest(asset_id=asset["asset_id"], config=ProcessingConfig(render_dpi=200)))
    assert changed.get("ir_run_id") is None
    assert changed["ocr_run_id"] == original["ocr_run_id"]
    resolved = engine.resolve_document(asset["asset_id"], ProcessingConfig())
    assert resolved.get("ir_run_id") is None
    assert any("integrity" in d["reason"] for d in resolved["reuse_diagnostics"])
    new_engine = ReportProcessingCoordinator(store, engine.assets, engine.dispatch, engine.cancel,
        engine.roots["ocr"], engine.roots["ir"], engine_versions={"ocr": "new", "ir": "new"}, integrity_check=engine.integrity_check)
    resolved = new_engine.resolve_document(asset["asset_id"], ProcessingConfig())
    assert resolved.get("ocr_run_id") is None
    assert resolved.get("ir_run_id") is None


@pytest.mark.parametrize("allow,expected", [(True, "ready_with_limits"), (False, "needs_attention")])
def test_limited_evidence_is_distinct_and_policy_checked(tmp_path, allow, expected):
    engine, store, asset, calls, _ = fixture(tmp_path)
    state = engine.create(ProcessingRequest(asset_id=asset["asset_id"], config=ProcessingConfig(allow_limited_evidence=allow)))
    engine.tick(); finish(engine, store, "ocr"); engine.tick()
    finish(engine, store, "ir", full=False, limited=True); engine.tick()
    stopped = engine.get(state["processing_id"])
    assert stopped["status"] == expected
    assert stopped["evidence_policy"]["excluded_target_ids"] == ["table-problem"]
    if not allow:
        resolved = engine.resolve_document(asset["asset_id"], ProcessingConfig(allow_limited_evidence=False))
        assert "ir_run_id" not in resolved


def test_explicit_evidence_transition_checks_pdf_and_fixed_ocr(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    state = complete(engine, store, asset)
    manifest = json.loads((engine.roots["ir"] / state["ir_run_id"] / "manifest.json").read_text())
    repaired_id = "ir-explicit-repair"
    target = engine.roots["ir"] / repaired_id
    target.mkdir()
    manifest["run_id"] = repaired_id
    manifest["source"]["pdf_sha256"] = "0" * 64
    (target / "manifest.json").write_text(json.dumps(manifest))
    (target / "payload.txt").write_text("valid fixture artifact")
    with pytest.raises(ValueError, match="内容身份不一致"):
        engine.transition_evidence(state["processing_id"], EvidenceTransitionRequest(ir_run_id=repaired_id, expected_revision=state["revision"]))
    manifest["source"]["pdf_sha256"] = asset["sha256"]
    (target / "manifest.json").write_text(json.dumps(manifest))
    updated = engine.transition_evidence(state["processing_id"], EvidenceTransitionRequest(ir_run_id=repaired_id, expected_revision=state["revision"]))
    assert updated["evidence_transitions"][0]["from"] == state["ir_run_id"]
    assert updated["status"] == "ready"
    assert (engine.roots["ir"] / state["ir_run_id"]).is_dir()
    with store._connect() as db:
        registered = db.execute("SELECT config_digest FROM report_processing_artifacts WHERE processing_id=? AND stage='ir'", (state["processing_id"],)).fetchone()[0]
    assert registered != engine._config_digest(updated, "ir")
    assert repaired_id not in {row.get("ir_run_id") for row in [engine.resolve_document(asset["asset_id"], ProcessingConfig())]}


def test_report_identity_metadata_and_explicit_asset_version(tmp_path):
    engine, _, asset, _, client = fixture(tmp_path)
    report = client.post("/api/reports", json={"asset_id": asset["asset_id"]}).json()
    patched = client.patch(f"/api/reports/{report['report_id']}", json={"expected_revision": report["revision"],
        "metadata": {"title": "企业可持续发展报告", "publisher_name": "集团", "reporting_year": 2025}})
    assert patched.status_code == 200
    assert "measurement_entity" not in patched.json()["metadata"]
    stale = client.patch(f"/api/reports/{report['report_id']}", json={"expected_revision": report["revision"], "metadata": {}})
    assert stale.status_code == 409
    (engine.assets.root / "revised.pdf").write_bytes(b"%PDF different explicit revised report")
    second = next(a for a in engine.assets.refresh()["assets"] if a["asset_id"] != asset["asset_id"])
    attached = client.post(f"/api/reports/{report['report_id']}/versions", json={"asset_id": second["asset_id"], "expected_revision": patched.json()["revision"]})
    assert attached.status_code == 201
    assert [v["version"] for v in attached.json()["asset_versions"]] == [1, 2]
    # Same bytes remain the same asset identity, independent of path labels.
    assert asset["asset_id"] == "pdf-" + sha256(b"%PDF isolated test report").hexdigest()[:24]
    with pytest.raises(ValueError):
        ReportMetadata(period_start="2026-01-01", period_end="2025-01-01")


def test_secrets_never_enter_state_and_cancel_retains_artifacts(tmp_path):
    engine, store, asset, calls, client = fixture(tmp_path)
    state = engine.create(ProcessingRequest(asset_id=asset["asset_id"], ocr_token="test-sensitive", qiniu_api_key="other-sensitive"))
    assert "sensitive" not in json.dumps(state)
    engine.tick()
    cancelled = client.post(f"/api/report-processing/runs/{state['processing_id']}/cancel", json={"expected_revision": engine.get(state["processing_id"])["revision"]})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert store.list()[0].cancel_requested
    assert engine.assets.resolve(asset["asset_id"]).is_file()
    events = client.get(f"/api/report-processing/runs/{state['processing_id']}/events").json()["events"]
    assert len(events) >= 3
    after = client.get(f"/api/report-processing/runs/{state['processing_id']}/events?after={events[-1]['event_id']}").json()["events"]
    assert after == []


def test_production_integrity_guard_rejects_manifest_only_success(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    state = engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    engine.tick()
    finish(engine, store, "ocr")
    engine.integrity_check = engine._check_integrity
    engine.tick()
    stopped = engine.get(state["processing_id"])
    assert stopped["status"] == "failed"
    assert "entrypoint" in stopped["error"] or "package_type" in stopped["error"]
    assert calls == ["ocr"]


def test_full_plan_artifact_enrollment_is_explicit_and_idempotent(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    state = complete(engine, store, asset)
    doc = engine.resolve_document(asset["asset_id"], ProcessingConfig())
    doc.update(ocr_run_id=state["ocr_run_id"], ir_run_id=state["ir_run_id"])
    assert engine.record_plan_artifacts({"plan_id": "historical"}, {"asset_id": asset["asset_id"]}) is None
    first = engine.record_plan_artifacts({"plan_id": "new-plan"}, doc)
    second = engine.record_plan_artifacts({"plan_id": "new-plan"}, doc)
    assert first["processing_id"] == second["processing_id"]
    assert first["status"] == "ready"
    assert first["origin_plan_id"] == "new-plan"


def test_report_summary_distinguishes_ocr_only_from_usable_and_corrupt_evidence(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    assert engine.asset_summary(asset)["processing_status"] == "unprocessed"
    state = complete(engine, store, asset)
    summary = engine.asset_summary(asset)
    assert summary["processing_status"] == "ready"
    assert summary["ir_run_id"] == state["ir_run_id"]
    assert summary["report_metadata"]["publisher_name"] is None
    (engine.roots["ir"] / state["ir_run_id"] / "payload.txt").write_text("corrupt")
    assert engine.asset_summary(asset)["processing_status"] == "needs_attention"
    assert engine.asset_summary(asset)["evidence_status"] == "blocked"


@pytest.mark.parametrize("change", ["artifact", "engine"])
def test_current_evidence_readiness_blocks_actions_without_rewriting_history(tmp_path, change):
    engine, store, asset, calls, client = fixture(tmp_path)
    state = complete(engine, store, asset)
    assert engine.detail(state)["current_evidence"]["ready"] is True
    if change == "artifact":
        (engine.roots["ir"] / state["ir_run_id"] / "payload.txt").write_text("corrupt")
    else:
        engine.engine_versions = {"ocr": "ocr-engine-v2", "ir": "ir-engine-v2"}
    detail = client.get(f"/api/report-processing/runs/{state['processing_id']}").json()
    summary = engine.asset_summary(asset)
    assert detail["current_evidence"] == summary["current_evidence"]
    assert detail["current_evidence"]["status"] == "blocked"
    assert detail["current_evidence"]["blockers"]
    assert "start_extraction" not in {a["action"] for a in detail["available_actions"]}
    assert "inspect_evidence" in {a["action"] for a in detail["available_actions"]}
    assert detail["status"] == summary["recorded_processing_status"] == "ready"
    assert detail["evidence_status"] == "full"
    assert summary["processing_status"] == "needs_attention"
    assert engine.get(state["processing_id"]) == state
    assert calls == ["ocr", "ir"]


def test_only_rebuild_requires_existing_compatible_ocr_and_no_orphan_is_queued(tmp_path):
    engine, store, asset, calls, client = fixture(tmp_path)
    response = client.post("/api/report-processing/runs", json={"asset_id": asset["asset_id"], "mode": "rebuild_ir"})
    assert response.status_code == 409
    assert "不能只重建结构" in response.json()["detail"]["message"]
    engine.tick()
    assert engine.list() == []
    assert store.list() == []
    assert calls == []


def test_readiness_guard_blocks_new_work_but_allows_historical_reads_and_cancel(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    state = engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    from fastapi import HTTPException
    def not_ready():
        raise HTTPException(503, {"code": "runtime_requires_restart", "message": "版本未就绪"})
    app = FastAPI()
    app.include_router(processing_router(engine, not_ready))
    client = TestClient(app)
    assert client.post("/api/report-processing/runs", json={"asset_id": asset["asset_id"]}).status_code == 503
    assert client.get(f"/api/report-processing/runs/{state['processing_id']}").status_code == 200
    assert client.post(f"/api/report-processing/runs/{state['processing_id']}/cancel", json={}).status_code == 200
    assert client.post(f"/api/report-processing/runs/{state['processing_id']}/resume", json={}).status_code == 503
    assert store.list() == []


def test_running_child_cancel_is_observed_before_parent_claims_cancelled(tmp_path):
    engine, store, asset, calls, client = fixture(tmp_path)
    state = engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    engine.tick()
    child = store.list()[0]
    store.update(child.task_id, status=PipelineTaskStatus.RUNNING)
    engine.tick()
    cancelled = engine.action(state["processing_id"], "cancel")
    assert cancelled["status"] == "cancelling"
    assert store.get(child.task_id).cancel_requested
    assert "resume" not in {a["action"] for a in engine.detail(cancelled)["available_actions"]}
    engine.tick()
    assert engine.get(state["processing_id"])["status"] == "cancelling"
    assert calls == ["ocr"]
    store.update(child.task_id, status=PipelineTaskStatus.CANCELLED)
    engine.tick()
    stopped = engine.get(state["processing_id"])
    assert stopped["status"] == "cancelled"
    assert "resume" in {a["action"] for a in engine.detail(stopped)["available_actions"]}
    assert calls == ["ocr"]


def test_report_identity_code_and_external_references_are_additive(tmp_path):
    engine, store, asset, calls, _ = fixture(tmp_path)
    report = engine.register_asset(asset)
    report = engine.update_metadata(report["report_id"], ReportMetadataPatch(expected_revision=report["revision"],
        metadata=ReportMetadata(title="合成报告", publisher_entity_ref="org:synthetic", reporting_year=2025,
            report_kind="sustainability", source_system="CrawlESG", source_report_id="R-1", lei="L-1",
            source_package_sha256="a" * 64)))
    assert report["report_id"].startswith("report-")
    assert report["report_code"] == "org:synthetic/2025/sustainability"
    assert report["metadata"]["source_report_id"] == "R-1"
