"""Isolated synthetic workers: never read or replay a real report artifact."""
from copy import deepcopy
import json
import threading

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from esg_v2.api.production_automation import production_automation_router
from esg_v2.control_plane.production_automation import (
    AutomationRequest, AutomationCreateRequest, AutomationAction, AutomationOrder,
    ProductionAutomationCoordinator, readonly_asset,
)
from esg_v2.control_plane.shared_contracts import ReportExtractionRequest
from test_report_processing import fixture as processing_fixture, finish


class FakePlans:
    def __init__(self, store):
        self.store, self._lock, self._plans, self.created = store, threading.RLock(), [], 0

    def list(self):
        return deepcopy(self._plans)

    def create_report(self, request, document):
        self.created += 1
        plan = {"plan_id": f"extract-{self.created}", "status": "active", "report_request": request.model_dump(mode="json"),
                "documents": [{**document, "status": "pending", "steps": {}}]}
        self._plans.append(plan)
        return deepcopy(plan)

    def action(self, plan_id, action):
        plan = next(p for p in self._plans if p["plan_id"] == plan_id)
        plan["status"] = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
        return deepcopy(plan)

    def finish(self, status="completed"):
        self._plans[-1]["status"] = status
        self._plans[-1]["documents"][0]["status"] = status


def setup(tmp_path, count=2):
    processing, store, asset, calls, _ = processing_fixture(tmp_path)
    assets = [asset]
    for index in range(2, count + 1):
        (processing.assets.root / f"report{index}.pdf").write_bytes(f"%PDF synthetic batch report {index}".encode())
        assets.append(processing.assets.refresh()["assets"][-1])
    plans = FakePlans(store)
    deliveries = []
    standard_version = {"digest": "a" * 64}

    def preview(request):
        return {"standards": [{"package_id": "esrs.e1-6", "digest": standard_version["digest"]}],
            "runtime_standard_snapshots": {}, "selection_snapshot": None, "selected_outputs": [{"metric_id": "dp07", "original_datapoint_id": "E1-6_07"}],
            "internal_dependencies": []}

    def authorize(authorization, role):
        if authorization != "Bearer synthetic-runtime-only":
            raise HTTPException(401, "需要工作台操作员权限")
        return {"operator_ref": "synthetic-operator", "roles": ["admin"]}

    def deliver(session, report, revision, authorization):
        assert authorization == "Bearer synthetic-runtime-only"
        deliveries.append((session, report, revision))
        return {"status": "imported", "deliveries": [{"receipt_id": "synthetic-receipt", "status": "imported"}]}

    def coordinator():
        return ProductionAutomationCoordinator(store, processing, plans, extraction_preview=preview,
            extraction_detail=lambda plan: {"execution_status": plan["status"], "stages": [], "job_ids": []},
            deliver=deliver, authorize=authorize)

    engine = coordinator()
    app = FastAPI(); app.include_router(production_automation_router(engine))
    return engine, processing, store, assets, calls, plans, deliveries, standard_version, coordinator, TestClient(app)


AUTH = "Bearer synthetic-runtime-only"


def request(assets, target="processing", failure_policy="continue"):
    body = {"items": [{"asset_id": a["asset_id"], "source_sha256": a["sha256"]} for a in assets], "target": target, "failure_policy": failure_policy}
    if target != "processing":
        body["extraction"] = {"input": {"kind": "pdf", "asset_id": assets[0]["asset_id"]},
            "standards": [{"package_id": "esrs.e1-6", "package_version": "1.0.0", "metric_ids": ["dp07"]}]}
    return AutomationRequest.model_validate(body)


def create(engine, request, key="batch-one", start=True):
    preview = engine.preview(request)
    return engine.create(AutomationCreateRequest(request=request, preview_id=preview["preview_id"], submission_key=key, start=start), AUTH)


def finish_processing(engine, processing, store):
    processing.tick(); finish(processing, store, "ocr")
    processing.tick(); finish(processing, store, "ir")
    processing.tick(); engine.tick()


