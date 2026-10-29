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
from esg_v2.control_plane.execution_api import register_execution_api


def task(store, kind=PipelineTaskType.OCR, name='task-1'):
    return store.create(task_id=name, task_type=kind, operation='build', title='测试', payload={}, native_job_id=f'run-{name}')


def test_logs_and_model_budget_are_not_business_progress(tmp_path):
    store = PipelineQueueStore(tmp_path/'queue.db')
    current = task(store, PipelineTaskType.TARGETED_EXTRACTION)
    progress = progress_from_event(current, {'kind':'progress', 'stage':'mapping', 'progress_current':2,
        'progress_total':8, 'counts':{'selected_clauses':16,'processed_clauses':3,'source_facts':9}})
    assert (progress.current,progress.total,progress.unit,progress.percent) == (3,16,'requirements',18.8)
    store.update(current.task_id, progress_json=progress.model_dump_json())
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), PipelineTaskHooks())
    scheduler._apply_event(store.get(current.task_id), {'kind':'log','stage':'completed','message':'11/11 fake complete'})
    unchanged = store.get(current.task_id)
    assert unchanged.progress.current == 3
    assert unchanged.stage != 'completed'
    assert store.events(current.task_id)[-1]['level'] == 'debug'




def production_extraction_stage_codes():
    root=Path(__file__).resolve().parents[3]
    source=root/'extraction/src/esg_targeted/pipeline/engine.py'
    return {v.value for node in ast.walk(ast.parse(source.read_text())) if isinstance(node,ast.Dict)
            for k,v in zip(node.keys,node.values) if isinstance(k,ast.Constant) and k.value=='stage' and isinstance(v,ast.Constant) and isinstance(v.value,str)}


def test_all_actual_extraction_producer_stages_have_explicit_business_labels(tmp_path):
    from esg_runtime.progress import STAGE_LABELS
    codes = production_extraction_stage_codes()
    assert {'retrieving','reading','extracting'} <= codes
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
        'counts':{'selected_clauses':16,'processed_clauses':3}})
    store.update(current.task_id,progress_json=measured.model_dump_json())
    current = store.get(current.task_id)
    for code in production_extraction_stage_codes():
        forged = progress_from_event(current,{'kind':'log','stage':code,'status':'completed',
            'counts':{'selected_clauses':1,'processed_clauses':1},'current_call':{'tool':'repair_format'}})
        assert forged.model_dump() == measured.model_dump()
    initial = task(store,PipelineTaskType.TARGETED_EXTRACTION,name='unmeasured')
    forged = progress_from_event(initial,{'kind':'log','stage':'finished','status':'completed',
        'counts':{'selected_clauses':1,'processed_clauses':1}})
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
