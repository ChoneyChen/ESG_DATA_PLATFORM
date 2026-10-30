import sqlite3
from types import SimpleNamespace
import pytest
from esg_pipeline.extraction import ExtractionCoordinator,ExtractionServiceUnavailable
from esg_contracts.sessions import ExtractionPlanRequest

@pytest.fixture
def coordinator(tmp_path):
    store=SimpleNamespace(_connect=lambda:sqlite3.connect(tmp_path/'state.db'))
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
