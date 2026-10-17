"""Temporary SQLite owners only: no live task, model, report or dataset."""
import json
import threading
from types import SimpleNamespace

import pytest
from esg_runtime.lifecycle import LifecycleBlocked
from esg_v2.control_plane.contracts import PipelineTaskType
from esg_v2.control_plane.store import PipelineQueueStore
from esg_v2.control_plane.record_deletion import ControlPlaneRecordDeletion, detach_terminal_library_records
from esg_v2.api.record_deletion import record_deletion_router
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def fixture(tmp_path):
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    with store._connect() as db:
        db.executescript("""
            CREATE TABLE report_processing_runs(processing_id TEXT PRIMARY KEY,status TEXT,state_json TEXT);
            CREATE TABLE report_processing_events(processing_id TEXT,state_json TEXT);
            CREATE TABLE report_processing_artifacts(processing_id TEXT,run_id TEXT);
            CREATE TABLE pipeline_plans(plan_id TEXT PRIMARY KEY,state_json TEXT);
            CREATE TABLE pipeline_plan_events(plan_id TEXT,detail_json TEXT);
        """)
    processing = SimpleNamespace(_lock=threading.RLock(), _secrets={})
    plans = SimpleNamespace(_lock=threading.RLock(), _secrets={})
    calls = []
    def cancel(identity, action, request=None):
        calls.append(identity)
        with store._connect() as db:
            row = db.execute("SELECT state_json FROM report_processing_runs WHERE processing_id=?", (identity,)).fetchone()
            state = json.loads(row[0]); state.update(status="cancelled", revision=state["revision"]+1)
            db.execute("UPDATE report_processing_runs SET status='cancelled',state_json=? WHERE processing_id=?", (json.dumps(state), identity))
            for step in state["steps"].values():
                if step.get("task_id"): db.execute("UPDATE pipeline_tasks SET status='cancelled' WHERE task_id=?", (step["task_id"],))
    processing.action = cancel
    plans.action = lambda *args: calls.append(args)
    def native(method, path, payload=None, **kwargs):
        calls.append((method, path))
        return {"available_actions": [{"payload": {"expected_revision": 1, "identity_sha256": "a"*64}}]} if method == "GET" else {"deleted": True, "source_artifacts_preserved": True}
    service = ControlPlaneRecordDeletion(store, processing, plans, native, lambda identity: store.request_cancel(identity))
    return service, store, calls


def add_processing(store, *, status="ready", identity="processing-synthetic", task="pt-synthetic"):
    store.create(task_id=task, task_type=PipelineTaskType.OCR, operation="ocr", title="合成任务",
        payload={"_plan_step_key": identity+":ocr:0"}, native_job_id="ocr-synthetic")
    store.update(task, status="running" if status=="running" else "completed")
    state = {"processing_id": identity, "status": status, "revision": 1, "created_at": "synthetic-time",
        "asset_id": "pdf-synthetic", "source_sha256": "a"*64, "steps": {"ocr": {"task_id": task, "status": "completed", "run_id": "ocr-synthetic"}}}
    with store._connect() as db:
        db.execute("INSERT INTO report_processing_runs VALUES(?,?,?)", (identity, status, json.dumps(state)))
        db.execute("INSERT INTO report_processing_events VALUES(?,?)", (identity, "{}"))
        db.execute("INSERT INTO report_processing_artifacts VALUES(?,?)", (identity, "ocr-synthetic"))
    store.pin_ir(identity, "ir-synthetic"); store.pin_ir(task, "ir-synthetic"); store.pin_ir("other-owner", "ir-synthetic")
    return state


def test_terminal_processing_sql_events_and_own_pins_are_really_deleted(tmp_path):
    service, store, calls = fixture(tmp_path); state = add_processing(store)
    source = tmp_path/"shared-evidence.json"; source.write_text("preserve source bytes")
    result = service.request("processing", state["processing_id"], **service.action("processing", state)["payload"])
    assert result["deleted"] is True and result["status"] == "deleted"
    assert calls == [] and source.read_text() == "preserve source bytes"
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM report_processing_runs").fetchone()
        assert not db.execute("SELECT 1 FROM report_processing_events").fetchone()
        assert not db.execute("SELECT 1 FROM pipeline_tasks").fetchone()
        assert [row[0] for row in db.execute("SELECT owner_id FROM ir_input_pins")] == ["other-owner"]


