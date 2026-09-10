from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Callable, Literal
from uuid import uuid4

import requests
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from esg_v2.assets import ReportAssetCatalog
from esg_v2.config import get_settings
from esg_v2.contracts import OcrJobState, OcrRunRequest, OptionalPayload
from esg_v2.control_plane.contracts import (
    DocumentIrPipelineTaskRequest,
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
from esg_v2.control_plane.plans import PipelinePlanCoordinator, PipelinePlanRequest, dispatch_key
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
from esg_v2.runtime_telemetry import RunTelemetryTracker, utc_now
from esg_v2.storage.job_store import JobStore
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
from esg_v2.utils.sanitization import sanitize_payload, sanitize_remote_url
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
)
report_assets = ReportAssetCatalog(settings.report_asset_root, settings.output_root)
pipeline_store = PipelineQueueStore(settings.pipeline_queue_db)
pipeline_secrets = RuntimeSecretVault()
pipeline_hooks = PlatformTaskHooks(settings, ocr_store, ir_store)
pipeline_scheduler = PipelineScheduler(
    pipeline_store,
    pipeline_secrets,
    pipeline_hooks,
    poll_interval_seconds=settings.pipeline_poll_interval_seconds,
)

app = FastAPI(title="ESG v2 Extraction Backend", version="0.2.0")
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


@app.on_event("shutdown")
def stop_pipeline_scheduler() -> None:
    pipeline_plans.stop()
    pipeline_scheduler.stop()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "esg-v2-backend"}


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
        rows.append(
            {
                "task_id": task_id,
                "task_type": specification["task_type"],
                "operation": specification["operation"],
                "title": specification["title"],
                "payload": {**specification["payload"], **({"_plan_step_key": dispatch_key.get()} if dispatch_key.get() else {})},
                "native_job_id": specification["native_job_id"],
                "requires_runtime_secret": any(secret_values.values()),
            }
        )
        secrets_by_task_id[task_id] = secret_values
    tasks = pipeline_store.create_many(rows)
    for task in tasks:
        pipeline_secrets.put(task.task_id, secrets_by_task_id[task.task_id])
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
def list_pipeline_tasks(limit: int = Query(300, ge=1, le=1000)) -> list[PipelineTask]:
    return pipeline_store.list(limit)


@app.delete("/api/pipeline/tasks/history")
def clear_pipeline_task_history() -> dict[str, object]:
    task_ids = pipeline_store.clear_history()
    return {
        "deleted_count": len(task_ids),
        "task_ids": task_ids,
        "preserved": [
            "queued_tasks",
            "running_tasks",
            "ocr_artifacts",
            "document_ir_artifacts",
            "targeted_extraction_artifacts",
            "semantic_index_assets",
        ],
    }


@app.get("/api/pipeline/tasks/{task_id}")
def get_pipeline_task(task_id: str) -> PipelineTask:
    try:
        return pipeline_store.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pipeline task not found") from exc


@app.get("/api/pipeline/tasks/{task_id}/events")
def get_pipeline_task_events(
    task_id: str,
    after: int = Query(0, ge=0),
) -> list[dict[str, object]]:
    try:
        return pipeline_store.events(task_id, after)
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


def _plan_ir_manifest(run_id):
    path = package_dir(settings.document_ir_output_root, run_id) / "manifest.json"
    original = None
    if path.is_file():
        original = json.loads(path.read_text(encoding="utf-8"))
        if original.get("can_build_evidence") or original.get("can_build_limited_evidence"):
            return original
        ocr_id = original.get("source", {}).get("ocr_run_id")
        for candidate in sorted(settings.document_ir_output_root.glob("*/manifest.json"), reverse=True):
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
            if ocr_id and manifest.get("source", {}).get("ocr_run_id") == ocr_id and (manifest.get("can_build_evidence") or manifest.get("can_build_limited_evidence")):
                return manifest
    # Retention can replace a revision; use the persisted queue reference.
    for task in pipeline_store.list(1000):
        if task.native_job_id == run_id:
            ocr_id = task.payload.get("ocr_run_id")
            for candidate in sorted(settings.document_ir_output_root.glob("*/manifest.json"), reverse=True):
                manifest = json.loads(candidate.read_text(encoding="utf-8"))
                if ocr_id and manifest.get("source", {}).get("ocr_run_id") == ocr_id:
                    return manifest
    return original