def test_preview_no_writes_no_queue_and_precise_selected_original_id(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path)
    before = processing.assets.catalog_path.read_bytes()
    preview = engine.preview(request(assets, "processing_extraction"))
    assert preview["can_submit"] and preview["queue_mutations"] == 0 and not preview["calls_model"]
    assert preview["items"][0]["selected_outputs"][0]["original_datapoint_id"] == "E1-6_07"
    assert calls == [] and store.list() == [] and engine.list() == [] and processing.list() == []
    assert processing.assets.catalog_path.read_bytes() == before


def test_batch_blocks_upfront_without_partial_enqueue(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path)
    req = request(assets)
    req.items[1].source_sha256 = "b" * 64
    preview = engine.preview(req)
    assert not preview["can_submit"] and preview["items"][1]["status"] == "blocked"
    with pytest.raises(ValueError, match="整批"):
        engine.create(AutomationCreateRequest(request=req, preview_id=preview["preview_id"], submission_key="bad"), AUTH)
    assert engine.list() == [] and store.list() == [] and calls == []


def test_frozen_standard_change_requires_new_preview(tmp_path):
    engine, processing, store, assets, calls, plans, _, standard, _, _ = setup(tmp_path)
    req = request(assets, "processing_extraction")
    old = engine.preview(req)
    standard["digest"] = "b" * 64
    with pytest.raises(ValueError, match="预览后"):
        engine.create(AutomationCreateRequest(request=req, preview_id=old["preview_id"], submission_key="stale"), AUTH)
    assert engine.list() == [] and plans.created == 0 and store.list() == []


def test_processing_fifo_uses_only_existing_unique_queue(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets))
    assert store.list() == []  # Deployment is metadata, never a parent worker slot.
    engine.tick(); processing.tick()
    assert calls == ["ocr"] and len(store.list()) == 1
    assert len(processing.list()) == 1
    finish(processing, store, "ocr"); processing.tick(); finish(processing, store, "ir"); processing.tick(); engine.tick()
    state = engine.get(state["automation_id"])
    assert state["items"][0]["status"] == "completed" and state["items"][1]["processing_id"] is None
    engine.tick(); processing.tick()
    assert calls == ["ocr", "ir", "ocr"]
    finish(processing, store, "ocr"); processing.tick(); finish(processing, store, "ir"); processing.tick(); engine.tick()
    assert engine.get(state["automation_id"])["status"] == "completed" and plans.created == 0


def test_submission_idempotency_and_conflicting_key(tmp_path):
    engine, _, _, assets, _, _, _, _, _, _ = setup(tmp_path)
    req = request(assets)
    a = create(engine, req); b = create(engine, req)
    assert a["automation_id"] == b["automation_id"] and len(engine.list()) == 1
    changed = req.model_copy(update={"name": "different request"})
    with pytest.raises(ValueError, match="相同提交键"):
        create(engine, changed)


def test_processing_create_checkpoint_gap_recovers_and_never_repeats(tmp_path):
    engine, processing, store, assets, calls, _, _, _, restart, _ = setup(tmp_path)
    state = create(engine, request(assets))
    engine.tick(); processing.tick()
    persisted = engine.get(state["automation_id"])
    persisted["items"][0].update(processing_id=None, status="pending", stage="pending")
    engine._save(persisted, "synthetic_gap")
    restarted = restart(); restarted.tick(); processing.tick()
    assert len(processing.list()) == 1 and calls == ["ocr"] and len(store.list()) == 1


