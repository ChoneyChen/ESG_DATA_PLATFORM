from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esg_v2.control_plane.contracts import PipelineTask, PipelineTaskStatus, utc_now
from esg_v2.control_plane.secrets import RuntimeSecretVault
from esg_v2.control_plane.store import PipelineQueueStore


EVENT_PREFIX = "ESG_PIPELINE_EVENT "


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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.recover_interrupted()
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
            task = self.store.claim_next()
            if task is None:
                self._wake.wait(self.poll_interval_seconds)
                self._wake.clear()
                continue
            self._execute(task)

    def _execute(self, task: PipelineTask) -> None:
        secrets = self.vault.pop(task.task_id)
        if task.requires_runtime_secret and not secrets and not self._configured_secret_available(task):
            self._finish_failed(
                task,
                "Runtime credential was unavailable after backend restart; re-add the task or configure the environment variable.",
            )
            return
        try:
            command = self.hooks.command(task, secrets)
            process = subprocess.Popen(
                command.argv,
                cwd=command.cwd,
                env=command.environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except Exception as exc:
            self._finish_failed(task, f"Worker start failed: {type(exc).__name__}: {exc}")
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
        self.store.add_event(task.task_id, "running", "info", f"Worker PID {process.pid} started")

        lines: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read_stdout, daemon=True).start()
        result: dict[str, Any] | None = None
        tail: list[str] = []
        output_closed = False
        last_probe = 0.0
        terminated = False
        while not output_closed or process.poll() is None:
            current = self.store.get(task.task_id)
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
                if snapshot:
                    self._apply_event(current, {"kind": "progress", **snapshot})
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
                if event.get("kind") == "result" and isinstance(event.get("payload"), dict):
                    result = event["payload"]
                self._apply_event(current, event)
            else:
                self.store.add_event(current.task_id, current.stage, "debug", text[-1000:])

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
            self.hooks.interrupted(self.store.get(task.task_id))
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
            self.store.add_event(task.task_id, "cancelled", "warning", "Worker process group terminated")
            self.hooks.cancelled(latest)
            return
        if return_code != 0:
            error = str((result or {}).get("error") or (tail[-1] if tail else f"Worker exited with code {return_code}"))
            self._finish_failed(task, error)
            return
        if result is None:
            self._finish_failed(task, "Worker completed without a structured result event")
            return
        native_status = str(result.get("status") or "")
        if task.task_type.value == "targeted_extraction" and native_status == "partial":
            self.store.update(
                task.task_id,
                status=PipelineTaskStatus.PARTIAL,
                stage="partial",
                message="Extraction produced partial results; review unresolved metrics",
                progress_current=max(latest.progress_current, latest.progress_total),
                worker_pid=None,
                finished_at=utc_now(),
                error=None,
            )
            self.store.add_event(task.task_id, "partial", "warning", "Task produced partial results", {
                "native_status": native_status,
                "result_counts": (result.get("summary") or {}).get("record_counts", {}),
            })
            self.hooks.completed(self.store.get(task.task_id), result)
            return
        self.store.update(
            task.task_id,
            status=PipelineTaskStatus.COMPLETED,
            stage="completed",
            message="Task completed",
            progress_current=max(latest.progress_current, latest.progress_total),
            worker_pid=None,
            finished_at=utc_now(),
            error=None,
        )
        self.store.add_event(task.task_id, "completed", "info", "Task completed successfully", {
            "native_status": result.get("status"),
            "readiness": result.get("readiness"),
            "result_counts": (result.get("summary") or {}).get("record_counts", {}),
        })
        self.hooks.completed(self.store.get(task.task_id), result)

    def _apply_event(self, task: PipelineTask, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "log")
        stage = str(event.get("stage") or task.stage or "running")
        message = str(event.get("message") or event.get("name") or stage)
        changes: dict[str, Any] = {"stage": stage, "message": message}
        if isinstance(event.get("progress_current"), (int, float)):
            changes["progress_current"] = int(event["progress_current"])
        if isinstance(event.get("progress_total"), (int, float)):
            changes["progress_total"] = int(event["progress_total"])
        self.store.update(task.task_id, **changes)
        if kind != "telemetry":
            self.store.add_event(
                task.task_id,
                stage,
                str(event.get("level") or "info"),
                message,
                event.get("detail") if isinstance(event.get("detail"), dict) else None,
            )
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
    def _configured_secret_available(task: PipelineTask) -> bool:
        if task.task_type.value == "ocr":
            return bool(os.getenv("PADDLEOCR_VL_API_TOKEN"))
        if task.task_type.value == "document_ir":
            return bool(os.getenv("QINIU_API_KEY"))
        if (
            task.task_type.value == "targeted_extraction"
            and task.payload.get("semantic_provider") == "qiniu_vlm"
        ):
            return bool(os.getenv("QINIU_API_KEY"))
        return True
