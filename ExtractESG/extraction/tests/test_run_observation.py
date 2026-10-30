import pytest
from esg_extraction.pipeline.state import RunStore
from esg_contracts.facts import RunRequest
from esg_contracts.sessions import NativeRun
from test_backbone import repository


@pytest.mark.parametrize('observation,expected',[(None,'running'),(True,'running'),(False,'interrupted')])
def test_process_observation_failure_is_not_a_terminal_result(tmp_path,repository,monkeypatch,observation,expected):
    from esg_runtime import process
    store=RunStore(tmp_path/'runs.db');request=RunRequest(ir_run_id='ir',clause_keys=['E1-5_01'])
    snapshot=repository.freeze(repository.select(['E1-5_01']))
    run=store.create(request,snapshot,{'document_id':'doc'})
    store.claim(run['run_id'])
    monkeypatch.setattr(process,'observed_alive',lambda *args:observation)
    state=store.observe(run['run_id'])
    assert state['status']==expected
    assert store.get(run['run_id'])['targets']==snapshot


def test_confirmed_exit_after_cancel_does_not_remain_running(tmp_path,repository,monkeypatch):
    from esg_runtime import process
    store=RunStore(tmp_path/'runs.db');request=RunRequest(ir_run_id='ir',clause_keys=['E1-5_01'])
    run=store.create(request,repository.freeze(repository.select(['E1-5_01'])),{})
    store.claim(run['run_id']);store.update(run['run_id'],cancel_requested=True)
    monkeypatch.setattr(process,'observed_alive',lambda *args:False)
    assert store.observe(run['run_id'])['status']=='cancelled'


def test_terminal_view_keeps_the_last_call_without_displaying_it_as_active():
    view=NativeRun(run_id='run',status='partial',progress={'stage':'reading','current':1,'total':1},current_call={'phase':'generation','generation_tokens':300})
    assert view.current_phase=='finished' and view.current_call is None
    assert view.last_call.generation_tokens==300