def _dispatch_plan_step(name, config, document, secrets):
    if name == "ocr":
        return queue_ocr_task(OcrPipelineTaskRequest(asset_id=document["asset_id"], ocr_provider=config["ocr_provider"], token=secrets.get("ocr_token")))
    if name == "ir":
        return queue_document_ir_task(DocumentIrPipelineTaskRequest(build_request=DocumentIrBuildRequest(
            ocr_run_id=document["ocr_run_id"], document_label=document["file_name"],
            execute_vlm_reviews=True, review_provider=config["review_provider"], qiniu_api_key=secrets.get("qiniu_api_key"),
        )))
    standard = next(s for s in config["standards"] if name == f"targeted:{s['package_id']}")
    return queue_targeted_extraction_task(TargetedExtractionPipelineTaskRequest(
        ir_run_id=document["ir_run_id"], **standard,
        semantic_provider=config["semantic_provider"], semantic_model=config["semantic_model"],
        retrieval_object_top_n=config["retrieval_object_top_n"], qiniu_api_key=secrets.get("qiniu_api_key"),
    ))


def _cancel_plan_child(task_id):
    task = pipeline_store.get(task_id)
    if task.status.value in {"queued", "running"}:
        cancel_pipeline_task(task_id)


pipeline_plans = PipelinePlanCoordinator(pipeline_store, _dispatch_plan_step, _plan_ir_manifest, _cancel_plan_child)


@app.get("/api/pipeline/plans")
def list_pipeline_plans():
    return pipeline_plans.list()


@app.post("/api/pipeline/plans", status_code=202)
def create_pipeline_plan(request: PipelinePlanRequest = Body(...)):
    # Validate the complete batch before any worker or child task is created.
    if (request.semantic_provider == "qiniu_vlm" or request.review_provider == "qiniu") and not (request.qiniu_api_key or settings.qiniu_api_key):
        raise HTTPException(400, "七牛云计划需要 API Key 或后端 QINIU_API_KEY")
    with requests.Session() as session:
        session.trust_env = False
        for standard in request.standards:
            try:
                response = session.get(f"{settings.targeted_backend_url}/api/standards/{standard.package_id}/{standard.package_version}", timeout=10)
                response.raise_for_status()
                package = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise HTTPException(400, f"标准包不可用：{standard.package_id}@{standard.package_version}") from exc
            valid_ids = {m["metric_id"] for m in package.get("metrics", [])}
            if not set(standard.metric_ids) <= valid_ids:
                raise HTTPException(400, "计划包含标准包之外的指标")
    documents = []
    for asset_id in request.asset_ids:
        try:
            asset = report_assets.get(asset_id)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, f"PDF 资产不存在：{asset_id}") from exc
        doc = {"asset_id": asset_id, "file_name": asset["file_name"], "source_sha256": asset["sha256"]}
        if request.reuse_existing:
            if asset.get("ocr_runs"):
                doc["ocr_run_id"] = asset["ocr_runs"][0]["run_id"]
                doc["ocr_reused"] = True
            for path in sorted(settings.document_ir_output_root.glob("*/manifest.json"), reverse=True):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                usable = manifest.get("can_build_evidence") or (request.allow_limited_evidence and manifest.get("can_build_limited_evidence"))
                if usable and manifest.get("source", {}).get("pdf_sha256") == asset["sha256"]:
                    doc.update(ir_run_id=manifest["run_id"], ocr_run_id=manifest["source"]["ocr_run_id"], ir_reused=True)
                    break
        documents.append(doc)
    return pipeline_plans.create(request, documents)


