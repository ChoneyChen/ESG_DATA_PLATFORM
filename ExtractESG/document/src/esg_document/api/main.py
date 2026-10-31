from __future__ import annotations

import json
import os

import shutil

from datetime import datetime, timezone

from pathlib import Path

from typing import Annotated, Literal

from uuid import uuid4

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile, Header

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse

from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, ConfigDict, Field

from esg_runtime.lifecycle import LifecycleBlocked

from esg_runtime.local_operator import install_loopback_marker

__version__ = "1.1.0"

from esg_document.runtime_compatibility import compatibility_snapshot

from esg_document.assets import ReportAssetCatalog

from esg_document.config import get_settings

from esg_contracts.document import (
    OcrJobState,
    OcrRunRequest,
    OptionalPayload,
    validate_page_indices,
)

from esg_pipeline.contracts import (
    DocumentIrPipelineTaskRequest,
    OcrPipelineTaskRequest,
    PipelineOrderRequest,
    PipelineTask,
    PipelineTaskType,
)

from esg_pipeline.hooks import PlatformTaskHooks

from esg_pipeline.scheduler import PipelineScheduler

from esg_pipeline.secrets import RuntimeSecretVault

from esg_pipeline.store import PipelineQueueStore

from esg_pipeline.report_processing import ReportProcessingCoordinator

from esg_pipeline.native_dispatch import reserve_native_run, ensure_native_state

from esg_document.api.report_processing import processing_router

from esg_document.api.operator_auth import authorize_operator

from esg_document.api.error_contract import register_api_errors

from esg_document.assets.library_deletion import LibraryDeletionService

from esg_document.api.library_deletion import library_deletion_router

from esg_document.api.processing_status import processing_status_router

from esg_document.storage.processing_lifecycle import ProcessingLifecycle

from esg_document.document.contracts import (
    DocumentIrBuildRequest,
    DocumentIrJobState,
    DocumentIrRepairRequest,
    PatchDecisionRequest,
    ReviewRetryRequest,
)

from esg_document.document.catalog import DocumentIrCatalog

from esg_document.document.identity import (
    document_id_from_sha256,
    manifest_document_id,
    manifest_document_label,
    normalize_sha256,
)

from esg_document.document.revision_service import DocumentIrRevisionService

from esg_document.document.reader import DocumentIrPackageReader

from esg_document.document.versioning import DocumentIrVersionManager

from esg_document.models.review_provider import provider_status

from esg_document.models.provider_rate_limit import ProviderRateLimitCoordinator

from esg_document.models.qiniu_adapter import QiniuModelAdapter

from esg_document.storage.job_store import JobStore

from esg_document.storage.inventory import DeletionPlanRequest, StorageInventoryService

from esg_document.storage.package_layout import (
    new_run_id,
    package_dir,
    require_package_dir_name,
    require_run_id,
)

from esg_document.workflows.ir_workflow import DocumentIrWorkflow

from esg_document.workflows.ocr_workflow import OcrWorkflow

from esg_data.sanitization import (
    sanitize_payload,
    sanitize_remote_url,
    is_sensitive_remote_url,
)

from esg_document.utils.hashing import sha256_file

from esg_pipeline.context import dispatch_key, execution_budget_seconds

settings = get_settings()

settings.output_root.mkdir(parents=True, exist_ok=True)

settings.document_ir_output_root.mkdir(parents=True, exist_ok=True)

settings.upload_root.mkdir(parents=True, exist_ok=True)

settings.storage_cleanup_root.mkdir(parents=True, exist_ok=True)

settings.report_asset_root.mkdir(parents=True, exist_ok=True)

settings.pipeline_task_root.mkdir(parents=True, exist_ok=True)

ocr_store = JobStore(settings.ocr_job_state_root, OcrJobState)

ir_store = JobStore(settings.document_ir_job_state_root, DocumentIrJobState)
from esg_document.api.document_archive import DocumentArchive

