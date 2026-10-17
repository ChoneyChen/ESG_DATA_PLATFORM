from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

from esg_targeted.contracts import ExtractionRequest
from esg_targeted.storage.execution_supervisor import watchdog_reason
from esg_targeted.storage.job_store import JobStore


def test_wake_after_wall_deadline_keeps_remaining_active_allowance():
    job = SimpleNamespace(cancel_requested=False)
    state = {"call_label": "embedding_index", "call_deadline": 700,
             "call_active_deadline": 610, "call_wall_safety_deadline": 86500}
    assert watchdog_reason(state, job, now=800, active_now=180, owner_alive=True) is None
    assert "active hard deadline" in watchdog_reason(state, job, now=1300, active_now=610, owner_alive=True)


def test_active_clock_still_has_independent_wall_safety_limit():
    job = SimpleNamespace(cancel_requested=False)
    state = {"call_label": "read", "call_active_deadline": 610,
             "call_wall_safety_deadline": 86500}
    assert "wall safety" in watchdog_reason(state, job, now=86500, active_now=180, owner_alive=True)


def test_legacy_deadline_cancel_and_owner_checks_remain_independent():
    job = SimpleNamespace(cancel_requested=False)
    state = {"call_label": "legacy", "call_deadline": 10}
    assert "hard deadline" in watchdog_reason(state, job, now=10, owner_alive=True)
    assert "Worker exited" in watchdog_reason(state, job, now=0, owner_alive=False)
    job.cancel_requested = True
    assert "Cancellation" in watchdog_reason(state, job, now=0, owner_alive=True)


def test_old_sqlite_supervision_migrates_and_clears_active_call_identity(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE execution_supervision (job_id TEXT PRIMARY KEY,owner_pid INTEGER NOT NULL,owner_started TEXT NOT NULL,watchdog_pid INTEGER,active INTEGER NOT NULL DEFAULT 1,call_sequence INTEGER NOT NULL DEFAULT 0,call_label TEXT,call_deadline REAL,heartbeat_at TEXT)")
    store = JobStore(path)
    request = ExtractionRequest(ir_run_id="fake", package_id="fake", package_version="1.0", metric_ids=["fake"])
    store.create("fake", request, str(tmp_path / "output"))
    store.start_supervision("fake", os.getpid(), store.process_started(os.getpid()))
    first = store.begin_model_call("fake", "read", 10, active_deadline=5, wall_safety_deadline=20)
    state = store.supervision("fake")
    assert state["call_active_deadline"] == 5 and state["call_wall_safety_deadline"] == 20
    store.end_model_call("fake", first)
    assert store.supervision("fake")["call_active_deadline"] is None
    second = store.begin_model_call("fake", "next", 30, active_deadline=15, wall_safety_deadline=40)
    store.end_model_call("fake", first)  # A late completion cannot clear the next call.
    assert store.supervision("fake")["call_sequence"] == second
    assert store.supervision("fake")["call_active_deadline"] == 15
    store.stop_supervision("fake", os.getpid())
    assert store.supervision("fake")["call_active_deadline"] is None
