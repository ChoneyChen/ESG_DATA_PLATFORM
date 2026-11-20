from types import SimpleNamespace
import pytest
from esg_pipeline.contracts import PipelineTaskType
from esg_pipeline.extraction import ExtractionCoordinator,ExtractionServiceUnavailable
from esg_pipeline.store import PipelineQueueStore
from esg_contracts.sessions import ExtractionBatchRequest, ExtractionPlanRequest

@pytest.fixture
def coordinator(tmp_path):
    store=PipelineQueueStore(tmp_path/'state.db')
    processing=SimpleNamespace(get=lambda key:{'processing_id':key,'status':'running','steps':{'ocr':{'status':'running','progress':{'current':2,'total':8,'unit':'pages'}}}})
    return ExtractionCoordinator(store,processing,lambda *a:None,lambda key:None,'http://127.0.0.1:18180')

def state():
    return {'session_id':'plan','name':'test','status':'running','stage':'extracting',
        'request':{'input':{'kind':'document_ir','ir_run_id':'ir'},'clause_keys':['TEST_01']},
        'run_id':'run','queue_task_id':'queue','target_snapshot':{'clauses':{'TEST_01':{}},'digest':'a'*64}}

def test_timeout_preserves_running_and_distinguishes_observation(coordinator):
    coordinator.remote=lambda *a:(_ for _ in ()).throw(ExtractionServiceUnavailable('temporary'))
    result=coordinator.detail(state())
    assert result['status']=='running' and result['observation']=='unavailable'
    assert result['clause_keys']==['TEST_01']

def test_terminal_phase_and_model_state_are_authoritative(coordinator):
    coordinator.remote=lambda *a:{'run_id':'run','status':'partial','progress':{'stage':'reading'},'current_call':{'phase':'generation'}}
    result=coordinator.detail(state())
    assert result['current_phase']=='finished' and result['run']['current_call'] is None
    assert result['run']['last_call']['phase']=='generation'

def test_cancel_waits_for_confirmed_exit_and_still_stops_queue_if_api_unavailable(coordinator):
    stopped=[];coordinator.cancel_task=stopped.append
    coordinator.save(state())
    coordinator.remote=lambda *a:(_ for _ in ()).throw(ExtractionServiceUnavailable('temporary'))
    result=coordinator.cancel('plan')
    assert result['status']=='cancelling' and stopped==['queue']
    coordinator.remote=lambda *a:{'run_id':'run','status':'cancelled'}
    coordinator.tick(coordinator.get('plan'))
    assert coordinator.get('plan')['status']=='cancelled'

def test_preparation_progress_is_projected_without_inventing_percent(coordinator):
    current=state();current.update(run_id=None,processing_id='prep',stage='preparing')
    result=coordinator.detail(current)
    assert result['current_phase']=='ocr'
    assert result['preparation']['steps'][0]['current']==2
    assert result['preparation']['steps'][0]['total']==8

def test_preview_rejects_changed_engine_without_creating_work(coordinator):
    coordinator.remote=lambda method,path,payload=None:({'clauses':{'TEST_01':{}},'digest':'a'*64} if path=='/api/targets/freeze' else {'ready':True,'fingerprint':'b'*64,'version':'test'})
    request=ExtractionPlanRequest(input={'kind':'document_ir','ir_run_id':'ir'},clause_keys=['TEST_01'],expected_engine_fingerprint='c'*64)
    with pytest.raises(ValueError,match='engine changed'):coordinator.create(request)
    assert coordinator.list()==[]


def test_submission_retry_returns_the_same_plan_and_rejects_changed_input(coordinator):
    calls=[]
    coordinator.preview=lambda request:(calls.append(request) or {'targets':{'clauses':{'TEST_01':{}},'digest':'a'*64},'source_sha256':None,'engine_fingerprint':'b'*64})
    request=ExtractionPlanRequest(input={'kind':'document_ir','ir_run_id':'ir'},clause_keys=['TEST_01'],submission_key='operation-1')
    first=coordinator.create(request);again=coordinator.create(request)
    assert first['session_id']==again['session_id'] and len(calls)==1
    with pytest.raises(ValueError,match='Submission key input changed'):
        coordinator.create(request.model_copy(update={'name':'changed input'}))


