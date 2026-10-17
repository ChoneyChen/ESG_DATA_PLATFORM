from fastapi.testclient import TestClient
from PlatESG.backend.main import create_app


def test_local_file_reservation_is_explicit_admin_scoped_and_keeps_sql_snapshots(tmp_path,monkeypatch):
    app=create_app({'PLATESG_DATA_DIR':str(tmp_path/'database'),'PLATESG_REVIEW_TOKEN':'synthetic-admin',
                    'PLATESG_AUTO_PUBLISH':'0'})
    calls=[]
    def call(method,payload):
        calls.append((method,payload))
        return {'schema_version':'lifecycle-references-v1','verified':True,'blockers':[],
                'scope':{'job_ids':payload['job_ids'],'ir_run_ids':[], 'source_sha256s':[],
                         'local_files_only':payload.get('local_files_only',False)},
                'reservation_id':'00000000-0000-4000-8000-000000000000'}
    monkeypatch.setattr(app.state.bridge,'call',call)
    client=TestClient(app)
    body={'job_ids':['synthetic-job'],'reason':'Remove local bytes, retain SQL snapshot','local_files_only':True}
    assert client.post('/internal/v1/lifecycle/reserve',json=body).status_code==401
    response=client.post('/internal/v1/lifecycle/reserve',json=body,headers={'Authorization':'Bearer synthetic-admin'})
    assert response.status_code==200 and response.json()['verified'] is True
    assert calls[0][0]=='lifecycle_reserve' and calls[0][1]['local_files_only'] is True
