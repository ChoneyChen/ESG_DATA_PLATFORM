from fastapi import FastAPI
from fastapi.testclient import TestClient
from esg_reports.repository import ReportRepository
from esg_v2.api.report_resources import resource_router


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