@app.post("/api/pipeline/plans/{plan_id}/{action}")
def act_pipeline_plan(plan_id: str, action: Literal["pause", "resume", "cancel"]):
    try:
        return pipeline_plans.action(plan_id, action)
    except KeyError as exc:
        raise HTTPException(404, "Plan not found") from exc


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
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@app.delete("/api/report-assets/{asset_id}")
def delete_report_asset(
    asset_id: str,
) -> dict[str, object]:
    active = [
        task
        for task in pipeline_store.list(1000)
        if task.payload.get("asset_id") == asset_id
        and task.status.value in {"queued", "running"}
    ]
    if active:
        raise HTTPException(status_code=409, detail="Report asset is used by an active pipeline task")
    try:
        return report_assets.delete(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report asset not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/pipeline/tasks/ocr", status_code=202)
def queue_ocr_task(request: OcrPipelineTaskRequest = Body(...)) -> PipelineTask:
    asset: dict[str, object] | None = None
    file_path: str | None = None
    if request.asset_id:
        try:
            asset = report_assets.get(request.asset_id)
            file_path = str(report_assets.resolve(request.asset_id))
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Report asset not found") from exc
    run_id = new_run_id("ocr")
    ocr_request = OcrRunRequest(
        file_path=file_path,
        file_url=request.file_url,
        token=None,
        model=request.model,
        ocr_provider=request.ocr_provider,
        allow_api_fallback=request.allow_api_fallback,
        optional_payload=request.optional_payload,
        poll_interval_seconds=request.poll_interval_seconds,
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
        },
    )
    try:
        ocr_store.create(state)
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
    )
    return task


@app.post("/api/pipeline/tasks/document-ir", status_code=202)
def queue_document_ir_task(
    request: DocumentIrPipelineTaskRequest = Body(...),
) -> PipelineTask:
    secret: str | None = None
    parent_run_id = request.parent_run_id
    if request.operation == "build":
        build = request.build_request
        assert build is not None
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
        run_id = build.run_id or new_run_id("ir")
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
    state = DocumentIrJobState(
        run_id=run_id,
        ocr_run_id=ocr_run_id,
        status="queued",
        message="Document IR job queued in unified pipeline",
        output_dir=output_dir,
        logs=[],
    )
    try:
        ir_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _queue_task(
        task_type=PipelineTaskType.DOCUMENT_IR,
        operation=request.operation,
        title=title,
        payload=payload,
        native_job_id=run_id,
        secrets={"QINIU_API_KEY": secret},
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
        else "本地/NuExtract3"
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


@app.post("/api/pipeline/tasks/targeted-extraction/{job_id}/resume", status_code=202)
def queue_targeted_extraction_resume(
    job_id: str,
    request: TargetedExtractionResumeRequest | None = Body(default=None),
) -> PipelineTask:
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                f"{settings.targeted_backend_url}/api/jobs/{job_id}",
                timeout=5,
            )
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="Targeted extraction job not found")
        record = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Targeted extraction backend unavailable") from exc
    if str(record.get("status")) not in {"interrupted", "failed", "cancelled", "partial"}:
        raise HTTPException(status_code=409, detail="Targeted extraction job is not resumable")
    payload = record.get("request") if isinstance(record.get("request"), dict) else {}
    secret = request.qiniu_api_key if request is not None else None
    if (
        payload.get("semantic_fill", True)
        and payload.get("semantic_provider") == "qiniu_vlm"
        and not secret
        and not settings.qiniu_api_key
    ):
        raise HTTPException(
            status_code=400,
            detail="云端任务续跑需要重新输入七牛 API Key；密钥不会持久化。",
        )
    return _queue_task(
        task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="resume",
        title=f"定向抽取续跑 · {job_id}",
        payload=payload,
        native_job_id=job_id,
        secrets={"QINIU_API_KEY": secret},
    )


