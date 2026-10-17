"""Cross-service handoff fault checks, with no model or live database access."""
import json
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from esg_v2.api.console import console_router


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'output';bundle=root/'tx-fixture';(bundle/'input').mkdir(parents=True)
    manifest={'source':{'pdf_sha256':'a'*64},'run_id':'ir-fixed'}
    (bundle/'manifest.json').write_text('{}')
    (bundle/'input/request.json').write_text(json.dumps({'ir_run_id':'ir-fixed'}))
    (bundle/'input/ir-manifest.json').write_text(json.dumps(manifest))
    asset={'asset_id':'pdf-fixture','sha256':'a'*64}
    report={'report_id':'report-confirmed','revision':2,'metadata':{'title':'Explicit report','publisher_entity_ref':'verified-group','period_start':'2025-01-01','period_end':'2025-12-31','language':'zh'},
        'asset_versions':[{'asset_id':'pdf-fixture','source_sha256':'a'*64,'version':1}]}
    processing=SimpleNamespace(report=lambda _:report,_manifest=lambda *_:(manifest,'digest'),integrity_check=lambda *_:None,assets=SimpleNamespace(get=lambda _:asset))
    plan={'plan_id':'plan-fixed','report_request':{},'documents':[{'ir_run_id':'ir-fixed','native_session_id':'native-fixed'}],'status':'partial'}
    # A nonempty real-shaped request marks a report extraction plan.
    plan['report_request']={'name':'isolated contract fixture'}
    calls=[]
    def rpc(method,url,**kwargs):
        calls.append((method,url,kwargs))
        class Response:
            status_code=200
            def json(self):
                return {'operator_ref':'server-actor','roles':['review']} if url.endswith('/me') else {'receipt_id':'canonical-receipt','status':'imported','imported':1}
        return Response()
    monkeypatch.setattr('esg_v2.api.console.requests.request',rpc)
    app=FastAPI();app.include_router(console_router(processing,SimpleNamespace(list=lambda:[plan]),lambda _:{'jobs':[{'job_id':'tx-fixture'}]},None,output_root=root))
    return TestClient(app),report,plan,asset,calls,bundle


def test_handoff_requires_auth_explicit_identity_revision_and_fixed_evidence(tmp_path,monkeypatch):
    c,report,plan,asset,calls,bundle=fixture(tmp_path,monkeypatch)
    url='/api/console/report-sessions/plan-fixed/deliver';body={'report_id':'report-confirmed','expected_revision':2}
    assert c.post(url,json=body).status_code==401
    headers={'Authorization':'Bearer fixture-secret'}
    assert c.post(url,json={**body,'expected_revision':1},headers=headers).status_code==409
    report['metadata']['period_start']=None
    response=c.post(url,json=body,headers=headers)
    assert response.status_code==409 and response.json()['detail']['field_blockers']==['period_start']
    report['metadata']['period_start']='2025-01-01'
    assert c.post(url,json={**body,'organization_ref':'other'},headers=headers).status_code==409
    asset['sha256']='b'*64
    assert c.post(url,json=body,headers=headers).status_code==409
    asset['sha256']='a'*64
    (bundle/'input/ir-manifest.json').write_text('{}')
    assert c.post(url,json=body,headers=headers).status_code==409
    assert not any(url.endswith('/deliveries') for _,url,_ in calls)


def test_delivery_uses_server_bundle_and_immutable_report_identity(tmp_path,monkeypatch):
    c,report,plan,asset,calls,bundle=fixture(tmp_path,monkeypatch)
    response=c.post('/api/console/report-sessions/plan-fixed/deliver',json={'report_id':'report-confirmed','expected_revision':2},headers={'Authorization':'Bearer fixture-secret'})
    assert response.status_code==200,response.text
    data=response.json();assert data['status']=='imported'
    assert data['deliveries'][0]['receipt_id']=='canonical-receipt'
    payload=calls[-1][2]['json']
    assert payload['bundle_path']==str(bundle.resolve())
    assert payload['report_metadata']['source_artifact_ref']=='pdf-fixture'
    assert payload['report_metadata']['organization_ref']=='verified-group'
    assert payload['report_metadata']['report_ref']=='report-confirmed'
    assert c.post('/api/console/report-sessions/plan-fixed/deliver',json={'report_id':'report-confirmed','expected_revision':2,'bundle_path':'/arbitrary'},headers={'Authorization':'Bearer fixture-secret'}).status_code==422
