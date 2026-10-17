import json
import ast
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException

from esg_v2.control_plane.contracts import PipelineTaskType, PipelineTaskStatus
from esg_v2.control_plane.store import PipelineQueueStore
from esg_v2.control_plane.scheduler import PipelineScheduler, PipelineTaskHooks
from esg_v2.control_plane.secrets import RuntimeSecretVault
from esg_v2.control_plane.progress import progress_from_event, stage_label, classify_error
from esg_v2.control_plane.plans import PipelinePlanCoordinator, PipelinePlanRequest
from esg_v2.control_plane.plan_lifecycle import PlanLifecycle
from esg_v2.control_plane.execution_api import register_execution_api


def task(store, kind=PipelineTaskType.OCR, name='task-1'):
    return store.create(task_id=name, task_type=kind, operation='build', title='测试', payload={}, native_job_id=f'run-{name}')


def test_logs_and_model_budget_are_not_business_progress(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store, PipelineTaskType.TARGETED_EXTRACTION)
    progress = progress_from_event(current, {'kind':'progress', 'stage':'mapping', 'progress_current':2,
        'progress_total':8, 'counts':{'selected_metrics':16,'processed_metrics':3,'core_facts':9}})
    assert (progress.current,progress.total,progress.unit,progress.percent) == (3,16,'requirements',18.8)
    store.update(current.task_id, progress_json=progress.model_dump_json())
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), PipelineTaskHooks())
    scheduler._apply_event(store.get(current.task_id), {'kind':'log','stage':'completed','message':'11/11 fake complete'})
    unchanged = store.get(current.task_id)
    assert unchanged.progress.current == 3
    assert unchanged.stage != 'completed'
    assert store.events(current.task_id)[-1]['level'] == 'debug'


def test_foundation16_progress_counts_source_tables_as_objects(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store, PipelineTaskType.TARGETED_EXTRACTION)
    call = {'tool': 'foundation_tables', 'route': 'foundation16', 'object_id': 'table-p0147-0001', 'index': 3, 'total': 6}
    progress = progress_from_event(current, {'kind': 'progress', 'stage': 'report_session', 'jobs': [
        {'status': 'running', 'summary': {'counts': {'selected_metrics': 3}, 'agent': {'current_call': call}}}]})
    assert (progress.current, progress.total, progress.unit, progress.stage_code) == (2, 6, 'objects', 'foundation_tables')
    assert progress.label == '逐表理解基础指标'


def production_extraction_stage_codes():
    """Discover the actual producer boundary, including conditional tool names."""
    root = Path(__file__).resolve().parents[3]
    sources = [root / 'extraction/src/esg_targeted/workflow.py',
               root / 'extraction/src/esg_targeted/evidence/disclosure.py']
    def literal_codes(node):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):
            return {node.value}
        if isinstance(node,ast.IfExp):
            return literal_codes(node.body) | literal_codes(node.orelse)
        return set()
    codes = set()
    for source in sources:
        for call in ast.walk(ast.parse(source.read_text())):
            if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute):
                continue
            method = call.func.attr
            if method in {'_stage','add_event'} and len(call.args) > 1:
                codes.update(literal_codes(call.args[1]))
            if method == '_event' and call.args:
                codes.update(literal_codes(call.args[0]))
            for keyword in call.keywords:
                if (method == '_call' and keyword.arg == 'tool') or (method == 'update' and keyword.arg == 'stage'):
                    codes.update(literal_codes(keyword.value))
    return codes


def test_all_actual_extraction_producer_stages_have_explicit_business_labels(tmp_path):
    from esg_runtime.progress import STAGE_LABELS
    codes = production_extraction_stage_codes()
    assert {'loading_inputs','semantic_fill','agent_plan','agent_gap_search','finished',
            'checkpoint_resume','checkpoint_rejected','metric_mapping','read_object_context'} <= codes
    missing = codes - set(STAGE_LABELS)
    assert not missing, f'Actual production stages lack Chinese labels: {sorted(missing)}'
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store,PipelineTaskType.TARGETED_EXTRACTION)
    for code in sorted(codes):
        progress = progress_from_event(current,{'kind':'progress','stage':code,'status':'running',
            'progress_current':7,'progress_total':7,'detail':{'generation_tokens':1000}})
        assert progress.stage_code == code and progress.label == STAGE_LABELS[code]
        assert any('\u4e00' <= char <= '\u9fff' for char in progress.label)
        assert progress.unit == 'unknown' and progress.current is None and progress.percent is None
        assert progress.determinate is False  # phase names/tokens do not imply measured coverage