document_archive = DocumentArchive(lambda: settings, lambda: ir_store)

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

report_assets = ReportAssetCatalog(
    settings.report_asset_root,
    settings.output_root,
    pipeline_queue_db=settings.pipeline_queue_db,
    metadata_root=settings.report_metadata_root,
)


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=4000)
    expected_revision: int | None = Field(default=None, ge=0)


def lifecycle_error(exc):
    return HTTPException(
        404 if isinstance(exc, (KeyError, FileNotFoundError)) else 409,
        {
            "code": "lifecycle_blocked",
            "message": str(exc),
            "blockers": exc.blockers if isinstance(exc, LifecycleBlocked) else [],
        },
    )


pipeline_store = PipelineQueueStore(settings.pipeline_queue_db)

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

library_deletion = LibraryDeletionService(
    report_assets, storage_inventory, ocr_store=ocr_store, ir_store=ir_store
)

app.include_router(library_deletion_router(library_deletion, authorize_operator))

app.include_router(processing_status_router(pipeline_store))

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:18081",
        "http://localhost:18081",
        "http://127.0.0.1:5180",
        "http://localhost:5180",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/ocr_output", StaticFiles(directory=str(settings.output_root)), name="ocr_output"
)

app.mount(
    "/document_ir_output",
    StaticFiles(directory=str(settings.document_ir_output_root)),
    name="document_ir_output",
)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "esg-v2-backend",
        "version": __version__,
        "compatibility": compatibility_snapshot(),
        "capabilities": {
            "clause_runs_v1": True,
            "unified_queue_only": True,
            "evidence_availability_v2": True,
            "compatibility_v1": True,
            "independent_clause_targets": True,
        },
    }


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
        if (
            specification["task_type"] == PipelineTaskType.OCR
            and isinstance(payload.get("file_url"), str)
            and is_sensitive_remote_url(payload["file_url"])
        ):
            secret_values["ESG_OCR_SOURCE_URL"] = payload["file_url"]
            payload["file_url"] = sanitize_remote_url(payload["file_url"])
            payload["_requires_source_url"] = True
        rows.append(
            {
                "task_id": task_id,
                "task_type": specification["task_type"],
                "operation": specification["operation"],
                "title": specification["title"],
                "payload": {
                    **payload,
                    **(
                        {"_plan_step_key": dispatch_key.get()}
                        if dispatch_key.get()
                        else {}
                    ),
                },
                "native_job_id": specification["native_job_id"],
                "requires_runtime_secret": any(secret_values.values()),
                "deduplicate_native": bool(
                    specification.get("deduplicate_native", False)
                ),
            }
        )
        secrets_by_task_id[task_id] = secret_values
    with pipeline_secrets.handoff():
        tasks = pipeline_store.create_many(rows)
        for task, row in zip(tasks, rows, strict=True):
            # A poller may claim a new row immediately after commit. Its pop()
            # waits for this handoff; a duplicate running task keeps its owner.
            if task.task_id == row["task_id"] or task.status.value == "queued":
                pipeline_secrets.put(
                    task.task_id, secrets_by_task_id[str(row["task_id"])]
                )
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
def list_pipeline_tasks(
    limit: int = Query(300, ge=1, le=1000), include_archived: bool = False
) -> list[PipelineTask]:
    return [
        _queue_with_references(task)
        for task in pipeline_store.list(limit, include_archived=include_archived)
    ]


