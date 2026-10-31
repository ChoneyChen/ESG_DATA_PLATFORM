"""Local operator checks never call another service or keep a credential."""
import pytest
from fastapi import FastAPI,Depends
from fastapi.testclient import TestClient
from esg_document.api.operator_auth import authorize_operator
from esg_runtime.local_operator import install_loopback_marker,held_authorization,LOCAL_OPERATOR,acting_for


def service():
    app=FastAPI();install_loopback_marker(app)
    @app.post("/write")
    def write():
        principal=authorize_operator(None,"admin")
        return {"principal":principal,"held":held_authorization(None)}
    return app


def test_local_operation_has_no_platform_or_network_dependency(monkeypatch):
    with TestClient(service(),client=("127.0.0.1",5000)) as client:
        result=client.post("/write")
        assert result.status_code==200 and result.json()["held"]==LOCAL_OPERATOR
    with acting_for(LOCAL_OPERATOR):
        assert authorize_operator(None,"admin")["operator_ref"]=="local-operator"
        assert held_authorization("Bearer rejected") is None


@pytest.mark.parametrize("host",["192.0.2.1","testclient"])
def test_nonlocal_request_cannot_acquire_local_identity(host):
    with TestClient(service(),client=(host,5000)) as client:
        assert client.post("/write").status_code==401


def test_local_access_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ESG_LOCAL_OPERATOR","0")
    with TestClient(service(),client=("127.0.0.1",5000)) as client:
        assert client.post("/write").status_code==401


def test_browser_write_from_foreign_origin_is_refused_before_local_authorization():
    with TestClient(service(),client=("127.0.0.1",5000)) as client:
        assert client.post("/write",headers={"Origin":"https://outside.example"}).status_code==403
        assert client.post("/write",headers={"Origin":"http://127.0.0.1:18081"}).status_code==200
