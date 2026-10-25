import json

from esg_v2.control_plane.contracts import PipelineTaskType, PipelineTaskStatus
from esg_v2.control_plane.plans import PipelinePlanCoordinator, PipelinePlanRequest, dispatch_key
from esg_v2.control_plane.store import PipelineQueueStore


def setup_plan(tmp_path, count=1):
    store = PipelineQueueStore(tmp_path / "queue.sqlite")
    calls = []
    def dispatch(name, config, doc, secrets):
        calls.append(name)
        tid = f"pt-{len(calls)}"
        return store.create(task_id=tid, task_type=PipelineTaskType.OCR, operation=name, title=name,
                            payload={"_plan_step_key":dispatch_key.get()}, native_job_id=f"run-{len(calls)}")
    inspect = lambda run: {"run_id":run, "can_build_evidence":True}
    engine = PipelinePlanCoordinator(store, dispatch, inspect, store.request_cancel)
    req = PipelinePlanRequest(asset_ids=[f"pdf-{i}" for i in range(count)], standards=[{"package_id":p,"package_version":"1.2.0","metric_ids":[f"{p}.dp09"]} for p in ("e1-5","e1-6")], qiniu_api_key="secret-not-persisted")
    plan = engine.create(req, [{"asset_id":a,"file_name":a + ".pdf"} for a in req.asset_ids])
    return store, engine, plan, calls


def complete_pending(store):
    for task in store.list():
        if task.status == PipelineTaskStatus.QUEUED:
            store.update(task.task_id, status=PipelineTaskStatus.COMPLETED)


def test_plan_dependencies_restart_and_multi_standard_completion(tmp_path):
    store, engine, plan, calls = setup_plan(tmp_path)
    engine.tick()
    assert calls == ["ocr"]
    engine = PipelinePlanCoordinator(store, engine.dispatch, engine.inspect_ir, engine.cancel)
    engine.tick()
    assert calls == ["ocr"]
    complete_pending(store)
    engine.tick()
    assert calls == ["ocr", "ir"]
    complete_pending(store)
    engine.tick()
    assert calls == ["ocr", "ir", "targeted:e1-5", "targeted:e1-6"]
    complete_pending(store)
    engine.tick()
    state = engine.list()[0]
    assert state["status"] == "completed"
    assert "secret-not-persisted" not in json.dumps(state)
    assert set(store.clear_history()) == {'pt-1', 'pt-2', 'pt-3', 'pt-4'}
    assert store.list() == []
    assert store.get('pt-1').archived_at  # The dependency journal remains readable.
    engine.tick()
    assert calls == ["ocr", "ir", "targeted:e1-5", "targeted:e1-6"]


def test_enqueue_checkpoint_gap_does_not_duplicate_work(tmp_path):
    store, engine, _, calls = setup_plan(tmp_path)
    engine.tick()
    state = engine.list()[0]
    state["documents"][0]["steps"] = {}
    engine.save(state)
    engine.tick()
    assert calls == ["ocr"]


def test_plan_cancel_waits_for_running_child_exit_and_never_dispatches_next(tmp_path):
    store,engine,plan,calls=setup_plan(tmp_path)
    engine.tick()
    store.update('pt-1',status=PipelineTaskStatus.RUNNING)
    pending=engine.action(plan['plan_id'],'cancel')
    assert pending['status']=='cancelling'
    assert store.get('pt-1').cancel_requested
    engine.tick()
    assert engine.list()[0]['status']=='cancelling' and calls==['ocr']
    store.update('pt-1',status=PipelineTaskStatus.CANCELLED)
    engine.tick()
    assert engine.list()[0]['status']=='cancelled' and calls==['ocr']


def test_one_document_failure_does_not_block_another_and_pause_stops_dispatch(tmp_path):
    store, engine, plan, calls = setup_plan(tmp_path, 2)
    engine.tick()
    store.update("pt-1", status=PipelineTaskStatus.FAILED, error="fixture failed")
    store.update("pt-2", status=PipelineTaskStatus.COMPLETED)
    engine.tick()
    assert calls == ["ocr", "ocr", "ir"]
    state = engine.action(plan["plan_id"], "pause")
    complete_pending(store)
    engine.tick()
    assert len(calls) == 3
    engine.action(plan["plan_id"], "resume")
    engine.tick()
    assert calls.count("ocr") == 3  # Only the failed OCR is retried.
    assert calls.count("ir") == 1


def test_limited_evidence_policy_is_explicit(tmp_path):
    store, engine, _, _ = setup_plan(tmp_path)
    state = engine.list()[0]
    state["documents"][0].update(ocr_run_id="ocr-existing", ir_run_id="ir-existing")
    state["config"]["allow_limited_evidence"] = False
    engine.save(state)
    engine.inspect_ir = lambda _: {"can_build_limited_evidence":True,"evidence_policy":{"mode":"limited"}}
    engine.tick()
    assert engine.list()[0]["status"] == "needs_attention"
    assert not store.list()


def test_resumed_plan_adopts_a_finished_session_instead_of_rerunning_it(tmp_path):
    store = PipelineQueueStore(tmp_path / "queue.sqlite")
    calls = []
    engine = PipelinePlanCoordinator(store, lambda *args: calls.append(args), lambda run: {"can_build_evidence": True},
        store.request_cancel, inspect_session=lambda plan, document: {
            "session_id": "report-finished", "execution_status": "finished", "result_status": "partial",
            "jobs": [{"status": "completed"}, {"status": "completed"}]})
    document = {"native_session_id": "report-finished", "steps": {}}
    plan = {"plan_id": "plan-resume", "config": {}, "documents": [document]}
    assert engine._step(plan, 0, document, "report_session") == "report-finished"
    assert calls == [] and document["steps"]["report_session"]["status"] == "partial"
    assert document["steps"]["report_session"]["message"] == "复用已完成的报告会话"


def test_terminal_report_plan_records_automatic_handoff_and_review(tmp_path):
    store, engine, _, _ = setup_plan(tmp_path)
    engine.on_report_handoff = lambda plan, document: {"status": "imported", "stage": "review",
        "report_id": "report-x", "message": "已自动交付到候选审核",
        "auto_review": {"approved": 3, "needs_human": 1, "rule_version": "v3"}}
    state = {"schema_version": "pipeline-plan-v1", "plan_id": "plan-x", "name": "x", "status": "partial",
             "created_at": "now", "config": {"report_request": {"name": "x"}},
             "documents": [{"status": "partial", "steps": {}}]}
    engine._maybe_handoff(state)
    record = state["documents"][0]["auto_handoff"]
    assert record["status"] == "imported" and record["stage"] == "review"
    assert record["auto_review"] == {"approved": 3, "needs_human": 1, "rule_version": "v3"}
    assert record["attempts"] == 1
    engine._maybe_handoff(state)
    assert state["documents"][0]["auto_handoff"]["attempts"] == 1


def test_deferred_handoff_does_not_consume_attempts(tmp_path):
    store, engine, _, _ = setup_plan(tmp_path)
    engine.on_report_handoff = lambda plan, document: {"status": "deferred", "stage": "automation"}
    state = {"schema_version": "pipeline-plan-v1", "plan_id": "plan-y", "name": "y", "status": "partial",
             "created_at": "now", "config": {"report_request": {"name": "y"}},
             "documents": [{"status": "partial", "steps": {}}]}
    engine._maybe_handoff(state)
    record = state["documents"][0]["auto_handoff"]
    assert record["status"] == "deferred" and record["attempts"] == 0
