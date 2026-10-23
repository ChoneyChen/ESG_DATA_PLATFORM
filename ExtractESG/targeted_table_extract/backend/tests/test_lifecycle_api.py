from tests.test_report_api_submission import api_client
from tests.test_report_session import request
from tests.test_lifecycle import Guard


def test_parent_api_archive_restore_preserves_cancel_and_resume_routes(api_client):
    client,api,calls=api_client
    session=client.post("/api/report-sessions?enqueue=false",json=request().model_dump(mode="json")).json()
    identity=session["session_id"]
    assert client.post(f"/api/report-sessions/{identity}/archive",json={"reason":"尚未取消"}).status_code==409
    assert client.post(f"/api/report-sessions/{identity}/cancel?terminal=true").status_code==200
    archived=client.post(f"/api/report-sessions/{identity}/archive",json={"reason":"整理会话","expected_revision":0})
    assert archived.status_code==200 and archived.json()["state"]=="archived"
    assert client.get("/api/report-sessions").json()==[]
    assert len(client.get("/api/report-sessions?include_archived=true").json())==1
    assert len(client.get("/api/jobs?include_archived=true").json())==3
    assert client.post(f"/api/report-sessions/{identity}/resume").status_code==409
    assert client.post(f"/api/report-sessions/{identity}/restore",json={"reason":"继续使用"}).status_code==200
    assert client.post(f"/api/report-sessions/{identity}/resume").status_code==202


def test_native_projection_delete_and_archive_return_actionable_blocker(api_client, monkeypatch):
    client,api,calls=api_client
    monkeypatch.setattr(api, "_intermediate_operator", lambda *args, **kwargs: "isolated-operator")
    session=client.post("/api/report-sessions?enqueue=false",json=request().model_dump(mode="json")).json()
    job=session["job_ids"][0]
    description=client.get(f"/api/jobs/{job}/lifecycle").json()
    assert description["available_actions"][0]["action"]=="open_parent"
    denied=client.post(f"/api/jobs/{job}/archive",json={"reason":"子结果不能独立操作"})
    assert denied.status_code==409
    assert denied.json()["detail"]["blockers"][0]["code"]=="parent_owned_projection"
    assert client.request("DELETE", f"/api/jobs/{job}", json={"expected_revision": 0,
        "identity_sha256": "0" * 64}).status_code==409


def test_native_discard_checks_sources_and_recovers_all_owned_artifacts(api_client):
    client,api,calls=api_client
    session=client.post("/api/report-sessions?enqueue=false",json=request().model_dump(mode="json")).json()
    identity=session["session_id"]
    client.post(f"/api/report-sessions/{identity}/cancel?terminal=true")
    blocked=client.post(f"/api/report-sessions/{identity}/discard",json={"reason":"无保护服务"})
    assert blocked.status_code==409
    assert blocked.json()["detail"]["blockers"][0]["code"]=="reference_check_unavailable"
    guard=Guard();api.lifecycle.guard=guard;api.lifecycle.trash.guard=guard
    discarded=client.post(f"/api/report-sessions/{identity}/discard",json={"reason":"丢弃合成测试"})
    assert discarded.status_code==200 and discarded.json()["state"]=="discarded"
    receipt=client.get("/api/lifecycle/trash").json()["items"][0]
    early=client.post(f"/api/lifecycle/trash/{receipt['cleanup_id']}/purge",json={"reason":"保留期未到"})
    assert early.status_code==409
    assert early.json()["detail"]["blockers"][0]["code"]=="retention_period"
    assert client.post(f"/api/report-sessions/{identity}/restore",json={"reason":"恢复合成成果"}).status_code==200
    assert len(client.get("/api/jobs").json())==3


def test_lifecycle_reason_and_revision_are_validated(api_client):
    client,api,calls=api_client
    session=client.post("/api/report-sessions?enqueue=false",json=request().model_dump(mode="json")).json()
    identity=session["session_id"]
    client.post(f"/api/report-sessions/{identity}/cancel?terminal=true")
    assert client.post(f"/api/report-sessions/{identity}/archive",json={"reason":" "}).status_code==422
    assert client.post(f"/api/report-sessions/{identity}/archive",json={"reason":"归档","extra":True}).status_code==422
    client.post(f"/api/report-sessions/{identity}/archive",json={"reason":"第一次"})
    assert client.post(f"/api/report-sessions/{identity}/restore",json={"reason":"旧页面","expected_revision":0}).status_code==409


def test_job_progress_uses_durable_counts_and_isolates_corrupt_artifacts(api_client, monkeypatch):
    client,api,_=api_client
    session=client.post("/api/report-sessions?enqueue=false",json=request().model_dump(mode="json")).json()
    identity=session["job_ids"][0]
    job=api.job_store.get(identity).model_copy(update={"progress_current":100,"progress_total":100})
    monkeypatch.setattr(api,"collect_stage",lambda *args,**kwargs: {"summary":{"counts":{"selected_metrics":4,"processed_metrics":1}}})
    progress=api._job_with_lifecycle(job)["progress"]
    assert progress["percent"]==25 and progress["unit"]=="requirements"
    assert progress['last_business_progress_at'] is None
    errored=job.model_copy(update={'error':"Model call 'repair_format' exceeded its external hard deadline; checkpoints retained."})
    error=api._job_with_lifecycle(errored)['error_detail']
    assert error['category']=='runtime' and error['code']=='execution_deadline'
    assert '检查点' in error['message'] and 'hard deadline' in error['technical_detail']
    def corrupt(*args,**kwargs):
        raise ValueError("invalid historical snapshot")
    monkeypatch.setattr(api,"collect_stage",corrupt)
    response=client.get("/api/jobs?include_archived=true")
    assert response.status_code==200 and len(response.json())==3
    assert all(not row["progress"]["determinate"] for row in response.json())
    assert all(row["progress_error"]["category"]=="source" for row in response.json())