def test_log_fields_cannot_forge_current_call_stage_status_or_coverage(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store,PipelineTaskType.TARGETED_EXTRACTION)
    measured = progress_from_event(current,{'kind':'progress','stage':'agent_plan',
        'counts':{'selected_metrics':16,'processed_metrics':3}})
    store.update(current.task_id,progress_json=measured.model_dump_json())
    current = store.get(current.task_id)
    for code in production_extraction_stage_codes():
        forged = progress_from_event(current,{'kind':'log','stage':code,'status':'completed',
            'counts':{'selected_metrics':1,'processed_metrics':1},'current_call':{'tool':'repair_format'}})
        assert forged.model_dump() == measured.model_dump()
    initial = task(store,PipelineTaskType.TARGETED_EXTRACTION,name='unmeasured')
    forged = progress_from_event(initial,{'kind':'log','stage':'finished','status':'completed',
        'counts':{'selected_metrics':1,'processed_metrics':1}})
    assert forged.stage_code == initial.stage and forged.status == initial.status.value
    assert forged.percent is None and not forged.determinate


def test_unknown_phase_is_not_guessed_from_prefix_or_task_type():
    for code in ('checkpoint_new_unregistered_phase','agent_new_phase','new_machine_code'):
        assert stage_label(code,task_type='targeted_extraction') == '执行阶段待识别'


def test_current_model_tool_has_the_same_central_chinese_label_and_no_invented_percent(tmp_path):
    current = task(PipelineQueueStore(tmp_path/'queue.db'),PipelineTaskType.TARGETED_EXTRACTION)
    progress = progress_from_event(current,{'kind':'progress','stage':'semantic_fill',
        'current_call':{'tool':'read_object_context','elapsed_seconds':8,'object_id':'fixture-table'}})
    assert progress.current_call['label'] == stage_label('read_object_context')
    assert progress.current_call['object_id'] == 'fixture-table'
    assert progress.current_call['elapsed_seconds'] == 8
    assert progress.percent is None and not progress.determinate


def test_historical_progress_labels_project_without_mutating_stored_counts_or_events(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store,PipelineTaskType.TARGETED_EXTRACTION)
    snapshot={'stage_code':'finished','label':'执行阶段待识别','status':'partial','current':3,'total':3,
        'unit':'requirements','determinate':True,'percent':100,'counts':{'core_facts':5},
        'last_business_progress_at':'2025-01-01T00:00:00+00:00'}
    raw=json.dumps(snapshot)
    store.update(current.task_id,status=PipelineTaskStatus.PARTIAL,progress_json=raw)
    read=store.get(current.task_id).progress
    assert read.label == stage_label('finished')
    assert read.status == 'partial' and read.current == 3 and read.counts == {'core_facts':5}
    assert read.last_business_progress_at == snapshot['last_business_progress_at']
    with store._connect() as db:
        retained=db.execute('SELECT progress_json FROM pipeline_tasks WHERE task_id=?',(current.task_id,)).fetchone()[0]
    assert retained == raw


@pytest.mark.parametrize('role,caption', [
    ('reviewer','复核原文并提出修订'), ('verifier','验证修订与原文证据'),
    ('unregistered-role','模型调用动作待识别'),
])
def test_ir_role_only_call_is_labelled_without_inventing_tools_or_completion(tmp_path,role,caption):
    current = task(PipelineQueueStore(tmp_path/'queue.db'),PipelineTaskType.DOCUMENT_IR)
    call={'role':role,'task_id':'actual-review-task','elapsed_seconds':19}
    progress=progress_from_event(current,{'kind':'telemetry','stage':'document_ir','current_call':call})
    assert progress.current_call['label'] == caption
    assert progress.current_call['task_id'] == call['task_id']
    assert progress.current_call['role'] == role
    assert progress.current_call['elapsed_seconds'] == 19
    assert not {'tool','route','action'} & set(progress.current_call)
    assert progress.percent is None and not progress.determinate
    assert progress.current is None and progress.status == current.status.value


def test_actual_semantic_brain_role_has_chinese_name_without_completion_claim():
    from esg_runtime.progress import BusinessProgress
    progress=BusinessProgress(stage_code='planning',label='old English',status='running',
        current_call={'role':'semantic_brain','tool':'agent_plan','elapsed_seconds':4})
    assert progress.current_call['role_label']=='报告理解与规划'
    assert progress.percent is None and progress.current is None


