from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

from esg_document.config import Settings
from esg_contracts.document import OcrJobState
from esg_pipeline.contracts import PipelineTask, PipelineTaskType, utc_now
from esg_pipeline.scheduler import PipelineTaskHooks, WorkerCommand
from esg_document.document.contracts import DocumentIrJobState
from esg_document.storage.job_store import JobStore
from esg_document.storage.package_layout import package_dir
from esg_data.sanitization import sanitize_remote_url


class PlatformTaskHooks(PipelineTaskHooks):
    def __init__(
        self,
        settings: Settings,
        ocr_store: JobStore[OcrJobState],
        ir_store: JobStore[DocumentIrJobState],
    ) -> None:
        self.settings = settings
        self.ocr_store = ocr_store
        self.ir_store = ir_store

    def command(self, task: PipelineTask, secrets: dict[str, str]) -> WorkerCommand:
        environment = os.environ.copy()
        environment.update(secrets)
        shared_runtime = self.settings.extraction_root.parent / "shared_runtime" / "src"
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(shared_runtime), environment.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        if task.task_type == PipelineTaskType.TARGETED_EXTRACTION:
            return self._targeted_command(task, environment)

        task_dir = self.settings.pipeline_task_root / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        request_path = task_dir / "request.json"
        request_path.write_text(
            json.dumps(
                {k: v for k, v in task.payload.items() if not k.startswith("_plan_")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        request_path.chmod(0o600)
        operation = (
            "ocr"
            if task.task_type == PipelineTaskType.OCR
            else f"document_ir_{task.operation}"
        )
        return WorkerCommand(
            argv=[
                sys.executable,
                "-m",
                "esg_pipeline.worker",
                "--operation",
                operation,
                "--native-job-id",
                str(task.native_job_id),
                "--request-file",
                str(request_path),
            ],
            cwd=self.settings.pipeline_task_root,
            environment=environment,
        )

    def started(self, task: PipelineTask) -> None:
        if task.task_type == PipelineTaskType.OCR:
            state = self.ocr_store.get(str(task.native_job_id))
            if state:
                state.status = "running"
                state.message = "OCR job running in unified queue"
                self.ocr_store.put(state)
        elif task.task_type == PipelineTaskType.DOCUMENT_IR:
            state = self.ir_store.get(str(task.native_job_id))
            if state:
                state.status = "running"
                state.message = "Document IR job running in unified queue"
                state.started_at = utc_now()
                state.updated_at = state.started_at
                self.ir_store.put(state)

    def event(self, task: PipelineTask, event: dict[str, Any]) -> None:
        if task.task_type == PipelineTaskType.OCR:
            state = self.ocr_store.get(str(task.native_job_id))
            if not state:
                return
            if event.get("kind") == "log":
                message = str(event.get("message") or "OCR running")
                state.message = message
                state.logs.append(message)
            elif event.get("kind") == "telemetry":
                state.progress_current = int(event.get("progress_current") or 0)
                state.progress_total = int(event.get("progress_total") or 0)
                state.last_progress_at = utc_now()
                state.progress_detail = (
                    dict(event.get("detail"))
                    if isinstance(event.get("detail"), dict)
                    else {}
                )
                state.message = str(event.get("message") or "OCR running")
            self.ocr_store.put(state)
        elif task.task_type == PipelineTaskType.DOCUMENT_IR:
            state = self.ir_store.get(str(task.native_job_id))
            if not state:
                return
            if event.get("kind") == "log":
                message = str(event.get("message") or "Document IR running")
                state.message = message
                state.logs.append(message)
            elif event.get("kind") == "telemetry" and isinstance(
                event.get("detail"), dict
            ):
                state.telemetry = event["detail"]
            state.updated_at = utc_now()
            self.ir_store.put(state)

    def probe(self, task):
        if task.task_type != PipelineTaskType.TARGETED_EXTRACTION:
            return None
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    f"{self.settings.extraction_backend_url}/api/runs/{task.native_job_id}",
                    timeout=3,
                )
                response.raise_for_status()
                record = response.json()
            progress = record.get("progress") or {}
            return {
                **record,
                "stage": progress.get("stage", "clause_extraction"),
                "message": "逐条条款提取",
                "counts": {
                    "selected_clauses": progress.get("total", 0),
                    "processed_clauses": progress.get("current", 0),
                    "source_facts": progress.get("fact_count", 0),
                },
                "progress_current": progress.get("current", 0),
                "progress_total": progress.get("total", 0),
            }
        except (requests.RequestException, ValueError):
            return None

    def completed(self, task: PipelineTask, result: dict[str, Any]) -> None:
        if task.task_type == PipelineTaskType.OCR:
            state = self.ocr_store.get(str(task.native_job_id))
            if not state:
                return
            state.status = "done"
            state.message = "OCR job completed"
            state.job_id = result.get("job_id")
            state.result_json_url = (
                sanitize_remote_url(str(result.get("result_json_url") or "")) or None
            )
            state.page_count = int(result.get("page_count") or 0)
            state.progress_current = state.page_count
            state.progress_total = max(state.progress_total, state.page_count)
            state.manifest_path = Path(str(result["manifest_path"]))
            self.ocr_store.put(state)
        elif task.task_type == PipelineTaskType.DOCUMENT_IR:
            retention = result.get("retention")
            if not isinstance(retention, dict) and isinstance(
                result.get("manifest"), dict
            ):
                retention = result["manifest"].get("retention")
            if isinstance(retention, dict):
                for run_id in retention.get("pruned_run_ids") or []:
                    self.ir_store.discard(str(run_id))
            state = self.ir_store.get(str(task.native_job_id))
            if not state:
                return
            state.status = "partial" if task.status.value == "partial" else "done"
            manifest = (
                result.get("manifest")
                if isinstance(result.get("manifest"), dict)
                else result
            )
            state.summary.update(
                readiness=manifest.get("readiness"),
                can_build_evidence=bool(manifest.get("can_build_evidence")),
                can_build_limited_evidence=bool(
                    manifest.get("can_build_limited_evidence")
                ),
                evidence_policy=manifest.get("evidence_policy", {}),
            )
            pruned_count = (
                len(retention.get("pruned_run_ids") or [])
                if isinstance(retention, dict)
                else 0
            )
            state.message = (
                f"Document IR completed; retained best revision and removed {pruned_count} superseded revision(s)"
                if pruned_count
                else "Document IR job completed"
            )
            state.manifest_path = (
                package_dir(
                    self.settings.document_ir_output_root, str(task.native_job_id)
                )
                / "manifest.json"
            )
            state.telemetry = result.get("telemetry") or state.telemetry
            if isinstance(retention, dict):
                state.summary["retention"] = retention
            state.finished_at = utc_now()
            state.updated_at = state.finished_at
            self.ir_store.put(state)

    def failed(self, task: PipelineTask, error: str) -> None:
        if task.task_type == PipelineTaskType.TARGETED_EXTRACTION:
            self._targeted_terminal(task, terminal_status="failed", error=error)
            return
        self._terminal_native(task, "failed", "Task failed", error)

    def cancelled(self, task: PipelineTask) -> None:
        if task.task_type == PipelineTaskType.TARGETED_EXTRACTION:
            self._targeted_terminal(task, terminal_status="cancelled")
            return
        self._terminal_native(task, "cancelled", "Task terminated", None)

    def interrupted(self, task: PipelineTask) -> None:
        if task.task_type == PipelineTaskType.TARGETED_EXTRACTION:
            self._targeted_terminal(task, terminal_status="interrupted")
            return
        self._terminal_native(
            task, "interrupted", "Task interrupted by backend shutdown", None
        )

    def _terminal_native(
        self,
        task: PipelineTask,
        status: str,
        message: str,
        error: str | None,
    ) -> None:
        if task.task_type == PipelineTaskType.OCR:
            state = self.ocr_store.get(str(task.native_job_id))
            if state:
                state.status = status
                state.message = message
                state.error = error
                if error:
                    state.logs.append(error)
                self.ocr_store.put(state)
        elif task.task_type == PipelineTaskType.DOCUMENT_IR:
            state = self.ir_store.get(str(task.native_job_id))
            if state:
                state.status = status
                state.message = message
                state.error = error
                if error:
                    state.logs.append(error)
                state.finished_at = utc_now()
                state.updated_at = state.finished_at
                self.ir_store.put(state)

    def _targeted_command(self, task, environment):
        if task.operation != "clause_extraction":
            raise ValueError("Retired extraction operation")
        source = self.settings.extraction_root.parent
        environment["PYTHONPATH"] = os.pathsep.join(
            [*(str(source / name / "src") for name in ("extraction", "contracts", "shared_runtime")),
             str(source.parent / "ClauseESG/src"), environment.get("PYTHONPATH", "")]
        )
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        return WorkerCommand(
            argv=[
                str(self.settings.extraction_python),
                "-m",
                "esg_extraction.pipeline.worker",
                str(task.native_job_id),
            ],
            cwd=source,
            environment=environment,
        )

    def _targeted_terminal(self, task, *, terminal_status, error=None):
        if not task.native_job_id:
            return
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.post(
                    f"{self.settings.extraction_backend_url}/api/runs/{task.native_job_id}/cancel"
                    if terminal_status == "cancelled"
                    else f"{self.settings.extraction_backend_url}/api/runs/{task.native_job_id}/execution-terminal",
                    json=None if terminal_status == "cancelled" else {"status": terminal_status, "error": (error or task.error or "Extraction worker did not finish")[:1000]},
                    timeout=3,
                )
        except requests.RequestException:
            pass
