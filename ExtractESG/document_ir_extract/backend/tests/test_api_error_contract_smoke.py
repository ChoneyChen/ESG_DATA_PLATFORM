from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from esg_v2.api.error_contract import register_api_errors


def test_uncaught_read_error_is_json_and_has_private_log_correlation():
    app = FastAPI()
    register_api_errors(app, "document")
    @app.get("/synthetic/read")
    def read(): raise KeyError("private synthetic report content")
    response = TestClient(app, raise_server_exceptions=False).get("/synthetic/read")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    detail = response.json()["detail"]
    assert detail["request_id"] == response.headers["X-Request-ID"]
    assert detail["layer"] == "document" and detail["code"] == "internal_service_error"
    assert "private" not in response.text and "KeyError" not in response.text


def test_uncertain_write_result_does_not_instruct_automatic_resubmission():
    app = FastAPI()
    register_api_errors(app, "document")
    @app.post("/synthetic/write")
    def write(): raise RuntimeError("synthetic uncertain outcome")
    response = TestClient(app, raise_server_exceptions=False).post("/synthetic/write")
    assert response.status_code == 500 and "勿重复提交" in response.json()["detail"]["message"]


def test_expected_domain_failure_keeps_its_own_status_and_detail():
    app = FastAPI()
    register_api_errors(app, "document")
    @app.get("/synthetic/conflict")
    def conflict(): raise HTTPException(409, {"code": "evidence_not_ready", "message": "synthetic conflict"})
    response = TestClient(app).get("/synthetic/conflict")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "evidence_not_ready"