def test_processing_then_extraction_holds_next_report_and_recovers_plan_gap(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, restart, _ = setup(tmp_path)
    state = create(engine, request(assets, "processing_extraction"))
    engine.tick(); finish_processing(engine, processing, store)
    assert plans.created == 1 and len(processing.list()) == 1
    persisted = engine.get(state["automation_id"])
    persisted["items"][0]["extraction_plan_id"] = None
    engine._save(persisted, "synthetic_extraction_gap")
    restarted = restart(); restarted.tick()
    assert plans.created == 1
    plans.finish(); restarted.tick(); restarted.tick()
    assert len(processing.list()) == 2 and plans.created == 1


def test_extraction_plan_pins_the_preparation_actually_used(tmp_path):
    """A foundation16 item recognises only candidate pages; its plan must say so."""
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    body = {"items": [{"asset_id": assets[0]["asset_id"], "source_sha256": assets[0]["sha256"]}],
        "target": "processing_extraction",
        "extraction": {"input": {"kind": "pdf", "asset_id": assets[0]["asset_id"]},
            "standards": [{"package_id": "esrs.e1-6", "package_version": "1.0.0", "metric_ids": ["dp07"]}],
            "extraction_route": "foundation16"}}
    state = create(engine, AutomationRequest.model_validate(body))
    engine.tick(); finish_processing(engine, processing, store)
    item = engine.get(state["automation_id"])["items"][0]
    assert item["processing_config"]["page_selection"] == "foundation16"
    assert plans.created == 1
    assert plans._plans[-1]["documents"][0]["processing_config"]["page_selection"] == "foundation16"
    assert plans._plans[-1]["documents"][0]["automation_key"] == state["automation_id"] + ":item-1"
    assert plans._plans[-1]["report_request"]["name"].startswith("批量报告生产 · ")
    assert "自动生产:" not in plans._plans[-1]["report_request"]["name"]


@pytest.mark.parametrize("policy, expected", [("continue", "active"), ("pause", "paused")])
def test_failed_item_policy_retains_actual_reason(tmp_path, policy, expected):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets, failure_policy=policy)); engine.tick(); processing.tick()
    finish(processing, store, "ocr", fail=True); processing.tick(); engine.tick()
    failed = engine.get(state["automation_id"])
    assert failed["status"] == expected and failed["items"][0]["status"] == "failed"
    assert failed["items"][0]["blockers"][0]["message"]
    engine.tick()
    assert len(processing.list()) == (2 if policy == "continue" else 1)


def test_pause_current_processing_finishes_without_next_stage_then_resume(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets, "processing_extraction")); engine.tick(); processing.tick()
    state = engine.get(state["automation_id"])
    engine.action(state["automation_id"], "pause", AutomationAction(expected_revision=state["revision"]), AUTH)
    engine.tick(); assert engine.get(state["automation_id"])["status"] == "pausing"
    finish(processing, store, "ocr"); processing.tick(); finish(processing, store, "ir"); processing.tick(); engine.tick()
    state = engine.get(state["automation_id"])
    assert state["status"] == "paused" and plans.created == 0 and len(processing.list()) == 1
    engine.action(state["automation_id"], "resume", AutomationAction(expected_revision=state["revision"]), AUTH)
    engine.tick(); assert plans.created == 1


def test_only_pending_items_reorder_with_optimistic_revision(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path, count=3)
    state = create(engine, request(assets)); engine.tick(); state = engine.get(state["automation_id"])
    order = AutomationOrder(expected_revision=state["revision"], item_ids=["item-3", "item-2"])
    updated = engine.reorder(state["automation_id"], order, AUTH)
    assert [i["item_id"] for i in updated["items"]] == ["item-1", "item-3", "item-2"]
    with pytest.raises(ValueError, match="状态已变化"):
        engine.reorder(state["automation_id"], order, AUTH)
    with pytest.raises(ValueError, match="尚未开始"):
        engine.reorder(state["automation_id"], AutomationOrder(expected_revision=updated["revision"], item_ids=["item-1", "item-2", "item-3"]), AUTH)


def test_cancel_waits_for_existing_children_without_new_enqueues(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets)); engine.tick(); processing.tick()
    state = engine.get(state["automation_id"])
    engine.action(state["automation_id"], "cancel", AutomationAction(expected_revision=state["revision"]), AUTH)
    processing.tick(); engine.tick()
    assert engine.get(state["automation_id"])["status"] == "cancelled"
    assert calls == ["ocr"] and len(processing.list()) == 1


def test_global_automation_submission_order(tmp_path):
    engine, processing, _, assets, _, _, _, _, _, _ = setup(tmp_path)
    older = create(engine, request(assets[:1]), "older")
    newer = create(engine, request(assets[1:]), "newer")
    engine.tick(); engine.tick()
    assert len(processing.list()) == 1
    assert engine.get(older["automation_id"])["items"][0]["processing_id"]
    assert engine.get(newer["automation_id"])["items"][0]["processing_id"] is None


