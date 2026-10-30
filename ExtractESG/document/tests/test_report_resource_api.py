from fastapi import FastAPI
from fastapi.testclient import TestClient
from esg_reports.repository import ReportRepository
from esg_document.api.report_resources import resource_router
import json
from esg_reports.repository import file_digest


def test_resource_listing_separates_existing_pdfs_from_metadata_only_rows(tmp_path):
    repo=ReportRepository(tmp_path/'library')
    present=repo.ensure_source('1'*64,'available.pdf')
    missing=repo.ensure_source('2'*64,'unavailable.pdf')
    class Assets:
        def refresh(self,**kwargs):return {'assets':[{'asset_id':present.asset_id,'sha256':present.sha256,'file_name':present.original_filename}]}
    app=FastAPI();app.include_router(resource_router(Assets(),repo));client=TestClient(app)
    rows=client.get('/api/report-resources').json()['reports']
    assert len(rows)==1 and rows[0]['pdf_id']==present.pdf_id and rows[0]['asset_available'] is True
    all_rows=client.get('/api/report-resources?include_unavailable=true').json()['reports']
    assert len(all_rows)==2 and next(r for r in all_rows if r['pdf_id']==missing.pdf_id)['asset_available'] is False


def test_browser_upload_uses_batch_metadata_and_does_not_start_ocr(tmp_path):
    repo=ReportRepository(tmp_path/'library')
    class Assets:
        def refresh(self,**kwargs):return {'assets':[{'asset_id':'pdf-'+file_digest(p)[:24],'sha256':file_digest(p),'file_name':p.name} for p in repo.library.glob('*.pdf')]}
    app=FastAPI();app.include_router(resource_router(Assets(),repo));client=TestClient(app)
    metadata={'company':{'name':'上传企业'},'publication_year':2026,'reporting_year':2025,'industry':{'name':'制造业'}}
    def upload(data=metadata,content=b'%PDF-1.7\nreport'):
        return client.post('/api/report-resources/uploads',files={'file':('report.pdf',content,'application/pdf')},data={'metadata':json.dumps(data)})
    response=upload();assert response.status_code==201,response.text
    resource=response.json()['resource'];assert resource['company']['name']=='上传企业'
    assert resource['publication_year']==2026 and resource['reporting_year']==2025
    assert upload().json()['result']['reports'][0]['status']=='already_present'
    assert len(client.get('/api/report-resources').json()['reports'])==1
    assert not (tmp_path/'ocr_output').exists()
    before=len(repo.list())
    invalid=upload({**metadata,'api_key':'never-echo-this-secret'},content=b'%PDF-1.7\nother')
    assert invalid.status_code==422 and 'never-echo-this-secret' not in invalid.text
    assert len(repo.list())==before and len(list(repo.library.glob('*.pdf')))==1
    assert upload(content=b'not a PDF').status_code==409
    blocked=client.post('/api/report-resources/uploads',files={'file':('new.pdf',b'%PDF-1.7\nforeign','application/pdf')},data={'metadata':json.dumps(metadata)},headers={'Origin':'https://foreign.example'})
    assert blocked.status_code==403 and len(repo.list())==before
