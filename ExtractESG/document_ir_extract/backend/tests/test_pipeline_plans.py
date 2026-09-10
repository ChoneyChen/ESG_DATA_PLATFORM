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
    assert store.clear_history() == []  # A plan's dependency journal is retained.


def test_enqueue_checkpoint_gap_does_not_duplicate_work(tmp_path):
    store, engine, _, calls = setup_plan(tmp_path)
    engine.tick()
    state = engine.list()[0]
    state["documents"][0]["steps"] = {}
    engine.save(state)
    engine.tick()
    assert calls == ["ocr"]


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