def test_delivery_restart_requires_runtime_authorization_without_reprocessing(tmp_path):
    engine, processing, store, assets, calls, plans, deliveries, _, restart, _ = setup(tmp_path, 1)
    report = processing.register_asset(assets[0])
    from esg_v2.control_plane.report_processing import ReportMetadataPatch, ReportMetadata
    report = processing.update_metadata(report["report_id"], ReportMetadataPatch(expected_revision=report["revision"],
        metadata=ReportMetadata(title="Synthetic report", publisher_entity_ref="org:synthetic", period_start="2025-01-01", period_end="2025-12-31")))
    payload = request(assets, "processing_extraction").model_dump(mode="json")
    payload["target"] = "processing_extraction_delivery"
    payload["items"][0].update(report_id=report["report_id"], report_revision=report["revision"])
    req = AutomationRequest.model_validate(payload)
    state = create(engine, req); engine.tick(); finish_processing(engine, processing, store); plans.finish()
    restarted = restart(); restarted.tick()
    state = restarted.get(state["automation_id"])
    assert state["status"] == "waiting_credentials" and deliveries == []
    restarted.action(state["automation_id"], "resume", AutomationAction(expected_revision=state["revision"]), AUTH)
    restarted.tick(); done = restarted.get(state["automation_id"])
    assert done["status"] == "completed" and len(deliveries) == 1 and calls == ["ocr", "ir"] and plans.created == 1
    assert "synthetic-runtime-only" not in json.dumps(done)
    assert b"synthetic-runtime-only" not in store.path.read_bytes()


def test_metadata_correction_after_planning_still_delivers(tmp_path):
    """The frozen binding is the PDF; fixing a wrong entity ref must not block delivery."""
    from esg_v2.control_plane.report_processing import ReportMetadataPatch, ReportMetadata
    engine, processing, store, assets, calls, plans, deliveries, _, _, _ = setup(tmp_path, 1)
    report = processing.register_asset(assets[0])
    report = processing.update_metadata(report["report_id"], ReportMetadataPatch(expected_revision=report["revision"],
        metadata=ReportMetadata(title="Synthetic report", publisher_entity_ref="org:wrong", period_start="2025-01-01", period_end="2025-12-31")))
    payload = request(assets, "processing_extraction").model_dump(mode="json")
    payload["target"] = "processing_extraction_delivery"
    payload["items"][0].update(report_id=report["report_id"], report_revision=report["revision"])
    state = create(engine, AutomationRequest.model_validate(payload))
    engine.tick(); finish_processing(engine, processing, store); plans.finish()
    corrected = processing.update_metadata(report["report_id"], ReportMetadataPatch(expected_revision=report["revision"],
        metadata=ReportMetadata(title="Synthetic report", publisher_entity_ref="org:synthetic", period_start="2025-01-01", period_end="2025-12-31")))
    engine.tick()
    done = engine.get(state["automation_id"])
    assert done["items"][0]["status"] == "completed" and len(deliveries) == 1
    assert deliveries[0][2] == corrected["revision"] and calls == ["ocr", "ir"]