def _queue_with_references(task):
    from esg_pipeline.shared_contracts import AvailableAction

    task.available_actions = []
    if task.status.value in {"queued", "running"}:
        task.available_actions = [
            AvailableAction(
                action="cancel",
                label="取消",
                endpoint=f"/api/pipeline/tasks/{task.task_id}/cancel",
            )
        ]
    return task


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
def reorder_pipeline_tasks(
    request: PipelineOrderRequest = Body(...),
) -> list[PipelineTask]:
    try:
        tasks = pipeline_store.reorder(request.task_ids)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Pipeline task not found: {exc}"
        ) from None
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
    return {
        "native_job_id": native_id,
        "task_id": task.task_id if task else None,
        "status": task.status.value if task else "not_active",
        "cancel_requested": bool(task.cancel_requested) if task else False,
        "task": task.model_dump(mode="json") if task else None,
    }


def _cancel_plan_child(task_id):
    task = pipeline_store.get(task_id)
    if task.status.value in {"queued", "running"}:
        cancel_pipeline_task(task_id)


def _dispatch_processing_step(stage, config, state, secrets):
    if stage == "ocr":
        return queue_ocr_task(
            OcrPipelineTaskRequest(
                asset_id=state["asset_id"],
                ocr_provider=config["ocr_provider"],
                model=config["ocr_model"],
                allow_api_fallback=config["allow_cloud_ocr"],
                token=secrets.get("ocr_token"),
                page_selection=config.get("page_selection", "all"),
                page_search_targets=config.get("page_search_targets") or [],
            )
        )
    if stage == "ir":
        return queue_document_ir_task(
            DocumentIrPipelineTaskRequest(
                build_request=DocumentIrBuildRequest(
                    ocr_run_id=state["ocr_run_id"],
                    document_label=state["file_name"],
                    render_dpi=config["render_dpi"],
                    execute_vlm_reviews=config["execute_optional_reviews"],
                    execute_required_reviews=config.get(
                        "execute_required_reviews", False
                    ),
                    max_auto_review_rounds=config["max_auto_review_rounds"],
                    review_provider=config["review_provider"],
                    qiniu_api_key=secrets.get("qiniu_api_key"),
                )
            )
        )
    raise ValueError("Unknown report-processing stage")


report_processing = ReportProcessingCoordinator(
    pipeline_store,
    report_assets,
    _dispatch_processing_step,
    _cancel_plan_child,
    settings.output_root,
    settings.document_ir_output_root,
    runtime_config={
        "ocr": {
            key: str(getattr(settings, key, ""))
            for key in (
                "local_paddleocr_model_path",
                "local_paddleocr_pipeline_version",
                "local_paddleocr_vlm_backend",
                "local_paddleocr_vlm_server_url",
                "local_paddleocr_runtime_python",
            )
        },
        "ir": {
            key: str(getattr(settings, key, ""))
            for key in (
                "qwen_model_path",
                "nuextract_model_path",
                "qiniu_vlm_model",
                "max_vlm_reviews_per_ir_run",
                "max_blocking_vlm_reviews_per_ir_run",
                "max_optional_vlm_reviews_per_ir_run",
            )
        },
    },
)


def _require_processing_ready():
    snapshot = compatibility_snapshot()
    if not snapshot.get("readiness", {}).get("ready"):
        raise HTTPException(
            503,
            {
                "code": "runtime_requires_restart",
                "layer": "compatibility",
                "message": "报告处理服务版本未就绪，请协调重启后继续；历史记录与成果保留",
                "retryable": True,
                "artifacts_retained": True,
                "blockers": snapshot.get("readiness", {}).get("blockers", []),
            },
        )


library_deletion.configure(cancel_task=cancel_pipeline_task, processing=report_processing)

app.include_router(processing_router(report_processing, _require_processing_ready))

processing_lifecycle = ProcessingLifecycle(
    report_processing, report_assets.reference_guard
)

report_processing.lifecycle_actions = processing_lifecycle.describe


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
        asset["available_actions"] = [
            {
                "action": "delete",
                "label": "删除",
                "method": "DELETE",
                "endpoint": f"/api/report-assets/{asset['asset_id']}",
                "preflight_endpoint": f"/api/report-assets/{asset['asset_id']}/deletion",
                "payload": {
                    "expected_revision": asset["lifecycle"].get("revision", 0),
                    "identity_sha256": asset["sha256"],
                },
                "requires_confirmation": True,
                "requires_operator": True,
                "requires_reason": False,
                "calls_model": False,
                "incurs_cost": False,
                "confirmation_message": "确认永久删除这份PDF文件？删除后无法恢复。",
            }
        ]
    return payload


