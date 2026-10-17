import copy
from fastapi.testclient import TestClient
from esg_v2.api.report_session_read import read_report_session
from esg_v2.control_plane.shared_contracts import ReportExtractionRequest
from test_report_session_control_plane import prepare_api


def test_an_unreadable_item_stays_visible_without_breaking_its_sibling_or_mutating_state():
    plans=[{'plan_id':'synthetic-good','name':'good'}, {'plan_id':'synthetic-incomplete','name':'incomplete','lifecycle':None}]
    before=copy.deepcopy(plans)
    def project(plan):
        if plan['plan_id']=='synthetic-incomplete':raise ValueError('private stored input must not be returned')
        return {'session_id':plan['plan_id'],'execution_status':'finished'}
    def deletion(kind,plan):return {'action':'delete','method':'DELETE','endpoint':'/api/report-sessions/'+plan['plan_id']}
    rows=[read_report_session(plan,project,deletion) for plan in plans]
    assert rows[0]['execution_status']=='finished'
    assert rows[1]['execution_status']=='unavailable' and rows[1]['read_status']=='incomplete'
    assert rows[1]['available_actions'][0]['action']=='delete'
    assert rows[1]['read_error']['request_id'].startswith('error-')
    assert 'private stored input' not in str(rows) and plans==before


def test_list_and_detail_keep_a_dangling_queue_plan_readable_without_dispatch(tmp_path,monkeypatch):
    api,queue,engine,remote,native=prepare_api(tmp_path,monkeypatch)
    req=ReportExtractionRequest(input={'kind':'pdf','asset_id':'pdf-synthetic'},
        standards=[{'package_id':'esrs.e1-6','package_version':'2.0.0','metric_ids':['esrs.e1-6.metric']}])
    plan=engine.create_report(req,{'asset_id':'pdf-synthetic','file_name':'synthetic.pdf'})
    plan['status']='needs_attention';document=plan['documents'][0];document['status']='failed'
    document['steps']['ocr']={'task_id':'pt-synthetic-deleted','status':'failed','history':[{'task_id':'pt-synthetic-old'},None]}
    engine.save(plan)
    good=engine.create_report(req,{'asset_id':'pdf-other','file_name':'other.pdf'})
    client=TestClient(api.app,raise_server_exceptions=False)
    listing=client.get('/api/report-sessions?include_archived=true')
    detail=client.get('/api/report-sessions/'+plan['plan_id'])
    assert listing.status_code==detail.status_code==200
    assert {row['session_id'] for row in listing.json()}=={plan['plan_id'],good['plan_id']}
    row=detail.json()
    assert row['reference_integrity']['status']=='missing' and row['read_status']=='incomplete'
    assert 'pt-synthetic-deleted' in row['reference_integrity']['missing_task_ids']
    assert row['preparation_stages'][0]['queue_record_missing'] is True
    assert [action['action'] for action in row['available_actions']]==['delete']
    assert queue.list()==[] and remote==[]
    assert engine.list()[0] != {}  # Reading creates no replacement queue records.


def test_completed_deleted_queue_step_returns_its_run_id_and_failed_step_never_dispatches(tmp_path,monkeypatch):
    api,queue,engine,remote,native=prepare_api(tmp_path,monkeypatch)
    document={'steps':{'ocr':{'queue_record_deleted':True,'status':'completed','run_id':'ocr-synthetic'}}}
    assert engine._step({'plan_id':'synthetic'},0,document,'ocr')=='ocr-synthetic'
    document['steps']['ocr'].update(status='failed',run_id=None)
    assert engine._step({'plan_id':'synthetic'},0,document,'ocr') is None
    assert document['status']=='interrupted' and remote==[] and queue.list()==[]
