"""Exercise the real API against temporary ledgers and a fake queue transport."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient

from esg_targeted import config
from tests.test_report_session import make_service, request


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    monkeypatch.setattr(config, "settings", service.settings)
    # This fixture restarts the API repeatedly inside one Python process. Its
    # boot identity must restart too, as it would in a real service process.
    # Compatibility enforcement remains enabled; stale-runtime behavior has a
    # dedicated real-HTTP test using isolated stores.
    from esg_targeted import runtime_compatibility
    monkeypatch.setattr(runtime_compatibility, "runtime_release",
        runtime_compatibility._module.RuntimeRelease(runtime_compatibility.EXTRACT_ROOT, "targeted-backend"))
    sys.modules.pop("esg_targeted.api.main", None)
    api = importlib.import_module("esg_targeted.api.main")
    # Tests must not discover a running publication service through its default
    # localhost URL or write synthetic reservations into a real local store.
    from esg_runtime.lifecycle import PublicationReferenceGuard
    def unavailable_reference_service(*args, **kwargs):
        raise OSError("isolated test: reference service unavailable")
    guard = PublicationReferenceGuard(transport=unavailable_reference_service)
    api.lifecycle.guard = guard
    api.lifecycle.trash.guard = guard
    calls = []
    monkeypatch.setattr(api, "enqueue_existing", lambda kind, identity, **credentials:
        calls.append((kind, identity, credentials)) or {"task_id": "task-fake"})
    monkeypatch.setattr(api, "cancel_existing", lambda identity:
        calls.append(("cancel", identity, {})) or {"cancel_requested": True})
    with TestClient(api.app) as client:
        yield client, api, calls
    sys.modules.pop("esg_targeted.api.main", None)


def test_native_report_api_submits_one_master_and_preserves_projection_ownership(api_client, monkeypatch):
    client, api, calls = api_client
    monkeypatch.setattr(api, "_intermediate_operator", lambda *args, **kwargs: "isolated-operator")
    response = client.post("/api/report-sessions", json=request().model_dump(mode="json"))
    assert response.status_code == 202
    report = response.json()
    assert len(report["job_ids"]) == 3
    assert calls[0][:2] == ("report-session", report["session_id"])
    assert client.request("DELETE", f"/api/jobs/{report['job_ids'][0]}", json={"expected_revision": 0,
        "identity_sha256": "0" * 64}).status_code == 409
    assert not hasattr(api, "executor")


def test_legacy_job_api_only_submits_existing_id_and_freezes_inputs(api_client):
    client, api, calls = api_client
    body = {"ir_run_id": "ir-fixture-e2-4", "package_id": "esrs.2023-set1.e2-4",
            "package_version": "2.0.0", "metric_ids": ["esrs.2023-set1.e2-4.dp02"]}
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 202
    job = response.json()
    assert calls[0][:2] == ("targeted-extraction", job["job_id"])
    assert api.artifact_store.exists(job["job_id"], "input/request.json")
    assert client.post(f"/api/jobs/{job['job_id']}/resume").status_code == 202
    assert calls[-1][:2] == calls[0][:2]


def test_native_cancel_notifies_queue_once_and_callback_never_recurses(api_client):
    client, _, calls = api_client
    report = client.post("/api/report-sessions?enqueue=false", json=request().model_dump(mode="json")).json()
    assert not calls
    assert client.post(f"/api/report-sessions/{report['session_id']}/cancel").status_code == 200
    assert calls == [("cancel", report["session_id"], {})]
    assert client.post(f"/api/report-sessions/{report['session_id']}/cancel?terminal=true").status_code == 200
    assert len(calls) == 1


def test_resume_credentials_reach_queue_but_never_report_ledger(api_client):
    client, api, calls = api_client
    report = client.post("/api/report-sessions?enqueue=false", json=request().model_dump(mode="json")).json()
    client.post(f"/api/report-sessions/{report['session_id']}/cancel?terminal=true")
    resumed = client.post(f"/api/report-sessions/{report['session_id']}/resume", json={"qiniu_api_key": "synthetic-runtime-secret"})
    assert resumed.status_code == 202
    assert calls[-1][2]["qiniu_api_key"] == "synthetic-runtime-secret"
    assert "synthetic-runtime-secret" not in str(api.report_sessions._state(report["session_id"]))