def test_existing_ir_plan_dispatches_the_selected_provider_without_credentials(coordinator):
    calls = []
    snapshot = {"clauses": {"TEST_01": {}}, "digest": "a" * 64}

    def remote(method, path, payload=None):
        calls.append((path, payload))
        if path == "/api/targets/freeze":
            return snapshot
        if path == "/api/engine":
            return {"ready": True, "fingerprint": "b" * 64, "version": "test"}
        if path == "/api/runs/preview":
            return {"calls_model": False}
        return {"run_id": "selected-run", "status": "queued"}

    coordinator.remote = remote
    coordinator.store.pin_ir = lambda *args: None
    coordinator.queue = lambda *args, **kwargs: SimpleNamespace(task_id="queue")
    choice = {"provider": "deepseek", "model": "deepseek-flash"}
    request = ExtractionPlanRequest(input={"kind": "document_ir", "ir_run_id": "ir"},
                                    clause_keys=["TEST_01"], model_selection=choice)
    assert coordinator.preview(request)["model_selection"] == choice
    plan = coordinator.create(request)
    coordinator.tick(coordinator.get(plan["session_id"]))
    created = next(payload for path, payload in calls if path == "/api/runs")
    assert created["request"]["model_selection"] == choice
    assert created["request"]["budget"] == request.budget.model_dump()
    assert created["request"]["ir_run_id"] == "ir"
    assert "api_key" not in created["request"]
    assert coordinator.get(plan["session_id"])["request"]["model_selection"] == choice


def test_preparation_cancel_waits_for_queue_exit_and_projects_actual_child_state(coordinator):
    child=SimpleNamespace(status=SimpleNamespace(value='running'),cancel_requested=True,progress=None)
    coordinator.store.get=lambda key:child
    coordinator.processing.get=lambda key:{'processing_id':key,'status':'cancelled','steps':{'ocr':{'status':'running','task_id':'child','progress':{'current':2,'total':20,'unit':'pages'}}}}
    stopped=[];coordinator.cancel_task=stopped.append
    current=state();current.update(status='cancelling',run_id=None,processing_id='prep',stage='preparing',cancel_requested=True)
    coordinator.save(current);coordinator.tick(current)
    observed=coordinator.detail(coordinator.get('plan'))
    assert observed['status']=='cancelling' and stopped==['child']
    assert observed['preparation']['steps'][0]['status']=='cancelling'
    child.status.value='cancelled'
    coordinator.tick(coordinator.get('plan'))
    observed=coordinator.detail(coordinator.get('plan'))
    assert observed['status']=='cancelled'
    assert observed['preparation']['steps'][0]['status']=='cancelled'


def test_terminal_child_requires_fresh_native_state_before_redispatch(coordinator):
    current = state()
    current['ir_run_id'] = 'ir'
    coordinator.store.get = lambda key: SimpleNamespace(status=SimpleNamespace(value='failed'))
    dispatched = []
    coordinator.queue = lambda *args, **kwargs: dispatched.append(args)
    reads = []
    coordinator.remote = lambda method, path: (reads.append((method, path)) or {
        'run_id': 'run', 'status': 'failed', 'error': 'Worker completed without a structured result event'
    })
    coordinator.save(current)

    coordinator.tick(current, {'run_id': 'run', 'status': 'queued'})

    assert reads == [('GET', '/api/runs/run')]
    assert dispatched == []
    assert coordinator.get('plan')['status'] == 'failed'


def test_batch_observation_cannot_overwrite_a_confirmed_preparation_cancellation(coordinator):
    current=state()
    current.update(run_id=None,ir_run_id=None,processing_id="prep",stage="preparing")
    coordinator.save(current)
    prepared={"processing_id":"prep","status":"running",
        "steps":{"ocr":{"status":"running"}}}
    coordinator.processing.get=lambda key:prepared
    def cancel_preparation(*args):
        prepared.update(status="cancelled",steps={"ocr":{"status":"cancelled"}})
    coordinator.processing.action=cancel_preparation
    def observed_before_cancel(states):
        assert states[0]["status"] == "running"
        assert coordinator.cancel("plan")["status"] == "cancelled"
        coordinator._stop.set()
        return {}
    coordinator.run_statuses=observed_before_cancel
    coordinator.start()
    coordinator._thread.join(timeout=5)
    assert not coordinator._thread.is_alive()
    persisted=coordinator.get("plan")
    assert persisted["status"] == "cancelled" and persisted["cancel_requested"]
    assert not persisted.get("error")