def test_ir_stage_count_is_completed_steps_not_current_index(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store, PipelineTaskType.DOCUMENT_IR)
    progress = progress_from_event(current, {'kind':'telemetry','detail':{'current_stage':{'index':9,'total':11},
        'stages':[{'index':1,'status':'done'},{'index':9,'status':'running'}]}})
    assert progress.current == 1 and progress.total == 11
    assert progress.label == '按缺口复核与修复'
    assert stage_label('ir.build.stage.0') == '结构与证据构建'
    assert stage_label('unknown_machine_code') == '执行阶段待识别'


def test_producer_error_category_has_priority_over_incidental_json_text():
    error = classify_error('JSON source bundle failed', task_type='document_ir',
        producer_error={'category':'source','code':'bad_evidence','message':'受影响表格证据不足'})
    assert error.category == 'source' and error.code == 'bad_evidence'
    assert classify_error('Output schema validation failed').category == 'protocol'
    assert classify_error("Model call 'repair_format' exceeded its external hard deadline").category == 'runtime'
    assert classify_error('subject_identity missing').category == 'identity'


def test_queue_state_event_revision_and_history_are_durable(tmp_path):
    path = tmp_path/'queue.db'
    store = PipelineQueueStore(path)
    current = task(store)
    updated = store.update(current.task_id,status=PipelineTaskStatus.PARTIAL,stage='partial',error='time budget exceeded')
    events = store.events(None)
    cursor = events[-1]['event_id']
    assert events[-1]['detail']['revision'] == updated.revision
    archived = store.archive(current.task_id,'保留历史',expected_revision=updated.revision)
    assert store.list() == []
    with pytest.raises(ValueError):
        store.restore(current.task_id,'恢复',expected_revision=updated.revision)
    replay = PipelineQueueStore(path).events(None,cursor)
    assert replay and all(item['event_id'] > cursor for item in replay)
    assert archived.archived_at and archived.archive_reason == '保留历史'
    store.restore(current.task_id,'恢复')
    assert store.get(current.task_id).status == PipelineTaskStatus.PARTIAL


def test_budget_excludes_queue_and_review_wait_but_charges_unknown_gap(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store)
    engine = PipelinePlanCoordinator(store,lambda *a:None,lambda *a:None,lambda *a:None)
    plan = {'documents':[{'steps':{'ocr':{'task_id':current.task_id}}}]}
    assert engine._execution_budget(plan,60)['used_seconds'] == 0
    now = datetime.now(timezone.utc)
    store.update(current.task_id,status=PipelineTaskStatus.INTERRUPTED,
        started_at=(now-timedelta(seconds=40)).isoformat(),finished_at=now.isoformat(),
        timing_json=json.dumps({'unobserved_seconds':20,'sleep_or_clock_adjustment_seconds':10}))
    budget = engine._execution_budget(plan,60)
    assert 29 <= budget['used_seconds'] <= 31
    assert budget['unobserved_seconds'] == 20
    plan['budget_baseline_task_ids'] = [current.task_id]
    assert engine._execution_budget(plan,60)['used_seconds'] == 0


def test_plan_lifecycle_reconciles_uncertain_remote_outcome_without_duplicate(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store)
    store.update(current.task_id,status=PipelineTaskStatus.COMPLETED)
    engine = PipelinePlanCoordinator(store,lambda *a:None,lambda *a:None,lambda *a:None)
    plan = engine.create(PipelinePlanRequest(asset_ids=['pdf'],standards=[{'package_id':'e1-6','package_version':'2.1.1','metric_ids':['dp07']}]),[{'asset_id':'pdf'}])
    plan['status']='completed'
    plan['documents'][0].update(native_session_id='native-1',steps={'report_session':{'task_id':current.task_id}})
    engine.save(plan)
    remote={'state':'active','revision':0}
    calls=[]
    def request(method,path,payload=None):
        if method == 'POST':
            calls.append(path)
            if path.endswith('/archive'):
                remote.update(state='archived',revision=1)
                raise HTTPException(503,'Response lost after remote commit')
            remote.update(state='active',revision=2)
        return dict(remote)
    service=PlanLifecycle(engine,request)
    with pytest.raises(HTTPException):
        service.operate(plan['plan_id'],'archive','测试')
    assert engine.list()[0]['lifecycle_intent']['action'] == 'archive'
    final=service.operate(plan['plan_id'],'archive','测试')
    assert final['state'] == 'archived' and len(calls) == 1
    assert store.get(current.task_id).archived_at
    service.operate(plan['plan_id'],'restore','恢复')


