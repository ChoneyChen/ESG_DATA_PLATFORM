from fastapi.testclient import TestClient
from esg_database.api import create_app


def test_invalid_archive_never_reaches_persistence():
    class Bridge:
        def call(self,*args):raise AssertionError('Invalid archive must not reach the database')
    client=TestClient(create_app(Bridge()))
    result=client.post('/v1/ingestion/exports',content=b'not a zip',headers={'Content-Type':'application/zip'})
    assert result.status_code==422
