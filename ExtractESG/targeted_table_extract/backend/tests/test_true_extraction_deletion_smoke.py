"""Synthetic temporary jobs and evidence; no failed production fixture."""
import json
from types import SimpleNamespace

import pytest
from esg_runtime.lifecycle import LifecycleBlocked
from esg_targeted.contracts import ExtractionRequest, JobStatus
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.record_deletion import ExtractionRecordDeletion
from esg_targeted.storage.recovery import recover_result_jobs, result_catalog_status
from esg_targeted.api.record_deletion import extraction_deletion_router
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class Guard:
    def __init__(self, referenced=False, verified=True): self.referenced=referenced; self.verified=verified; self.scopes=[]
    def check(self, scope): return {"verified": self.verified, "blockers": [{"code":"published_source_reference", "message":"合成已发布来源"}] if self.referenced else []}
    def reserve(self, scope, reason): self.scopes.append(scope); return "synthetic-reservation"
    def update_reservation(self, *args): return {}


def fixture(tmp_path, guard=None, status=JobStatus.COMPLETED):
    jobs=JobStore(tmp_path/"jobs.sqlite3"); artifacts=ArtifactStore(tmp_path/"outputs")
    request=ExtractionRequest(ir_run_id="ir-synthetic",package_id="esrs.synthetic",package_version="1.0.0",metric_ids=["synthetic"])
    job=jobs.create("tx-synthetic",request,str(artifacts.job_dir("tx-synthetic")))
    jobs.update(job.job_id,status=status)
    artifacts.write_json(job.job_id,"input/request.json",request.model_dump(mode="json"))
    artifacts.write_json(job.job_id,"manifest.json",{"job_id":job.job_id,"execution_status":"completed","outcomes":[]})
    with jobs._connect() as db: db.execute("CREATE TABLE report_sessions(session_id TEXT PRIMARY KEY,state_json TEXT)")
    session=SimpleNamespace(settings=SimpleNamespace(ir_roots=(tmp_path/"ir",)))
    calls=[]
    def cancel(kind, identity):
        calls.append((kind,identity)); jobs.request_cancel(identity); jobs.update(identity,status=JobStatus.CANCELLED)
    service=ExtractionRecordDeletion(jobs,artifacts,session,cancel,guard=guard or Guard())
    return service,jobs,artifacts,calls


def test_unreferenced_job_record_events_and_outputs_really_deleted(tmp_path):
    service,jobs,artifacts,calls=fixture(tmp_path)
    payload=service.describe("job","tx-synthetic")["available_actions"][0]["payload"]
    result=service.request("job","tx-synthetic",**payload)
    assert result["deleted"] is True and result["status"]=="deleted" and not result["source_artifacts_preserved"]
    assert jobs.list(include_archived=True)==[] and not artifacts._job_path("tx-synthetic").exists() and calls==[]
    with jobs._connect() as db: assert not db.execute("SELECT 1 FROM events").fetchone()
    assert service.guard.scopes==[{"job_ids":["tx-synthetic"],"ir_run_ids":[]}]


@pytest.mark.parametrize("guard",[Guard(referenced=True),Guard(verified=False)])
def test_delete_execution_record_keeps_referenced_or_unverified_source_and_does_not_resurrect(tmp_path,guard):
    service,jobs,artifacts,calls=fixture(tmp_path,guard)
    before=artifacts.resolve("tx-synthetic","manifest.json").read_bytes()
    service.request("job","tx-synthetic",**service.describe("job","tx-synthetic")["available_actions"][0]["payload"])
    assert jobs.list(include_archived=True)==[] and artifacts.resolve("tx-synthetic","manifest.json").read_bytes()==before
    assert recover_result_jobs(jobs,artifacts)==[]
    assert result_catalog_status(jobs,artifacts)["orphan_artifact_count"]==0