def test_report_batch_slides_on_terminal_reports_and_survives_restart(coordinator):
    snapshot = {"clauses": {"TEST_01": {}}, "digest": "a" * 64}
    calls = []
    runs = {}
    coordinator.processing.assets = SimpleNamespace(get=lambda key: {"sha256": "b" * 64})
    coordinator.store.get = lambda key: SimpleNamespace(status=SimpleNamespace(value="queued"))
    coordinator.queue = lambda run_id, *args, **kwargs: SimpleNamespace(task_id=run_id + "-task")

    def remote(method, path, payload=None):
        calls.append(path)
        if path == "/api/engine":
            return {"ready": True, "fingerprint": "c" * 64, "version": "test"}
        if path == "/api/targets/freeze":
            return snapshot
        if path == "/api/runs":
            run_id = payload["submission_key"] + "-run"
            runs[run_id] = {"run_id": run_id, "status": "queued"}
            return runs[run_id]
        if path == "/api/runs/status":
            return {"runs": [runs[key] for key in payload["run_ids"]]}
        if path.endswith("/resume"):
            run = runs[path.split("/")[3]]
            run.update(status="queued", resumable=False)
            return run
        return runs[path.rsplit("/", 1)[-1]]

    coordinator.remote = remote
    request = ExtractionBatchRequest(submission_key="report-batch", sessions=[
        ExtractionPlanRequest(input={"kind": "pdf", "asset_id": f"asset-{index}"},
                              clause_keys=["TEST_01"], name=f"Report {index}")
        for index in range(1, 14)
    ])
    batch = coordinator.create_batch(request)
    ids = [member.session_id for member in batch.members]
    assert [member.name for member in batch.members] == [f"Report {i}" for i in range(1, 14)]
    assert (batch.window_size, batch.active_count, batch.waiting_count) == (10, 10, 3)
    assert coordinator.create_batch(request) == batch
    assert calls == ["/api/engine", "/api/targets/freeze"]
    for session_id in ids:
        coordinator.tick(coordinator.get(session_id))
    assert len(runs) == 10
    # Reading can yield repeatedly without freeing a report's place.
    runs[ids[0] + "-run"]["execution_continues"] = True
    coordinator.tick(coordinator.get(ids[0]))
    assert coordinator.batches.get(batch.batch_id).waiting_count == 3
    # A report cancelled before admission never creates a native run.
    coordinator.cancel(ids[10])
    assert coordinator.get(ids[10])["status"] == "cancelled"
    assert coordinator.get(ids[10]).get("admitted_at") is None
    runs[ids[0] + "-run"].update(status="completed", execution_continues=False)
    coordinator.tick(coordinator.get(ids[0]))
    after_first = coordinator.batches.get(batch.batch_id)
    assert (after_first.active_count, after_first.waiting_count, after_first.finished_count) == (10, 1, 2)
    assert after_first.members[11].admitted_at
    assert after_first.members[12].status == "waiting_for_window"

    restarted = ExtractionCoordinator(coordinator.store, coordinator.processing,
                                      coordinator.queue, coordinator.cancel_task, coordinator.base_url)
    restarted.remote = remote
    assert restarted.batches.get(batch.batch_id) == after_first
    assert restarted.batch_waiting_count() == 1
    assert len(restarted.list(terminal_limit=0, include_waiting=False)) == 10
    restarted.tick(restarted.get(ids[11]))
    runs[ids[1] + "-run"].update(status="failed", resumable=True)
    restarted.tick(restarted.get(ids[1]))
    assert restarted.get(ids[12])["status"] == "queued"
    assert restarted.resume(ids[1])["status"] == "waiting_for_window"
    assert not any(path.endswith("/resume") for path in calls)
    runs[ids[2] + "-run"].update(status="failed", resumable=True)
    restarted.tick(restarted.get(ids[2]))
    assert restarted.get(ids[1])["status"] == "queued"
    restarted.tick(restarted.get(ids[1]))
    assert any(path.endswith("/resume") for path in calls)
    assert restarted.batches.get(batch.batch_id).active_count == 10


def test_batch_submission_is_atomic_when_any_report_cannot_be_previewed(coordinator):
    coordinator.remote = lambda method, path, payload=None: (
        {"ready": True, "fingerprint": "c" * 64, "version": "test"} if path == "/api/engine"
        else {"clauses": {"TEST_01": {}}, "digest": "a" * 64}
    )
    assets = {"valid": {"sha256": "b" * 64}}
    coordinator.processing.assets = SimpleNamespace(get=assets.__getitem__)
    request = ExtractionBatchRequest(submission_key="retry", sessions=[
        ExtractionPlanRequest(input={"kind": "pdf", "asset_id": key}, clause_keys=["TEST_01"])
        for key in ("valid", "missing")
    ])
    with pytest.raises(KeyError, match="missing"):
        coordinator.create_batch(request)
    assert coordinator.batches.list() == [] and coordinator.list() == []
    assets["missing"] = {"sha256": "d" * 64}
    batch = coordinator.create_batch(request)
    assert len(batch.members) == 2
    with pytest.raises(ValueError, match="内容已变化"):
        coordinator.create_batch(request.model_copy(update={"sessions": request.sessions[::-1]}))