@app.get("/api/models/status")
def get_model_status(
    provider: Literal["qiniu", "local_nuextract"] | None = None,
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
) -> dict[str, object]:
    try:
        result = storage_inventory.execute_deletion_plan(plan_id)
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
) -> OcrJobState:
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
        },
    )
    try:
        ocr_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    thread = threading.Thread(target=_run_background_job, args=(run_id, request), daemon=True)
    thread.start()
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
    if not request.ocr_run_id:
        raise HTTPException(status_code=400, detail="ocr_run_id is required")
    try:
        require_package_dir_name(request.ocr_run_id)
        if request.parent_ir_run_id:
            require_package_dir_name(request.parent_ir_run_id)
        if request.run_id:
            require_run_id(request.run_id, "ir")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not ocr_store.get(request.ocr_run_id):
        ocr_dir = package_dir(settings.output_root, request.ocr_run_id)
        if not (ocr_dir / "manifest.json").exists():
            raise HTTPException(status_code=404, detail="OCR run not found")

    run_id = request.run_id or DocumentIrWorkflow._new_run_id(request.ocr_run_id)
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    if output_dir.exists():
        raise HTTPException(status_code=409, detail=f"Document IR run already exists: {run_id}")
    state = DocumentIrJobState(
        run_id=run_id,
        ocr_run_id=request.ocr_run_id,
        status="queued",
        message="Document IR job queued",
        output_dir=output_dir,
        logs=[],
    )
    try:
        ir_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    thread = threading.Thread(target=_run_document_ir_background_job, args=(run_id, request), daemon=True)
    thread.start()
    return state


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
    return [item.model_dump(mode="json") for item in _ir_reader(run_id).load_document().tables]


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
    parent = _document_ir_output_dir(run_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent Document IR run not found")
    parent_document = _ir_reader(run_id).load_document().model_dump(mode="json")
    ocr_run_id = str((parent_document.get("metadata") or {}).get("ocr_run_id") or "")
    child_run_id = DocumentIrRevisionService._new_run_id(ocr_run_id, "repair")
    state = DocumentIrJobState(
        run_id=child_run_id,
        ocr_run_id=ocr_run_id,
        status="queued",
        message="Targeted Document IR repair queued",
        output_dir=package_dir(settings.document_ir_output_root, child_run_id),
        logs=[],
    )
    try:
        ir_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    thread = threading.Thread(target=_run_document_ir_repair_background_job, args=(child_run_id, request), daemon=True)
    thread.start()
    return state


@app.post("/api/document-ir/jobs/{run_id}/reviews/retry")
def retry_document_ir_reviews(run_id: str, request: ReviewRetryRequest = Body(...)) -> DocumentIrJobState:
    if "review_provider" not in request.model_fields_set:
        request = request.model_copy(update={"review_provider": settings.default_review_provider})
    parent = _document_ir_output_dir(run_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent Document IR run not found")
    if request.review_provider == "qiniu":
        adapter = QiniuModelAdapter(settings, api_key=request.qiniu_api_key)
        provider_limits = ProviderRateLimitCoordinator(settings, adapter.credential_scope_id)
        provider_block = provider_limits.account_block()
        if provider_block is not None:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "qiniu_account_tpd_circuit_open",
                    "message": "七牛账号每日 Token 限额仍处于熔断期，本次未创建空重试版本。",
                    "provider_state": provider_block,
                },
            )
    document = _ir_reader(run_id).load_document()
    child_run_id = DocumentIrRevisionService._new_run_id(document.metadata.ocr_run_id, "review-retry")
    state = DocumentIrJobState(
        run_id=child_run_id,
        ocr_run_id=document.metadata.ocr_run_id,
        status="queued",
        message="Pending Document IR reviews queued for retry",
        output_dir=package_dir(settings.document_ir_output_root, child_run_id),
        logs=[],
    )
    try:
        ir_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    thread = threading.Thread(
        target=_run_document_ir_review_retry_background_job,
        args=(child_run_id, run_id, request),
        daemon=True,
    )
    thread.start()
    return state


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