def test_active_request_cancels_and_deferred_tick_deletes_after_stop(tmp_path):
    service, store, calls = fixture(tmp_path); state = add_processing(store, status="running")
    result = service.request("processing", state["processing_id"], **service.action("processing", state)["payload"])
    assert result["deleted"] is False and result["status"] == "deletion_pending"
    assert calls == [state["processing_id"]]
    # New service recovers the durable intent without the old browser/session.
    restarted = ControlPlaneRecordDeletion(store, service.processing, service.plans, service.native_request, service.cancel_task)
    restarted.tick()
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM report_processing_runs").fetchone()
        assert not db.execute("SELECT 1 FROM record_deletion_requests").fetchone()


def test_stale_identity_never_cancels_or_deletes(tmp_path):
    service, store, calls = fixture(tmp_path); state = add_processing(store)
    with pytest.raises(LifecycleBlocked, match="变化"):
        service.request("processing", state["processing_id"], expected_revision=1, identity_sha256="f"*64)
    assert calls == [] and store.get("pt-synthetic")


def test_owned_terminal_queue_record_deletion_prunes_pointer_keeps_completed_stage(tmp_path):
    service, store, calls = fixture(tmp_path); state = add_processing(store)
    with store._connect() as db: row, _ = service._state("queue", "pt-synthetic", db)
    result = service.request("queue", "pt-synthetic", **service.action("queue", row)["payload"])
    assert result["deleted"] is True
    with store._connect() as db:
        parent = json.loads(db.execute("SELECT state_json FROM report_processing_runs").fetchone()[0])
    assert parent["steps"]["ocr"]["queue_record_deleted"] is True
    assert parent["steps"]["ocr"]["status"] == "completed" and "task_id" not in parent["steps"]["ocr"]


def test_owner_cascade_also_prunes_legacy_references_from_other_parents(tmp_path):
    service,store,calls=fixture(tmp_path);state=add_processing(store)
    other={'plan_id':'plan-other','status':'completed','documents':[{'status':'completed','steps':{
        'ocr':{'task_id':'pt-synthetic','status':'completed','run_id':'ocr-synthetic',
               'history':['pt-synthetic',{'task_id':'pt-synthetic'},None]}}}]}
    with store._connect() as db:db.execute('INSERT INTO pipeline_plans VALUES(?,?)',(other['plan_id'],json.dumps(other)))
    assert service.request('processing',state['processing_id'],**service.action('processing',state)['payload'])['deleted'] is True
    with store._connect() as db:
        remaining=json.loads(db.execute('SELECT state_json FROM pipeline_plans').fetchone()[0])
    step=remaining['documents'][0]['steps']['ocr']
    assert 'task_id' not in step and step['history']==[None]
    assert step['run_id']=='ocr-synthetic' and step['queue_record_deleted'] is True


def test_deleting_failed_queue_stops_parent_instead_of_silently_requeuing(tmp_path):
    service,store,calls=fixture(tmp_path);state=add_processing(store)
    store.update('pt-synthetic',status='failed')
    with store._connect() as db:row,_=service._state('queue','pt-synthetic',db)
    assert service.request('queue','pt-synthetic',**service.action('queue',row)['payload'])['deleted'] is True
    with store._connect() as db:
        status,raw=db.execute('SELECT status,state_json FROM report_processing_runs').fetchone()
    parent=json.loads(raw)
    assert status==parent['status']=='interrupted'
    assert parent['steps']['ocr']['requires_new_task'] is True
    assert store.list()==[]


def test_session_delete_uses_native_owner_then_physically_removes_facade(tmp_path):
    service, store, calls = fixture(tmp_path)
    state = {"plan_id": "plan-synthetic", "status": "partial", "created_at": "synthetic-time", "documents": [{"native_session_id": "report-synthetic", "steps": {}}]}
    with store._connect() as db: db.execute("INSERT INTO pipeline_plans VALUES(?,?)", (state["plan_id"], json.dumps(state)))
    result = service.request("session", state["plan_id"], **service.action("session", state)["payload"], authorization="Bearer synthetic")
    assert result["deleted"] is True and result["source_artifacts_preserved"] is True
    assert calls == [("GET", "/api/report-sessions/report-synthetic/deletion"), ("DELETE", "/api/report-sessions/report-synthetic")]
    with store._connect() as db: assert not db.execute("SELECT 1 FROM pipeline_plans").fetchone()


