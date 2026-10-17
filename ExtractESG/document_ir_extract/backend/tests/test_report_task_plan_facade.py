"""Task selection crosses the report facade without loading models or live records."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from esg_targeted.contracts import ReportExtractionRequest, ReportModelPlan
from test_report_session_control_plane import prepare_api, request


def test_new_plan_pins_native_preview_models_and_preserves_authorization(tmp_path, monkeypatch):
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    ir = api.settings.document_ir_output_root / 'ir-existing'
    ir.mkdir()
    (ir / 'manifest.json').write_text(json.dumps({'can_build_evidence': True, 'ocr_run_id': 'ocr-existing'}))
    selected = request().model_copy(update={'model_plan': ReportModelPlan(),
        'role_config': request().role_config.model_copy(update={'specialist': request().role_config.semantic_brain})})
    roles = selected.role_config.model_dump(mode='json')
    for role in ('semantic_brain', 'specialist'):
        roles[role]['model'] = 'Synthetic-Qwen-4bit'
    monkeypatch.setattr(api, 'preview_report_session', lambda value: {'roles': roles})
    result = api.create_report_session(selected)
    queued = queue.list()[0].payload
    assert queued['role_config']['specialist'] == {'provider': 'local_qwen', 'model': 'Synthetic-Qwen-4bit'}
    assert queued['role_config']['cloud_authorization'] == roles['cloud_authorization']
    assert result['native_session_id'] == state['session_id']


@pytest.mark.parametrize('task', ['review', 'ocr'])
def test_cloud_preparation_missing_credential_rejects_before_queue_mutation(tmp_path, monkeypatch, task):
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    plan = ({'visual_review_provider': 'qiniu', 'execute_required_reviews': True} if task == 'review'
        else {'ocr_provider': 'paddle_api'})
    selected = ReportExtractionRequest.model_validate({
        'input': {'kind': 'pdf', 'asset_id': 'synthetic-only'}, 'standards': request().model_dump()['standards'],
        'model_plan': plan, 'role_config': {'semantic_brain': {'provider': 'local_qwen'},
            'specialist': {'provider': 'local_qwen'}, 'cloud_authorization': {'allowed': True, 'allow_ocr_api': True}}})
    roles = selected.role_config.model_dump(mode='json')
    monkeypatch.setattr(api, 'preview_report_session', lambda value: {'roles': roles, 'document_preparation': {'ir_reused': False, 'ocr_reused': False}})
    with pytest.raises(HTTPException) as error:
        api.create_report_session(selected)
    assert error.value.status_code == 400 and queue.list() == [] and engine.list() == []
    assert remote == []


def test_pdf_preview_maps_real_preparation_but_does_not_schedule(tmp_path, monkeypatch):
    api, queue, engine, remote, state = prepare_api(tmp_path, monkeypatch)
    selected = ReportExtractionRequest.model_validate({'input': {'kind': 'pdf', 'asset_id': 'synthetic-only'},
        'standards': request().model_dump()['standards'], 'model_plan': {'visual_review_provider': 'local_nuextract', 'execute_visual_reviews': True}})
    configurations = []
    monkeypatch.setattr(api, 'report_assets', SimpleNamespace(get=lambda asset: {'asset_id': asset}))
    monkeypatch.setattr(api, 'report_processing', type('SyntheticPreparation', (), {
        'resolve_document': lambda self, asset, config: configurations.append(config) or {'asset_id': asset, 'file_name': 'synthetic.pdf'}})())
    preview = api.preview_report_session(selected)
    assert configurations[0].review_provider == 'local_nuextract'
    assert preview['document_preparation']['review_provider'] == 'local_nuextract'
    assert preview['document_preparation']['optional_reviews'] is True
    assert preview['document_preparation']['ocr_provider'] == 'local_paddleocr'
    assert not preview['document_preparation']['ocr_reused'] and queue.list() == []