def test_provider_failure_holds_only_its_batch_and_resumes_from_original_session(coordinator):
    coordinator.remote = lambda method, path, payload=None: (
        {"ready": True, "fingerprint": "c" * 64, "version": "test"} if path == "/api/engine"
        else {"clauses": {"TEST_01": {}}, "digest": "a" * 64}
    )
    coordinator.processing.assets = SimpleNamespace(get=lambda key: {"sha256": "b" * 64})
    batch = coordinator.create_batch(ExtractionBatchRequest(submission_key="provider-pause", sessions=[
        ExtractionPlanRequest(input={"kind": "pdf", "asset_id": f"asset-{index}"}, clause_keys=["TEST_01"],
            model_selection={"provider": "deepseek", "model": "deepseek-flash"} if index < 11 else None)
        for index in range(12)
    ]))
    owner, blocked = [coordinator.get(member.session_id) for member in batch.members[:2]]
    owner.update(status="running", run_id="native-owner")
    coordinator.save(owner)

    def enqueue(key, batch_id, sid, provider):
        return coordinator.store.create(task_id=key, task_type=PipelineTaskType.TARGETED_EXTRACTION,
            operation="clause_extraction", title=key, native_job_id=key,
            payload={"session_id": sid, "batch_id": batch_id, "model_provider": provider})

    current = enqueue("active", batch.batch_id, owner["session_id"], "deepseek")
    enqueue("waiting", batch.batch_id, blocked["session_id"], "deepseek")
    other = enqueue("unrelated", "other-batch", "elsewhere", "deepseek")
    assert coordinator.store.claim_next().task_id == current.task_id
    from esg_pipeline.scheduler import PipelineScheduler, PipelineTaskHooks

    result = {"run_id": "native-owner", "status": "interrupted", "resumable": True,
              "provider_interruption": {"provider": "deepseek", "category": "provider_balance", "phase": "reading"}}
    PipelineScheduler(coordinator.store, None, PipelineTaskHooks())._finish_native_state(current, result)
    coordinator.tick(owner, result)
    observed = coordinator.batches.get(batch.batch_id)
    assert observed.submitted_count == 12
    assert observed.provider_pauses[0].session_id == owner["session_id"]
    assert observed.members[10].status == "waiting_for_window"
    assert observed.members[11].status == "queued"
    assert coordinator.store.claim_next().task_id == other.task_id
    coordinator.store.update(other.task_id, status="completed")
    assert coordinator.store.claim_next() is None

    cleared = dict(result, status="queued", provider_interruption=None)
    coordinator.remote = lambda method, path, payload=None: cleared if path.endswith("/resume") else result
    coordinator.queue = lambda run_id, *args, **kwargs: enqueue("continued", batch.batch_id, owner["session_id"], "deepseek")
    # A mixed-provider completion frees the report seat for the operator's resume.
    free = coordinator.get(batch.members[11].session_id)
    free["status"] = "completed"
    coordinator.save(free)
    coordinator.resume(owner["session_id"])
    coordinator.tick(coordinator.get(owner["session_id"]))
    assert coordinator.get(owner["session_id"])["run_id"] == "native-owner"
    assert coordinator.batches.get(batch.batch_id).provider_pauses == []
    assert coordinator.store.claim_next().task_id == "waiting"