def test_active_deletion_waits_and_recovers_persistent_intent_after_restart(tmp_path):
    service,jobs,artifacts,calls=fixture(tmp_path,status=JobStatus.RUNNING)
    result=service.request("job","tx-synthetic",**service.describe("job","tx-synthetic")["available_actions"][0]["payload"])
    assert result["status"]=="deletion_pending" and result["deleted"] is False
    restarted=ExtractionRecordDeletion(jobs,artifacts,service.sessions,service.cancel_callback,guard=Guard())
    restarted.tick()
    assert jobs.list(include_archived=True)==[] and calls==[("job","tx-synthetic")]


def test_stale_identity_never_cancels_or_deletes(tmp_path):
    service,jobs,artifacts,calls=fixture(tmp_path)
    with pytest.raises(LifecycleBlocked): service.request("job","tx-synthetic",expected_revision=0,identity_sha256="f"*64)
    assert jobs.get("tx-synthetic") and calls==[]


def test_terminal_projection_delete_prunes_only_its_parent_pointer(tmp_path):
    service,jobs,artifacts,calls=fixture(tmp_path)
    parent="report-"+"a"*32
    request=jobs.get("tx-synthetic").request.model_copy(update={"report_session_id":parent})
    jobs.create("tx-other",request,str(artifacts.job_dir("tx-other")));jobs.update("tx-other",status=JobStatus.COMPLETED)
    with jobs._connect() as db:
        db.execute("UPDATE jobs SET request_json=? WHERE job_id='tx-synthetic'",(request.model_dump_json(),))
        db.execute("INSERT INTO report_sessions VALUES(?,?)",(parent,json.dumps({"session_id":parent,"revision":1,"created_at":"parent-time","execution_status":"finished","projections":[{"job_id":"tx-synthetic"},{"job_id":"tx-other"}]})))
    result=service.request("job","tx-synthetic",**service.describe("job","tx-synthetic")["available_actions"][0]["payload"])
    with jobs._connect() as db: state=json.loads(db.execute("SELECT state_json FROM report_sessions").fetchone()[0])
    assert result["deleted"] is True and state["projections"]==[{"job_id":"tx-other"}]
    assert jobs.get("tx-other") and calls==[]


def test_deleting_last_projection_removes_parent_record_as_well(tmp_path):
    service,jobs,artifacts,calls=fixture(tmp_path,Guard(referenced=True))
    parent="report-"+"a"*32
    request=jobs.get("tx-synthetic").request.model_copy(update={"report_session_id":parent})
    with jobs._connect() as db:
        db.execute("UPDATE jobs SET request_json=? WHERE job_id='tx-synthetic'",(request.model_dump_json(),))
        db.execute("INSERT INTO report_sessions VALUES(?,?)",(parent,json.dumps({"session_id":parent,"revision":1,"created_at":"parent-time","execution_status":"finished","projections":[{"job_id":"tx-synthetic"}]})))
    service.request("job","tx-synthetic",**service.describe("job","tx-synthetic")["available_actions"][0]["payload"])
    with jobs._connect() as db: assert not db.execute("SELECT 1 FROM report_sessions").fetchone()
    assert artifacts.resolve("tx-synthetic","manifest.json").is_file()


def test_native_delete_http_auth_and_true_delete_response(tmp_path):
    service,jobs,artifacts,calls=fixture(tmp_path)
    def authorize(value):
        if value!="Bearer synthetic": raise HTTPException(401,"synthetic principal required")
    app=FastAPI();app.include_router(extraction_deletion_router(service,authorize));client=TestClient(app)
    assert client.get("/api/jobs/tx-synthetic/deletion").status_code==401
    description=client.get("/api/jobs/tx-synthetic/deletion",headers={"Authorization":"Bearer synthetic"}).json()
    result=client.request("DELETE","/api/jobs/tx-synthetic",headers={"Authorization":"Bearer synthetic"},json=description["available_actions"][0]["payload"])
    assert result.status_code==200 and result.json()["deleted"] is True and jobs.list(include_archived=True)==[]
