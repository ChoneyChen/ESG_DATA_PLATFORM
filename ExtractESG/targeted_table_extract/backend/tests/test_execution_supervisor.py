from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from esg_targeted.contracts import ExtractionRequest, JobStatus
from esg_targeted.storage.execution_supervisor import SupervisedJob, supervised_model_call, watchdog_reason
from esg_targeted.storage.job_store import JobStore


@pytest.fixture(autouse=True)
def temporary_execution_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("ESG_EXECUTION_SLOT_PATH", str(tmp_path / "model-execution.lock"))
    monkeypatch.setenv("ESG_PIPELINE_QUEUE_DB", str(tmp_path / "pipeline-queue.sqlite3"))
    monkeypatch.delenv("ESG_EXECUTION_SLOT_FD", raising=False)


def _job(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create("job-fake", ExtractionRequest(ir_run_id="ir-fake", package_id="test.package", package_version="2.0.0", metric_ids=["metric-1"]), str(tmp_path / "bundle"))
    return store


def test_recovery_preserves_a_live_worker_and_resume_refuses_it(tmp_path):
    store = _job(tmp_path)
    store.claim("job-fake", f"{os.getpid()}:fake")
    store.update("job-fake", status=JobStatus.RUNNING)
    assert store.recover_abandoned() == []
    store.update("job-fake", status=JobStatus.INTERRUPTED)
    with pytest.raises(ValueError, match="live worker"):
        store.prepare_resume("job-fake")


def test_watchdog_distinguishes_deadline_cancel_and_liveness():
    job = SimpleNamespace(cancel_requested=False)
    supervision = {"call_label": "read-table", "call_deadline": 10}
    assert watchdog_reason(supervision, job, now=9, owner_alive=True) is None
    assert "hard deadline" in watchdog_reason(supervision, job, now=10, owner_alive=True)
    assert "Worker exited" in watchdog_reason(supervision, job, now=9, owner_alive=False)
    job.cancel_requested = True
    assert "Cancellation" in watchdog_reason(supervision, job, now=9, owner_alive=True)


def test_nested_context_owns_one_watchdog_and_clears_call_deadline(tmp_path):
    store = _job(tmp_path)
    with SupervisedJob(store.path, "job-fake"):
        first = store.supervision("job-fake")["watchdog_pid"]
        with SupervisedJob(store.path, "job-fake"):
            assert store.supervision("job-fake")["watchdog_pid"] == first
            with supervised_model_call(store.path, "job-fake", "fake-small-call", timeout_seconds=20):
                assert store.supervision("job-fake")["call_deadline"] > time.time()
            assert store.supervision("job-fake")["call_deadline"] is None
    assert not store.supervision("job-fake")["active"]


def test_external_watchdog_interrupts_fake_native_hang_without_token_yields(tmp_path):
    store = _job(tmp_path)
    checkpoint = tmp_path / "bundle" / "checkpoint.txt"
    checkpoint.parent.mkdir()
    checkpoint.write_text("accepted evidence", encoding="utf-8")
    code = """
import os,sys,time
from pathlib import Path
from esg_targeted.contracts import JobStatus
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.execution_supervisor import SupervisedJob, supervised_model_call
store=JobStore(Path(sys.argv[1]))
with SupervisedJob(store.path,'job-fake'):
    store.claim('job-fake',str(os.getpid())+':fake')
    store.update('job-fake',status=JobStatus.RUNNING)
    with supervised_model_call(store.path,'job-fake','fake-native-hang',timeout_seconds=0.2):
        time.sleep(30)
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(store.path)], start_new_session=True)
    try:
        assert process.wait(timeout=8) != 0
        job = store.get("job-fake")
        assert job.status == JobStatus.INTERRUPTED
        assert "fake-native-hang" in job.error and "hard deadline" in job.error
        assert checkpoint.read_text(encoding="utf-8") == "accepted evidence"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_cancel_terminal_notification_still_stops_the_owned_worker(tmp_path, monkeypatch):
    from esg_targeted.storage.execution_supervisor import watch_job

    store = _job(tmp_path)
    pid = os.getpid()
    started = store.process_started(pid)
    store.start_supervision("job-fake", pid, started)
    store.claim("job-fake", f"{pid}:fake")
    store.request_cancel("job-fake")
    store.update("job-fake", status=JobStatus.CANCELLED)
    stopped = []
    monkeypatch.setattr("esg_targeted.storage.execution_supervisor._terminate_owner", lambda owner, birth: stopped.append((owner, birth)) or True)
    watch_job(store.path, "job-fake")
    assert stopped == [(pid, started)]
    assert not store.supervision("job-fake")["active"]
    assert store.get("job-fake").worker_id is None


def test_busy_shared_slot_preserves_job_and_checkpoint_for_resume(tmp_path):
    from esg_targeted.storage.execution_slot import ExecutionSlot, ExecutionSlotBusy, execution_slot_path

    store = _job(tmp_path)
    slot = ExecutionSlot(execution_slot_path()).acquire()
    try:
        with pytest.raises(ExecutionSlotBusy):
            with SupervisedJob(store.path, "job-fake"):
                raise AssertionError("busy slot must not start a worker")
        assert store.get("job-fake").stage == "execution_slot_conflict"
        assert store.get("job-fake").status == JobStatus.INTERRUPTED
        assert store.supervision("job-fake") is None
    finally:
        slot.close()
    assert store.prepare_resume("job-fake").status == JobStatus.QUEUED


def test_inherited_flock_survives_scheduling_parent_fd_close(tmp_path):
    from esg_targeted.storage.execution_slot import ExecutionSlot, ExecutionSlotBusy, execution_slot_path

    slot = ExecutionSlot(execution_slot_path()).acquire()
    code = """
import sys,time
from esg_targeted.storage.execution_slot import ExecutionSlot,execution_slot_path
slot=ExecutionSlot(execution_slot_path()).acquire()
print('held',flush=True)
time.sleep(0.3)
slot.close()
"""
    process = subprocess.Popen([sys.executable, "-c", code], pass_fds=(slot.fd,),
                               env={**os.environ, "ESG_EXECUTION_SLOT_FD": str(slot.fd)}, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "held"
        slot.close()
        with pytest.raises(ExecutionSlotBusy):
            ExecutionSlot(execution_slot_path()).acquire()
        process.wait(timeout=3)
        free = ExecutionSlot(execution_slot_path()).acquire()
        free.close()
    finally:
        slot.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def test_two_projection_supervisors_preserve_the_worker_inherited_descriptor(tmp_path, monkeypatch):
    from esg_targeted.storage.execution_slot import ExecutionSlot, execution_slot_path, inherited_slot_is_valid

    store = _job(tmp_path)
    store.create("job-second", store.get("job-fake").request, str(tmp_path / "second-bundle"))
    owner = ExecutionSlot(execution_slot_path()).acquire()
    owner_fd = owner.fd
    monkeypatch.setenv("ESG_EXECUTION_SLOT_FD", str(owner_fd))
    try:
        assert inherited_slot_is_valid()
        for job_id in ("job-fake", "job-second"):
            with SupervisedJob(store.path, job_id) as supervisor:
                assert supervisor.slot.fd != owner_fd
                assert os.fstat(supervisor.slot.fd).st_ino == os.fstat(owner_fd).st_ino
            assert os.fstat(owner_fd) and inherited_slot_is_valid()
        wrong = os.open(tmp_path / "unrelated", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            monkeypatch.setenv("ESG_EXECUTION_SLOT_FD", str(wrong))
            assert not inherited_slot_is_valid()
        finally:
            os.close(wrong)
    finally:
        owner.close()


def test_pre_upgrade_live_job_blocks_new_execution_without_touching_owner(tmp_path):
    from esg_targeted.storage.execution_slot import ExecutionSlotBusy

    store = _job(tmp_path)
    store.create("job-old", store.get("job-fake").request, str(tmp_path / "old-bundle"))
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        store.claim("job-old", f"{process.pid}:old-worker")
        store.update("job-old", status=JobStatus.RUNNING)
        with pytest.raises(ExecutionSlotBusy, match="job-old"):
            with SupervisedJob(store.path, "job-fake"):
                pass
        assert process.poll() is None
        assert store.get("job-old").worker_id == f"{process.pid}:old-worker"
        assert store.get("job-fake").stage == "execution_slot_conflict"
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_pre_upgrade_pipeline_worker_blocks_api_admission(tmp_path):
    import sqlite3
    from esg_targeted.storage.execution_slot import ExecutionSlotBusy

    store = _job(tmp_path)
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)", "pipeline-old"])
    try:
        with sqlite3.connect(os.environ["ESG_PIPELINE_QUEUE_DB"]) as connection:
            connection.execute("CREATE TABLE pipeline_tasks(task_id TEXT,native_job_id TEXT,task_type TEXT,worker_pid INTEGER)")
            connection.execute("INSERT INTO pipeline_tasks VALUES('pipeline-old','ocr-old','ocr',?)", (process.pid,))
        with pytest.raises(ExecutionSlotBusy, match="pipeline-old"):
            with SupervisedJob(store.path, "job-fake"):
                pass
        assert process.poll() is None
        assert store.get("job-fake").status == JobStatus.INTERRUPTED
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_stopped_worker_finalizes_candidates_without_promoting_them_to_core(tmp_path):
    import json
    from esg_targeted.storage.execution_supervisor import _publish_interrupted_stage

    output_dir = tmp_path / "job-fake"
    store = JobStore(tmp_path / "jobs.sqlite3")
    request = ExtractionRequest(ir_run_id="ir-fake", package_id="test.package", package_version="2.0.0", metric_ids=["metric-1"])
    store.create("job-fake", request, str(output_dir))
    store.update("job-fake", status=JobStatus.INTERRUPTED, error="external call deadline")
    (output_dir / "agent/sources").mkdir(parents=True)
    (output_dir / "input").mkdir()
    (output_dir / "agent/calls").mkdir()
    (output_dir / "input/request.json").write_text(request.model_dump_json(), encoding="utf-8")
    (output_dir / "agent/state.json").write_text(json.dumps({"state": "running",
        "current_call": {"attempt": 1, "phase": "generation"},
        "steps": [{"status": "running", "metric_ids": ["metric-1"]}], "gaps": []}), encoding="utf-8")
    (output_dir / "agent/calls/0001-source.json").write_text(json.dumps({"phase": "generation", "generation_tokens": 30}), encoding="utf-8")
    (output_dir / "agent/sources/object.json").write_text(json.dumps({"facts": [{"source_fact_id": "candidate-1"}], "gaps": []}), encoding="utf-8")
    _publish_interrupted_stage(store, "job-fake")
    agent = json.loads((output_dir / "agent/state.json").read_text(encoding="utf-8"))
    assert agent["state"] == agent["current_call"]["phase"] == agent["steps"][0]["status"] == "interrupted"
    call = json.loads((output_dir / "agent/calls/0001-source.json").read_text(encoding="utf-8"))
    assert call["phase"] == "interrupted" and call["generation_tokens"] == 30
    snapshot = json.loads((output_dir / "progress/snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["summary"]["execution_status"] == "interrupted"
    assert snapshot["summary"]["counts"]["candidate_source_facts"] == 1
    assert snapshot["summary"]["counts"]["core_facts"] == 0
    assert snapshot["summary"]["result_status"] == "needs_confirmation"
    assert store.get("job-fake").summary["errors"] == ["external call deadline"]
    assert not (output_dir / "manifest.json").exists()