def test_credentialless_local_operator_is_held_and_rechecked_for_delivery(tmp_path):
    from esg_runtime.local_operator import install_loopback_marker, operator_headers
    engine, processing, store, assets, calls, plans, deliveries, _, _, _ = setup(tmp_path, 1)
    report = processing.register_asset(assets[0])
    from esg_v2.control_plane.report_processing import ReportMetadataPatch, ReportMetadata
    report = processing.update_metadata(report["report_id"], ReportMetadataPatch(expected_revision=report["revision"],
        metadata=ReportMetadata(title="Synthetic report", publisher_entity_ref="org:synthetic", period_start="2025-01-01", period_end="2025-12-31")))
    payload = request(assets, "processing_extraction").model_dump(mode="json")
    payload["target"] = "processing_extraction_delivery"
    payload["items"][0].update(report_id=report["report_id"], report_revision=report["revision"])
    req = AutomationRequest.model_validate(payload)
    checks = []

    def authorize(authorization, role):
        # Stands in for the publication service: only a credential-less call
        # made for this machine is the local operator.
        if operator_headers(authorization) != {}:
            raise HTTPException(401, "需要工作台操作员权限")
        checks.append(role)
        return {"operator_ref": "local-operator", "roles": ["read", "review", "admin"]}

    def deliver(session, report_id, revision, authorization):
        assert authorization is None and operator_headers(None) == {}
        deliveries.append((session, report_id, revision))
        return {"status": "imported", "deliveries": [{"receipt_id": "synthetic-receipt", "status": "imported"}]}

    engine.authorize, engine.deliver = authorize, deliver
    app = FastAPI(); install_loopback_marker(app); app.include_router(production_automation_router(engine))
    local, remote = TestClient(app, client=("127.0.0.1", 50000)), TestClient(app, client=("192.0.2.10", 50000))
    preview = local.post("/api/production-automations/preview", json=req.model_dump(mode="json")).json()
    body = {"request": req.model_dump(mode="json"), "preview_id": preview["preview_id"], "submission_key": "local-batch", "start": True}
    assert remote.post("/api/production-automations", json=body).status_code == 401
    created = local.post("/api/production-automations", json=body)
    assert created.status_code == 202
    engine.tick(); finish_processing(engine, processing, store); plans.finish()
    engine.tick()  # outside any request: the held stand-in acts and is checked again
    done = engine.get(created.json()["automation_id"])
    assert done["status"] == "completed" and len(deliveries) == 1 and checks[-1] == "review"


def test_local_only_contract_rejects_cloud_or_any_secret(tmp_path):
    _, _, _, assets, _, _, _, _, _, _ = setup(tmp_path)
    req = request(assets, "processing_extraction").model_dump(mode="json")
    req["extraction"]["qiniu_api_key"] = "should-not-persist"
    with pytest.raises(ValueError, match="凭据"):
        AutomationRequest.model_validate(req)
    req = request(assets).model_dump(mode="json"); req["processing"]["config"]["allow_cloud_ocr"] = True
    with pytest.raises(ValueError, match="本地"):
        AutomationRequest.model_validate(req)


def test_authenticated_api_unknown_ids_and_read_have_no_dispatch(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, client = setup(tmp_path)
    assert client.get("/api/production-automations").status_code == 401
    headers = {"Authorization": AUTH}
    assert client.get("/api/production-automations", headers=headers).json()["plans"] == []
    assert client.get("/api/production-automations/unknown", headers=headers).status_code == 404
    req = request(assets).model_dump(mode="json")
    preview = client.post("/api/production-automations/preview", json=req, headers=headers).json()
    response = client.post("/api/production-automations", json={"request": req, "preview_id": preview["preview_id"], "submission_key": "api-only", "start": False}, headers=headers)
    assert response.status_code == 202 and response.json()["status"] == "planned"
    assert calls == [] and store.list() == [] and processing.list() == []
    assert client.get("/api/production-automations/" + response.json()["automation_id"], headers=headers).json()["status_label"] == "待部署"


def test_engine_change_before_advance_fails_without_queue_creation(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets)); processing.engine_versions = {"ocr": "changed", "ir": "changed"}
    engine.tick(); state = engine.get(state["automation_id"])
    assert state["items"][0]["status"] == "failed" and "构建版本" in state["items"][0]["message"]
    assert calls == [] and store.list() == []


def test_standard_change_after_processing_keeps_artifact_and_blocks_extraction(tmp_path):
    engine, processing, store, assets, calls, plans, _, standard, _, _ = setup(tmp_path, 1)
    state = create(engine, request(assets, "processing_extraction")); engine.tick()
    standard["digest"] = "b" * 64
    finish_processing(engine, processing, store)
    state = engine.get(state["automation_id"])
    assert state["status"] == "partial" and state["items"][0]["status"] == "failed"
    assert "禁止自动换版" in state["items"][0]["message"] and plans.created == 0
    assert processing.list()[0]["status"] == "ready" and calls == ["ocr", "ir"]


def test_archived_frozen_asset_is_still_processed_but_not_newly_selected(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path, 1)
    state = create(engine, request(assets))
    processing.assets.lifecycle(assets[0]["asset_id"], "archive", reason="synthetic archive")
    assert not engine.preview(request(assets))["can_submit"]
    engine.tick(); processing.tick()
    assert len(processing.list()) == 1 and calls == ["ocr"]


