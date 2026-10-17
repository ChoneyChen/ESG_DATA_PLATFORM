"""Deadlines and resource ownership inside the unified pipeline worker.

The external watchdog remains independent of MLX native evaluation. Dispatch
belongs to the unified queue; this module supervises one worker-owned job.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from esg_targeted.contracts import JobStatus
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.execution_slot import ExecutionSlot, ExecutionSlotBusy, execution_slot_path
from esg_targeted.storage.power import IdleSleepAssertion, WatchdogClock


_contexts: set[tuple[str, str]] = set()
_lock = threading.Lock()
_MODULE = "esg_targeted.storage.execution_supervisor"


def legacy_worker_conflict(store: JobStore, slot_path: Path) -> str | None:
    """Admission check for known workers started before inherited slots existed."""
    pid = os.getpid()
    worker = store.other_live_worker(pid)
    if worker:
        return f"Existing extraction job {worker[0]} still owns live worker PID {worker[1]}"
    queue_path = Path(os.getenv("ESG_PIPELINE_QUEUE_DB", str(slot_path.parent / "pipeline-queue.sqlite3"))).expanduser().resolve()
    if not queue_path.exists():
        return None
    # The extraction API does not own or mutate the unified queue database.
    with sqlite3.connect(queue_path.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM pipeline_tasks WHERE worker_pid IS NOT NULL").fetchall()
    for row in rows:
        worker_pid = row["worker_pid"]
        started = row["worker_started"] if "worker_started" in row.keys() else None
        if worker_pid != pid and store._worker_alive(str(worker_pid), started):
            if started or store.process_matches(worker_pid, row["task_id"], row["native_job_id"]):
                return f"Existing pipeline task {row['task_id']} ({row['native_job_id'] or row['task_type']}) still owns live worker PID {worker_pid}"
    return None


class SupervisedJob:
    def __init__(self, state_db: Path, job_id: str) -> None:
        self.store = JobStore(Path(state_db))
        self.job_id = job_id
        self.key = (str(Path(state_db).resolve()), job_id)
        self.process: subprocess.Popen | None = None
        self.nested = False
        self.slot: ExecutionSlot | None = None
        self.power: IdleSleepAssertion | None = None

    def __enter__(self):
        with _lock:
            self.nested = self.key in _contexts
            if self.nested:
                return self
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("Native model work must run in the isolated unified pipeline worker")
            started = self.store.process_started(os.getpid())
            if not started:
                raise RuntimeError("Cannot establish worker process birth identity")
            try:
                slot_path = execution_slot_path()
                conflict = legacy_worker_conflict(self.store, slot_path)
                if conflict:
                    raise ExecutionSlotBusy(conflict)
                self.slot = ExecutionSlot(slot_path).acquire()
                self.store.start_supervision(self.job_id, os.getpid(), started)
                self.power = IdleSleepAssertion(lambda detail: self.store.add_event(
                    self.job_id, "resource_lifecycle", "info", detail["kind"], detail))
                self.power.__enter__()
                self.process = subprocess.Popen(
                    [sys.executable, "-m", _MODULE, "watch", str(self.store.path), self.job_id],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, start_new_session=True,
                )
                self.store.set_watchdog(self.job_id, os.getpid(), self.process.pid)
            except ExecutionSlotBusy as exc:
                self.store.update(self.job_id, status=JobStatus.INTERRUPTED, stage="execution_slot_conflict", error=str(exc) + "; checkpoints retained, resume when the slot is free.")
                self.store.add_event(self.job_id, "execution_slot_conflict", "warning", str(exc))
                raise
            except Exception:
                self.store.stop_supervision(self.job_id, os.getpid())
                if self.slot:
                    self.slot.close()
                if self.power:
                    self.power.close()
                raise
            _contexts.add(self.key)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.nested:
            return False
        try:
            self.store.stop_supervision(self.job_id, os.getpid())
            self.store.release_owned_worker(self.job_id, os.getpid())
        finally:
            if self.power:
                self.power.close()
            if self.slot:
                self.slot.close()
            with _lock:
                _contexts.discard(self.key)
            if self.process:
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        try:
                            self.process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            pass
        return False


@contextlib.contextmanager
def supervised_model_call(state_db: Path, job_id: str, label: str, *, timeout_seconds: float):
    """Register the complete call (including native prefill) before entering it."""
    if timeout_seconds <= 0:
        raise ValueError("Model call timeout must be positive")
    store = JobStore(Path(state_db))
    wall_started, active_started = time.time(), time.monotonic()
    deadline = wall_started + timeout_seconds
    active_deadline = active_started + timeout_seconds
    # A separate broad wall safeguard handles genuinely abandoned processes;
    # ordinary host suspend does not spend the model's active allowance.
    wall_safety_deadline = wall_started + max(86400.0, timeout_seconds * 8)
    sequence = store.begin_model_call(job_id, label, deadline,
        active_deadline=active_deadline, wall_safety_deadline=wall_safety_deadline)
    outcome = "returned"
    if sequence is not None:
        store.add_event(job_id, "model_call_started", "info", f"Model call '{label}' started", {
            "sequence": sequence, "label": label, "wall_started": wall_started,
            "wall_deadline": deadline, "timeout_seconds": timeout_seconds,
            "active_deadline": active_deadline, "wall_safety_deadline": wall_safety_deadline,
            "deadline_policy": "monotonic_active_time; suspend_excluded; separate_wall_safety_limit"})
    try:
        yield
    except BaseException as exc:
        outcome = getattr(exc, "category", "error")
        raise
    finally:
        if sequence is not None:
            store.end_model_call(job_id, sequence)
            store.add_event(job_id, "model_call_finished", "info", f"Model call '{label}' ended", {
                "sequence": sequence, "label": label, "outcome": outcome,
                "wall_elapsed_seconds": round(time.time() - wall_started, 3),
                "monotonic_elapsed_seconds": round(time.monotonic() - active_started, 3)})


def watchdog_reason(supervision: dict, job, *, now: float, owner_alive: bool,
                    active_now: float | None = None) -> str | None:
    if not owner_alive:
        return "Worker exited before a terminal result; checkpoints retained for resume."
    if job.cancel_requested:
        return "Cancellation requested; supervised worker stopped, checkpoints retained."
    active_deadline = supervision.get("call_active_deadline")
    if active_deadline is not None:
        active_now = time.monotonic() if active_now is None else active_now
        if active_now >= active_deadline:
            return f"Model call '{supervision['call_label']}' exceeded its external active hard deadline; checkpoints retained."
        if supervision.get("call_wall_safety_deadline") is not None and now >= supervision["call_wall_safety_deadline"]:
            return f"Model call '{supervision['call_label']}' exceeded its independent wall safety deadline; checkpoints retained."
        return None
    if supervision.get("call_deadline") is not None and now >= supervision["call_deadline"]:
        return f"Model call '{supervision['call_label']}' exceeded its external hard deadline; checkpoints retained."
    return None


def _terminate_owner(pid: int, started: str) -> bool:
    if not JobStore._worker_alive(str(pid), started):
        return True
    # A CLI started in the user's shell must never kill that shell's group.
    try:
        kill = os.killpg if os.getpgid(pid) == pid else os.kill
        kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and JobStore._worker_alive(str(pid), started):
            time.sleep(0.1)
        if JobStore._worker_alive(str(pid), started):
            kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and JobStore._worker_alive(str(pid), started):
                time.sleep(0.05)
    except ProcessLookupError:
        pass
    return not JobStore._worker_alive(str(pid), started)


def _publish_interrupted_stage(store: JobStore, job_id: str) -> None:
    """Only the watchdog after verified worker death may finalize its artifacts."""
    job = store.get(job_id)
    try:
        from esg_targeted.io import write_json
        state_path = Path(job.output_dir) / "agent/state.json"
        if state_path.is_file():
            agent = json.loads(state_path.read_text(encoding="utf-8"))
            agent.update(state=job.status.value, terminal_reason=job.error)
            if isinstance(agent.get("current_call"), dict) and agent["current_call"].get("phase") in {"started", "loading", "prefill", "generation"}:
                agent["current_call"].update(phase=job.status.value, error=job.error)
                attempt = agent["current_call"].get("attempt")
                if isinstance(attempt, int):
                    for call_path in (Path(job.output_dir) / "agent/calls").glob(f"{attempt:04d}-*.json"):
                        call = json.loads(call_path.read_text(encoding="utf-8"))
                        call.update(phase=job.status.value, error=job.error, failure_category=job.status.value)
                        write_json(call_path, call)
            for step in agent.get("steps", []):
                if step.get("status") == "running":
                    step.update(status=job.status.value, error=job.error)
            write_json(state_path, agent)
        from esg_targeted.results.stages import publish_stage_from_inputs
        result = publish_stage_from_inputs(Path(job.output_dir), job_id,
            status=job.status.value, error=job.error, export_bundle=True)
        summary = result.get("summary") if isinstance(result, dict) else None
        if summary:
            store.update(job_id, summary=summary)
    except (ImportError, FileNotFoundError):
        # Legacy/synthetic jobs may have no persisted inputs or StagePublisher.
        return
    except Exception as exc:
        store.add_event(job_id, "stage_snapshot", "warning", "Could not finalize retained stage artifacts", {"error": str(exc)})
    finally:
        # Export eligibility is independent of stage visibility. The hook may
        # have saved a source-only stage before discovering missing legacy
        # package/IR snapshots. Keep that truthful stage in the job ledger.
        snapshot_path = Path(job.output_dir) / 'progress/snapshot.json'
        if snapshot_path.is_file():
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
                if snapshot.get('schema_version') == 'report-stage-snapshot-v1' and snapshot.get('summary',{}).get('job_id') == job_id:
                    store.update(job_id, summary=snapshot['summary'])
            except (OSError, ValueError, KeyError):
                pass  # An unreadable stage is not evidence of accepted facts.


def watch_job(state_db: Path, job_id: str) -> None:
    store = JobStore(state_db)
    last_heartbeat = 0.0
    clock = WatchdogClock(time.time(), time.monotonic())
    while True:
        supervision = store.supervision(job_id)
        if not supervision or not supervision["active"]:
            return
        job = store.get(job_id)
        pid, started = supervision["owner_pid"], supervision["owner_started"]
        alive = store._worker_alive(str(pid), started)
        if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING} and alive and not job.cancel_requested:
            store.stop_supervision(job_id, pid)
            return
        now = time.time()
        observation = clock.observe(now, time.monotonic(), deadline=supervision.get("call_deadline"))
        if observation:
            if supervision.get("call_active_deadline") is not None:
                observation.update(deadline_policy="monotonic_active_time; suspend_excluded; separate_wall_safety_limit",
                    active_deadline=supervision["call_active_deadline"],
                    active_deadline_overdue_seconds=round(max(0.0, time.monotonic() - supervision["call_active_deadline"]), 3),
                    wall_safety_deadline=supervision.get("call_wall_safety_deadline"))
            store.add_event(job_id, "resource_lifecycle", "warning", "Watchdog resumed after an observation gap", observation)
        reason = watchdog_reason(supervision, job, now=now, active_now=time.monotonic(), owner_alive=alive)
        if reason:
            if store.interrupt_owned_worker(job_id, pid, started, reason, cancelled=job.cancel_requested):
                stopped = _terminate_owner(pid, started)
                # Test adapters may not return a bool; independently verify the birth identity.
                if stopped or not store._worker_alive(str(pid), started):
                    store.release_owned_worker(job_id, pid)
                    _publish_interrupted_stage(store, job_id)
                else:
                    store.add_event(job_id, "resource_lifecycle", "warning", "Worker termination is pending; artifact finalization deferred", {"owner_pid": pid})
            return
        if time.monotonic() - last_heartbeat >= 5:
            store.heartbeat(job_id)
            last_heartbeat = time.monotonic()
        time.sleep(0.5)


if __name__ == "__main__":
    action, db_arg, job_arg = sys.argv[1:]
    if action == "watch":
        watch_job(Path(db_arg), job_arg)
    else:
        raise SystemExit("Unsupported supervisor action")
