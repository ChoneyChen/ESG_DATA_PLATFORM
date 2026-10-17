import json

from fastapi.testclient import TestClient

from PlatESG.backend.main import create_app
from PlatESG.backend.bridge import DatabaseError


def scope():
    return {'native_session_id': 'report-'+'a'*32,
        'report_identity': {'upstream_system': 'isolated-test', 'organization_ref': 'test-company',
            'report_ref': 'test-report', 'report_version_ref': 'test-report:asset:1',
            'source_artifact_ref': 'pdf-'+'b'*24, 'source_sha256': 'b'*64,
            'report_period_start': '2025-01-01', 'report_period_end': '2025-12-31'},
        'fixed_source': {'document_id': 'test-document', 'ir_run_id': 'test-ir', 'ir_revision': 1},
        'job_ids': ['tx-status-test'], 'artifacts': [{'job_id': 'tx-status-test',
            'artifact_revision_id': 'original', 'bundle_path': '/tmp/tx-status-test', 'manifest_sha256': 'c'*64}]}


def app_fixture(monkeypatch):
    operators=[{'token': 'fixture-read-only', 'operator_ref': 'fixture-reader', 'roles': ['read']},
        {'token': 'fixture-no-read', 'operator_ref': 'fixture-no-read', 'roles': ['review']}]
    app=create_app({'PLATESG_AUTO_PUBLISH': '0', 'PLATESG_OPERATORS_JSON': json.dumps(operators)})
    calls=[]
    counts={'archived_source_count': 3, 'selected_candidate_count': 0, 'internal_candidate_count': 0, 'published_observation_count': 0}
    response={'schema_version':'delivery-status-v1', 'status':'source_archived', 'checked_at':'2026-10-08T00:00:00Z',
        'native_session_id':scope()['native_session_id'], 'report_ref':'test-report', 'report_counts_scope':'fixed_report_version',
        'identity_basis':'sql_batch_stage_match', 'publication_dataset_version':3,
        'report_counts':counts, 'session_counts':counts, 'current_revision_counts':counts,
        'jobs':[], 'reason_codes':[], 'reason_messages':[]}
    monkeypatch.setattr(app.state.bridge, 'call', lambda method, params=None, **kw: calls.append((method,params)) or response)
    return app,calls,response


def test_delivery_status_is_strict_bounded_authenticated_read_query_and_typed_private_contract(monkeypatch):
    app,calls,response=app_fixture(monkeypatch);client=TestClient(app)
    path='/internal/v1/delivery-status/query';auth={'Authorization':'Bearer fixture-read-only'}
    assert client.post(path,json=scope()).status_code==401
    assert client.post(path,headers={'Authorization':'Bearer fixture-no-read'},json=scope()).status_code==403
    assert calls==[]
    result=client.post(path,headers=auth,json=scope());assert result.status_code==200,result.text
    assert result.json()==response
    assert calls==[('delivery_status',scope())]
    for body in [{**scope(),'operator_ref':'forged'}, {**scope(),'action':'deliver'},
        {**scope(),'job_ids':['tx-status-test']*101}, {**scope(),'job_ids':['tx-status-test','tx-status-test']},
        {**scope(),'artifacts':[scope()['artifacts'][0]]*2},
        {**scope(),'artifacts':[{**scope()['artifacts'][0],'bundle_path':'../../arbitrary'}]},
        {**scope(),'report_identity':{**scope()['report_identity'],'report_period_end':'2024-12-31'}},
        {**scope(),'fixed_source':{**scope()['fixed_source'],'actor':'forged'}}]:
        assert client.post(path,headers=auth,json=body).status_code==422
    assert len(calls)==1
    assert client.get('/v1/delivery-status').status_code==404
    assert client.post('/v1/delivery-status/query',json=scope()).status_code==405
    for revision in [True, False, '1', 1.0]:
        assert client.post(path,headers=auth,json={**scope(),'fixed_source':{**scope()['fixed_source'],'ir_revision':revision}}).status_code==422
    schema=client.get('/openapi.json').json()
    assert '/internal/v1/delivery-status/query' in schema['paths']
    assert 'DeliveryStatusResponse' in schema['components']['schemas']


def test_adapter_offline_is_503_with_no_write_retry_or_invented_zero(monkeypatch):
    app,calls,_=app_fixture(monkeypatch)
    def offline(method,params=None,**kwargs):
        calls.append(method)
        raise DatabaseError('isolated adapter unavailable','adapter_unavailable')
    monkeypatch.setattr(app.state.bridge,'call',offline)
    result=TestClient(app).post('/internal/v1/delivery-status/query',headers={'Authorization':'Bearer fixture-read-only'},json=scope())
    assert result.status_code==503
    assert result.json()['code']=='adapter_unavailable'
    assert 'counts' not in result.json()
    assert calls==['delivery_status']