@app.post("/api/report-assets", status_code=201)
async def upload_report_asset(
    file: Annotated[UploadFile, File(...)],
) -> dict[str, object]:
    try:
        return report_assets.add(file.filename or "report.pdf", file.file)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409, detail=f"PDF already exists: {exc}"
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/report-assets/{asset_id}/file")
def get_report_asset_file(asset_id: str) -> FileResponse:
    try:
        path = report_assets.resolve(asset_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Report asset not found") from exc
    # Inline so the workbench can show the page instead of forcing a download.
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/pdf",
        content_disposition_type="inline",
    )


@app.get("/api/report-sources/{source_sha256}/file")
def get_report_source_file(source_sha256: str) -> FileResponse:
    import re
    if not re.fullmatch(r"[a-f0-9]{64}",source_sha256):
        raise HTTPException(404,"Report source not found")
    try:
        asset=report_assets.get("pdf-"+source_sha256[:24])
        if asset["sha256"]!=source_sha256:raise KeyError(source_sha256)
        path=report_assets.resolve(asset["asset_id"])
    except (KeyError,FileNotFoundError,ValueError) as exc:
        raise HTTPException(404,"Report source not found") from exc
    return FileResponse(path,filename="source-"+source_sha256+".pdf",media_type="application/pdf",content_disposition_type="inline")


@app.get("/api/report-assets/{asset_id}/lifecycle")
def get_report_asset_lifecycle(asset_id: str):
    try:
        return report_assets.lifecycle_description(asset_id)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise lifecycle_error(exc) from exc


def _register_asset_lifecycle_actions():
    def action_endpoint(action):
        def endpoint(asset_id: str, request: LifecycleCommand):
            try:
                return report_assets.lifecycle(asset_id, action, **request.model_dump())
            except (KeyError, ValueError, FileNotFoundError) as exc:
                raise lifecycle_error(exc) from exc

        return endpoint

    for action in ("archive", "restore", "discard"):
        app.add_api_route(
            f"/api/report-assets/{{asset_id}}/{action}",
            action_endpoint(action),
            methods=["POST"],
            name=f"report_asset_{action}",
        )


_register_asset_lifecycle_actions()


