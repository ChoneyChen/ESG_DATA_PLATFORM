"""A synthetic unfinished PDF plan must remain readable without an IR identity."""
import json

from fastapi.testclient import TestClient
import pytest

from esg_v2.control_plane.shared_contracts import ReportExtractionRequest
from test_report_session_control_plane import prepare_api


@pytest.mark.parametrize("state", ["active", "needs_attention", "interrupted", "cancelled"])
def test_list_and_detail_before_ir_do_not_adopt_an_unrelated_root(tmp_path, monkeypatch, state):
    api, queue, engine, remote, native = prepare_api(tmp_path, monkeypatch)
    req = ReportExtractionRequest(input={"kind": "pdf", "asset_id": "pdf-synthetic"},
        standards=[{"package_id": "esrs.e1-6", "package_version": "2.0.0", "metric_ids": ["esrs.e1-6.metric"]}])
    plan = engine.create_report(req, {"asset_id": "pdf-synthetic", "file_name": "synthetic.pdf"})
    plan["status"] = state
    plan["documents"][0]["status"] = "pending" if state == "active" else "failed"
    engine.save(plan)
    unrelated = api.settings.document_ir_output_root / "ir-unrelated-root"
    unrelated.mkdir()
    (unrelated / "manifest.json").write_text(json.dumps({"run_id": unrelated.name,
        "can_build_evidence": True, "source": {"pdf_sha256": "a" * 64}}))
    client = TestClient(api.app, raise_server_exceptions=False)
    listing = client.get("/api/report-sessions?include_archived=false")
    detail = client.get("/api/report-sessions/" + plan["plan_id"])
    assert listing.status_code == detail.status_code == 200
    assert len(listing.json()) == 1 and listing.json()[0]["session_id"] == plan["plan_id"]
    assert detail.json()["native_session_id"] is None
    assert detail.json()["result_status"] == "not_evaluated"
    assert not any(a["action"] in {"local_repair", "evidence_transition"} for a in detail.json()["available_actions"])
    assert remote == [] and queue.list() == []


def test_transition_before_fixed_ir_is_a_domain_conflict_without_dispatch(tmp_path, monkeypatch):
    api, queue, engine, remote, native = prepare_api(tmp_path, monkeypatch)
    req = ReportExtractionRequest(input={"kind": "pdf", "asset_id": "pdf-synthetic"},
        standards=[{"package_id": "esrs.e1-6", "package_version": "2.0.0", "metric_ids": ["esrs.e1-6.metric"]}])
    plan = engine.create_report(req, {"asset_id": "pdf-synthetic", "file_name": "synthetic.pdf"})
    response = TestClient(api.app, raise_server_exceptions=False).post(
        "/api/report-sessions/" + plan["plan_id"] + "/evidence-transition",
        json={"new_ir_run_id": "ir-unrelated-root", "reason": "synthetic invalid-stage request"})
    assert response.status_code == 409 and "尚未生成" in response.json()["detail"]
    assert remote == [] and queue.list() == []