def test_new_runtime_dependency_unavailable_pauses_without_failing_remaining_items(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets))
    def unavailable(target):
        raise HTTPException(503, {"code": "runtime_requires_restart", "message": "服务需要重启"})
    engine.require_ready = unavailable
    engine.tick(); state = engine.get(state["automation_id"])
    assert state["status"] == "paused" and all(i["status"] == "pending" for i in state["items"])
    assert calls == [] and store.list() == []


def test_sql_lease_prevents_second_coordinator_dispatch(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, restart, _ = setup(tmp_path)
    state = create(engine, request(assets))
    assert engine._lease()
    restarted = restart(); restarted.tick()
    assert processing.list() == []
    engine._release_lease(); restarted.tick()
    assert len(processing.list()) == 1


def test_rebuild_ir_preview_requires_compatible_ocr_before_deployment(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    req = request(assets); req.processing.mode = "rebuild_ir"
    preview = engine.preview(req)
    assert not preview["can_submit"]
    assert all("兼容的识别" in i["blockers"][0]["message"] for i in preview["items"])
    assert processing.list() == [] and calls == [] and engine.list() == []


def test_readonly_tick_progress_does_not_increment_revision_when_unchanged(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets)); engine.tick(); engine.tick()
    before = engine.get(state["automation_id"])
    engine.tick(); after = engine.get(state["automation_id"])
    assert before["revision"] == after["revision"] and before["updated_at"] == after["updated_at"]


def test_running_batch_is_not_starved_by_ui_recency_limit(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path, 1)
    older = create(engine, request(assets), "real-oldest")
    template = engine.get(older["automation_id"])
    with store._connect() as db:
        for index in range(501):
            newer = deepcopy(template)
            newer.update(automation_id=f"synthetic-inactive-{index}", status="planned")
            db.execute("INSERT INTO production_automations VALUES (?,?,?,?,?)", (newer["automation_id"], f"inactive-{index}", "synthetic", 1, json.dumps(newer)))
    assert older["automation_id"] not in {p["automation_id"] for p in engine.list()}
    engine.tick()
    assert engine.get(older["automation_id"])["items"][0]["processing_id"] and len(processing.list()) == 1


def test_pause_after_extraction_creation_prevents_next_report_until_resumed(tmp_path):
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    state = create(engine, request(assets, "processing_extraction")); engine.tick(); finish_processing(engine, processing, store)
    state = engine.get(state["automation_id"])
    engine.action(state["automation_id"], "pause", AutomationAction(expected_revision=state["revision"]), AUTH)
    engine.tick(); state = engine.get(state["automation_id"])
    assert state["status"] == "paused" and plans.list()[0]["status"] == "paused" and len(processing.list()) == 1
    engine.action(state["automation_id"], "resume", AutomationAction(expected_revision=state["revision"]), AUTH)
    assert plans.list()[0]["status"] == "active"
    engine.tick(); assert len(processing.list()) == 1
    plans.finish(); engine.tick(); engine.tick(); assert len(processing.list()) == 2


def test_concurrent_idempotent_submissions_return_same_parent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    engine, processing, store, assets, calls, plans, _, _, _, _ = setup(tmp_path)
    req = request(assets); preview = engine.preview(req)
    submit = AutomationCreateRequest(request=req, preview_id=preview["preview_id"], submission_key="parallel-once")
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(lambda _: engine.create(submit, AUTH), range(4)))
    assert len({s["automation_id"] for s in states}) == 1 and len(engine.list()) == 1
    assert processing.list() == [] and store.list() == [] and calls == []