@app.get("/api/report-assets-history")
def report_asset_history():
    return {
        "items": [
            asset
            for asset in report_assets.refresh(include_archived=True)["assets"]
            if asset["lifecycle"]["state"] != "active"
        ],
        "trash": report_assets.trash.list(),
    }


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
            raise HTTPException(
                status_code=404, detail="Report asset not found"
            ) from exc
    try:
        # Whole-document requests keep their pre-page-targeting reservation digest.
        whole_document = (
            request.page_indices is None and request.page_selection == "all"
        )
        reservation = request.model_dump(
            mode="json",
            exclude={"page_selection", "page_indices"} if whole_document else None,
        )
        run_id, native_digest = reserve_native_run(
            pipeline_store, "ocr", reservation, dispatch_key.get()
        )
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
        page_search_targets=request.page_search_targets,
        page_indices=request.page_indices,
    )
    state = OcrJobState(
        run_id=run_id,
        status="queued",
        message="OCR job queued in unified pipeline",
        output_dir=package_dir(settings.output_root, run_id),
        summary={
            "document_label": asset["file_name"]
            if asset
            else sanitize_remote_url(str(request.file_url)),
            "document_id": f"doc-sha256-{asset['sha256']}" if asset else None,
            "asset_id": request.asset_id,
            "requested_ocr_provider": request.ocr_provider,
            "allow_api_fallback": request.allow_api_fallback,
            "page_selection": "explicit"
            if request.page_indices is not None
            else request.page_selection,
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
        ocr_manifest_path = (
            package_dir(settings.output_root, build.ocr_run_id) / "manifest.json"
        )
        if not ocr_manifest_path.exists():
            raise HTTPException(
                status_code=404, detail="OCR run not found or incomplete"
            )
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
                    update={
                        "pdf_path": str(report_assets.resolve(str(asset["asset_id"])))
                    }
                )
        try:
            run_id, native_digest = reserve_native_run(
                pipeline_store,
                "ir",
                build.model_copy(update={"qiniu_api_key": None}).model_dump(
                    mode="json"
                ),
                dispatch_key.get(),
                preferred_run_id=build.run_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        payload = build.model_copy(
            update={"run_id": run_id, "qiniu_api_key": None}
        ).model_dump(mode="json")
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
        if not document_archive._document_ir_output_dir(repair.parent_ir_run_id):
            raise HTTPException(
                status_code=404, detail="Parent Document IR run not found"
            )
        document = document_archive._ir_reader(repair.parent_ir_run_id).load_document()
        run_id = DocumentIrRevisionService._new_run_id(
            document.metadata.ocr_run_id, "repair"
        )
        payload = repair.model_copy(update={"qiniu_api_key": None}).model_dump(
            mode="json"
        )
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
        if not document_archive._document_ir_output_dir(parent_run_id):
            raise HTTPException(
                status_code=404, detail="Parent Document IR run not found"
            )
        document = document_archive._ir_reader(parent_run_id).load_document()
        run_id = DocumentIrRevisionService._new_run_id(
            document.metadata.ocr_run_id, "review-retry"
        )
        payload = retry.model_copy(update={"qiniu_api_key": None}).model_dump(
            mode="json"
        )
        payload["parent_run_id"] = parent_run_id
        secret = retry.qiniu_api_key
        ocr_run_id = document.metadata.ocr_run_id
        title = f"IR 复核重试 · {parent_run_id}"
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    if output_dir.exists():
        raise HTTPException(
            status_code=409, detail=f"Document IR run already exists: {run_id}"
        )
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
        ensure_native_state(
            ir_store,
            state,
            dispatch_key.get() if request.operation == "build" else None,
            native_digest,
        )
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


@app.get("/api/models/status")
def get_model_status(
    provider: Literal["qiniu", "local_nuextract", "local_qwen"] | None = None,
) -> dict[str, object]:
    return provider_status(settings, provider)


@app.get("/api/storage/inventory")
def get_storage_inventory() -> dict[str, object]:
    return storage_inventory.inventory()


@app.post("/api/storage/deletion-plans")
def create_storage_deletion_plan(
    request: DeletionPlanRequest = Body(...),
) -> dict[str, object]:
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
        result = storage_inventory.execute_deletion_plan(
            plan_id,
            reason=request.reason if request else "清理未被引用的报告处理中间成果。",
        )
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
    return {
        "items": storage_inventory.trash.list(),
        "asset_items": report_assets.trash.list(),
    }


@app.post("/api/storage/trash/{cleanup_id}/restore")
def restore_storage_trash(cleanup_id: str, request: LifecycleCommand):
    try:
        if storage_inventory.trash._receipt_path(cleanup_id).exists():
            return storage_inventory.trash.restore(cleanup_id, reason=request.reason)
        return report_assets.restore_receipt(cleanup_id, reason=request.reason)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise lifecycle_error(exc) from exc


@app.post("/api/storage/trash/{cleanup_id}/purge")
def purge_storage_trash(cleanup_id: str, request: LifecycleCommand):
    try:
        trash = (
            storage_inventory.trash
            if storage_inventory.trash._receipt_path(cleanup_id).exists()
            else report_assets.trash
        )
        return trash.purge(cleanup_id, reason=request.reason)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise lifecycle_error(exc) from exc


@app.post("/api/ocr/jobs")
async def create_ocr_job(
    file: Annotated[UploadFile | None, File()] = None,
    file_url: Annotated[str | None, Form()] = None,
    token: Annotated[str | None, Form()] = None,
    model: Annotated[str, Form()] = settings.paddle_model,
    ocr_provider: Annotated[str, Form()] = settings.default_ocr_provider,
    allow_api_fallback: Annotated[bool, Form()] = False,
    useDocOrientationClassify: Annotated[bool, Form()] = False,
    useDocUnwarping: Annotated[bool, Form()] = False,
    useChartRecognition: Annotated[bool, Form()] = False,
    poll_interval_seconds: Annotated[float, Form()] = settings.poll_interval_seconds,
    page_selection: Annotated[str, Form()] = "all",
    page_indices: Annotated[
        str | None,
        Form(description="Comma-separated original 0-based PDF page indices"),
    ] = None,
) -> OcrJobState:
    _require_processing_ready()
    if bool(file) == bool(file_url):
        raise HTTPException(
            status_code=400, detail="Provide exactly one of file or file_url"
        )
    if ocr_provider not in {"local_first", "local_paddleocr", "paddle_api"}:
        raise HTTPException(
            status_code=400, detail=f"Unsupported OCR provider: {ocr_provider}"
        )
    if file_url and ocr_provider == "local_paddleocr":
        raise HTTPException(
            status_code=400, detail="Local PaddleOCR-VL requires an uploaded PDF file"
        )
    if file_url and ocr_provider == "local_first" and not allow_api_fallback:
        raise HTTPException(
            status_code=400,
            detail="A URL source requires API fallback in local-first mode; upload the PDF for local OCR",
        )
    if page_selection not in {"all", "clauses"}:
        raise HTTPException(
            status_code=400, detail=f"Unsupported page selection: {page_selection}"
        )
    try:
        target_pages = (
            [int(item) for item in page_indices.split(",") if item.strip()]
            if page_indices
            else None
        )
        validate_page_indices(target_pages)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid page_indices: {exc}"
        ) from None
    if file_url and (target_pages is not None or page_selection != "all"):
        raise HTTPException(
            status_code=400, detail="Page-targeted OCR requires an uploaded PDF file"
        )
    if target_pages is not None and page_selection != "all":
        raise HTTPException(
            status_code=400,
            detail="Provide either page_indices or page_selection, not both",
        )

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
            "document_label": upload_path.name
            if upload_path
            else sanitize_remote_url(str(file_url or "Remote PDF")),
            "document_id": document_id_from_sha256(sha256_file(upload_path))
            if upload_path
            else None,
            "requested_ocr_provider": request.ocr_provider,
            "allow_api_fallback": request.allow_api_fallback,
            "page_selection": "explicit"
            if target_pages is not None
            else page_selection,
        },
    )
    try:
        ocr_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    _queue_task(
        task_type=PipelineTaskType.OCR,
        operation="ocr",
        title=f"OCR · {state.summary['document_label']}",
        payload=request.model_copy(update={"token": None}).model_dump(mode="json"),
        native_job_id=run_id,
        secrets={"PADDLEOCR_VL_API_TOKEN": token},
    )
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
            states[run_id].summary = {
                **states[run_id].summary,
                **_ocr_summary(manifest),
            }
            states[run_id].manifest_path = manifest_path
            if states[run_id].status == "done":
                page_count = int(
                    manifest.get("page_count") or states[run_id].page_count
                )
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
        request = request.model_copy(
            update={"review_provider": settings.default_review_provider}
        )
    task = queue_document_ir_task(DocumentIrPipelineTaskRequest(build_request=request))
    return ir_store.get(str(task.native_job_id))


