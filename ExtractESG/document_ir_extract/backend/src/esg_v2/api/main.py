from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from urllib.parse import quote

import requests
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from esg_runtime.lifecycle import LifecycleBlocked
from esg_runtime.local_operator import install_loopback_marker

from esg_v2 import __version__
from esg_v2.runtime_compatibility import compatibility_snapshot, check_compatibility, check_peer, runtime_release
from esg_v2.assets import ReportAssetCatalog
from esg_v2.config import get_settings
from esg_v2.contracts import OcrJobState, OcrRunRequest, OptionalPayload, validate_page_indices
from esg_v2.control_plane.contracts import (
    DocumentIrPipelineTaskRequest,
    ExistingPipelineTaskRequest,
    OcrPipelineTaskRequest,
    PipelineOrderRequest,
    PipelineTask,
    PipelineTaskType,
    TargetedExtractionBatchPipelineTaskRequest,
    TargetedExtractionPipelineTaskRequest,
    TargetedExtractionResumeRequest,
)
from esg_v2.control_plane.hooks import PlatformTaskHooks
from esg_v2.control_plane.scheduler import PipelineScheduler
from esg_v2.control_plane.secrets import RuntimeSecretVault
from esg_v2.control_plane.store import PipelineQueueStore
from esg_v2.control_plane.plans import PipelinePlanCoordinator, PipelinePlanRequest, dispatch_key, execution_budget_seconds, display_plan_name
from esg_v2.control_plane.plan_lifecycle import PlanLifecycle
from esg_v2.control_plane.progress import stage_label, classify_error
from esg_v2.control_plane.report_processing import ProcessingConfig, ReportProcessingCoordinator
from esg_v2.control_plane.production_automation import ProductionAutomationCoordinator
from esg_v2.control_plane.record_deletion import ControlPlaneRecordDeletion, detach_terminal_library_records
from esg_v2.api.record_deletion import record_deletion_router
from esg_v2.control_plane.native_dispatch import reserve_native_run, ensure_native_state
from esg_v2.api.report_processing import processing_router
from esg_v2.api.console import console_router, MaterializationRequest, DeliveryRequest
from esg_v2.api.production_automation import production_automation_router
from esg_v2.api.operator_auth import authorize_operator
from esg_v2.api.error_contract import register_api_errors
from esg_v2.assets.library_deletion import LibraryDeletionService
from esg_v2.api.library_deletion import library_deletion_router
from esg_v2.api.report_session_read import read_report_session, summarize_report_session
from esg_v2.control_plane.model_plan import report_processing_candidates, report_processing_config, report_model_task_options
from esg_v2.api.processing_status import processing_status_router
from esg_v2.api.report_session_actions import report_evidence_actions
from esg_v2.storage.processing_lifecycle import ProcessingLifecycle, processing_lifecycle_router
from esg_v2.control_plane.shared_contracts import ExtractionRequest, ReportExtractionRequest
from esg_v2.document.contracts import (
    DocumentIrBuildRequest,
    DocumentIrJobState,
    DocumentIrRepairRequest,
    PatchDecisionRequest,
    ReviewRetryRequest,
)
from esg_v2.document.catalog import DocumentIrCatalog
from esg_v2.document.identity import (
    document_id_from_sha256,
    manifest_document_id,
    manifest_document_label,
    normalize_sha256,
)
from esg_v2.document.revision_service import DocumentIrRevisionService
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.versioning import DocumentIrVersionManager
from esg_v2.models.review_provider import provider_status
from esg_v2.models.provider_rate_limit import ProviderRateLimitCoordinator
from esg_v2.models.qiniu_adapter import QiniuModelAdapter
from esg_v2.runtime_telemetry import utc_now
from esg_v2.storage.job_store import JobStore
from esg_v2.storage.queue_lifecycle import QueueReferenceLifecycle
from esg_v2.storage.inventory import (
    DeletionPlanRequest,
    StorageInventoryService,
)
from esg_v2.storage.package_layout import (
    new_run_id,
    package_dir,
    require_package_dir_name,
    require_run_id,
)
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
from esg_v2.workflows.ocr_workflow import OcrWorkflow
from esg_v2.utils.sanitization import sanitize_payload, sanitize_remote_url, is_sensitive_remote_url
from esg_v2.utils.hashing import sha256_file


settings = get_settings()
settings.output_root.mkdir(parents=True, exist_ok=True)
settings.document_ir_output_root.mkdir(parents=True, exist_ok=True)
settings.upload_root.mkdir(parents=True, exist_ok=True)
settings.storage_cleanup_root.mkdir(parents=True, exist_ok=True)
settings.report_asset_root.mkdir(parents=True, exist_ok=True)
settings.pipeline_task_root.mkdir(parents=True, exist_ok=True)

ocr_store = JobStore(settings.ocr_job_state_root, OcrJobState)
ir_store = JobStore(settings.document_ir_job_state_root, DocumentIrJobState)
ocr_workflow = OcrWorkflow(settings)
ir_workflow = DocumentIrWorkflow(settings)
revision_service = DocumentIrRevisionService(settings)
storage_inventory = StorageInventoryService(
    ocr_output_root=settings.output_root,
    ir_output_root=settings.document_ir_output_root,
    ocr_state_root=settings.ocr_job_state_root,
    ir_state_root=settings.document_ir_job_state_root,
    upload_root=settings.upload_root,
    cleanup_root=settings.storage_cleanup_root,
    pipeline_queue_db=settings.pipeline_queue_db,
)
report_assets = ReportAssetCatalog(settings.report_asset_root, settings.output_root, pipeline_queue_db=settings.pipeline_queue_db)


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=4000)
    expected_revision: int | None = Field(default=None, ge=0)


def lifecycle_error(exc):
    return HTTPException(404 if isinstance(exc,(KeyError,FileNotFoundError)) else 409,
        {"code":"lifecycle_blocked","message":str(exc),"blockers":exc.blockers if isinstance(exc,LifecycleBlocked) else []})
pipeline_store = PipelineQueueStore(settings.pipeline_queue_db)
queue_references = QueueReferenceLifecycle(pipeline_store, settings.document_ir_output_root, report_assets.reference_guard)
pipeline_secrets = RuntimeSecretVault()
pipeline_hooks = PlatformTaskHooks(settings, ocr_store, ir_store)
pipeline_scheduler = PipelineScheduler(
    pipeline_store,
    pipeline_secrets,
    pipeline_hooks,
    poll_interval_seconds=settings.pipeline_poll_interval_seconds,
)

app = FastAPI(title="ESG v2 Extraction Backend", version=__version__)
install_loopback_marker(app)
register_api_errors(app, "document")
library_deletion = LibraryDeletionService(report_assets, storage_inventory, ocr_store=ocr_store, ir_store=ir_store)
app.include_router(library_deletion_router(library_deletion, authorize_operator))
app.include_router(processing_status_router(pipeline_store))
from esg_v2.control_plane.execution_api import register_execution_api
register_execution_api(app, pipeline_store)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/ocr_output", StaticFiles(directory=str(settings.output_root)), name="ocr_output")
app.mount(
    "/document_ir_output",
    StaticFiles(directory=str(settings.document_ir_output_root)),
    name="document_ir_output",
)


@app.on_event("startup")
def start_pipeline_scheduler() -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    ocr_store.recover_interrupted()
    ir_store.recover_interrupted()
    pipeline_scheduler.start()
    pipeline_plans.start()
    report_processing.start()
    production_automation.start()
    execution_deletion.start()
    library_deletion.start()


@app.on_event("shutdown")
def stop_pipeline_scheduler() -> None:
    library_deletion.stop()
    execution_deletion.stop()
    production_automation.stop()
    report_processing.stop()
    pipeline_plans.stop()
    pipeline_scheduler.stop()


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "service": "esg-v2-backend", "version": __version__,
            "compatibility": compatibility_snapshot(),
            "capabilities": {"report_sessions_v1": True, "unified_queue_only": True,
                             "evidence_availability_v2": True, "compatibility_v1": True,
                             "production_automation_v1": True}}


@app.get("/health/compatibility")
def get_compatibility() -> dict:
    return compatibility_snapshot()


def _new_pipeline_task_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pt-{timestamp}-{uuid4().hex[:10]}"


def _new_target_job_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"tx-{timestamp}-{uuid4().hex[:10]}"


def _queue_task(
    *,
    task_type: PipelineTaskType,
    operation: str,
    title: str,
    payload: dict[str, object],
    native_job_id: str,
    secrets: dict[str, str | None] | None = None,
    deduplicate_native: bool = False,
) -> PipelineTask:
    return _queue_tasks(
        [
            {
                "task_type": task_type,
                "operation": operation,
                "title": title,
                "payload": payload,
                "native_job_id": native_job_id,
                "secrets": secrets or {},
                "deduplicate_native": deduplicate_native,
            }
        ]
    )[0]


def _queue_tasks(specifications: list[dict[str, object]]) -> list[PipelineTask]:
    rows: list[dict[str, object]] = []
    secrets_by_task_id: dict[str, dict[str, str | None]] = {}
    for specification in specifications:
        task_id = _new_pipeline_task_id()
        secret_values = specification.get("secrets")
        if not isinstance(secret_values, dict):
            secret_values = {}
        secret_values = dict(secret_values)
        payload = dict(specification["payload"])
        if execution_budget_seconds.get():
            payload["_total_timeout_seconds"] = execution_budget_seconds.get()
        if specification["task_type"] == PipelineTaskType.OCR and isinstance(payload.get("file_url"), str) and is_sensitive_remote_url(payload["file_url"]):
            secret_values["ESG_OCR_SOURCE_URL"] = payload["file_url"]
            payload["file_url"] = sanitize_remote_url(payload["file_url"])
            payload["_requires_source_url"] = True
        rows.append(
            {
                "task_id": task_id,
                "task_type": specification["task_type"],
                "operation": specification["operation"],
                "title": specification["title"],
                "payload": {**payload, **({"_plan_step_key": dispatch_key.get()} if dispatch_key.get() else {})},
                "native_job_id": specification["native_job_id"],
                "requires_runtime_secret": any(secret_values.values()),
                "deduplicate_native": bool(specification.get("deduplicate_native", False)),
            }
        )
        secrets_by_task_id[task_id] = secret_values
    with pipeline_secrets.handoff():
        tasks = pipeline_store.create_many(rows)
        for task, row in zip(tasks, rows, strict=True):
            # A poller may claim a new row immediately after commit. Its pop()
            # waits for this handoff; a duplicate running task keeps its owner.
            if task.task_id == row["task_id"] or task.status.value == "queued":
                pipeline_secrets.put(task.task_id, secrets_by_task_id[str(row["task_id"])])
    pipeline_scheduler.wake()
    return tasks


def _guard_qiniu_queue(*, provider: str, api_key: str | None, enabled: bool) -> None:
    """Reject known account-level outages before creating an empty child revision."""
    if not enabled or provider != "qiniu":
        return
    adapter = QiniuModelAdapter(settings, api_key=api_key)
    provider_block = ProviderRateLimitCoordinator(
        settings,
        adapter.credential_scope_id,
    ).account_block()
    if provider_block is not None:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "qiniu_account_tpd_circuit_open",
                "message": "七牛账号每日 Token 限额仍处于熔断期，本次未加入会生成空版本的任务。",
                "provider_state": provider_block,
            },
        )


@app.get("/api/pipeline/tasks")
def list_pipeline_tasks(limit: int = Query(300, ge=1, le=1000), include_archived: bool = False) -> list[PipelineTask]:
    return [_queue_with_references(task) for task in pipeline_store.list(limit, include_archived=include_archived)]


def _queue_with_references(task):
    from esg_v2.control_plane.shared_contracts import AvailableAction
    references = queue_references.describe(task.task_id)
    task.source_references = references
    task.available_actions = [action for action in task.available_actions if action.action not in {"archive", "restore", "discard", "delete", "release-references", "restore-references"}]
    with pipeline_store._connect() as db:
        raw, _ = execution_deletion._state("queue", task.task_id, db)
        owned = execution_deletion._parent_blockers("queue", raw, db)
    if not owned:
        task.available_actions.append(AvailableAction.model_validate(execution_deletion.describe("queue", raw["task_id"], raw)))
    references["available_actions"] = []
    return task


@app.delete("/api/pipeline/tasks/history")
def clear_pipeline_task_history(authorization: str | None = Header(default=None)) -> dict[str, object]:
    authorize_operator(authorization, "admin")
    with pipeline_store._connect() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM pipeline_tasks WHERE status NOT IN ('queued','running') ORDER BY created_at LIMIT 1000")]
    deleted, pending, blocked = [], [], []
    for row in rows:
        try:
            result = execution_deletion.request("queue", row["task_id"],
                **execution_deletion.describe("queue", row["task_id"], row)["payload"], authorization=authorization)
            (deleted if result.get("deleted") else pending).append(row["task_id"])
        except (KeyError, ValueError) as exc:
            blocked.append({"task_id": row["task_id"], "message": str(exc)})
    return {"operation": "delete", "deleted_count": len(deleted), "pending_count": len(pending),
        "task_ids": deleted, "pending_task_ids": pending, "blockers": blocked,
        "source_artifacts_preserved": True}