def test_terminal_archive_restore_preserves_children_sources_and_result_status(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path, 1)
    state = create(engine, request(assets)); engine.tick(); finish_processing(engine, processing, store)
    state = engine.get(state["automation_id"])
    assert state["status"] == "completed"
    with store._connect() as db:
        children_before = [dict(row) for row in db.execute("SELECT * FROM pipeline_tasks ORDER BY task_id")]
    processing_before = processing.list(True)
    source_before = {str(p): p.read_bytes() for root in (processing.assets.root, *processing.roots.values()) for p in root.rglob("*") if p.is_file()}
    archived = engine.action(state["automation_id"], "archive", AutomationAction(expected_revision=state["revision"], reason="已完成的合成批次整理"), AUTH)
    assert archived["status"] == "completed" and archived["archived_at"] and archived["lifecycle"]["state"] == "archived"
    assert archived["archive_scope"] == {"parent_record_only": True, "child_records_retained": True, "source_artifacts_retained": True, "permanent_deletion": False}
    assert [a["action"] for a in archived["available_actions"]] == ["restore"]
    assert engine.list() == [] and len(engine.list(True)) == 1
    assert engine.get(state["automation_id"])["items"] == state["items"]
    with pytest.raises(ValueError, match="归档状态已变化"):
        engine.action(state["automation_id"], "archive", AutomationAction(expected_revision=archived["revision"]), AUTH)
    restored = engine.action(state["automation_id"], "restore", AutomationAction(expected_revision=archived["revision"], reason="恢复合成计划"), AUTH)
    assert restored["status"] == "completed" and restored["archived_at"] is None and restored["lifecycle"]["state"] == "active"
    assert restored["lifecycle"]["revision"] == restored["revision"] and len(engine.list()) == 1
    with store._connect() as db:
        assert children_before == [dict(row) for row in db.execute("SELECT * FROM pipeline_tasks ORDER BY task_id")]
    assert processing_before == processing.list(True)
    assert source_before == {str(p): p.read_bytes() for root in (processing.assets.root, *processing.roots.values()) for p in root.rglob("*") if p.is_file()}
    assert calls == ["ocr", "ir"]


def test_only_terminal_parent_can_archive_with_revision_and_no_run_actions(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, _ = setup(tmp_path, 1)
    state = create(engine, request(assets), start=False)
    with pytest.raises(ValueError, match="先取消"):
        engine.action(state["automation_id"], "archive", AutomationAction(expected_revision=state["revision"]), AUTH)
    state = engine.action(state["automation_id"], "cancel", AutomationAction(expected_revision=state["revision"]), AUTH)
    engine.tick(); state = engine.get(state["automation_id"])
    assert state["status"] == "cancelled"
    with pytest.raises(ValueError, match="状态已变化"):
        engine.action(state["automation_id"], "archive", AutomationAction(expected_revision=state["revision"] - 1), AUTH)
    archived = engine.action(state["automation_id"], "archive", AutomationAction(expected_revision=state["revision"]), AUTH)
    with pytest.raises(ValueError, match="只能恢复记录"):
        engine.action(state["automation_id"], "resume", AutomationAction(expected_revision=archived["revision"]), AUTH)
    assert store.list() == [] and processing.list() == [] and calls == []


def test_archive_api_list_toggle_and_auth_do_not_dispatch(tmp_path):
    engine, processing, store, assets, calls, _, _, _, _, client = setup(tmp_path, 1)
    state = create(engine, request(assets), start=False)
    state = engine.action(state["automation_id"], "cancel", AutomationAction(expected_revision=state["revision"]), AUTH)
    engine.tick(); state = engine.get(state["automation_id"])
    path = "/api/production-automations/" + state["automation_id"]
    body = {"expected_revision": state["revision"], "reason": "合成 API 整理"}
    assert client.post(path + "/archive", json=body).status_code == 401
    headers = {"Authorization": AUTH}
    archived = client.post(path + "/archive", json=body, headers=headers)
    assert archived.status_code == 200 and archived.json()["status"] == "cancelled"
    assert client.get("/api/production-automations", headers=headers).json()["plans"] == []
    assert len(client.get("/api/production-automations?include_archived=true", headers=headers).json()["plans"]) == 1
    assert client.get(path, headers=headers).json()["lifecycle"]["state"] == "archived"
    restored = client.post(path + "/restore", json={"expected_revision": archived.json()["revision"], "reason": "合成 API 恢复"}, headers=headers)
    assert restored.status_code == 200 and restored.json()["status"] == "cancelled"
    assert store.list() == [] and processing.list() == [] and calls == []
