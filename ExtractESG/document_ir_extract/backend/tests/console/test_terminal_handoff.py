"""Immutable artifact handoff and facade projection, isolated mocked transport."""
import hashlib
import json
import shutil

from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.console.test_delivery import fixture
from tests.test_report_session_control_plane import prepare_api, request


def test_console_delivers_only_native_fixed_revision_and_rejects_changed_manifest(tmp_path,monkeypatch):
    client,report,plan,asset,calls,original=fixture(tmp_path,monkeypatch)
    revision_id='partial-'+'a'*32
    revision=original/'terminal-revisions'/revision_id
    shutil.copytree(original/'input',revision/'input')
    manifest={'job_id':original.name,'execution_status':'failed','artifact_revision':{'revision_id':revision_id}}
    (revision/'manifest.json').write_text(json.dumps(manifest))
    digest=hashlib.sha256((revision/'manifest.json').read_bytes()).hexdigest()
    descriptor={'job_id':original.name,'artifact_revision_id':revision_id,'bundle_relative_path':f'terminal-revisions/{revision_id}',
        'manifest_sha256':digest,'created_at':'2026-10-07T00:00:00Z','source_fact_count':5,'core_fact_count':0,'status':'available'}
    # Resolve the endpoint's captured session projection without real services.
    # Session callback lives in a closure, so construct a fresh router fixture.
    from fastapi import FastAPI
    from types import SimpleNamespace
    from esg_v2.api.console import console_router
    fixed_ir=json.loads((original/'input/ir-manifest.json').read_text())
    processing=SimpleNamespace(report=lambda _:report,_manifest=lambda *_:(fixed_ir,'sha'),integrity_check=lambda *_:None,
        assets=SimpleNamespace(get=lambda _:asset))
    session={'jobs':[{'job_id':original.name}],'delivery_artifacts':[descriptor]}
    app=FastAPI();app.include_router(console_router(processing,SimpleNamespace(list=lambda:[plan]),lambda _:session,None,output_root=original.parent))
    client=TestClient(app)
    body={'report_id':report['report_id'],'expected_revision':report['revision']}
    headers={'Authorization':'Bearer isolated-review-token'}
    response=client.post('/api/console/report-sessions/plan-fixed/deliver',json=body,headers=headers)
    assert response.status_code==200,response.text
    assert calls[-1][2]['json']['bundle_path']==str(revision.resolve())
    assert response.json()['deliveries'][0]['saved_source_fact_count']==5
    assert response.json()['deliveries'][0]['saved_core_fact_count']==0
    posted=len([call for call in calls if call[1].endswith('/deliveries')])
    (revision/'manifest.json').write_text(json.dumps(manifest)+' ')
    assert client.post('/api/console/report-sessions/plan-fixed/deliver',json=body,headers=headers).status_code==409
    assert len([call for call in calls if call[1].endswith('/deliveries')])==posted
    descriptor['bundle_relative_path']='../../outside'
    assert client.post('/api/console/report-sessions/plan-fixed/deliver',json=body,headers=headers).status_code==409


def test_facade_keeps_actual_delivery_metadata_and_translates_auth_materialization(tmp_path,monkeypatch):
    api,queue,engine,_,state=prepare_api(tmp_path,monkeypatch)
    ir=api.settings.document_ir_output_root/'ir-existing';ir.mkdir()
    (ir/'manifest.json').write_text(json.dumps({'run_id':'ir-existing','ocr_run_id':'ocr-existing','can_build_evidence':True}))
    created=api.create_report_session(request())
    state.update(execution_status='failed',result_status='needs_confirmation',
        delivery_artifacts=[{'job_id':'tx-fixed','source_fact_count':291,'core_fact_count':0,'status':'available'}],
        delivery_counts={'saved_source_facts':291,'saved_core_facts':0,'available_bundle_count':1,'published_observations':None},
        delivery_artifact_blockers=[])
    detail=api.get_report_session(created['session_id'])
    assert detail['delivery_artifacts']==state['delivery_artifacts']
    assert detail['delivery_counts']==state['delivery_counts']
    calls=[]
    def transport(method,path,payload=None,**kwargs):
        calls.append((method,path,payload,kwargs));return {'approval_or_publication_performed':False}
    monkeypatch.setattr(api,'_targeted_report_request',transport)
    client=TestClient(api.app)
    url=f"/api/report-sessions/{created['session_id']}/materialize"
    assert client.post(url,json={'reason':'显式阶段保全'}).status_code==401
    response=client.post(url,json={'reason':'显式阶段保全','job_ids':['tx-fixed']},headers={'Authorization':'Bearer isolated-review-token'})
    assert response.status_code==200
    assert calls[-1][1]==f"/api/report-sessions/{state['session_id']}/materialize"
    assert calls[-1][3]=={'authorization':'Bearer isolated-review-token'}
    assert client.post(url,json={'reason':'显式阶段保全','requested_by':'forged'},headers={'Authorization':'Bearer isolated-review-token'}).status_code==422