@app.get("/api/pipeline/tasks/{task_id}")
def get_pipeline_task(task_id: str) -> PipelineTask:
    try:
        return _queue_with_references(pipeline_store.get(task_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline task not found") from exc


@app.get("/api/pipeline/tasks/{task_id}/events")
def get_pipeline_task_events(
    task_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=2000),
) -> list[dict[str, object]]:
    try:
        return pipeline_store.events(task_id, after, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline task not found") from exc


@app.put("/api/pipeline/tasks/order")
def reorder_pipeline_tasks(request: PipelineOrderRequest = Body(...)) -> list[PipelineTask]:
    try:
        tasks = pipeline_store.reorder(request.task_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Pipeline task not found: {exc}") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    pipeline_scheduler.wake()
    return tasks


@app.post("/api/pipeline/tasks/{task_id}/cancel")
def cancel_pipeline_task(task_id: str) -> PipelineTask:
    try:
        previous = pipeline_store.get(task_id)
        task = pipeline_store.request_cancel(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline task not found") from exc
    pipeline_secrets.discard(task_id)
    if previous.status.value == "queued" and task.status.value == "cancelled":
        pipeline_hooks.cancelled(task)
    pipeline_scheduler.wake()
    return task


@app.post("/api/pipeline/tasks/native/{native_id}/cancel")
def cancel_native_pipeline_task(native_id: str) -> dict[str, object]:
    try:
        require_package_dir_name(native_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    active = pipeline_store.active_native_task(native_id)
    task = cancel_pipeline_task(active.task_id) if active else None
    return {"native_job_id": native_id, "task_id": task.task_id if task else None,
        "status": task.status.value if task else "not_active",
        "cancel_requested": bool(task.cancel_requested) if task else False,
        "task": task.model_dump(mode="json") if task else None}


def _plan_ir_manifest(run_id):
    path = package_dir(settings.document_ir_output_root, run_id) / "manifest.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _dispatch_plan_step(name, config, document, secrets):
    preparation = document.get("processing_config") or {}
    if name == "ocr":
        return queue_ocr_task(OcrPipelineTaskRequest(asset_id=document["asset_id"],
            ocr_provider=preparation.get("ocr_provider", config["ocr_provider"]),
            model=preparation.get("ocr_model", "PaddleOCR-VL-1.6"),
            allow_api_fallback=preparation.get("allow_cloud_ocr", config["ocr_provider"] != "local_paddleocr"),
            page_selection=preparation.get("page_selection", "all"),
            token=secrets.get("ocr_token")))
    if name == "ir":
        return queue_document_ir_task(DocumentIrPipelineTaskRequest(build_request=DocumentIrBuildRequest(
            ocr_run_id=document["ocr_run_id"], document_label=document["file_name"],
            execute_vlm_reviews=preparation.get("execute_optional_reviews", config.get("execute_optional_reviews", False)),
            execute_required_reviews=preparation.get("execute_required_reviews", config.get("execute_required_reviews", False)),
            review_provider=preparation.get("review_provider", config["review_provider"]),
            render_dpi=preparation.get("render_dpi", 144),
            max_auto_review_rounds=preparation.get("max_auto_review_rounds", 3),
            qiniu_api_key=secrets.get("qiniu_api_key"),
        )))
    if name == "report_session":
        request_payload = {**config["report_request"], "input": {"kind": "document_ir", "ir_run_id": document["ir_run_id"]}}
        session_id = config.get("_resume_job_id") or document.get("native_session_id")
        if request_payload.get('extension_selections') and not session_id:
            frozen = request_payload.get('frozen_runtime_standard_snapshots')
            if not frozen:
                raise ValueError('报告计划缺少完整冻结延伸快照，禁止重新生成或自动换版。')
            actual = _targeted_report_request('POST','/api/report-sessions/preview',request_payload)
            if actual.get('runtime_standard_snapshots') != frozen:
                raise ValueError('报告计划的父包或延伸组合快照与执行合同不一致，创建被阻塞。')
        if config.get("_transition_queued"):
            session = _targeted_report_request("GET", f"/api/report-sessions/{session_id}")
        elif config.get("_resume_job_id"):
            session = _targeted_report_request("POST", f"/api/report-sessions/{session_id}/resume?enqueue=false")
        elif session_id:
            session = _targeted_report_request("GET", f"/api/report-sessions/{session_id}")
        else:
            session = _targeted_report_request("POST", f"/api/report-sessions?enqueue=false&idempotency_key={quote(str(config['_plan_id']) + ':' + str(config['_document_index']), safe='')}", request_payload)
            document["native_session_id"] = session["session_id"]
            # Persist creation before enqueuing, making the HTTP/queue crash gap recoverable.
            plan = next(p for p in pipeline_plans.list() if p["plan_id"] == config["_plan_id"])
            plan["documents"][config["_document_index"]]["native_session_id"] = session["session_id"]
            pipeline_plans.save(plan)
        return _queue_task(task_type=PipelineTaskType.TARGETED_EXTRACTION,
            operation="report_session_resume" if config.get("_resume_job_id") else "report_session",
            title=f"报告提取 · {document.get('file_name') or document['ir_run_id']}",
            payload={**request_payload, "budget": ReportExtractionRequest.model_validate(request_payload).effective_budget().model_dump(mode="json")},
            native_job_id=session["session_id"], secrets={"QINIU_API_KEY": secrets.get("qiniu_api_key"),
                "PADDLEOCR_VL_API_TOKEN": secrets.get("ocr_token")},
            # A plan step may be dispatched again while its first queue entry is
            # still active (request-thread tick beside the coordinator loop).
            # Adopt that entry instead of failing the document.
            deduplicate_native=bool(dispatch_key.get()))
    if config.get("_resume_job_id"):
        return queue_targeted_extraction_resume(config["_resume_job_id"], TargetedExtractionResumeRequest(qiniu_api_key=secrets.get("qiniu_api_key")))
    standard = next(s for s in config["standards"] if name == f"targeted:{s['package_id']}")
    return queue_targeted_extraction_task(TargetedExtractionPipelineTaskRequest(
        ir_run_id=document["ir_run_id"], **standard,
        semantic_provider=config["semantic_provider"], semantic_model=config["semantic_model"],
        retrieval_object_top_n=config["retrieval_object_top_n"], qiniu_api_key=secrets.get("qiniu_api_key"),
        extraction_mode=config.get("extraction_mode", "auto"), shared_object_budget=config.get("shared_object_budget", 3),
        agent_max_steps=config.get("agent_max_steps", 8),
    ))


def _cancel_plan_child(task_id):
    task = pipeline_store.get(task_id)
    if task.status.value in {"queued", "running"}:
        cancel_pipeline_task(task_id)


def _inspect_plan_session(plan, document):
    """Read-only adoption of a report session that already finished."""
    session_id = document.get("native_session_id")
    return _targeted_report_request("GET", f"/api/report-sessions/{session_id}") if session_id else None


pipeline_plans = PipelinePlanCoordinator(pipeline_store, _dispatch_plan_step, _plan_ir_manifest, _cancel_plan_child,
    inspect_session=_inspect_plan_session)


def _dispatch_processing_step(stage, config, state, secrets):
    if stage == "ocr":
        return queue_ocr_task(OcrPipelineTaskRequest(asset_id=state["asset_id"],
            ocr_provider=config["ocr_provider"], model=config["ocr_model"],
            allow_api_fallback=config["allow_cloud_ocr"], token=secrets.get("ocr_token"),
            page_selection=config.get("page_selection", "all")))
    if stage == "ir":
        return queue_document_ir_task(DocumentIrPipelineTaskRequest(build_request=DocumentIrBuildRequest(
            ocr_run_id=state["ocr_run_id"], document_label=state["file_name"],
            render_dpi=config["render_dpi"], execute_vlm_reviews=config["execute_optional_reviews"],
            execute_required_reviews=config.get("execute_required_reviews", False),
            max_auto_review_rounds=config["max_auto_review_rounds"],
            review_provider=config["review_provider"], qiniu_api_key=secrets.get("qiniu_api_key"))))
    raise ValueError("Unknown report-processing stage")


report_processing = ReportProcessingCoordinator(pipeline_store, report_assets,
    _dispatch_processing_step, _cancel_plan_child, settings.output_root, settings.document_ir_output_root,
    runtime_config={"ocr": {key: str(getattr(settings, key, "")) for key in (
        "local_paddleocr_model_path", "local_paddleocr_pipeline_version", "local_paddleocr_vlm_backend",
        "local_paddleocr_vlm_server_url", "local_paddleocr_runtime_python")},
        "ir": {key: str(getattr(settings, key, "")) for key in (
        "qwen_model_path", "nuextract_model_path", "qiniu_vlm_model", "max_vlm_reviews_per_ir_run",
        "max_blocking_vlm_reviews_per_ir_run", "max_optional_vlm_reviews_per_ir_run")}})
pipeline_plans.record_processing = report_processing.record_plan_artifacts


def _require_processing_ready():
    snapshot = compatibility_snapshot()
    if not snapshot.get("readiness", {}).get("ready"):
        raise HTTPException(503, {"code": "runtime_requires_restart", "layer": "compatibility",
            "message": "报告处理服务版本未就绪，请协调重启后继续；历史记录与成果保留", "retryable": True,
            "artifacts_retained": True, "blockers": snapshot.get("readiness", {}).get("blockers", [])})


app.include_router(processing_router(report_processing, _require_processing_ready))
processing_lifecycle = ProcessingLifecycle(report_processing, report_assets.reference_guard)
report_processing.lifecycle_actions = processing_lifecycle.describe
app.include_router(processing_lifecycle_router(processing_lifecycle))


def _targeted_catalog_request(path: str) -> object:
    """Read the targeted catalog through the backend-configured service URL.

    The pipeline UI intentionally talks to one backend only.  Keeping this hop
    here prevents stale browser-local targeted URLs from hiding standards in
    the full-pipeline planner.  A read-only catalog stays readable while that
    service is busy extracting, so it is retried instead of failing a batch.
    """
    for attempt in range(3):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(f"{settings.targeted_backend_url}{path}", timeout=30)
                response.raise_for_status()
                return response.json()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 502
            if status_code == 404:
                message = "所需接口不可用，定向后端可能尚未更新" if path in {
                    "/api/compatibility", "/api/standards/catalog", "/api/standards/diagnostics"
                } or path.startswith("/api/extraction-profiles/") else "标准包不存在或已退役"
                raise HTTPException(404, message) from exc
            if status_code == 409:
                try:
                    detail = exc.response.json().get("detail", "所选标准范围暂不可执行")
                except ValueError:
                    detail = "所选标准范围暂不可执行"
                raise HTTPException(409, detail) from exc
            raise HTTPException(502, "定向抽取后端未能返回标准目录") from exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == 2:
                raise HTTPException(503, "定向抽取后端正忙，标准目录暂不可用") from exc
            time.sleep(1.5 * (attempt + 1))
        except (requests.RequestException, ValueError) as exc:
            raise HTTPException(503, "无法连接定向抽取后端，标准目录暂不可用") from exc


@app.get("/api/pipeline/standards")
def list_pipeline_standards():
    return _targeted_catalog_request("/api/standards")


@app.get('/api/pipeline/extensions')
def list_pipeline_extensions():
    return _targeted_catalog_request('/api/extensions')


@app.get("/api/pipeline/compatibility")
def pipeline_compatibility() -> dict:
    document = compatibility_snapshot()
    targeted = None
    blockers = list(document["readiness"]["blockers"])
    try:
        targeted = _targeted_catalog_request("/api/compatibility")
        blockers.extend(check_peer(targeted, runtime_release.started, "targeted-backend"))
    except HTTPException as exc:
        blockers.append(str(exc.detail))
    return {"protocol_version": document["protocol_version"], "ready": not blockers,
            "blockers": blockers, "document": document, "targeted": targeted}


@app.get("/api/pipeline/standards/catalog")
def pipeline_standard_catalog():
    return _targeted_catalog_request("/api/standards/catalog")


def _targeted_write(path: str, body: dict) -> dict:
    """Forward one lifecycle action to the targeted backend, preserving its refusal reason."""
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(f"{settings.targeted_backend_url}{path}", json=body, timeout=60)
            response.raise_for_status()
            return response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        try:
            detail = exc.response.json().get("detail", "条款操作被拒绝")
        except ValueError:
            detail = "条款操作被拒绝"
        raise HTTPException(status if status in {400, 404, 409} else 502, detail) from exc
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(503, "无法连接定向后端，条款操作暂不可用") from exc


@app.get("/api/pipeline/clause-catalog")
def pipeline_clause_catalog():
    return _targeted_catalog_request("/api/clause-catalog")


@app.get("/api/pipeline/clause-catalog/{package_id}/{package_version}/{source_datapoint_id}")
def pipeline_clause_package(package_id: str, package_version: str, source_datapoint_id: str):
    return _targeted_catalog_request(
        f"/api/clause-catalog/{quote(package_id, safe='')}/{quote(package_version, safe='')}"
        f"/{quote(source_datapoint_id, safe='')}")


@app.get("/api/pipeline/clause-profiles/{package_id}/{package_version}")
def pipeline_clause_profiles(package_id: str, package_version: str):
    return _targeted_catalog_request(
        f"/api/clause-profiles/{quote(package_id, safe='')}/{quote(package_version, safe='')}")


@app.get("/api/pipeline/clause-lifecycle")
def pipeline_clause_lifecycle():
    return _targeted_catalog_request("/api/clause-lifecycle")


@app.post("/api/pipeline/clause-lifecycle/acceptances")
def pipeline_clause_acceptance(request: dict = Body(...)):
    return _targeted_write("/api/clause-lifecycle/acceptances", request)


@app.post("/api/pipeline/clause-lifecycle/retirements")
def pipeline_clause_retirement(request: dict = Body(...)):
    return _targeted_write("/api/clause-lifecycle/retirements", request)


@app.get("/api/pipeline/standards/{package_id}/{package_version}/diff/{other_version}")
def pipeline_standard_diff(package_id: str, package_version: str, other_version: str):
    return _targeted_catalog_request(
        f"/api/standards/{quote(package_id, safe='')}/{quote(package_version, safe='')}"
        f"/diff/{quote(other_version, safe='')}")


@app.get("/api/pipeline/standards/{package_id}/{package_version}/dependents/{source_datapoint_id}")
def pipeline_standard_dependents(package_id: str, package_version: str, source_datapoint_id: str):
    return _targeted_catalog_request(
        f"/api/standards/{quote(package_id, safe='')}/{quote(package_version, safe='')}"
        f"/dependents/{quote(source_datapoint_id, safe='')}")


@app.get("/api/pipeline/standards/diagnostics")
def pipeline_standard_diagnostics():
    return _targeted_catalog_request("/api/standards/diagnostics")


@app.get("/api/pipeline/extraction-profiles/foundation")
def pipeline_extraction_foundation(profile: str = "base16"):
    return _targeted_catalog_request(f"/api/extraction-profiles/foundation?profile={quote(profile)}")


@app.get("/api/pipeline/extraction-profiles/platform-major")
def get_extraction_platform_major(topics: str | None = None):
    suffix = f"?topics={quote(topics, safe=',')}" if topics else ""
    return _targeted_catalog_request("/api/extraction-profiles/platform-major" + suffix)


@app.get("/api/pipeline/standards/{package_id}/{package_version}")
def get_pipeline_standard(package_id: str, package_version: str):
    return _targeted_catalog_request(f"/api/standards/{package_id}/{package_version}")


@app.get('/api/pipeline/model-task-options')
def get_report_model_task_options():
    return report_model_task_options(settings)


@app.get("/api/pipeline/plans")
def list_pipeline_plans():
    return pipeline_plans.list()


@app.post("/api/pipeline/plans", status_code=202)
def create_pipeline_plan(request: PipelinePlanRequest = Body(...)):
    _require_extraction_ready()
    if request.extension_selections:
        raise HTTPException(400, '延伸条款请通过报告提取入口创建，以固定完整批准快照。')
    # Validate the complete batch before any worker or child task is created.
    if (request.semantic_provider == "qiniu_vlm" or request.review_provider == "qiniu") and not (request.qiniu_api_key or settings.qiniu_api_key):
        raise HTTPException(400, "七牛云计划需要 API Key 或后端 QINIU_API_KEY")
    for standard in request.standards:
        try:
            package = _targeted_catalog_request(
                f"/api/standards/{standard.package_id}/{standard.package_version}"
            )
        except HTTPException as exc:
            raise HTTPException(
                400,
                f"标准包不可用：{standard.package_id}@{standard.package_version}",
            ) from exc
        valid_ids = {m["metric_id"] for m in package.get("metrics", [])}
        if not set(standard.metric_ids) <= valid_ids:
            raise HTTPException(400, "计划包含标准包之外的指标")
    documents = []
    for asset_id in request.asset_ids:
        try:
            asset = report_assets.get(asset_id)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, f"PDF 资产不存在：{asset_id}") from exc
        doc = report_processing.resolve_document(asset_id, ProcessingConfig(
            ocr_provider=request.ocr_provider, allow_cloud_ocr=request.ocr_provider != "local_paddleocr",
            execute_optional_reviews=request.execute_optional_reviews, review_provider=request.review_provider,
            execute_required_reviews=request.execute_required_reviews,
            allow_limited_evidence=request.allow_limited_evidence), reuse=request.reuse_existing)
        documents.append(doc)
    return pipeline_plans.create(request, documents)


@app.post("/api/pipeline/plans/{plan_id}/{action}")
def act_pipeline_plan(plan_id: str, action: Literal["pause", "resume", "cancel"]):
    try:
        return pipeline_plans.action(plan_id, action)
    except KeyError as exc:
        raise HTTPException(404, "Plan not found") from exc


def _targeted_report_request(method, path, payload=None, *, authorization=None):
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.request(method, f"{settings.targeted_backend_url}{path}", json=payload, timeout=60,
                **({'headers': {'Authorization': authorization}} if authorization else {}))
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "Report backend rejected the request")
            except ValueError:
                detail = "Report backend rejected the request"
            raise HTTPException(response.status_code, detail)
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(503, "报告提取后端暂不可用；已有任务和成果保留") from exc


plan_lifecycle = PlanLifecycle(pipeline_plans, _targeted_report_request)


def _require_extraction_ready():
    compatibility = pipeline_compatibility()
    if not compatibility.get('ready'):
        raise HTTPException(503, {'code': 'business_not_ready', 'layer': 'compatibility',
            'message': '服务版本或标准合同未就绪，请查看兼容诊断', 'blockers': compatibility.get('blockers', [])})


def _report_document(request):
    if request.input.kind == "document_ir":
        manifest = _plan_ir_manifest(request.input.ir_run_id)
        if not manifest:
            raise HTTPException(404, "Document IR revision not found")
        return {"asset_id": None, "file_name": manifest_document_label(manifest),
            "ir_run_id": request.input.ir_run_id, "ocr_run_id": manifest.get("ocr_run_id") or manifest.get("source", {}).get("ocr_run_id")}
    try:
        asset = report_assets.get(request.input.asset_id)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Report PDF asset not found") from exc
    documents = [report_processing.resolve_document(request.input.asset_id, config)
                 for config in report_processing_candidates(request)]
    # Reuse the most complete compatible IR; otherwise reuse an OCR that a
    # review-free preparation can build on; otherwise start the cheapest one.
    return (next((d for d in documents if d.get("ir_run_id")), None)
            or next((d for d in documents[1:] if d.get("ocr_run_id")), None) or documents[-1])


def _report_plan_detail(plan):
    from esg_v2.control_plane.outcomes import execution_errors

    document = plan["documents"][0]
    native = None
    native_unavailable = None
    if document.get("native_session_id"):
        try:
            native = _targeted_report_request("GET", f"/api/report-sessions/{document['native_session_id']}")
        except HTTPException as exc:
            native_unavailable = {'code': 'extraction_read_unavailable', 'category': 'dependency',
                'message': '提取后端暂不可读；报告任务和准备阶段仍可查看', 'technical_detail': str(exc.detail),
                'recoverable': True, 'next_action': 'refresh'}
    statuses = {"active": "running" if document.get("status") == "running" else "queued",
        "completed": "finished", "partial": "finished", "needs_attention": "failed",
        "interrupted": "interrupted", "cancelled": "cancelled", "cancelling": "stopping", "paused": "interrupted"}
    report_step = document["steps"].get("report_session", {})
    outcome = report_step.get("outcome") or {}
    execution_status = statuses.get(plan["status"], "queued")
    if plan["status"] == "active":
        current_step = report_step or next(iter(reversed(list(document["steps"].values()))), {})
        if current_step.get("status") in {"queued", "running"}:
            execution_status = current_step["status"]
    elif plan["status"] not in {"cancelled", "cancelling", "paused"}:
        # Queue outcomes retain the actual terminal execution state. Older
        # plans persisted the same interruption under needs_attention/failed.
        stopped = outcome.get("execution_status") or report_step.get("status") or (native or {}).get("execution_status")
        if stopped in {"interrupted", "cancelled", "failed"}:
            execution_status = stopped
    native_errors = execution_errors(native)
    stage_errors = execution_errors(outcome)
    error = (document.get("error") if document.get("termination_reason") == "total_timeout" else
        next(iter(native_errors or stage_errors), None) or document.get("error"))
    platform_errors = list(dict.fromkeys(e for e in (
        document.get("platform_error"), report_step.get("platform_error"),
        document.get("error") if document.get("error") != error else None,
    ) if e))
    errors = list(dict.fromkeys([*native_errors, *stage_errors,
        *([document["error"]] if document.get("error") else []), *platform_errors]))
    preparation = []
    for name, step in document['steps'].items():
        queue_task = None
        if isinstance(step.get('task_id'), str) and step.get('task_id'):
            try:
                queue_task = pipeline_store.get(step['task_id']).model_dump(mode='json')
            except KeyError:
                pass
        preparation.append({**step, **(queue_task or {}), 'name': name, 'label': stage_label({'ir':'document_ir'}.get(name, name)),
            'error_detail': (queue_task or {}).get('error_detail'),
            'queue_record_missing': bool(step.get('task_id') and queue_task is None)})
    lifecycle = plan_lifecycle.describe(plan, (native or {}).get('lifecycle'))
    result = {**(native or {}), "session_id": plan["plan_id"], "native_session_id": document.get("native_session_id"),
        "schema_version": "report-session-platform-v1", "name": display_plan_name(plan), "request": plan["report_request"],
        "execution_status": execution_status, "native_execution_status": (native or {}).get("execution_status"),
        "result_status": (native or {}).get("result_status", "needs_confirmation" if document.get("status") == "waiting_review" else "not_evaluated"),
        "revision": f"{plan['updated_at']}:{(native or {}).get('revision', 0)}", "created_at": plan["created_at"],
        "updated_at": plan["updated_at"], "error": error, "errors": errors, "platform_errors": platform_errors,
        "job_ids": (native or {}).get("job_ids", []),
        "preparation_stages": preparation, "database_status": (native or {}).get('database_status', 'not_imported'),
        "execution_budget": pipeline_plans._execution_budget(plan, ReportExtractionRequest.model_validate(plan['report_request']).effective_budget().total_timeout_seconds),
        "model_budget": (native or {}).get("runtime_budget"),
        "lifecycle": lifecycle, 'native_unavailable': native_unavailable,
        "auto_handoff": document.get("auto_handoff"),
        'error_detail': native_unavailable or next((step['error_detail'] for step in reversed(preparation)
            if step.get('error_detail') and step['error_detail'].get('technical_detail') == error), None)
            or (native or {}).get('error_detail') or (classify_error(str(error)).model_dump(mode='json') if error else None),
        "evidence_policy": document.get("evidence_policy", {}), "available_actions": [
            {**a, "endpoint": a.get("endpoint", "").replace(str(document.get("native_session_id") or "__no_native__"), plan["plan_id"])}
            for a in (native or {}).get("available_actions", []) if a.get('action') not in {'archive', 'restore', 'discard'}]}
    jobs_by_id = {job["job_id"]: job for job in (native or {}).get("jobs", [])}
    result["stages"] = [{**stage,
        "label": stage_label(stage.get("stage", "unknown")),
        "package_id": jobs_by_id.get(stage.get("job_id"), {}).get("request", {}).get("package_id"),
        "disclosure_requirement": jobs_by_id.get(stage.get("job_id"), {}).get("request", {}).get("package_id", "").rsplit(".", 1)[-1].upper()}
        for stage in (native or {}).get("stages", [])]
    if plan["status"] in {"active", "paused"} and not any(a.get("action") == "cancel" for a in result["available_actions"]):
        result["available_actions"].append({"action": "cancel", "label": "取消任务", "scope": {"session_id": plan["plan_id"]},
            "endpoint": f"/api/report-sessions/{plan['plan_id']}/cancel", "calls_model": False, "incurs_cost": False})
    unfinished_projections = [job["job_id"] for job in (native or {}).get("jobs", [])
        if job.get("status") != "completed" and job.get("job_id")]
    unfinished_preparation = not document.get("native_session_id") and document.get("status") not in {"completed", "partial"}
    if (unfinished_projections or unfinished_preparation) and plan["status"] in {"partial", "needs_attention", "interrupted", "cancelled", "paused"} and not any(a.get("action") == "resume" for a in result["available_actions"]):
        result["available_actions"].append({"action": "resume", "label": "恢复未完成动作", "scope": {"session_id": plan["plan_id"],
            "job_ids": unfinished_projections},
            "endpoint": f"/api/report-sessions/{plan['plan_id']}/resume", "calls_model": True,
            "incurs_cost": plan["report_request"]["role_config"]["semantic_brain"]["provider"] == "qiniu_vlm"})
    result["available_actions"] = report_evidence_actions(
        plan, native, result["available_actions"], ir_root=settings.document_ir_output_root,
        execution_status=result["execution_status"])
    result['available_actions'] = [action for action in result['available_actions'] if action.get('action') not in {'archive','restore','discard','delete'}]
    result['available_actions'].append(execution_deletion.describe('session', plan['plan_id'], plan))
    result['deletion_pending'] = execution_deletion.pending('session', plan['plan_id'])
    reference_integrity = lifecycle.get('reference_integrity', {})
    result['reference_integrity'] = reference_integrity
    missing_current = [step.get('task_id') for step in preparation if step.get('queue_record_missing')]
    if reference_integrity.get('status') in {'missing','invalid'}:
        result['read_status'] = 'incomplete'
        result['state_notice'] = ('关联执行记录已缺失或引用格式不完整；现有来源与成果状态单独保留。')
        if missing_current or reference_integrity.get('invalid_reference_paths'):
            result['available_actions'] = [action for action in result['available_actions'] if action.get('action') in {'delete','cancel'}]
    if lifecycle['state'] != 'active':
        result['available_actions'] = [execution_deletion.describe('session', plan['plan_id'], plan)]
    elif result['execution_status'] == 'stopping':
        result['available_actions'] = [execution_deletion.describe('session', plan['plan_id'], plan)]
    return result


def _find_report_plan(session_id):
    return next((p for p in pipeline_plans.list() if p.get("report_request") and (p["plan_id"] == session_id or any(isinstance(d,dict) and d.get('native_session_id') == session_id for d in (p.get('documents') or [])))), None)


console_api = console_router(report_processing,pipeline_plans,_report_plan_detail,_targeted_report_request)
app.include_router(console_api)


def _auto_report_handoff(plan, document):
    from esg_runtime.local_operator import LOCAL_OPERATOR, acting_for
    from esg_v2.api.console import HandoffRequest

    key = plan.get("automation_key")
    if key:
        try:
            owner = production_automation.get(str(key).split(":", 1)[0])
        except KeyError:
            owner = None
        if owner and not owner.get("archived_at") \
                and owner.get("status") in {"planned", "active", "pausing", "paused", "waiting_credentials"}:
            return {"status": "deferred", "stage": "automation",
                "message": "由批量生产计划交付，不做重复衔接"}
    with acting_for(LOCAL_OPERATOR):
        return console_api.handoff(plan["plan_id"], HandoffRequest(), None)


# The coordinator, not a browser, finishes the chain for a report extraction.
pipeline_plans.on_report_handoff = _auto_report_handoff


def _automation_ready(target):
    _require_processing_ready()
    if target != "processing":
        _require_extraction_ready()


def _automation_deliver(session_id, report_id, report_revision, authorization):
    return console_api.deliver_candidates(session_id, DeliveryRequest(
        report_id=report_id, expected_revision=report_revision,
        reason="自动生产计划交付已冻结成果进入候选审核"), authorization)


production_automation = ProductionAutomationCoordinator(
    pipeline_store, report_processing, pipeline_plans,
    extraction_preview=lambda request: _targeted_report_request(
        "POST", "/api/report-sessions/preview", request.model_dump(mode="json")),
    extraction_detail=_report_plan_detail, deliver=_automation_deliver,
    authorize=authorize_operator, require_ready=_automation_ready,
)
app.include_router(production_automation_router(production_automation))
execution_deletion = ControlPlaneRecordDeletion(pipeline_store, report_processing, pipeline_plans,
    _targeted_report_request, cancel_pipeline_task, automations=production_automation)
app.include_router(record_deletion_router(execution_deletion, authorize_operator))


def _processing_record_actions(state):
    description = processing_lifecycle.describe(state)
    description['available_actions'] = [execution_deletion.describe('processing', state['processing_id'], state)]
    description['deletion_pending'] = execution_deletion.pending('processing', state['processing_id'])
    return description


report_processing.lifecycle_actions = _processing_record_actions
library_deletion.configure(cancel_task=cancel_pipeline_task, processing=report_processing,
    plans=pipeline_plans, automations=production_automation, native_request=_targeted_report_request,
    cleanup_records=detach_terminal_library_records)


@app.post("/api/report-sessions/preview")
def preview_report_session(request: ReportExtractionRequest):
    document = _report_document(request)
    payload = request.model_dump(mode="json")
    if document.get("ir_run_id"):
        payload["input"] = {"kind": "document_ir", "ir_run_id": document["ir_run_id"]}
    plan = _targeted_report_request("POST", "/api/report-sessions/preview", payload)
    preparation = (ProcessingConfig.model_validate(document["processing_config"]) if document.get("processing_config")
                   else report_processing_config(request))
    plan["document_preparation"] = {"ocr_provider": preparation.ocr_provider, 'ocr_model':preparation.ocr_model,
        "input_kind": request.input.kind, "ir_run_id": document.get("ir_run_id"),
        "ocr_reused": bool(document.get("ocr_run_id")), "ir_reused": bool(document.get("ir_run_id")),
        'review_provider':preparation.review_provider,'required_reviews':preparation.execute_required_reviews,
        "optional_reviews": preparation.execute_optional_reviews, "page_selection": preparation.page_selection}
    return plan


@app.post("/api/report-sessions", status_code=202)
def create_report_session(request: ReportExtractionRequest):
    _require_extraction_ready()
    preview = preview_report_session(request)  # Validate all immutable inputs before queue mutations.
    if request.model_plan is not None:
        from esg_v2.control_plane.shared_contracts import RoleConfig
        roles=preview.get('roles')
        if not isinstance(roles,dict):raise HTTPException(409,'预览未确认所选模型分配，请重新预览。')
        request=request.model_copy(update={'role_config':RoleConfig.model_validate({**request.role_config.model_dump(mode='json'),**roles})})
    frozen = preview.get('runtime_standard_snapshots', {})
    if request.extension_selections and not frozen:
        raise HTTPException(409,'预览未返回完整延伸组合快照，禁止创建报告任务。')
    if request.frozen_runtime_standard_snapshots and request.frozen_runtime_standard_snapshots != frozen:
        raise HTTPException(409,'报告提交的冻结延伸快照与批准合同不一致，禁止重写或换版。')
    request = request.model_copy(update={'frozen_runtime_standard_snapshots':frozen})
    preparation = report_processing_config(request)
    prepared = preview.get("document_preparation", {})
    cloud_review = (request.input.kind == "pdf" and not prepared.get("ir_reused")
        and preparation.review_provider == "qiniu"
        and (preparation.execute_required_reviews or preparation.execute_optional_reviews))
    if (cloud_review or any(role.provider == "qiniu_vlm" for role in (request.role_config.semantic_brain, request.role_config.specialist))) and not (request.qiniu_api_key or settings.qiniu_api_key):
        raise HTTPException(400, "云端角色需要本次密钥或后端配置")
    if (request.input.kind == "pdf" and not prepared.get("ocr_reused")
        and preparation.ocr_provider == "paddle_api"
        and not (request.ocr_token or getattr(settings, "paddle_token", None))):
        raise HTTPException(400, "所选云 OCR 需要本次 OCR Token 或后端配置")
    plan = pipeline_plans.create_report(request, _report_document(request))
    _invalidate_session_summaries()
    pipeline_plans.tick()
    return _report_plan_detail(_find_report_plan(plan["plan_id"]))


def _invalidate_session_summaries() -> None:
    """Kept for callers that create plans; the parsed-plan cache lives in the store."""
    return None


@app.get("/api/report-sessions")
def list_report_sessions(include_archived: bool = Query(False), view: Literal["full", "summary"] = Query("full")):
    rows = [read_report_session(p, _report_plan_detail, lambda kind, plan, _p=p: execution_deletion.describe(kind, plan['plan_id'], _p)) for p in pipeline_plans.list() if p.get("report_request")
        and (include_archived or p.get('lifecycle', {}).get('state', 'active') == 'active')]
    return [summarize_report_session(row) for row in rows] if view == "summary" else rows


@app.get("/api/report-sessions/{session_id}")
def get_report_session(session_id: str):
    plan = _find_report_plan(session_id)
    if plan:
        return read_report_session(plan, _report_plan_detail, lambda kind, plan, _p=plan: execution_deletion.describe(kind, plan['plan_id'], _p))
    return _targeted_report_request("GET", f"/api/report-sessions/{session_id}")


@app.get("/api/report-sessions/{session_id}/facts")
def get_report_session_facts(session_id: str):
    plan = _find_report_plan(session_id)
    native = plan["documents"][0].get("native_session_id") if plan else session_id
    if not native:
        return {"session_id": session_id, "source_facts": [], "mappings": [], "gaps": [], "status": "not_evaluated"}
    return _targeted_report_request("GET", f"/api/report-sessions/{native}/facts")


@app.get("/api/report-sessions/{session_id}/foundation16")
def get_report_session_foundation16(session_id: str):
    plan = _find_report_plan(session_id)
    native = plan["documents"][0].get("native_session_id") if plan else session_id
    if not native:
        return {"session_id": session_id, "status": "not_started", "plan": None}
    return _targeted_report_request("GET", f"/api/report-sessions/{native}/foundation16")


@app.get("/api/report-sessions/{session_id}/report-identity")
def report_session_identity(session_id: str):
    """Propose the report identity the platform needs, with its evidence.

    The operator confirms it; nothing here is inferred silently. Everything is
    either already registered, printed on the document, or stated by the
    extraction result (which records the period statement it read).
    """
    plan = _find_report_plan(session_id)
    if not plan:
        raise HTTPException(404, "报告提取会话不存在")
    document = plan["documents"][0]
    asset_id = document.get("asset_id")
    registered = next((report for report in report_processing.reports()
                       if any(version.get("asset_id") == asset_id for version in report.get("asset_versions", []))), None)
    label = None
    ir_run = document.get("ir_run_id")
    if ir_run:
        try:
            manifest, _ = report_processing._manifest("ir", ir_run)
            label = (manifest.get("document") or {}).get("document_label")
        except (OSError, ValueError, KeyError):
            label = None
    if not label and asset_id:
        try:
            label = report_processing.assets.get(asset_id).get("file_name")
        except KeyError:
            label = None
    period, period_source = None, None
    metadata = (registered or {}).get("metadata") or {}
    if metadata.get("period_start") and metadata.get("period_end"):
        period = {"start": metadata["period_start"], "end": metadata["period_end"], "source": "registered"}
        period_source = "registered"
    else:
        native = document.get("native_session_id")
        if native:
            try:
                result = _targeted_report_request("GET", f"/api/report-sessions/{native}/foundation16")
                statement = ((result or {}).get("result") or {}).get("reporting_period") or {}
                year = statement.get("year")
                if isinstance(year, int):
                    basis = statement.get("basis")
                    period = {"start": f"{year}-01-01", "end": f"{year}-12-31",
                              "year": year, "source": "extraction", "basis": basis,
                              "evidence": statement.get("evidence") or basis,
                              "weak": basis in {"report_file_label", "most_common_table_header_year"}}
                    period_source = "extraction"
            except HTTPException:
                period = None
    title = metadata.get("title") or label
    return {
        "session_id": session_id,
        "asset_id": asset_id,
        "registered": None if not registered else {
            "report_id": registered["report_id"], "revision": registered["revision"]},
        "proposal": {
            "title": title,
            "publisher_name": metadata.get("publisher_name"),
            "publisher_entity_ref": metadata.get("publisher_entity_ref"),
            "period_start": (period or {}).get("start"),
            "period_end": (period or {}).get("end"),
            "language": metadata.get("language") or "zh",
        },
        "evidence": {
            "title": "document_label" if title == label else "registered",
            "period": period_source,
            "period_detail": period,
        },
        "blockers": [] if (title and period) else ["report_period_or_title_unconfirmed"],
    }


@app.post('/api/report-sessions/{session_id}/materialize')
def materialize_report_session(session_id: str, request: MaterializationRequest,
                               authorization: str | None = Header(default=None)):
    if not authorization:
        raise HTTPException(401, '保存交付成果需要工作台操作员凭据')
    plan = _find_report_plan(session_id)
    native = plan['documents'][0].get('native_session_id') if plan else session_id
    if not native:
        raise HTTPException(409, '报告尚无原生提取会话')
    return _targeted_report_request('POST', f'/api/report-sessions/{native}/materialize',
        request.model_dump(mode='json'), authorization=authorization)


@app.post("/api/report-sessions/{session_id}/resume", status_code=202)
def resume_report_session(session_id: str, request: TargetedExtractionResumeRequest | None = Body(default=None)):
    _require_extraction_ready()
    plan = _find_report_plan(session_id)
    if not plan:
        raise HTTPException(404, "Platform report session not found")
    if not any(action.get("action") == "resume" for action in _report_plan_detail(plan)["available_actions"]):
        raise HTTPException(409, "没有未完成动作可以续跑；补充证据后显式采用新的证据版本")
    pipeline_plans.action(plan["plan_id"], "resume", secrets={"qiniu_api_key": request.qiniu_api_key if request else None})
    pipeline_plans.tick()
    return _report_plan_detail(_find_report_plan(plan["plan_id"]))


@app.post("/api/report-sessions/{session_id}/evidence-transition")
def transition_report_evidence(session_id: str, request: dict = Body(...)):
    _require_extraction_ready()
    request = dict(request)
    credentials = {name: request.pop(name, None) for name in ("qiniu_api_key", "ocr_token")}
    plan = _find_report_plan(session_id)
    if not plan:
        raise HTTPException(404, "Platform report session not found")
    document = plan["documents"][0]
    if not document.get("ir_run_id"):
        raise HTTPException(409, "当前报告尚未生成固定DocumentIR，不能采用修复证据版本；请先完成报告处理。")
    with pipeline_plans._lock:
        pipeline_plans._secrets.setdefault(plan["plan_id"], {}).update(
            {name: value for name, value in credentials.items() if value})
    if document.get("native_session_id"):
        native = document["native_session_id"]
        # The extraction domain performs checkpoint invalidation and immutable
        # projection history; the platform remains the sole execution dispatcher.
        session = _targeted_report_request("POST", f"/api/report-sessions/{native}/evidence-transition?enqueue=false", request)
        with pipeline_plans._lock:
            step = document["steps"].setdefault("report_session", {"attempt": 0})
            if step.get("task_id"):
                step.setdefault("history", []).append(step.pop("task_id"))
            step.update(attempt=step.get("attempt", 0) + 1, status="pending", resume_run_id=native, transition_queued=True)
            document.update(ir_run_id=request["new_ir_run_id"], status="pending")
            document.setdefault("evidence_transitions", []).append({"to": request["new_ir_run_id"], "reason": request.get("reason"), "at": utc_now()})
            plan["status"] = "active"
            plan["budget_started_at"] = utc_now()
            pipeline_store.pin_ir(plan["plan_id"], request["new_ir_run_id"])
            pipeline_plans.save(plan)
        pipeline_plans.tick()
        return _report_plan_detail(_find_report_plan(plan["plan_id"]))
    new_run_id = str(request.get("new_ir_run_id") or request.get("ir_run_id") or "")
    reason = str(request.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "Evidence version transitions require a reason")
    try:
        require_package_dir_name(new_run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    old = _plan_ir_manifest(document["ir_run_id"])
    new = _plan_ir_manifest(new_run_id)
    if not old or not new:
        raise HTTPException(404, "Evidence revision not found")
    old_identity, new_identity = manifest_document_id(old), manifest_document_id(new)
    if not old_identity or old_identity != new_identity:
        raise HTTPException(409, "Evidence transitions require the same report identity")
    if not (new.get("can_build_evidence") or (plan["config"]["allow_limited_evidence"] and new.get("can_build_limited_evidence"))):
        raise HTTPException(409, "The replacement revision has no authorized consumable evidence")
    document.setdefault("evidence_transitions", []).append({"from": document["ir_run_id"], "to": new_run_id, "reason": reason, "at": utc_now()})
    document["ir_run_id"] = new_run_id
    pipeline_store.pin_ir(plan["plan_id"], new_run_id)
    pipeline_plans.save(plan)
    pipeline_plans.action(plan["plan_id"], "resume")
    pipeline_plans.tick()
    return _report_plan_detail(_find_report_plan(plan["plan_id"]))


@app.post("/api/report-sessions/{session_id}/cancel")
def cancel_report_session(session_id: str):
    plan = _find_report_plan(session_id)
    if not plan:
        raise HTTPException(404, "Platform report session not found")
    pipeline_plans.action(plan["plan_id"], "cancel")
    native = plan["documents"][0].get("native_session_id")
    if native:
        _targeted_report_request("POST", f"/api/report-sessions/{native}/cancel")
    return _report_plan_detail(_find_report_plan(plan["plan_id"]))


@app.get('/api/report-sessions/{session_id}/lifecycle')
def report_session_lifecycle(session_id: str):
    plan = _find_report_plan(session_id)
    if not plan:
        return _targeted_report_request('GET', f'/api/report-sessions/{session_id}/lifecycle')
    native_id = plan['documents'][0].get('native_session_id')
    native = _targeted_report_request('GET', f'/api/report-sessions/{native_id}/lifecycle') if native_id else None
    return plan_lifecycle.describe(plan, native)


def _report_lifecycle_command(session_id, action, request):
    plan = _find_report_plan(session_id)
    if not plan:
        return _targeted_report_request('POST', f'/api/report-sessions/{session_id}/{action}', request.model_dump())
    try:
        plan_lifecycle.operate(plan['plan_id'], action, request.reason, request.expected_revision)
    except (KeyError, ValueError) as exc:
        raise HTTPException(409, {'code': 'lifecycle_conflict', 'message': str(exc)}) from exc
    return _report_plan_detail(_find_report_plan(plan['plan_id']))


def _register_report_lifecycle():
    for action in ('archive', 'restore', 'discard'):
        def command(session_id: str, request: LifecycleCommand, _action=action):
            return _report_lifecycle_command(session_id, _action, request)
        app.add_api_route(f'/api/report-sessions/{{session_id}}/{action}', command, methods=['POST'])


_register_report_lifecycle()


@app.get('/api/jobs/{job_id}/lifecycle')
def extraction_job_lifecycle(job_id: str):
    return _targeted_report_request('GET', f'/api/jobs/{job_id}/lifecycle')


def _register_job_lifecycle():
    for action in ('archive', 'restore', 'discard'):
        def command(job_id: str, request: LifecycleCommand, _action=action):
            return _targeted_report_request('POST', f'/api/jobs/{job_id}/{_action}', request.model_dump())
        app.add_api_route(f'/api/jobs/{{job_id}}/{action}', command, methods=['POST'])


_register_job_lifecycle()


@app.get('/api/pipeline/tasks/{task_id}/references')
def queue_source_references(task_id: str):
    try:
        return queue_references.describe(task_id, check_references=True)
    except (KeyError, ValueError) as exc:
        raise lifecycle_error(exc) from exc


def _register_queue_reference_actions():
    for action in ('release-references', 'restore-references'):
        def command(task_id: str, request: LifecycleCommand, _action=action):
            try:
                return queue_references.command(task_id, _action, **request.model_dump())
            except (KeyError, ValueError) as exc:
                raise lifecycle_error(exc) from exc
        app.add_api_route(f'/api/pipeline/tasks/{{task_id}}/{action}', command, methods=['POST'])


_register_queue_reference_actions()


@app.get('/api/lifecycle/trash')
def extraction_trash():
    return _targeted_report_request('GET', '/api/lifecycle/trash')


@app.post('/api/lifecycle/trash/{cleanup_id}/purge')
def purge_extraction_trash(cleanup_id: str, request: LifecycleCommand):
    return _targeted_report_request('POST', f'/api/lifecycle/trash/{cleanup_id}/purge', request.model_dump())


@app.post('/api/lifecycle/trash/{cleanup_id}/restore')
def restore_extraction_trash(cleanup_id: str, request: LifecycleCommand):
    return _targeted_report_request('POST', f'/api/lifecycle/trash/{cleanup_id}/restore', request.model_dump())


@app.get("/api/pipeline/status")
def pipeline_status() -> dict[str, object]:
    tasks = pipeline_store.list(1000)
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    targeted: dict[str, object]
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(f"{settings.targeted_backend_url}/api/health", timeout=2)
        targeted = {
            "available": response.status_code == 200,
            "status_code": response.status_code,
        }
    except requests.RequestException as exc:
        targeted = {"available": False, "error": type(exc).__name__}
    return {
        "mode": "single_worker_isolated_process",
        "counts": counts,
        "active_task": next(
            (task.model_dump(mode="json") for task in tasks if task.status.value == "running"),
            None,
        ),
        "queued_order": [task.task_id for task in tasks if task.status.value == "queued"],
        "services": {
            "document_backend": {"available": True, "url": "http://127.0.0.1:18080"},
            "targeted_backend": {**targeted, "url": settings.targeted_backend_url},
        },
        "paths": {
            "queue_db": str(settings.pipeline_queue_db),
            "report_assets": str(settings.report_asset_root),
        },
    }


@app.get("/api/report-assets")
def list_report_assets() -> dict[str, object]:
    payload = report_assets.refresh()
    queued_by_asset: dict[str, list[dict[str, str]]] = {}
    for task in pipeline_store.list(1000):
        asset_id = task.payload.get("asset_id")
        if asset_id and task.status.value in {"queued", "running"}:
            queued_by_asset.setdefault(str(asset_id), []).append(
                {"task_id": task.task_id, "status": task.status.value}
            )
    for asset in payload["assets"]:
        asset["pipeline_tasks"] = queued_by_asset.get(asset["asset_id"], [])
        asset.update(report_processing.asset_summary(asset))
        asset['available_actions']=[{'action':'delete','label':'删除','method':'DELETE',
            'endpoint':f"/api/report-assets/{asset['asset_id']}",'preflight_endpoint':f"/api/report-assets/{asset['asset_id']}/deletion",
            'payload':{'expected_revision':asset['lifecycle'].get('revision',0),'identity_sha256':asset['sha256']},
            'requires_confirmation':True,'requires_operator':True,'requires_reason':False,'calls_model':False,'incurs_cost':False,
            'confirmation_message':'确认永久删除这份PDF文件？删除后无法恢复。'}]
    return payload


@app.post("/api/report-assets", status_code=201)
async def upload_report_asset(file: Annotated[UploadFile, File(...)]) -> dict[str, object]:
    try:
        return report_assets.add(file.filename or "report.pdf", file.file)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"PDF already exists: {exc}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/report-assets/{asset_id}/file")
def get_report_asset_file(asset_id: str) -> FileResponse:
    try:
        path = report_assets.resolve(asset_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Report asset not found") from exc
    # Inline so the workbench can show the page instead of forcing a download.
    return FileResponse(path, filename=path.name, media_type="application/pdf",
                        content_disposition_type="inline")


@app.get("/api/report-assets/{asset_id}/lifecycle")
def get_report_asset_lifecycle(asset_id: str):
    try:
        return report_assets.lifecycle_description(asset_id)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise lifecycle_error(exc) from exc


def _register_asset_lifecycle_actions():
    def action_endpoint(action):
        def endpoint(asset_id: str, request: LifecycleCommand):
            try:
                return report_assets.lifecycle(asset_id,action,**request.model_dump())
            except (KeyError,ValueError,FileNotFoundError) as exc:
                raise lifecycle_error(exc) from exc
        return endpoint
    for action in ("archive","restore","discard"):
        app.add_api_route(f"/api/report-assets/{{asset_id}}/{action}",action_endpoint(action),methods=["POST"],name=f"report_asset_{action}")


_register_asset_lifecycle_actions()


@app.get("/api/report-assets-history")
def report_asset_history():
    return {"items":[asset for asset in report_assets.refresh(include_archived=True)["assets"]
        if asset["lifecycle"]["state"] != "active"], "trash":report_assets.trash.list()}


@app.post("/api/pipeline/tasks/ocr", status_code=202)
def queue_ocr_task(request: OcrPipelineTaskRequest = Body(...)) -> PipelineTask:
    _require_processing_ready()
    asset: dict[str, object] | None = None
    file_path: str | None = None
    if request.asset_id:
        try:
            asset = report_assets.get(request.asset_id)
            file_path = str(report_assets.resolve(request.asset_id))
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Report asset not found") from exc
    try:
        # Whole-document requests keep their pre-page-targeting reservation digest.
        whole_document = request.page_indices is None and request.page_selection == "all"
        reservation = request.model_dump(mode="json", exclude={"page_selection", "page_indices"} if whole_document else None)
        run_id, native_digest = reserve_native_run(pipeline_store, "ocr", reservation, dispatch_key.get())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    ocr_request = OcrRunRequest(
        file_path=file_path,
        file_url=request.file_url,
        token=None,
        model=request.model,
        ocr_provider=request.ocr_provider,
        allow_api_fallback=request.allow_api_fallback,
        optional_payload=request.optional_payload,
        poll_interval_seconds=request.poll_interval_seconds,
        page_selection=request.page_selection,
        page_indices=request.page_indices,
    )
    state = OcrJobState(
        run_id=run_id,
        status="queued",
        message="OCR job queued in unified pipeline",
        output_dir=package_dir(settings.output_root, run_id),
        summary={
            "document_label": asset["file_name"] if asset else sanitize_remote_url(str(request.file_url)),
            "document_id": f"doc-sha256-{asset['sha256']}" if asset else None,
            "asset_id": request.asset_id,
            "requested_ocr_provider": request.ocr_provider,
            "allow_api_fallback": request.allow_api_fallback,
            "page_selection": "explicit" if request.page_indices is not None else request.page_selection,
        },
    )
    try:
        ensure_native_state(ocr_store, state, dispatch_key.get(), native_digest)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    payload = ocr_request.model_dump(mode="json")
    payload["asset_id"] = request.asset_id
    task = _queue_task(
        task_type=PipelineTaskType.OCR,
        operation="ocr",
        title=f"OCR · {asset['file_name'] if asset else '远程 PDF'}",
        payload=payload,
        native_job_id=run_id,
        secrets={"PADDLEOCR_VL_API_TOKEN": request.token},
        deduplicate_native=bool(dispatch_key.get()),
    )
    return task


@app.post("/api/pipeline/tasks/document-ir", status_code=202)
def queue_document_ir_task(
    request: DocumentIrPipelineTaskRequest = Body(...),
) -> PipelineTask:
    _require_processing_ready()
    secret: str | None = None
    parent_run_id = request.parent_run_id
    if request.operation == "build":
        build = request.build_request
        assert build is not None
        try:
            require_package_dir_name(build.ocr_run_id)
            if build.run_id:
                require_run_id(build.run_id, "ir")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _guard_qiniu_queue(
            provider=build.review_provider,
            api_key=build.qiniu_api_key,
            enabled=build.execute_vlm_reviews,
        )
        ocr_manifest_path = package_dir(settings.output_root, build.ocr_run_id) / "manifest.json"
        if not ocr_manifest_path.exists():
            raise HTTPException(status_code=404, detail="OCR run not found or incomplete")
        if not build.pdf_path:
            ocr_manifest = json.loads(ocr_manifest_path.read_text(encoding="utf-8"))
            source = ocr_manifest.get("source")
            source = source if isinstance(source, dict) else {}
            source_sha = str(source.get("sha256") or "")
            asset = next(
                (
                    item
                    for item in report_assets.refresh().get("assets", [])
                    if item.get("sha256") == source_sha
                ),
                None,
            )
            if asset:
                build = build.model_copy(
                    update={"pdf_path": str(report_assets.resolve(str(asset["asset_id"]))) }
                )
        try:
            run_id, native_digest = reserve_native_run(pipeline_store, "ir",
                build.model_copy(update={"qiniu_api_key": None}).model_dump(mode="json"), dispatch_key.get(), preferred_run_id=build.run_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        payload = build.model_copy(update={"run_id": run_id, "qiniu_api_key": None}).model_dump(mode="json")
        secret = build.qiniu_api_key
        ocr_run_id = build.ocr_run_id
        title = f"Document IR · {build.document_label or build.ocr_run_id}"
    elif request.operation == "repair":
        repair = request.repair_request
        assert repair is not None
        _guard_qiniu_queue(
            provider=repair.review_provider,
            api_key=repair.qiniu_api_key,
            enabled=repair.execute_vlm_reviews,
        )
        if not _document_ir_output_dir(repair.parent_ir_run_id):
            raise HTTPException(status_code=404, detail="Parent Document IR run not found")
        document = _ir_reader(repair.parent_ir_run_id).load_document()
        run_id = DocumentIrRevisionService._new_run_id(document.metadata.ocr_run_id, "repair")
        payload = repair.model_copy(update={"qiniu_api_key": None}).model_dump(mode="json")
        secret = repair.qiniu_api_key
        ocr_run_id = document.metadata.ocr_run_id
        title = f"IR 定向修复 · {repair.parent_ir_run_id}"
    else:
        retry = request.review_retry_request
        assert retry is not None and parent_run_id is not None
        _guard_qiniu_queue(
            provider=retry.review_provider,
            api_key=retry.qiniu_api_key,
            enabled=True,
        )
        if not _document_ir_output_dir(parent_run_id):
            raise HTTPException(status_code=404, detail="Parent Document IR run not found")
        document = _ir_reader(parent_run_id).load_document()
        run_id = DocumentIrRevisionService._new_run_id(document.metadata.ocr_run_id, "review-retry")
        payload = retry.model_copy(update={"qiniu_api_key": None}).model_dump(mode="json")
        payload["parent_run_id"] = parent_run_id
        secret = retry.qiniu_api_key
        ocr_run_id = document.metadata.ocr_run_id
        title = f"IR 复核重试 · {parent_run_id}"
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    if output_dir.exists():
        raise HTTPException(status_code=409, detail=f"Document IR run already exists: {run_id}")
    native_digest = native_digest if request.operation == "build" else None
    state = DocumentIrJobState(
        run_id=run_id,
        ocr_run_id=ocr_run_id,
        status="queued",
        message="Document IR job queued in unified pipeline",
        output_dir=output_dir,
        logs=[],
    )
    try:
        ensure_native_state(ir_store, state, dispatch_key.get() if request.operation == "build" else None, native_digest)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if dispatch_key.get():
        pipeline_store.pin_ir(str(dispatch_key.get()), run_id)
    return _queue_task(
        task_type=PipelineTaskType.DOCUMENT_IR,
        operation=request.operation,
        title=title,
        payload=payload,
        native_job_id=run_id,
        secrets={"QINIU_API_KEY": secret},
        deduplicate_native=bool(dispatch_key.get()) and request.operation == "build",
    )


def _validate_targeted_queue_request(
    request: TargetedExtractionPipelineTaskRequest,
) -> None:
    if (
        request.semantic_fill
        and request.semantic_provider == "qiniu_vlm"
        and not request.qiniu_api_key
        and not settings.qiniu_api_key
    ):
        raise HTTPException(
            status_code=400,
            detail="七牛云定向抽取需要本次任务 API Key 或后端 QINIU_API_KEY。",
        )


def _targeted_queue_specification(
    request: TargetedExtractionPipelineTaskRequest,
) -> dict[str, object]:
    run_id = _new_target_job_id()
    payload = request.model_copy(update={"qiniu_api_key": None}).model_dump(mode="json")
    dist = (settings.targeted_app_root.parent / "standard_packages/dist").resolve()
    candidates = [(dist / request.package_id / request.package_version / "package.json").resolve(),
                  (dist / ".retired" / request.package_id / request.package_version / "package.json").resolve()]
    source = next((p for p in candidates if dist in p.parents and p.is_file()), None)
    if source is None:
        raise HTTPException(400, "标准包不可用，任务未入队")
    snapshot = settings.pipeline_task_root / run_id / "standard-package.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, snapshot)
    payload["standard_snapshot_path"] = str(snapshot)
    if payload.get("semantic_provider") != request.semantic_provider:
        raise HTTPException(
            status_code=500,
            detail="定向抽取 Provider 在入队合同编译时丢失；任务未创建。",
        )
    provider_label = (
        f"七牛云/{request.semantic_model or settings.qiniu_vlm_model or '后端默认模型'}"
        if request.semantic_provider == "qiniu_vlm"
        else "本地/Qwen3.5" if request.semantic_provider == "local_qwen" else "本地/NuExtract3"
    )
    return {
        "task_type": PipelineTaskType.TARGETED_EXTRACTION,
        "operation": "run",
        "title": f"定向抽取 · {provider_label} · {request.package_id} · {request.ir_run_id}",
        "payload": payload,
        "native_job_id": run_id,
        "secrets": {"QINIU_API_KEY": request.qiniu_api_key},
    }


def _validate_targeted_queued_task(
    task: PipelineTask,
    request: TargetedExtractionPipelineTaskRequest,
) -> None:
    if task.payload.get("semantic_provider") != request.semantic_provider:
        pipeline_store.request_cancel(task.task_id)
        raise HTTPException(
            status_code=500,
            detail="定向抽取 Provider 在队列持久化后不一致；任务已取消。",
        )


@app.post("/api/pipeline/tasks/targeted-extraction", status_code=202)
def queue_targeted_extraction_task(
    request: TargetedExtractionPipelineTaskRequest = Body(...),
) -> PipelineTask:
    _validate_targeted_queue_request(request)
    task = _queue_tasks([_targeted_queue_specification(request)])[0]
    _validate_targeted_queued_task(task, request)
    return task


@app.post("/api/pipeline/tasks/targeted-extraction/batch", status_code=202)
def queue_targeted_extraction_batch(
    request: TargetedExtractionBatchPipelineTaskRequest = Body(...),
) -> list[PipelineTask]:
    for item in request.requests:
        _validate_targeted_queue_request(item)
    tasks = _queue_tasks(
        [_targeted_queue_specification(item) for item in request.requests]
    )
    for task, item in zip(tasks, request.requests, strict=True):
        _validate_targeted_queued_task(task, item)
    return tasks


def _existing_credentials(request):
    return {"QINIU_API_KEY": request.qiniu_api_key if request else None,
            "PADDLEOCR_VL_API_TOKEN": request.ocr_token if request else None}


def _existing_active_task(native_id, request):
    with pipeline_secrets.handoff():
        active = pipeline_store.active_native_task(native_id)
        if active and active.status.value == "queued":
            pipeline_secrets.put(active.task_id, _existing_credentials(request))
            pipeline_scheduler.wake()
    return active


def _validate_existing_cloud_credentials(payload, request):
    roles = payload.get("role_config") or payload.get("roles") or {}
    cloud = payload.get("semantic_provider") == "qiniu_vlm" or any(
        role.get("provider") == "qiniu_vlm" for role in roles.values() if isinstance(role, dict))
    if cloud and not ((request.qiniu_api_key if request else None) or settings.qiniu_api_key):
        raise HTTPException(400, "云端任务需要本次密钥或统一 Worker 环境配置；密钥不持久化")


@app.post("/api/pipeline/tasks/targeted-extraction/{job_id}/run-existing", status_code=202)
def queue_existing_targeted_job(job_id: str, request: ExistingPipelineTaskRequest | None = Body(default=None)) -> PipelineTask:
    try:
        require_package_dir_name(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    active = _existing_active_task(job_id, request)
    if active:
        return active
    record = _targeted_report_request("GET", f"/api/jobs/{job_id}")
    if not isinstance(record, dict) or record.get("job_id") != job_id:
        raise HTTPException(502, "目标服务返回了不一致的原生任务身份")
    if record.get("status") not in {"queued", "interrupted", "failed", "cancelled", "partial"}:
        raise HTTPException(409, "原生任务已完成、仍有 Worker 或不处于可执行状态")
    try:
        payload = ExtractionRequest.model_validate(record.get("request")).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(502, "原生任务快照不符合执行合同") from exc
    if payload.get("report_session_id"):
        raise HTTPException(409, "标准投影由所属报告 Session 调度；请恢复所属报告任务")
    _validate_existing_cloud_credentials(payload, request)
    return _queue_task(task_type=PipelineTaskType.TARGETED_EXTRACTION, operation="targeted_existing",
        title=f"定向抽取 · 既存任务 {job_id}", payload=payload, native_job_id=job_id,
        secrets=_existing_credentials(request), deduplicate_native=True)


@app.post("/api/pipeline/tasks/report-session/{session_id}/run-existing", status_code=202)
def queue_existing_report_session(session_id: str, request: ExistingPipelineTaskRequest | None = Body(default=None)) -> PipelineTask:
    try:
        require_package_dir_name(session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    active = _existing_active_task(session_id, request)
    if active:
        return active
    record = _targeted_report_request("GET", f"/api/report-sessions/{session_id}")
    if not isinstance(record, dict) or record.get("session_id") != session_id:
        raise HTTPException(502, "目标服务返回了不一致的报告 Session 身份")
    if record.get("execution_status") != "queued":
        raise HTTPException(409, "报告任务需先由原生 resume/证据转换合同进入 queued，或已有 Worker/完整成果")
    try:
        native_request = ReportExtractionRequest.model_validate(record.get("request"))
        payload = native_request.model_dump(mode="json")
        payload["budget"] = native_request.effective_budget().model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(502, "原生报告快照不符合执行合同") from exc
    if native_request.input.kind != "document_ir":
        raise HTTPException(502, "原生报告 Session 尚未固定文档证据版本")
    _validate_existing_cloud_credentials(payload, request)
    resuming = any(j.get("status") in {"interrupted", "failed", "cancelled", "partial"} for j in record.get("jobs", []) if isinstance(j, dict))
    return _queue_task(task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="report_session_resume" if resuming else "report_session",
        title=f"报告提取 · 既存 Session {session_id}", payload=payload, native_job_id=session_id,
        secrets=_existing_credentials(request), deduplicate_native=True)


@app.post("/api/pipeline/tasks/targeted-extraction/{job_id}/resume", status_code=202)
def queue_targeted_extraction_resume(
    job_id: str,
    request: TargetedExtractionResumeRequest | None = Body(default=None),
) -> PipelineTask:
    return queue_existing_targeted_job(job_id, ExistingPipelineTaskRequest(
        qiniu_api_key=request.qiniu_api_key if request else None))


@app.get("/api/models/status")
def get_model_status(
    provider: Literal["qiniu", "local_nuextract", "local_qwen"] | None = None,
) -> dict[str, object]:
    return provider_status(settings, provider)


@app.get("/api/storage/inventory")
def get_storage_inventory() -> dict[str, object]:
    return storage_inventory.inventory()


@app.post("/api/storage/deletion-plans")
def create_storage_deletion_plan(request: DeletionPlanRequest = Body(...)) -> dict[str, object]:
    try:
        return storage_inventory.create_deletion_plan(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/storage/deletion-plans/{plan_id}/execute")
def execute_storage_deletion_plan(
    plan_id: str,
    request: LifecycleCommand | None = Body(default=None),
) -> dict[str, object]:
    try:
        result = storage_inventory.execute_deletion_plan(plan_id,reason=request.reason if request else "清理未被引用的报告处理中间成果。")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    for item in result.get("deleted_runs", []):
        if item.get("kind") == "ocr":
            ocr_store.discard(str(item["run_id"]))
        elif item.get("kind") == "ir":
            ir_store.discard(str(item["run_id"]))
    return result


@app.get("/api/storage/trash")
def list_storage_trash():
    return {"items":storage_inventory.trash.list(), "asset_items":report_assets.trash.list()}


@app.post("/api/storage/trash/{cleanup_id}/restore")
def restore_storage_trash(cleanup_id: str, request: LifecycleCommand):
    try:
        if storage_inventory.trash._receipt_path(cleanup_id).exists():
            return storage_inventory.trash.restore(cleanup_id,reason=request.reason)
        return report_assets.restore_receipt(cleanup_id,reason=request.reason)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise lifecycle_error(exc) from exc


@app.post("/api/storage/trash/{cleanup_id}/purge")
def purge_storage_trash(cleanup_id: str, request: LifecycleCommand):
    try:
        trash=storage_inventory.trash if storage_inventory.trash._receipt_path(cleanup_id).exists() else report_assets.trash
        return trash.purge(cleanup_id,reason=request.reason)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise lifecycle_error(exc) from exc


@app.post("/api/ocr/jobs")
async def create_ocr_job(
    file: Annotated[UploadFile | None, File()] = None,
    file_url: Annotated[str | None, Form()] = None,
    token: Annotated[str | None, Form()] = None,
    model: Annotated[str, Form()] = settings.paddle_model,
    ocr_provider: Annotated[str, Form()] = settings.default_ocr_provider,
    allow_api_fallback: Annotated[bool, Form()] = True,
    useDocOrientationClassify: Annotated[bool, Form()] = False,
    useDocUnwarping: Annotated[bool, Form()] = False,
    useChartRecognition: Annotated[bool, Form()] = False,
    poll_interval_seconds: Annotated[float, Form()] = settings.poll_interval_seconds,
    page_selection: Annotated[str, Form()] = "all",
    page_indices: Annotated[str | None, Form(description="Comma-separated original 0-based PDF page indices")] = None,
) -> OcrJobState:
    _require_processing_ready()
    if bool(file) == bool(file_url):
        raise HTTPException(status_code=400, detail="Provide exactly one of file or file_url")
    if ocr_provider not in {"local_first", "local_paddleocr", "paddle_api"}:
        raise HTTPException(status_code=400, detail=f"Unsupported OCR provider: {ocr_provider}")
    if file_url and ocr_provider == "local_paddleocr":
        raise HTTPException(status_code=400, detail="Local PaddleOCR-VL requires an uploaded PDF file")
    if file_url and ocr_provider == "local_first" and not allow_api_fallback:
        raise HTTPException(
            status_code=400,
            detail="A URL source requires API fallback in local-first mode; upload the PDF for local OCR",
        )
    if page_selection not in {"all", "foundation16"}:
        raise HTTPException(status_code=400, detail=f"Unsupported page selection: {page_selection}")
    try:
        target_pages = [int(item) for item in page_indices.split(",") if item.strip()] if page_indices else None
        validate_page_indices(target_pages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid page_indices: {exc}") from None
    if file_url and (target_pages is not None or page_selection != "all"):
        raise HTTPException(status_code=400, detail="Page-targeted OCR requires an uploaded PDF file")
    if target_pages is not None and page_selection != "all":
        raise HTTPException(status_code=400, detail="Provide either page_indices or page_selection, not both")

    run_id = new_run_id("ocr")
    output_dir = settings.output_root / run_id
    upload_path: Path | None = None

    if file is not None:
        safe_name = Path(file.filename or "upload.pdf").name
        upload_dir = settings.upload_root / run_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / safe_name
        with upload_path.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)

    request = OcrRunRequest(
        file_path=str(upload_path) if upload_path else None,
        file_url=file_url,
        token=token,
        model=model,
        ocr_provider=ocr_provider,
        allow_api_fallback=allow_api_fallback,
        optional_payload=OptionalPayload(
            useDocOrientationClassify=useDocOrientationClassify,
            useDocUnwarping=useDocUnwarping,
            useChartRecognition=useChartRecognition,
        ),
        poll_interval_seconds=poll_interval_seconds,
        page_selection=page_selection,
        page_indices=target_pages,
    )

    state = OcrJobState(
        run_id=run_id,
        status="queued",
        message="OCR job queued",
        output_dir=output_dir,
        logs=[],
        summary={
            "document_label": upload_path.name if upload_path else sanitize_remote_url(str(file_url or "Remote PDF")),
            "document_id": document_id_from_sha256(sha256_file(upload_path)) if upload_path else None,
            "requested_ocr_provider": request.ocr_provider,
            "allow_api_fallback": request.allow_api_fallback,
            "page_selection": "explicit" if target_pages is not None else page_selection,
        },
    )
    try:
        ocr_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    _queue_task(task_type=PipelineTaskType.OCR, operation="ocr", title=f"OCR · {state.summary['document_label']}",
                payload=request.model_copy(update={"token": None}).model_dump(mode="json"),
                native_job_id=run_id, secrets={"PADDLEOCR_VL_API_TOKEN": token})
    return state


@app.get("/api/ocr/providers/status")
def get_ocr_provider_status() -> dict[str, object]:
    return ocr_workflow.provider_router.status()


@app.get("/api/ocr/jobs")
def list_ocr_jobs() -> list[OcrJobState]:
    states = {state.run_id: state for state in ocr_store.list()}
    for manifest_path in settings.output_root.glob("*/manifest.json"):
        run_id = manifest_path.parent.name
        if run_id in states:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            states[run_id].summary = {**states[run_id].summary, **_ocr_summary(manifest)}
            states[run_id].manifest_path = manifest_path
            if states[run_id].status == "done":
                page_count = int(manifest.get("page_count") or states[run_id].page_count)
                states[run_id].page_count = page_count
                states[run_id].progress_current = page_count
                states[run_id].progress_total = page_count
        else:
            state = _ocr_state_from_manifest(run_id)
            if state:
                states[run_id] = state
    return sorted(states.values(), key=lambda item: item.run_id, reverse=True)


@app.get("/api/ocr/jobs/{run_id}")
def get_ocr_job(run_id: str) -> OcrJobState:
    state = ocr_store.get(run_id)
    if not state:
        state = _ocr_state_from_manifest(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="OCR run not found")
    manifest_path = state.output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state.summary = {**state.summary, **_ocr_summary(manifest)}
        state.manifest_path = manifest_path
        if state.status == "done":
            page_count = int(manifest.get("page_count") or state.page_count)
            state.page_count = page_count
            state.progress_current = page_count
            state.progress_total = page_count
    return state


@app.get("/api/ocr/jobs/{run_id}/manifest")
def get_ocr_manifest(run_id: str) -> dict[str, object]:
    state = ocr_store.get(run_id)
    if not state:
        state = _ocr_state_from_manifest(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="OCR run not found")
    manifest_path = state.output_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not available yet")
    return sanitize_payload(json.loads(manifest_path.read_text(encoding="utf-8")))


@app.get("/api/ocr/jobs/{run_id}/artifacts")
def list_ocr_artifacts(run_id: str) -> dict[str, object]:
    state = ocr_store.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="OCR run not found")
    if not state.output_dir.exists():
        return {"run_id": run_id, "files": []}
    files = []
    for path in sorted(state.output_dir.rglob("*")):
        if path.is_file():
            if path.name == ".DS_Store":
                continue
            rel = path.relative_to(state.output_dir)
            files.append(
                {
                    "path": str(rel),
                    "size": path.stat().st_size,
                    "url": f"/ocr_output/{run_id}/{rel.as_posix()}",
                }
            )
    return {"run_id": run_id, "files": files}


@app.post("/api/document-ir/jobs")
def create_document_ir_job(
    request: DocumentIrBuildRequest = Body(...),
) -> DocumentIrJobState:
    if "review_provider" not in request.model_fields_set:
        request = request.model_copy(update={"review_provider": settings.default_review_provider})
    task = queue_document_ir_task(DocumentIrPipelineTaskRequest(build_request=request))
    return ir_store.get(str(task.native_job_id))


@app.get("/api/document-ir/jobs")
def list_document_ir_jobs() -> list[DocumentIrJobState]:
    states = {state.run_id: state for state in ir_store.list()}
    for manifest_path in settings.document_ir_output_root.glob("*/manifest.json"):
        run_id = manifest_path.parent.name
        if run_id in states:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            states[run_id].summary = {
                **states[run_id].summary,
                **_document_ir_summary(manifest, states[run_id].telemetry),
            }
            states[run_id].manifest_path = manifest_path
        else:
            state = _document_ir_state_from_manifest(run_id)
            if state:
                states[run_id] = state
    return sorted(states.values(), key=lambda item: item.run_id, reverse=True)


@app.get("/api/document-ir/review-worklist")
def list_document_ir_review_worklist() -> dict[str, object]:
    catalog = DocumentIrCatalog(
        settings.document_ir_output_root,
        ocr_output_root=settings.output_root,
    )
    latest_runs = catalog.list_latest_lineage_revisions()
    entries: list[dict[str, object]] = []
    failed_run_ids: list[str] = []
    for row in latest_runs:
        run_id = str(row["run_id"])
        try:
            payload = _ir_reader(run_id).unresolved_review_worklist()
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
            failed_run_ids.append(run_id)
            continue
        run = {
            "run_id": run_id,
            "status": "done",
            "summary": {
                "document_id": row.get("document_id"),
                "document_label": row.get("document_label"),
                "lineage_id": row.get("lineage_id"),
                "ir_revision": row.get("ir_revision"),
                "readiness": row.get("readiness"),
                "written_at": row.get("written_at"),
            },
        }
        for item in payload.get("entries") or []:
            entries.append({"run": run, **item})
    return {
        "schema_version": "document-ir-review-worklist-catalog-v1",
        "latest_run_count": len(latest_runs),
        "failed_run_ids": failed_run_ids,
        "entries": entries,
    }


@app.get("/api/document-ir/jobs/{run_id}")
def get_document_ir_job(run_id: str) -> DocumentIrJobState:
    state = ir_store.get(run_id)
    if not state:
        state = _document_ir_state_from_manifest(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Document IR run not found")
    manifest_path = state.output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state.summary = {**state.summary, **_document_ir_summary(manifest, state.telemetry)}
        state.manifest_path = manifest_path
    return state


@app.get("/api/document-ir/jobs/{run_id}/manifest")
def get_document_ir_manifest(run_id: str) -> dict[str, object]:
    output_dir = _document_ir_output_dir(run_id)
    if not output_dir:
        raise HTTPException(status_code=404, detail="Document IR run not found")
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not available yet")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


@app.get("/api/document-ir/jobs/{run_id}/quality-report")
def get_document_ir_quality_report(run_id: str) -> dict[str, object]:
    return _read_ir_entrypoint(run_id, "quality_report", "quality_report.json")


@app.get("/api/document-ir/jobs/{run_id}/validation-report")
def get_document_ir_validation_report(run_id: str) -> dict[str, object]:
    return _read_ir_entrypoint(run_id, "validation_report", "validation_report.json")


@app.get("/api/document-ir/jobs/{run_id}/document")
def get_document_ir_document(run_id: str) -> dict[str, object]:
    return _ir_reader(run_id).load_document().model_dump(mode="json")


@app.get("/api/document-ir/jobs/{run_id}/pages")
def list_document_ir_pages(run_id: str) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in _ir_reader(run_id).load_document().pages]


@app.get("/api/document-ir/jobs/{run_id}/pages/{page_index}")
def get_document_ir_page(run_id: str, page_index: int) -> dict[str, object]:
    if page_index < 0:
        raise HTTPException(status_code=400, detail="page_index must be non-negative")
    try:
        return _ir_reader(run_id).page(page_index)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@app.get("/api/document-ir/jobs/{run_id}/tables")
def list_document_ir_tables(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).tables()


@app.get("/api/document-ir/jobs/{run_id}/tables/{table_id}")
def get_document_ir_table(run_id: str, table_id: str) -> dict[str, object]:
    safe_table_id = Path(table_id).name
    if safe_table_id != table_id:
        raise HTTPException(status_code=400, detail="Invalid table id")
    try:
        return _ir_reader(run_id).table(safe_table_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@app.get("/api/document-ir/jobs/{run_id}/logical-tables")
def list_document_ir_logical_tables(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).list_logical_tables()


@app.get("/api/document-ir/jobs/{run_id}/logical-tables/{logical_table_id}")
def get_document_ir_logical_table(run_id: str, logical_table_id: str) -> dict[str, object]:
    safe_logical_table_id = Path(logical_table_id).name
    if safe_logical_table_id != logical_table_id:
        raise HTTPException(status_code=400, detail="Invalid logical table id")
    try:
        return _ir_reader(run_id).logical_table(safe_logical_table_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@app.get("/api/document-ir/jobs/{run_id}/figures")
def list_document_ir_figures(run_id: str) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in _ir_reader(run_id).load_document().figures]


@app.get("/api/document-ir/jobs/{run_id}/structure-edges")
def get_document_ir_structure_edges(run_id: str) -> list[dict[str, object]]:
    reader = _ir_reader(run_id)
    if reader.is_package_v1:
        return reader.read_jsonl(str(reader.manifest["entrypoints"]["structure_edges"]))
    return _read_ir_json(run_id, "structure_edges.json")  # type: ignore[return-value]


@app.get("/api/document-ir/jobs/{run_id}/correction-patches")
def get_document_ir_correction_patches(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("correction_patches")


@app.get("/api/document-ir/jobs/{run_id}/model-calls")
def get_document_ir_model_calls(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("model_calls")


@app.get("/api/document-ir/jobs/{run_id}/reviewer-results")
def get_document_ir_reviewer_results(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("reviewer_results")


@app.get("/api/document-ir/jobs/{run_id}/atomic-patches")
def get_document_ir_atomic_patches(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("atomic_patches")


@app.get("/api/document-ir/jobs/{run_id}/patch-transactions")
def get_document_ir_patch_transactions(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("patch_transactions")


@app.get("/api/document-ir/jobs/{run_id}/guard-results")
def get_document_ir_guard_results(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("guard_results")


@app.get("/api/document-ir/jobs/{run_id}/verifier-results")
def get_document_ir_verifier_results(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("verifier_results")


@app.get("/api/document-ir/jobs/{run_id}/final-decisions")
def get_document_ir_final_decisions(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("final_decisions")


@app.get("/api/document-ir/jobs/{run_id}/candidate-revisions")
def get_document_ir_candidate_revisions(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("candidate_revisions")


@app.get("/api/document-ir/jobs/{run_id}/reviews/{task_id}")
def get_document_ir_review_bundle(run_id: str, task_id: str) -> dict[str, object]:
    safe_task_id = Path(task_id).name
    if safe_task_id != task_id:
        raise HTTPException(status_code=400, detail="Invalid task id")
    try:
        return _ir_reader(run_id).review_task_bundle(safe_task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@app.post("/api/document-ir/jobs/{run_id}/patches/{patch_id}/decision")
def decide_document_ir_patch(run_id: str, patch_id: str, request: PatchDecisionRequest = Body(...)) -> dict[str, object]:
    try:
        result = revision_service.decide_patch(run_id, patch_id, request)
        _discard_pruned_ir_states(result)
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/document-ir/jobs/{run_id}/review-tasks/{task_id}/decision")
def decide_document_ir_review_task(run_id: str, task_id: str, request: PatchDecisionRequest = Body(...)) -> dict[str, object]:
    try:
        result = revision_service.decide_task(run_id, task_id, request)
        _discard_pruned_ir_states(result)
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/document-ir/jobs/{run_id}/repair")
def repair_document_ir(run_id: str, request: DocumentIrRepairRequest = Body(...)) -> DocumentIrJobState:
    if "review_provider" not in request.model_fields_set:
        request = request.model_copy(update={"review_provider": settings.default_review_provider})
    if request.parent_ir_run_id != run_id:
        raise HTTPException(status_code=400, detail="parent_ir_run_id must match the route run_id")
    task = queue_document_ir_task(DocumentIrPipelineTaskRequest(operation="repair", repair_request=request))
    return ir_store.get(str(task.native_job_id))


@app.post("/api/document-ir/jobs/{run_id}/reviews/retry")
def retry_document_ir_reviews(run_id: str, request: ReviewRetryRequest = Body(...)) -> DocumentIrJobState:
    if "review_provider" not in request.model_fields_set:
        request = request.model_copy(update={"review_provider": settings.default_review_provider})
    task = queue_document_ir_task(DocumentIrPipelineTaskRequest(operation="review_retry", parent_run_id=run_id,
        review_retry_request=request))
    return ir_store.get(str(task.native_job_id))


@app.get("/api/document-ir/jobs/{run_id}/conflict-groups")
def get_document_ir_conflict_groups(run_id: str) -> list[dict[str, object]]:
    return _ir_reader(run_id).review_collection("conflict_groups")


@app.get("/api/document-ir/jobs/{run_id}/artifact-index")
def get_document_ir_artifact_index(run_id: str) -> list[dict[str, object]]:
    payload = _read_ir_entrypoint(run_id, "artifacts", "artifact_index.json")
    return payload.get("artifacts", payload) if isinstance(payload, dict) else payload


@app.get("/api/document-ir/revisions/{ocr_run_id}")
def list_document_ir_revisions(ocr_run_id: str) -> list[dict[str, object]]:
    try:
        require_package_dir_name(ocr_run_id)
        return DocumentIrVersionManager(settings.document_ir_output_root).list_revisions(ocr_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/document-ir/documents")
def list_document_ir_documents() -> list[dict[str, object]]:
    return DocumentIrCatalog(
        settings.document_ir_output_root,
        ocr_output_root=settings.output_root,
    ).list_documents()


@app.get("/api/document-ir/documents/{document_id}/revisions")
def list_document_revisions(document_id: str) -> list[dict[str, object]]:
    try:
        return DocumentIrCatalog(
            settings.document_ir_output_root,
            ocr_output_root=settings.output_root,
        ).list_document_revisions(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/document-ir/jobs/{run_id}/review-tasks")
def get_document_ir_review_tasks(run_id: str) -> list[dict[str, object]]:
    output_dir = _document_ir_output_dir(run_id)
    if not output_dir:
        raise HTTPException(status_code=404, detail="Document IR run not found")
    return _ir_reader(run_id).review_tasks()


@app.get("/api/document-ir/jobs/{run_id}/human-review/inbox")
def get_document_ir_human_review_inbox(run_id: str) -> dict[str, object]:
    output_dir = _document_ir_output_dir(run_id)
    if not output_dir:
        raise HTTPException(status_code=404, detail="Document IR run not found")
    return _ir_reader(run_id).human_review_inbox()


@app.get("/api/document-ir/jobs/{run_id}/artifacts")
def list_document_ir_artifacts(run_id: str) -> dict[str, object]:
    output_dir = _document_ir_output_dir(run_id)
    if not output_dir:
        raise HTTPException(status_code=404, detail="Document IR run not found")
    if not output_dir.exists():
        return {"run_id": run_id, "files": []}
    files = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            if path.name == ".DS_Store":
                continue
            rel = path.relative_to(output_dir)
            files.append(
                {
                    "path": str(rel),
                    "size": path.stat().st_size,
                    "url": f"/document_ir_output/{run_id}/{rel.as_posix()}",
                }
            )
    return {"run_id": run_id, "files": files}


def _document_ir_output_dir(run_id: str) -> Path | None:
    state = ir_store.get(run_id)
    if state:
        return state.output_dir
    try:
        output_dir = package_dir(settings.document_ir_output_root, run_id)
    except ValueError:
        return None
    if (output_dir / "manifest.json").exists():
        return output_dir
    return None


def _read_ir_json(run_id: str, relative_path: str):
    output_dir = _document_ir_output_dir(run_id)
    if not output_dir:
        raise HTTPException(status_code=404, detail="Document IR run not found")
    path = output_dir / relative_path
    try:
        path.resolve().relative_to(output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid artifact path") from None
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not available: {relative_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _ir_reader(run_id: str) -> DocumentIrPackageReader:
    output_dir = _document_ir_output_dir(run_id)
    if not output_dir:
        raise HTTPException(status_code=404, detail="Document IR run not found")
    try:
        return DocumentIrPackageReader(output_dir, ocr_output_root=settings.output_root)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


def _read_ir_entrypoint(run_id: str, key: str, legacy: str):
    try:
        return _ir_reader(run_id).read_json(key, legacy)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


def _document_ir_state_from_manifest(run_id: str) -> DocumentIrJobState | None:
    try:
        output_dir = package_dir(settings.document_ir_output_root, run_id)
    except ValueError:
        return None
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = DocumentIrJobState(
        run_id=run_id,
        ocr_run_id=str(manifest.get("ocr_run_id") or ""),
        status="done",
        message="Document IR job completed",
        output_dir=output_dir,
        manifest_path=manifest_path,
        summary=_document_ir_summary(manifest, {}),
    )
    ir_store.put(state)
    return state


def _ocr_state_from_manifest(run_id: str) -> OcrJobState | None:
    try:
        output_dir = package_dir(settings.output_root, run_id)
    except ValueError:
        return None
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = OcrJobState(
        run_id=run_id,
        status="done",
        message="OCR job completed",
        output_dir=output_dir,
        job_id=str(manifest.get("job_id") or "") or None,
        result_json_url=sanitize_remote_url(str(manifest.get("result_json_url") or "")) or None,
        page_count=int(manifest.get("page_count") or 0),
        progress_current=int(manifest.get("page_count") or 0),
        progress_total=int(manifest.get("page_count") or 0),
        manifest_path=manifest_path,
        summary=_ocr_summary(manifest),
    )
    ocr_store.put(state)
    return state


def _document_ir_summary(manifest: dict[str, object], telemetry: dict[str, object]) -> dict[str, object]:
    model_calls = telemetry.get("model_calls")
    if not isinstance(model_calls, dict):
        model_calls = {}
    ocr_run_id = str(manifest.get("ocr_run_id") or "")
    ocr_manifest = _read_ocr_manifest(ocr_run_id)
    source = manifest.get("source")
    source = source if isinstance(source, dict) else {}
    lineage_id = DocumentIrVersionManager(settings.document_ir_output_root).lineage_id(manifest)
    return {
        "document_id": manifest_document_id(manifest),
        "document_label": manifest_document_label(manifest, ocr_manifest),
        "external_document_id": manifest.get("external_document_id"),
        "source_pdf_sha256": normalize_sha256(source.get("pdf_sha256") or source.get("sha256")),
        "lineage_id": lineage_id,
        "ocr_run_id": ocr_run_id,
        "readiness": manifest.get("readiness", "unknown"),
        "ir_revision": manifest.get("ir_revision", 1),
        "can_build_evidence": manifest.get("can_build_evidence", False),
        "can_build_limited_evidence": manifest.get("can_build_limited_evidence", False),
        "evidence_policy": manifest.get("evidence_policy", {}),
        "page_count": manifest.get("page_count", 0),
        "page_coverage": manifest.get("page_coverage") or {"mode": "all"},
        "block_count": manifest.get("block_count", 0),
        "table_count": manifest.get("table_count", 0),
        "figure_count": manifest.get("figure_count", 0),
        "spread_count": manifest.get("spread_count", 0),
        "review_task_count": manifest.get("review_task_count", 0),
        "model_actual_attempts": model_calls.get("actual_attempts", 0),
        "review_retry_result": manifest.get("review_retry_result"),
        "retention": manifest.get("retention"),
        "written_at": manifest.get("written_at"),
    }


def _discard_pruned_ir_states(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    retention = payload.get("retention")
    if not isinstance(retention, dict) and payload.get("schema_version") == "document-ir-retention-result-v1":
        retention = payload
    if not isinstance(retention, dict):
        return
    for run_id in retention.get("pruned_run_ids") or []:
        ir_store.discard(str(run_id))


def _ocr_summary(manifest: dict[str, object]) -> dict[str, object]:
    source = manifest.get("source")
    source = source if isinstance(source, dict) else {}
    return {
        "document_id": manifest_document_id(manifest),
        "document_label": manifest_document_label(manifest),
        "source_pdf_sha256": normalize_sha256(source.get("sha256")),
        "page_count": manifest.get("page_count", 0),
        "model": manifest.get("model"),
        "ocr_provider": manifest.get("ocr_provider", "paddle_api"),
        "provider_route": manifest.get("provider_route"),
        "provider_metadata": manifest.get("provider_metadata"),
        "pdf_preflight": manifest.get("pdf_preflight"),
        "provider_input": manifest.get("provider_input"),
        "page_selection": manifest.get("page_selection") or {"mode": "all"},
        "written_at": manifest.get("written_at"),
    }


def _pdf_preflight_summary(preflight: dict[str, object]) -> dict[str, object]:
    source = preflight.get("source")
    source = source if isinstance(source, dict) else {}
    return {
        "schema_version": preflight.get("schema_version"),
        "status": preflight.get("status"),
        "strategy": preflight.get("strategy"),
        "reasons": preflight.get("reasons", []),
        "page_count": source.get("page_count", 0),
        "local_render_validation": source.get("local_render_validation"),
    }


def _read_ocr_manifest(ocr_run_id: str) -> dict[str, object] | None:
    if not ocr_run_id:
        return None
    path = settings.output_root / ocr_run_id / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