@app.post("/api/document-ir/jobs/{run_id}/patches/{patch_id}/decision")
def decide_document_ir_patch(
    run_id: str, patch_id: str, request: PatchDecisionRequest = Body(...)
) -> dict[str, object]:
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
def decide_document_ir_review_task(
    run_id: str, task_id: str, request: PatchDecisionRequest = Body(...)
) -> dict[str, object]:
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
def repair_document_ir(
    run_id: str, request: DocumentIrRepairRequest = Body(...)
) -> DocumentIrJobState:
    if "review_provider" not in request.model_fields_set:
        request = request.model_copy(
            update={"review_provider": settings.default_review_provider}
        )
    if request.parent_ir_run_id != run_id:
        raise HTTPException(
            status_code=400, detail="parent_ir_run_id must match the route run_id"
        )
    task = queue_document_ir_task(
        DocumentIrPipelineTaskRequest(operation="repair", repair_request=request)
    )
    return ir_store.get(str(task.native_job_id))


@app.post("/api/document-ir/jobs/{run_id}/reviews/retry")
def retry_document_ir_reviews(
    run_id: str, request: ReviewRetryRequest = Body(...)
) -> DocumentIrJobState:
    if "review_provider" not in request.model_fields_set:
        request = request.model_copy(
            update={"review_provider": settings.default_review_provider}
        )
    task = queue_document_ir_task(
        DocumentIrPipelineTaskRequest(
            operation="review_retry", parent_run_id=run_id, review_retry_request=request
        )
    )
    return ir_store.get(str(task.native_job_id))


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
        result_json_url=sanitize_remote_url(str(manifest.get("result_json_url") or ""))
        or None,
        page_count=int(manifest.get("page_count") or 0),
        progress_current=int(manifest.get("page_count") or 0),
        progress_total=int(manifest.get("page_count") or 0),
        manifest_path=manifest_path,
        summary=_ocr_summary(manifest),
    )
    ocr_store.put(state)
    return state


