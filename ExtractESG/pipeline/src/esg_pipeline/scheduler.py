from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esg_pipeline.contracts import PipelineTask, PipelineTaskStatus, utc_now
from esg_pipeline.execution_slot import ExecutionSlot, ExecutionSlotBusy
from esg_pipeline.secrets import RuntimeSecretVault
from esg_pipeline.store import PipelineQueueStore
from esg_pipeline.progress import progress_from_event, classify_error
from esg_contracts.worker import EVENT_PREFIX


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    """Liveness polling is distinct from durable stage progress.

    Keep complete snapshots for outcomes. Only their event comparison discards
    volatile heartbeat clocks and quantizes token streaming progress.
    """
    volatile = {
        "updated_at",
        "heartbeat_at",
        "last_progress_at",
        "observed_at",
        "revision",
        "elapsed_seconds",
        "wall_elapsed_seconds",
        "monotonic_elapsed_seconds",
    }

    def stable(value):
        if isinstance(value, dict):
            return {
                key: int(item) // 32
                if key == "generation_tokens" and isinstance(item, (int, float))
                else stable(item)
                for key, item in value.items()
                if key not in volatile
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    return json.dumps(
        stable(snapshot),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class WorkerCommand:
    argv: list[str]
    cwd: Path
    environment: dict[str, str]


class PipelineTaskHooks:
    """Presentation/state adapter; scheduler remains independent of workflows."""

    def command(self, task: PipelineTask, secrets: dict[str, str]) -> WorkerCommand:
        raise NotImplementedError

    def started(self, task: PipelineTask) -> None:
        pass

    def event(self, task: PipelineTask, event: dict[str, Any]) -> None:
        pass

    def probe(self, task: PipelineTask) -> dict[str, Any] | None:
        return None

    def completed(self, task: PipelineTask, result: dict[str, Any]) -> None:
        pass

    def failed(self, task: PipelineTask, error: str) -> None:
        pass

    def cancelled(self, task: PipelineTask) -> None:
        pass

    def interrupted(self, task: PipelineTask) -> None:
        pass


class PipelineScheduler:
    def __init__(
        self,
        store: PipelineQueueStore,
        vault: RuntimeSecretVault,
        hooks: PipelineTaskHooks,
        *,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.store = store
        self.vault = vault
        self.hooks = hooks
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_process: subprocess.Popen[str] | None = None
        self._active_lock = threading.Lock()
        self.slot_path = Path(
            os.getenv(
                "ESG_EXECUTION_SLOT_PATH",
                str(store.path.parent / "model-execution.lock"),
            )
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # A detached worker may have finished while the API was absent. Honor
        # its durable business state before labeling a missing process interrupted.
        for task in self.store.list():
            if (
                task.status == PipelineTaskStatus.RUNNING
                and not self.store.worker_alive(task.task_id)
            ):
                try:
                    snapshot = self.hooks.probe(task)
                except Exception:
                    snapshot = None
                try:
                    if snapshot and snapshot.get("status") in {
                        "completed",
                        "partial",
                        "failed",
                        "cancelled",
                        "interrupted",
                    }:
                        self._finish_native_state(task, snapshot)
                    else:
                        result = self._read_worker_result(task)
                        if result is not None:
                            self._finish_native_state(
                                task,
                                {
                                    **result,
                                    "status": result.get("status") or "completed",
                                },
                                result=result,
                            )
                except Exception as exc:  # noqa: BLE001 - one unreadable result must not block every other task
                    self.store.add_event(
                        task.task_id,
                        "recovery_failed",
                        "error",
                        f"Finished worker state could not be recorded: {type(exc).__name__}: {str(exc)[:300]}",
                        {},
                    )
        for task_id in self.store.recover_interrupted():
            self.hooks.interrupted(self.store.get(task_id))
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="esg-unified-pipeline-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._active_lock:
            process = self._active_process
        if process and process.poll() is None:
            self._terminate_process_group(process)
        if self._thread:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            existing = next(
                (
                    task
                    for task in self.store.list()
                    if task.status == PipelineTaskStatus.RUNNING
                ),
                None,
            )
            if existing:
                self._observe_existing(existing)
                continue
            try:
                slot = ExecutionSlot(self.slot_path).acquire()
            except ExecutionSlotBusy:
                self._wake.wait(self.poll_interval_seconds)
                self._wake.clear()
                continue
            task = self.store.claim_next()
            if task is None:
                slot.close()
                self._wake.wait(self.poll_interval_seconds)
                self._wake.clear()
                continue
            self._execute(task, slot)

    def _execute(self, task: PipelineTask, slot: ExecutionSlot) -> None:
        secrets = self.vault.pop(task.task_id)
        if (
            task.requires_runtime_secret
            and not secrets
            and not self._configured_secret_available(task)
        ):
            slot.close()
            self._finish_failed(
                task,
                "Runtime credential was unavailable after backend restart; re-add the task or configure the environment variable.",
            )
            return
        try:
            command = self.hooks.command(task, secrets)
            worker_log = self.store.path.parent / "tasks" / task.task_id / "worker.log"
            worker_log.parent.mkdir(parents=True, exist_ok=True)
            offset = worker_log.stat().st_size if worker_log.exists() else 0
            with worker_log.open("a", encoding="utf-8") as output:
                process = subprocess.Popen(
                    command.argv,
                    cwd=command.cwd,
                    env={
                        **command.environment,
                        "ESG_EXECUTION_SLOT_FD": str(slot.fd),
                        "ESG_EXECUTION_SLOT_PATH": str(slot.path),
                    },
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                    pass_fds=(slot.fd,),
                )
            slot.close()
        except Exception as exc:
            slot.close()
            self._finish_failed(
                task, f"Worker start failed: {type(exc).__name__}: {exc}"
            )
            return

        with self._active_lock:
            self._active_process = process
        self.store.update(
            task.task_id,
            worker_pid=process.pid,
            stage="running",
            message="Isolated worker is running",
        )
        task = self.store.get(task.task_id)
        self.hooks.started(task)
        self.store.add_event(
            task.task_id,
            "running",
            "info",
            f"Worker PID {process.pid} started",
            {"worker_log": str(worker_log), "worker_log_offset": offset},
        )

        lines: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            # The worker owns a durable file descriptor, not a PIPE reader in
            # this API process. API disappearance cannot break its stdout.
            with worker_log.open(encoding="utf-8", errors="replace") as output:
                output.seek(offset)
                pending = ""
                while True:
                    line = output.readline()
                    if line:
                        pending += line
                        if pending.endswith("\n"):
                            lines.put(pending)
                            pending = ""
                    elif process.poll() is not None:
                        if pending:
                            lines.put(pending)
                        break
                    else:
                        time.sleep(0.05)
            lines.put(None)

        threading.Thread(target=read_stdout, daemon=True).start()
        result: dict[str, Any] | None = None
        tail: list[str] = []
        output_closed = False
        last_probe = 0.0
        last_snapshot = None
        runtime_clock = self._runtime_clock()
        terminated = False
        timed_out = False
        while not output_closed or process.poll() is None:
            current = self.store.get(task.task_id)
            self._observe_clock(current, runtime_clock)
            if self._deadline_expired(current) and process.poll() is None:
                timed_out = True
                self._terminate_process_group(process)
            if current.cancel_requested and process.poll() is None:
                terminated = True
                self._terminate_process_group(process)
            now = time.monotonic()
            if now - last_probe >= 1:
                last_probe = now
                try:
                    snapshot = self.hooks.probe(current)
                except Exception:
                    snapshot = None
                fingerprint = snapshot_fingerprint(snapshot) if snapshot else None
                if snapshot and fingerprint != last_snapshot:
                    last_snapshot = fingerprint
                    try:
                        self._apply_event(current, {"kind": "progress", **snapshot})
                    except Exception as exc:  # noqa: BLE001 - a bad snapshot must not stop the scheduler
                        self.store.add_event(
                            task.task_id,
                            "progress_rejected",
                            "warning",
                            f"Progress snapshot rejected: {type(exc).__name__}: {str(exc)[:300]}",
                            {},
                        )
            try:
                line = lines.get(timeout=self.poll_interval_seconds)
            except queue.Empty:
                continue
            if line is None:
                output_closed = True
                continue
            text = line.rstrip()
            if not text:
                continue
            tail = [*tail[-39:], text[-2000:]]
            if text.startswith(EVENT_PREFIX):
                try:
                    event = json.loads(text.removeprefix(EVENT_PREFIX))
                except json.JSONDecodeError:
                    event = {"kind": "log", "message": text}
                if event.get("kind") == "result" and isinstance(
                    event.get("payload"), dict
                ):
                    result = self._normalize_result(current, event["payload"])
                self._apply_event(current, event)
            else:
                self.store.add_event(
                    current.task_id, current.stage, "debug", text[-1000:]
                )

        return_code = process.wait()
        with self._active_lock:
            self._active_process = None
        latest = self.store.get(task.task_id)
        if self._stop.is_set() and not latest.cancel_requested:
            self.store.update(
                task.task_id,
                status=PipelineTaskStatus.INTERRUPTED,
                stage="interrupted",
                message="Backend stopped while task was running",
                worker_pid=None,
                finished_at=utc_now(),
            )
            self.store.add_event(
                task.task_id,
                "interrupted",
                "warning",
                "Worker process group stopped with the backend",
            )
            self.store.record_outcome(
                task.task_id, self.hooks.probe(self.store.get(task.task_id))
            )
            self.hooks.interrupted(self.store.get(task.task_id))
            return
        if timed_out:
            self._finish_native_state(
                latest,
                {
                    **(self.hooks.probe(latest) or {}),
                    "status": "interrupted",
                    "error": "Task reached its total wall time budget; saved artifacts and checkpoints retained",
                },
            )
            return
        if terminated or latest.cancel_requested:
            self.store.update(
                task.task_id,
                status=PipelineTaskStatus.CANCELLED,
                stage="cancelled",
                message="Task terminated",
                worker_pid=None,
                finished_at=utc_now(),
            )
            self.store.record_outcome(
                task.task_id, self.hooks.probe(self.store.get(task.task_id))
            )
            self.store.add_event(
                task.task_id, "cancelled", "warning", "Worker process group terminated"
            )
            self.hooks.cancelled(latest)
            return
        native_snapshot = (
            self.hooks.probe(latest)
            if latest.task_type.value == "targeted_extraction"
            else None
        )
        if native_snapshot and native_snapshot.get("status") in {
            "interrupted",
            "cancelled",
            "failed",
        }:
            self._finish_native_state(latest, native_snapshot)
            return
        if return_code != 0:
            error = str(
                (result or {}).get("error")
                or (tail[-1] if tail else f"Worker exited with code {return_code}")
            )
            self._finish_failed(task, error)
            return
        if result is None:
            self._finish_failed(
                task, "Worker completed without a structured result event"
            )
            return
        native_status = str(result.get("status") or "")
        terminal_progress = {"kind": "progress", **result}
        if task.task_type.value == "ocr" and result.get("page_count"):
            terminal_progress.update(
                progress_current=result["page_count"],
                progress_total=result["page_count"],
                stage="ocr",
            )
        if task.task_type.value == "document_ir" and result.get("telemetry"):
            terminal_progress["detail"] = result["telemetry"]
        self._apply_event(self.store.get(task.task_id), terminal_progress)
        latest = self.store.get(task.task_id)
        if native_status == "partial":
            self.store.update(
                task.task_id,
                status=PipelineTaskStatus.PARTIAL,
                stage="partial",
                message="Extraction produced partial results; review unresolved metrics",
                progress_current=latest.progress_current,
                worker_pid=None,
                finished_at=utc_now(),
                error=None,
            )
            self.store.record_outcome(task.task_id, result)
            self.store.add_event(
                task.task_id,
                "partial",
                "warning",
                "Task produced partial results",
                result,
            )
            self.hooks.completed(self.store.get(task.task_id), result)
            return
        self.store.update(
            task.task_id,
            status=PipelineTaskStatus.COMPLETED,
            stage="completed",
            message="Task completed",
            progress_current=latest.progress_current,
            worker_pid=None,
            finished_at=utc_now(),
            error=None,
        )
        self.store.record_outcome(task.task_id, result)
        self.store.add_event(
            task.task_id, "completed", "info", "Task completed successfully", result
        )
        self.hooks.completed(self.store.get(task.task_id), result)

    def _observe_existing(self, task: PipelineTask) -> None:
        """Reconnect a still-live worker after API restart, without launching it twice."""
        self.store.add_event(
            task.task_id,
            "reconnected",
            "info",
            "Reconnected the existing worker; no model restart",
        )
        self.store.update(task.task_id, worker_pid=task.worker_pid)
        snapshot = None
        last_snapshot = None
        runtime_clock = self._runtime_clock()
        while not self._stop.is_set() and self.store.worker_alive(task.task_id):
            current = self.store.get(task.task_id)
            self._observe_clock(current, runtime_clock)
            if current.cancel_requested or self._deadline_expired(current):
                self._terminate_existing(current)
                if not self.store.worker_alive(task.task_id):
                    self._finish_native_state(
                        current,
                        {
                            **(self.hooks.probe(current) or {}),
                            "status": "cancelled"
                            if current.cancel_requested
                            else "interrupted",
                            "error": "Existing worker stopped by operator cancellation"
                            if current.cancel_requested
                            else "Task reached its total wall time budget; checkpoints retained",
                        },
                    )
                return
            try:
                observed = self.hooks.probe(current)
            except Exception:
                observed = None
            if observed:
                snapshot = observed
                fingerprint = snapshot_fingerprint(observed)
                if fingerprint != last_snapshot:
                    self._apply_event(current, {"kind": "progress", **observed})
                    last_snapshot = fingerprint
            self._stop.wait(max(0.1, self.poll_interval_seconds))
        if self._stop.is_set():
            return  # API shutdown does not erase ownership of a surviving worker.
        try:
            snapshot = self.hooks.probe(self.store.get(task.task_id)) or snapshot
        except Exception:
            pass
        if snapshot and snapshot.get("status") in {
            "completed",
            "partial",
            "failed",
            "cancelled",
            "interrupted",
        }:
            self._finish_native_state(self.store.get(task.task_id), snapshot)
        else:
            result = self._read_worker_result(task)
            if result is not None:
                self._finish_native_state(
                    task,
                    {**result, "status": result.get("status") or "completed"},
                    result=result,
                )
            else:
                self._finish_native_state(
                    task,
                    {
                        "status": "interrupted",
                        "error": "Reconnected worker exited without a terminal result; checkpoints retained",
                    },
                )

    def _read_worker_result(self, task: PipelineTask) -> dict | None:
        """Recover an explicit terminal return after API loss, not infer success."""
        path = self.store.path.parent / "tasks" / task.task_id / "worker.log"
        if not path.is_file():
            return None
        # Read only the bounded tail; native artifacts remain the source of truth.
        starts = [
            e["detail"]["worker_log_offset"]
            for e in self.store.events(task.task_id)
            if e.get("detail") and "worker_log_offset" in e["detail"]
        ]
        attempt_offset = starts[-1] if starts else 0
        with path.open("rb") as output:
            output.seek(max(attempt_offset, path.stat().st_size - 1024 * 1024))
            lines = output.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            if not line.startswith(EVENT_PREFIX):
                continue
            try:
                event = json.loads(line.removeprefix(EVENT_PREFIX))
            except json.JSONDecodeError:
                continue
            if (
                not isinstance(event, dict)
                or event.get("kind") != "result"
                or not isinstance(event.get("payload"), dict)
            ):
                continue
            result = self._normalize_result(task, event["payload"])
            status = result.get("status")
            if status and status not in {
                "completed",
                "partial",
                "failed",
                "cancelled",
                "interrupted",
            }:
                return None
            # OCR/IR workers emit `result` only after returning successfully;
            # targeted workers additionally carry their explicit business status.
            if task.task_type.value == "targeted_extraction" and not status:
                return None
            return result
        return None

    def _finish_native_state(
        self, task: PipelineTask, snapshot: dict, *, result: dict | None = None
    ) -> None:
        status = PipelineTaskStatus(snapshot["status"])
        error = snapshot.get("error")
        progress = progress_from_event(
            task,
            {
                **snapshot,
                "kind": "telemetry",
                "detail": snapshot.get("telemetry") or {},
            },
        )
        self.store.update(
            task.task_id,
            status=status,
            stage=status.value,
            message=error or f"Native task {status.value}",
            error=error,
            progress_current=snapshot.get("progress_current", task.progress_current),
            progress_total=snapshot.get("progress_total", task.progress_total),
            progress_json=progress.model_dump_json(),
            error_detail_json=classify_error(
                str(error),
                task_type=task.task_type.value,
                producer_error=snapshot.get("error_detail"),
            ).model_dump_json()
            if error
            else None,
            worker_pid=None,
            finished_at=utc_now(),
        )
        latest = self.store.record_outcome(
            task.task_id, result if result is not None else snapshot
        )
        self.store.add_event(
            task.task_id,
            status.value,
            "info" if status == PipelineTaskStatus.COMPLETED else "warning",
            latest.message,
            result if result is not None else snapshot,
        )
        if status in {PipelineTaskStatus.COMPLETED, PipelineTaskStatus.PARTIAL}:
            self.hooks.completed(
                latest,
                result
                if result is not None
                else {"status": status.value, "summary": snapshot.get("summary")},
            )
        elif status == PipelineTaskStatus.CANCELLED:
            self.hooks.cancelled(latest)
        elif status == PipelineTaskStatus.INTERRUPTED:
            self.hooks.interrupted(latest)
        else:
            self.hooks.failed(latest, error or "Native task failed")

    def _terminate_existing(self, task: PipelineTask) -> None:
        pid = task.worker_pid
        if not pid or not self.store.worker_alive(task.task_id):
            return
        try:
            kill = os.killpg if os.getpgid(pid) == pid else os.kill
            kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and self.store.worker_alive(task.task_id):
                time.sleep(0.1)
            if self.store.worker_alive(task.task_id):
                kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _apply_event(self, task: PipelineTask, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "log")
        stage = str(event.get("stage") or task.stage or "running")
        message = str(event.get("message") or event.get("name") or stage)
        if kind == "log":
            self.store.add_event(
                task.task_id, task.stage, "debug", message, {"event_type": "raw_log"}
            )
            self.hooks.event(self.store.get(task.task_id), event)
            return
        if kind == "result":
            self.hooks.event(task, event)
            return  # Terminal business state is saved by the completion handler.
        progress = progress_from_event(task, event)
        changes: dict[str, Any] = {"stage": stage, "message": message}
        changes.update(
            stage=progress.stage_code,
            message=progress.label,
            progress_json=progress.model_dump_json(),
        )
        if event.get("error"):
            changes["error_detail_json"] = classify_error(
                str(event["error"]),
                task_type=task.task_type.value,
                producer_error=event.get("error_detail"),
            ).model_dump_json()
        if isinstance(event.get("progress_current"), (int, float)):
            changes["progress_current"] = int(event["progress_current"])
        if isinstance(event.get("progress_total"), (int, float)):
            changes["progress_total"] = int(event["progress_total"])

        # Repeated heartbeat or identical progress does not create another
        # business event. Exact raw logs remain independently available.
        def business(value):
            value = dict(value)
            value.pop("last_business_progress_at", None)
            call = value.get("current_call")
            if isinstance(call, dict):
                value["current_call"] = {
                    k: v
                    for k, v in call.items()
                    if k not in {"elapsed_seconds", "heartbeat_at"}
                }
            return value

        if not task.progress or business(progress.model_dump()) != business(
            task.progress.model_dump()
        ):
            self.store.update(task.task_id, **changes)
        self.hooks.event(self.store.get(task.task_id), event)

    def _finish_failed(self, task: PipelineTask, error: str) -> None:
        self.store.update(
            task.task_id,
            status=PipelineTaskStatus.FAILED,
            stage="failed",
            message="Task failed",
            worker_pid=None,
            finished_at=utc_now(),
            error=error,
        )
        self.store.record_outcome(
            task.task_id, self.hooks.probe(self.store.get(task.task_id))
        )
        self.store.add_event(task.task_id, "failed", "error", error)
        self.hooks.failed(self.store.get(task.task_id), error)

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=8)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _normalize_result(task: PipelineTask, result: dict) -> dict:
        if task.operation in {"report_session", "report_session_resume"}:
            execution = result.get("execution_status")
            status = (
                ("completed" if result.get("result_status") == "usable" else "partial")
                if execution == "finished"
                else execution
            )
            return {**result, "status": status}
        return result

    @staticmethod
    def _wall_deadline(task: PipelineTask) -> float | None:
        if not task.started_at:
            return None
        budget = task.payload.get("budget") or {}
        try:
            timeout = float(
                budget.get("total_timeout_seconds")
                or task.payload.get("_total_timeout_seconds")
                or task.payload.get("total_timeout_seconds")
                or 14400
            )
            started = datetime.fromisoformat(task.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return started.timestamp() + timeout
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _deadline_expired(task: PipelineTask) -> bool:
        deadline = PipelineScheduler._wall_deadline(task)
        return deadline is not None and time.time() >= deadline

    @staticmethod
    def _runtime_clock():
        from esg_runtime.power import WatchdogClock

        return WatchdogClock(time.time(), time.monotonic())

    def _observe_clock(self, task, clock):
        wall, monotonic = time.time(), time.monotonic()
        wall_gap, active_gap = (
            max(0.0, wall - clock.wall_last),
            max(0.0, monotonic - clock.monotonic_last),
        )
        detail = clock.observe(wall, monotonic, deadline=self._wall_deadline(task))
        timing = task.timing.model_copy(deep=True)
        adjustment = max(0.0, wall_gap - active_gap)
        pending = getattr(
            clock, "pending_timing", {"active": 0.0, "unknown": 0.0, "adjustment": 0.0}
        )
        if max(wall_gap, active_gap) < clock.gap_threshold_seconds:
            pending["active"] += min(wall_gap, active_gap)
        else:
            pending["unknown"] += min(wall_gap, active_gap)
            pending["adjustment"] += adjustment
        clock.pending_timing = pending
        timing.active_observed_seconds += pending["active"]
        timing.unobserved_seconds += pending["unknown"]
        timing.sleep_or_clock_adjustment_seconds += pending["adjustment"]
        timing.last_observed_at = utc_now()
        timing.observation_complete = timing.unobserved_seconds == 0
        if (
            not task.timing.last_observed_at
            or wall - getattr(clock, "timing_persisted_at", wall) >= 1
            or detail
        ):
            self.store.update(task.task_id, timing_json=timing.model_dump_json())
            clock.pending_timing = {"active": 0.0, "unknown": 0.0, "adjustment": 0.0}
            clock.timing_persisted_at = wall
        if detail:
            self.store.add_event(
                task.task_id,
                "resource_lifecycle",
                "warning",
                "Scheduler resumed after an observation gap; wall deadline rechecked",
                detail,
            )

    @staticmethod
    def _configured_secret_available(task: PipelineTask) -> bool:
        if task.task_type.value == "ocr":
            if task.payload.get("_requires_source_url") and not os.getenv(
                "ESG_OCR_SOURCE_URL"
            ):
                return False
            return bool(os.getenv("PADDLEOCR_VL_API_TOKEN")) or bool(
                os.getenv("ESG_OCR_SOURCE_URL")
            )
        if task.task_type.value == "document_ir":
            return bool(os.getenv("QINIU_API_KEY"))
        if task.task_type.value == "targeted_extraction" and (
            task.payload.get("semantic_provider") == "qiniu_vlm"
            or any(
                role.get("provider") == "qiniu_vlm"
                for role in (task.payload.get("role_config") or {}).values()
                if isinstance(role, dict)
            )
        ):
            return bool(os.getenv("QINIU_API_KEY"))
        return True
