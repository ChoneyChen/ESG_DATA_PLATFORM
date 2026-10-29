from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from esg_v2.api.extraction import extraction_router


def test_stale_document_service_cannot_create_or_preview_extraction():
    class Coordinator:
        def list(self):return []
        def preview(self,request):raise AssertionError('stale code cannot freeze a task')
        def create(self,request):raise AssertionError('stale code cannot create a task')
    def stale():raise HTTPException(503,'restart required')
    app=FastAPI();app.include_router(extraction_router(Coordinator(),stale));client=TestClient(app)
    body={'input':{'kind':'document_ir','ir_run_id':'ir-example'},'clause_keys':['TEST_01']}
    assert client.post('/api/report-sessions/preview',json=body).status_code==503
    assert client.post('/api/report-sessions',json=body).status_code==503
    assert client.get('/api/report-sessions').json()==[]
