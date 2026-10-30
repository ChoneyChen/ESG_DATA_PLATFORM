import time
import pytest
from esg_runtime.model_calls import ModelCallRequest, ModelCapabilities, prepare_call, finish_call


def test_effective_budget_is_explicit_and_capped_once():
    receipt=prepare_call(ModelCallRequest(task_type='reading',input_modalities=['text'],prompt_digest='a',requested_output_tokens=8192,requested_timeout_seconds=300),
        ModelCapabilities(provider='local',model='test',input_modalities=['text'],max_output_tokens=4096,timeout_seconds=100,output_constraint='grammar_schema'))
    assert receipt.request.requested_output_tokens==8192
    assert receipt.effective_output_tokens==4096 and receipt.effective_timeout_seconds==100
    completed=finish_call(receipt,time.perf_counter(),{'generation_tokens':20})
    assert completed['status']=='succeeded' and completed['output_tokens']==20


def test_modality_failure_is_rejected_before_provider_invocation():
    with pytest.raises(ValueError,match='modality'):
        prepare_call(ModelCallRequest(task_type='reading',input_modalities=['image'],prompt_digest='a',requested_timeout_seconds=2),
            ModelCapabilities(provider='local',model='text',input_modalities=['text'],timeout_seconds=100,output_constraint='none'))


def test_owned_release_needs_only_the_deployed_module(tmp_path):
    from esg_runtime.release import ModuleRelease
    owned=tmp_path/'backend';owned.mkdir()
    source=owned/'app.py';source.write_text('first')
    release=ModuleRelease(tmp_path,'standalone',('backend/*.py',))
    assert not release.snapshot()['restart_required']
    source.write_text('second')
    assert release.snapshot()['restart_required']


def test_consumed_module_changes_invalidate_the_dependent_release(tmp_path):
    from esg_runtime.release import ModuleRelease
    database = tmp_path/'database';database.mkdir()
    source = database/'query.mjs';source.write_text('first')
    owned = ModuleRelease(database, 'database', ('*.mjs',))
    app = ModuleRelease(tmp_path, 'app', ('*.py',), dependencies=(('database', owned),))
    assert not app.snapshot()['restart_required']
    source.write_text('second')
    assert app.snapshot()['restart_required']