def test_new_attempt_links_same_report_without_replacing_previous_results(coordinator):
    from copy import deepcopy

    engine_fingerprint = "a" * 64
    hashes = {"first": "b" * 64, "second": "c" * 64}
    coordinator.processing.assets = SimpleNamespace(get=lambda key: {"sha256": hashes[key]})

    def remote(method, path, payload=None):
        if path == "/api/engine":
            return {"ready": True, "fingerprint": engine_fingerprint, "version": "test"}
        if path == "/api/targets/freeze":
            assert "replaces_session_id" not in payload
            return {"clauses": {"TEST_01": {}}, "digest": engine_fingerprint}
        assert path == "/api/runs/original-run"
        return {"run_id": "original-run", "status": "completed", "progress": {"fact_count": 5}}

    coordinator.remote = remote
    first = ExtractionPlanRequest(input={"kind": "pdf", "asset_id": "first"}, clause_keys=["TEST_01"])
    second = first.model_copy(update={"input": first.input.model_copy(update={"asset_id": "second"})})
    original_batch = coordinator.create_batch(ExtractionBatchRequest(submission_key="original", sessions=[first, second]))
    source_id = original_batch.members[0].session_id
    source = coordinator.get(source_id)
    source.update(status="completed", run_id="original-run")
    coordinator.save(source)
    preserved = deepcopy(coordinator.get(source_id))

    engine_fingerprint = "d" * 64
    request = first.model_copy(update={"replaces_session_id": source_id, "submission_key": "retry"})
    attempt = coordinator.create(request)
    assert attempt["engine_fingerprint"] == engine_fingerprint
    assert attempt["request"]["replaces_session_id"] == source_id
    assert coordinator.create(request)["session_id"] == attempt["session_id"]
    parallel = coordinator.create_batch(ExtractionBatchRequest(submission_key="followup", sessions=[
        request.model_copy(update={"submission_key": None}), second,
    ]))
    assert parallel.members[0].replaces_session_id == source_id
    assert parallel.members[1].replaces_session_id is None
    with pytest.raises(ValueError, match="同一份报告"):
        coordinator.create(second.model_copy(update={"replaces_session_id": source_id}))
    with pytest.raises(ValueError, match="找不到"):
        coordinator.create(first.model_copy(update={"replaces_session_id": "missing"}))

    restarted = ExtractionCoordinator(coordinator.store, coordinator.processing,
                                      coordinator.queue, coordinator.cancel_task, coordinator.base_url)
    restarted.remote = remote
    restored = restarted.batches.get(original_batch.batch_id)
    linked = [attempt["session_id"], parallel.members[0].session_id]
    assert restored.submitted_count == restored.total_count == 2
    assert restored.members[0].replacement_session_ids == linked
    assert restarted.get(source_id) == preserved
    detail = restarted.detail(restarted.get(source_id))
    assert detail["replacement_session_ids"] == linked
    assert detail["progress"]["fact_count"] == 5


def test_batch_shows_actual_queue_state_for_admitted_reports(coordinator):
    coordinator.remote = lambda method, path, payload=None: (
        {"ready": True, "fingerprint": "c" * 64, "version": "test"} if path == "/api/engine"
        else {"clauses": {"TEST_01": {}}, "digest": "a" * 64}
    )
    coordinator.processing.assets = SimpleNamespace(get=lambda key: {"sha256": "b" * 64})
    request = ExtractionBatchRequest(submission_key="queue-projection", sessions=[
        ExtractionPlanRequest(input={"kind": "pdf", "asset_id": f"asset-{index}"},
                              clause_keys=["TEST_01"])
        for index in range(11)
    ])
    batch = coordinator.create_batch(request)
    first = coordinator.get(batch.members[0].session_id)
    task = coordinator.store.create(
        task_id="reading", task_type=PipelineTaskType.TARGETED_EXTRACTION, operation="clause_extraction",
        title=first["name"], payload={}, native_job_id="native-reading",
    )
    first.update(status="running", queue_task_id=task.task_id)
    coordinator.save(first)
    observed = coordinator.batches.get(batch.batch_id)
    assert observed.members[0].status == "queued"
    assert observed.active_count == 10 and observed.waiting_count == 1
    assert coordinator.get(first["session_id"])["status"] == "running"

    coordinator.store.update(task.task_id, status="running")
    assert coordinator.batches.get(batch.batch_id).members[0].status == "running"
    coordinator.store.update(task.task_id, status="queued", stage="window_yielded")
    observed = coordinator.batches.get(batch.batch_id)
    assert observed.members[0].status == "queued"
    assert observed.members[0].admitted_at == batch.members[0].admitted_at
    assert observed.active_count == 10 and observed.waiting_count == 1

    coordinator.store.update(task.task_id, status="completed")
    second = coordinator.get(batch.members[1].session_id)
    next_task = coordinator.store.create(
        task_id="next-reading", task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="clause_extraction", title=second["name"], payload={}, native_job_id="native-next-reading",
    )
    coordinator.store.update(next_task.task_id, status="running")
    second.update(status="running", queue_task_id=next_task.task_id)
    coordinator.save(second)
    observed = coordinator.batches.get(batch.batch_id)
    assert [member.position for member in observed.members if member.status == "running"] == [2]
    assert observed.active_count == 10 and observed.waiting_count == 1
