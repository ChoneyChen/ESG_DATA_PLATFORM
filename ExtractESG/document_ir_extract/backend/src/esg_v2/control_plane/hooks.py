from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

from esg_v2.config import Settings
from esg_v2.contracts import OcrJobState
from esg_v2.control_plane.contracts import PipelineTask, PipelineTaskType, utc_now
from esg_v2.control_plane.scheduler import PipelineTaskHooks, WorkerCommand
from esg_v2.document.contracts import DocumentIrJobState
from esg_v2.storage.job_store import JobStore
from esg_v2.storage.package_layout import package_dir
from esg_v2.utils.sanitization import sanitize_remote_url


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
        if task.task_type == PipelineTaskType.TARGETED_EXTRACTION:
            return self._targeted_command(task, environment)

        task_dir = self.settings.pipeline_task_root / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        request_path = task_dir / "request.json"
        request_path.write_text(
            json.dumps({k: v for k, v in task.payload.items() if not k.startswith("_plan_")}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        request_path.chmod(0o600)
        operation = "ocr" if task.task_type == PipelineTaskType.OCR else f"document_ir_{task.operation}"
        return WorkerCommand(
            argv=[
                sys.executable,
                "-m",
                "esg_v2.control_plane.worker",
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
                    dict(event.get("detail")) if isinstance(event.get("detail"), dict) else {}
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
            elif event.get("kind") == "telemetry" and isinstance(event.get("detail"), dict):
                state.telemetry = event["detail"]
            state.updated_at = utc_now()
            self.ir_store.put(state)

    def probe(self, task: PipelineTask) -> dict[str, Any] | None:
        if task.task_type != PipelineTaskType.TARGETED_EXTRACTION or not task.native_job_id:
            return None
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    f"{self.settings.targeted_backend_url}/api/jobs/{task.native_job_id}",
                    timeout=2,
                )
            if response.status_code != 200:
                return None
            record = response.json()
        except (requests.RequestException, ValueError):
            return None
        return {
            "stage": str(record.get("stage") or "targeted_extraction"),
            "message": f"定向抽取 · {record.get('stage') or record.get('status')}",
            "progress_current": int(record.get("progress_current") or 0),
            "progress_total": int(record.get("progress_total") or 0),
        }

    def completed(self, task: PipelineTask, result: dict[str, Any]) -> None:
        if task.task_type == PipelineTaskType.OCR:
            state = self.ocr_store.get(str(task.native_job_id))
            if not state:
                return
            state.status = "done"
            state.message = "OCR job completed"
            state.job_id = result.get("job_id")
            state.result_json_url = sanitize_remote_url(str(result.get("result_json_url") or "")) or None
            state.page_count = int(result.get("page_count") or 0)
            state.progress_current = state.page_count
            state.progress_total = max(state.progress_total, state.page_count)
            state.manifest_path = Path(str(result["manifest_path"]))
            self.ocr_store.put(state)
        elif task.task_type == PipelineTaskType.DOCUMENT_IR:
            retention = result.get("retention")
            if not isinstance(retention, dict) and isinstance(result.get("manifest"), dict):
                retention = result["manifest"].get("retention")
            if isinstance(retention, dict):
                for run_id in retention.get("pruned_run_ids") or []:
                    self.ir_store.discard(str(run_id))
            state = self.ir_store.get(str(task.native_job_id))
            if not state:
                return
            state.status = "done"
            pruned_count = len(retention.get("pruned_run_ids") or []) if isinstance(retention, dict) else 0
            state.message = (
                f"Document IR completed; retained best revision and removed {pruned_count} superseded revision(s)"
                if pruned_count
                else "Document IR job completed"
            )
            state.manifest_path = package_dir(
                self.settings.document_ir_output_root, str(task.native_job_id)
            ) / "manifest.json"
            state.telemetry = result.get("telemetry") or state.telemetry
            if isinstance(retention, dict):
                state.summary["retention"] = retention
            state.finished_at = utc_now()
            state.updated_at = state.finished_at
            self.ir_store.put(state)

    def failed(self, task: PipelineTask, error: str) -> None:
        if task.task_type == PipelineTaskType.TARGETED_EXTRACTION:
            self._targeted_terminal(task, terminal_status="failed")
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
        self._terminal_native(task, "interrupted", "Task interrupted by backend shutdown", None)

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

    def _targeted_command(
        self,
        task: PipelineTask,
        environment: dict[str, str],
    ) -> WorkerCommand:
        if not self.settings.targeted_runtime_python.is_file():
            raise FileNotFoundError(
                f"Targeted extraction Python not found: {self.settings.targeted_runtime_python}"
            )
        backend_root = self.settings.targeted_app_root / "backend"
        payload = task.payload
        if task.operation == "resume":
            environment["PYTHONPATH"] = os.pathsep.join(
                [
                    str(backend_root / "src"),
                    str(self.settings.targeted_app_root.parent / "standard_packages" / "src"),
                    environment.get("PYTHONPATH", ""),
                ]
            ).rstrip(os.pathsep)
            return WorkerCommand(
                argv=[
                    str(self.settings.targeted_runtime_python),
                    "-m",
                    "esg_targeted.cli",
                    "resume",
                    "--job-id",
                    str(task.native_job_id),
                ],
                cwd=backend_root,
                environment=environment,
            )
        argv = [
            str(self.settings.targeted_runtime_python),
            "-m",
            "esg_targeted.cli",
            "run",
            "--job-id",
            str(task.native_job_id),
            "--ir-run-id",
            str(payload["ir_run_id"]),
            "--package-id",
            str(payload["package_id"]),
            "--package-version",
            str(payload["package_version"]),
        ]
        for metric_id in payload.get("metric_ids") or []:
            argv.extend(["--metric", str(metric_id)])
        if payload.get("standard_snapshot_path"):
            argv.extend(["--standard-snapshot", str(payload["standard_snapshot_path"])])
        if not payload.get("semantic_search", True):
            argv.append("--no-semantic-search")
        if not payload.get("semantic_fill", True):
            argv.append("--retrieval-only")
        semantic_provider = payload.get("semantic_provider")
        if semantic_provider not in {"local_nuextract", "qiniu_vlm"}:
            raise ValueError(
                "Targeted extraction task lost its explicit semantic_provider contract"
            )
        argv.extend(["--semantic-provider", str(semantic_provider)])
        if payload.get("semantic_model"):
            argv.extend(["--semantic-model", str(payload["semantic_model"])])
        argv.extend(
            [
                "--retrieval-object-top-n",
                str(payload.get("retrieval_object_top_n", 3)),
            ]
        )
        if not payload.get("visual_fallback", True):
            argv.append("--no-visual-fallback")
        if payload.get("force_unready_ir"):
            argv.append("--force-unready-ir")
        python_paths = [
            str(backend_root / "src"),
            str(self.settings.targeted_app_root.parent / "standard_packages" / "src"),
        ]
        if environment.get("PYTHONPATH"):
            python_paths.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(python_paths)
        return WorkerCommand(argv=argv, cwd=backend_root, environment=environment)

    def _targeted_terminal(self, task: PipelineTask, *, terminal_status: str) -> None:
        if not task.native_job_id:
            return
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.post(
                    f"{self.settings.targeted_backend_url}/api/jobs/{task.native_job_id}/cancel",
                    params={"terminal": "true", "terminal_status": terminal_status},
                    timeout=3,
                )
        except requests.RequestException:
            return