def test_sleep_observation_distinguishes_known_and_unobserved_time(tmp_path, monkeypatch):
    from esg_v2.control_plane import scheduler as module
    from esg_targeted.storage.power import WatchdogClock
    store=PipelineQueueStore(tmp_path/'queue.db')
    current=task(store)
    scheduler=PipelineScheduler(store,RuntimeSecretVault(),PipelineTaskHooks())
    clock=WatchdogClock(10,10)
    monkeypatch.setattr(module.time,'time',lambda:12)
    monkeypatch.setattr(module.time,'monotonic',lambda:12)
    scheduler._observe_clock(current,clock)
    monkeypatch.setattr(module.time,'time',lambda:92)
    monkeypatch.setattr(module.time,'monotonic',lambda:22)
    scheduler._observe_clock(store.get(current.task_id),clock)
    timing=store.get(current.task_id).timing
    assert timing.active_observed_seconds == 2
    assert timing.unobserved_seconds == 10
    assert timing.sleep_or_clock_adjustment_seconds == 70
    assert timing.observation_complete is False


def test_sse_replays_after_last_event_id_and_does_not_own_execution(tmp_path):
    import asyncio
    from fastapi import FastAPI
    store=PipelineQueueStore(tmp_path/'queue.db')
    current=task(store)
    cursor=store.events(None)[-1]['event_id']
    store.update(current.task_id,stage='ocr',progress_current=2,progress_total=5)
    app=FastAPI()
    register_execution_api(app,store)
    endpoint=next(route.endpoint for route in app.routes if getattr(route,'path','')=='/api/pipeline/events/stream')
    class Request:
        headers={'last-event-id':str(cursor)}
        checks=0
        async def is_disconnected(self):
            self.checks+=1
            return self.checks>1
    async def collect():
        response=await endpoint(Request(),after=0,task_id=None)
        return ''.join([chunk async for chunk in response.body_iterator])
    payload=asyncio.run(collect())
    assert f'id: {cursor}\n' not in payload
    assert 'event: execution' in payload and 'state_changed' in payload
    assert store.get(current.task_id).status == PipelineTaskStatus.QUEUED


def test_guarded_parent_discard_releases_history_pins_and_restores_exact_revision(tmp_path):
    store=PipelineQueueStore(tmp_path/'queue.db')
    first=task(store,name='old-task')
    second=task(store,name='new-task')
    for child in (first,second):
        store.update(child.task_id,status=PipelineTaskStatus.COMPLETED)
        store.pin_ir(child.task_id,'ir-source')
    manifest={'run_id':'ir-source','source':'fixed'}
    engine=PipelinePlanCoordinator(store,lambda *a:None,lambda *a:dict(manifest),lambda *a:None)
    plan=engine.create(PipelinePlanRequest(asset_ids=['pdf'],standards=[{'package_id':'e1-6','package_version':'2.1.1','metric_ids':['dp07']}]),[{'asset_id':'pdf'}])
    store.pin_ir(plan['plan_id'],'ir-source')
    plan['status']='completed'
    plan['documents'][0].update(native_session_id='native',steps={'report_session':{'task_id':second.task_id,'history':[first.task_id]}})
    engine.save(plan)
    remote={'state':'active','revision':0}
    def request(method,path,payload=None):
        if method=='POST':
            remote.update(state='discarded' if path.endswith('/discard') else 'active',revision=remote['revision']+1)
        return dict(remote)
    service=PlanLifecycle(engine,request)
    service.operate(plan['plan_id'],'discard','丢弃未入库成果')
    assert PipelineQueueStore(tmp_path/'queue.db').pinned_ir_references()==set()
    assert store.get(first.task_id).archived_at and store.get(second.task_id).archived_at
    manifest['source']='changed'
    with pytest.raises(ValueError,match='原始证据版本'):
        service.operate(plan['plan_id'],'restore','恢复')
    manifest['source']='fixed'
    service.operate(plan['plan_id'],'restore','恢复')
    assert store.pinned_ir_references()=={'ir-source'}
    assert not store.get(first.task_id).archived_at and not store.get(second.task_id).archived_at