def test_library_detach_only_selected_document_preserves_other_dispatch_index(tmp_path):
    service, store, calls = fixture(tmp_path)
    docs = []
    for index in range(2):
        identity = f"pt-{index}"
        store.create(task_id=identity, task_type=PipelineTaskType.OCR, operation="ocr", title="合成任务",
            payload={"_plan_step_key": f"plan-batch:{index}:ocr:0", "asset_id": f"pdf-{index}"}, native_job_id=f"ocr-{index}")
        store.update(identity, status="completed")
        docs.append({"asset_id": f"pdf-{index}", "status": "completed", "ocr_run_id": f"ocr-{index}", "ir_run_id": f"ir-{index}", "steps": {"ocr": {"task_id": identity}}})
    state = {"plan_id": "plan-batch", "status": "completed", "documents": docs}
    with store._connect() as db:
        db.execute("INSERT INTO pipeline_plans VALUES(?,?)", (state["plan_id"], json.dumps(state)))
        db.execute("BEGIN IMMEDIATE") if not db.in_transaction else None
        result = detach_terminal_library_records(db, run_ids={"ocr-0"})
    with store._connect() as db:
        parent = json.loads(db.execute("SELECT state_json FROM pipeline_plans").fetchone()[0])
    assert result["task_ids"] == ["pt-0"]
    assert parent["documents"] == [{**docs[1], "dispatch_document_index": 1}]
    assert store.get("pt-1") and calls == []


def test_library_detach_waits_for_active_owner(tmp_path):
    service, store, calls = fixture(tmp_path); add_processing(store, status="running")
    with store._connect() as db:
        with pytest.raises(LifecycleBlocked): detach_terminal_library_records(db, asset_id="pdf-synthetic")
    assert store.get("pt-synthetic")


def test_delete_http_uses_admin_and_returns_real_json_deletion_result(tmp_path):
    service, store, calls = fixture(tmp_path); state = add_processing(store)
    def authorize(value, role):
        if value != "Bearer synthetic" or role != "admin": raise HTTPException(401, "synthetic authorization required")
    app = FastAPI(); app.include_router(record_deletion_router(service, authorize)); client = TestClient(app)
    path = "/api/report-processing/runs/" + state["processing_id"]
    payload = service.action("processing", state)["payload"]
    assert client.request("DELETE", path, json=payload).status_code == 401
    assert client.request("DELETE", path, headers={"Authorization": "Bearer synthetic"}, json={**payload, "expected_revision": True}).status_code == 422
    result = client.request("DELETE", path, headers={"Authorization": "Bearer synthetic"}, json=payload)
    assert result.status_code == 200 and result.json()["deleted"] is True and result.json()["status"] == "deleted"


def test_delete_http_waiting_is_202_and_not_a_false_deleted_success(tmp_path):
    service, store, calls = fixture(tmp_path); state = add_processing(store, status="running")
    app = FastAPI(); app.include_router(record_deletion_router(service, lambda *_: None)); client = TestClient(app)
    result = client.request("DELETE", "/api/report-processing/runs/" + state["processing_id"], json=service.action("processing", state)["payload"])
    assert result.status_code == 202 and result.json()["deleted"] is False and result.json()["status"] == "deletion_pending"


def test_native_pending_deletion_after_restart_uses_local_server_authorization(tmp_path):
    service, store, calls = fixture(tmp_path)
    state = {"plan_id": "plan-pending", "status": "cancelled", "created_at": "frozen-identity",
        "documents": [{"native_session_id": "report-synthetic", "steps": {}}]}
    with store._connect() as db:
        db.execute("INSERT INTO pipeline_plans VALUES(?,?)", (state["plan_id"], json.dumps(state)))
        db.execute("INSERT INTO record_deletion_requests VALUES(?,?,?,?,?,?,?)", ("session", state["plan_id"], "frozen-identity", "pending", "request-time", "request-time", None))
    authorizations = []
    def native(method, path, payload=None, *, authorization=None):
        authorizations.append(authorization)
        return {"available_actions": [{"payload": {"expected_revision": 1, "identity_sha256": "a"*64}}]} if method == "GET" else {"deleted": True}
    restarted = ControlPlaneRecordDeletion(store, service.processing, service.plans, native, service.cancel_task,
        local_authorization=lambda: "Bearer synthetic-after-restart")
    assert restarted._authorization == {}
    restarted.tick()
    assert authorizations == ["Bearer synthetic-after-restart", "Bearer synthetic-after-restart"]
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM pipeline_plans").fetchone()
        assert not db.execute("SELECT 1 FROM record_deletion_requests").fetchone()