def _discard_pruned_ir_states(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    retention = payload.get("retention")
    if (
        not isinstance(retention, dict)
        and payload.get("schema_version") == "document-ir-retention-result-v1"
    ):
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


from esg_document.api.evidence import evidence_router
from esg_reports.repository import ReportRepository
from esg_document.api.report_resources import resource_router

report_resources = ReportRepository(settings.report_asset_root, registry=settings.report_registry_db)
app.include_router(resource_router(report_assets, report_resources))


def _resource_for_evidence(reference):
    if reference.report_resource:
        return (
            report_resources.by_sha(reference.report_resource.sha256)
            or reference.report_resource
        )
    return None


app.include_router(
    evidence_router(
        lambda key: package_dir(settings.document_ir_output_root, key),
        _resource_for_evidence,
    )
)


@app.on_event("startup")
def start_current_pipeline():
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    ocr_store.recover_interrupted()
    ir_store.recover_interrupted()
    pipeline_scheduler.start()
    report_processing.start()


@app.on_event("shutdown")
def stop_current_pipeline():
    report_processing.stop()
    pipeline_scheduler.stop()


@app.get("/api/pipeline/status")
def current_pipeline_status():
    return {
        "status": "ok",
        "queue": pipeline_store.list(300),
        "extraction_engine": "clause-backbone-1.0",
        "auto_publish": False,
    }


from esg_contracts.operations import TaskObservation


@app.get("/api/operations/tasks")
def operation_tasks() -> list[TaskObservation]:
    return [
        TaskObservation.model_validate(task.model_dump(mode="json"))
        for task in pipeline_store.list(300)
    ]


@app.get("/api/operations/tasks/{task_id}")
def operation_task(task_id: str) -> TaskObservation:
    try:
        return TaskObservation.model_validate(
            pipeline_store.get(task_id).model_dump(mode="json")
        )
    except KeyError as exc:
        raise HTTPException(404, "Task does not exist") from exc


app.include_router(document_archive.router())
