import base64
import json

from fastapi.testclient import TestClient

from PlatESG.backend.main import create_app


def test_source_cursor_query_is_read_authorized_bounded_and_forwarded_without_rewriting(tmp_path,monkeypatch):
    app=create_app({'PLATESG_DATA_DIR':str(tmp_path/'data'),'PLATESG_AUTO_PUBLISH':'0',
                    'PLATESG_REVIEW_TOKEN':'fixture-read-cursor','PLATESG_LIFECYCLE_STORE_SCOPE':'isolated'})
    cursor=base64.urlsafe_b64encode(json.dumps({'v':1,'report_ref':'synthetic-report',
        'created_at':'2026-10-07T01:02:03.123456Z','snapshot_id':'00000000-0000-5000-a000-000000000001'},
        separators=(',',':')).encode()).decode().rstrip('=')
    with TestClient(app) as client:
        calls=[]
        def query(method,request):
            calls.append((method,request))
            return {'items':[],'has_more':False,'next_cursor':None,'scope':'materialized_report_sources_not_selected_output_or_published_observations'}
        monkeypatch.setattr(app.state.bridge,'call',query)
        params={'report_ref':'synthetic-report','limit':500,'cursor':cursor}
        assert client.get('/internal/v1/source-facts',params=params).status_code==401
        auth={'Authorization':'Bearer fixture-read-cursor'}
        result=client.get('/internal/v1/source-facts',params=params,headers=auth)
        assert result.status_code==200
        assert calls==[('source_facts',params)]
        assert result.json()['next_cursor'] is None
        for invalid in ['', 'x'*2049]:
            assert client.get('/internal/v1/source-facts',params={**params,'cursor':invalid},headers=auth).status_code==422
        assert len(calls)==1