def _run_background_job(run_id: str, request: OcrRunRequest) -> None:
    output_dir = package_dir(settings.output_root, run_id)
    queued = ocr_store.get(run_id)
    state = OcrJobState(
        run_id=run_id,
        status="running",
        message="OCR job running",
        output_dir=output_dir,
        logs=[],
        summary=dict(queued.summary) if queued else {},
    )
    ocr_store.put(state)

    def log(message: str) -> None:
        current = ocr_store.get(run_id) or state
        current.logs.append(message)
        current.message = message
        ocr_store.put(current)

    def progress(snapshot: dict[str, object]) -> None:
        current = ocr_store.get(run_id) or state
        current.progress_current = int(snapshot.get("extractedPages") or 0)
        current.progress_total = int(snapshot.get("totalPages") or 0)
        current.last_progress_at = utc_now()
        current.progress_detail = dict(snapshot)
        current.message = str(
            snapshot.get("message")
            or f"OCR running: {current.progress_current}/{current.progress_total or '?'} pages"
        )
        ocr_store.put(current)

    try:
        result = ocr_workflow.run(request, run_id=run_id, log=log, progress=progress)
        done = ocr_store.get(run_id) or state
        done.status = "done"
        done.message = "OCR job completed"
        done.job_id = result.job_id
        done.result_json_url = result.result_json_url
        done.page_count = result.page_count
        done.progress_current = result.page_count
        done.progress_total = max(done.progress_total, result.page_count)
        done.manifest_path = result.manifest_path
        result_manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        done.summary = {
            **_ocr_summary(result_manifest),
            "markdown_files": len(result.page_markdown_files),
            "downloaded_files": len(result.downloaded_files),
        }
        ocr_store.put(done)
    except Exception as exc:
        failed = ocr_store.get(run_id) or state
        failed.status = "failed"
        failed.message = "OCR job failed"
        failed.error = str(exc)
        failed.logs.append(str(exc))
        preflight_path = output_dir / "source" / "preflight.json"
        if preflight_path.exists():
            try:
                preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                preflight = None
            if isinstance(preflight, dict):
                failed.summary["pdf_preflight"] = _pdf_preflight_summary(preflight)
                failed.summary["provider_input"] = preflight.get("provider_input")
        submit_path = output_dir / "provider" / "submit-response.json"
        if submit_path.exists():
            try:
                submit = json.loads(submit_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                submit = None
            data = submit.get("data") if isinstance(submit, dict) else None
            if isinstance(data, dict) and data.get("jobId"):
                failed.job_id = str(data["jobId"])
        ocr_store.put(failed)


def _start_document_ir_runtime(
    run_id: str,
    ocr_run_id: str,
    *,
    message: str,
    initial_logs: list[str] | None = None,
) -> tuple[DocumentIrJobState, RunTelemetryTracker, Callable[[str], None]]:
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    queued = ir_store.get(run_id)
    started_at = utc_now()
    state = DocumentIrJobState(
        run_id=run_id,
        ocr_run_id=ocr_run_id,
        status="running",
        message=message,
        output_dir=output_dir,
        logs=list(initial_logs or []),
        created_at=queued.created_at if queued else started_at,
        started_at=started_at,
        updated_at=started_at,
    )
    ir_store.put(state)

    def publish_telemetry(snapshot: dict[str, object]) -> None:
        current = ir_store.get(run_id) or state
        current.telemetry = snapshot
        current.updated_at = str(snapshot.get("updated_at") or utc_now())
        current.started_at = str(snapshot.get("started_at") or current.started_at or started_at)
        ir_store.put(current)

    tracker = RunTelemetryTracker(publish_telemetry, started_at=started_at)

    def log(message: str) -> None:
        current = ir_store.get(run_id) or state
        current.logs.append(message)
        current.message = message
        current.updated_at = utc_now()
        ir_store.put(current)

    return state, tracker, log


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
        "page_count": manifest.get("page_count", 0),
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


def _run_document_ir_background_job(run_id: str, request: DocumentIrBuildRequest) -> None:
    state, tracker, log = _start_document_ir_runtime(
        run_id,
        request.ocr_run_id,
        message="Document IR job running",
    )

    try:
        result = ir_workflow.run(
            request,
            run_id=run_id,
            log=log,
            telemetry=tracker.event,
        )
        _discard_pruned_ir_states(result.retention)
        final_telemetry = tracker.finish("done")
        done = ir_store.get(run_id) or state
        done.status = "done"
        done.message = "Document IR job completed"
        done.manifest_path = result.manifest_path
        done.telemetry = final_telemetry
        done.finished_at = str(final_telemetry.get("finished_at") or utc_now())
        done.updated_at = done.finished_at
        result_manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        done.summary = _document_ir_summary(result_manifest, final_telemetry)
        done.summary["retention"] = result.retention
        ir_store.put(done)
    except Exception as exc:
        final_telemetry = tracker.finish("failed")
        failed = ir_store.get(run_id) or state
        failed.status = "failed"
        failed.message = "Document IR job failed"
        failed.error = str(exc)
        failed.logs.append(str(exc))
        failed.telemetry = final_telemetry
        failed.finished_at = str(final_telemetry.get("finished_at") or utc_now())
        failed.updated_at = failed.finished_at
        ir_store.put(failed)

def _run_document_ir_repair_background_job(run_id: str, request: DocumentIrRepairRequest) -> None:
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    ocr_run_id = ""
    try:
        ocr_run_id = _ir_reader(request.parent_ir_run_id).load_document().metadata.ocr_run_id
    except (HTTPException, FileNotFoundError, ValueError):
        pass
    state, tracker, log = _start_document_ir_runtime(
        run_id,
        ocr_run_id,
        message="Targeted Document IR repair running",
        initial_logs=[f"Repair targets: {', '.join(request.target_ids)}"],
    )
    try:
        manifest = revision_service.repair(
            request,
            run_id=run_id,
            telemetry=tracker.event,
            log=log,
        )
        _discard_pruned_ir_states(manifest)
        final_telemetry = tracker.finish("done")
        done = ir_store.get(run_id) or state
        done.status = "done"
        done.message = "Targeted Document IR repair completed"
        done.manifest_path = output_dir / "manifest.json"
        done.telemetry = final_telemetry
        done.finished_at = str(final_telemetry.get("finished_at") or utc_now())
        done.updated_at = done.finished_at
        done.summary = _document_ir_summary(manifest, final_telemetry)
        ir_store.put(done)
    except Exception as exc:
        final_telemetry = tracker.finish("failed")
        failed = ir_store.get(run_id) or state
        failed.status = "failed"
        failed.message = "Targeted Document IR repair failed"
        failed.error = str(exc)
        failed.logs.append(str(exc))
        failed.telemetry = final_telemetry
        failed.finished_at = str(final_telemetry.get("finished_at") or utc_now())
        failed.updated_at = failed.finished_at
        ir_store.put(failed)


def _run_document_ir_review_retry_background_job(
    run_id: str,
    parent_run_id: str,
    request: ReviewRetryRequest,
) -> None:
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    ocr_run_id = ""
    try:
        ocr_run_id = _ir_reader(parent_run_id).load_document().metadata.ocr_run_id
    except (HTTPException, FileNotFoundError, ValueError):
        pass
    selected = ", ".join(request.task_ids) if request.task_ids else "all blocking unresolved tasks"
    state, tracker, log = _start_document_ir_runtime(
        run_id,
        ocr_run_id,
        message="Pending Document IR reviews retry running",
        initial_logs=[f"Review retry scope: {selected}"],
    )
    try:
        manifest = revision_service.resume_reviews(
            parent_run_id,
            request,
            run_id=run_id,
            telemetry=tracker.event,
            log=log,
        )
        _discard_pruned_ir_states(manifest)
        final_telemetry = tracker.finish("done")
        done = ir_store.get(run_id) or state
        done.status = "done"
        retry_result = manifest.get("review_retry_result") or {}
        selected_count = int(retry_result.get("selected_count") or 0)
        resolved_count = int(retry_result.get("resolved_count") or 0)
        remaining_count = int(retry_result.get("remaining_count") or 0)
        provider_blocked = bool(retry_result.get("provider_limit_triggered"))
        if provider_blocked:
            done.message = (
                f"Review retry completed: {resolved_count}/{selected_count} resolved; "
                f"{remaining_count} remain because the provider quota circuit opened"
            )
        else:
            done.message = (
                f"Review retry completed: {resolved_count}/{selected_count} resolved; "
                f"{remaining_count} remain"
            )
        done.manifest_path = output_dir / "manifest.json"
        done.telemetry = final_telemetry
        done.finished_at = str(final_telemetry.get("finished_at") or utc_now())
        done.updated_at = done.finished_at
        done.summary = _document_ir_summary(manifest, final_telemetry)
        ir_store.put(done)
    except Exception as exc:
        final_telemetry = tracker.finish("failed")
        failed = ir_store.get(run_id) or state
        failed.status = "failed"
        failed.message = "Pending Document IR reviews retry failed"
        failed.error = str(exc)
        failed.logs.append(str(exc))
        failed.telemetry = final_telemetry
        failed.finished_at = str(final_telemetry.get("finished_at") or utc_now())
        failed.updated_at = failed.finished_at
        ir_store.put(failed)
