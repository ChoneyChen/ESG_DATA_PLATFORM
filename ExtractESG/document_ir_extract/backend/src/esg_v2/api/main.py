from __future__ import annotations

import json
import importlib.util
import shutil
import threading
from pathlib import Path
from typing import Annotated, Callable

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from esg_v2.config import get_settings
from esg_v2.contracts import OcrJobState, OcrRunRequest, OptionalPayload
from esg_v2.document.contracts import (
    DocumentIrBuildRequest,
    DocumentIrJobState,
    DocumentIrRepairRequest,
    PatchDecisionRequest,
    ReviewRetryRequest,
)
from esg_v2.document.revision_service import DocumentIrRevisionService
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.versioning import DocumentIrVersionManager
from esg_v2.evidence.builder import EvidenceAdmissionError, EvidenceInventoryBuilder
from esg_v2.evidence.contracts import EvidenceBuildRequest
from esg_v2.evidence.local_index import LocalEvidenceIndex
from esg_v2.evidence.package import EvidencePackageReader
from esg_v2.models.model_registry import QiniuModelRegistry
from esg_v2.models.provider_rate_limit import ProviderRateLimitCoordinator
from esg_v2.models.qiniu_adapter import QiniuModelAdapter
from esg_v2.runtime_telemetry import RunTelemetryTracker, utc_now
from esg_v2.storage.job_store import JobStore
from esg_v2.storage.package_layout import (
    new_run_id,
    package_dir,
    require_package_dir_name,
    require_run_id,
)
from esg_v2.targeted.contracts import TargetedJobState, TargetedRunRequest
from esg_v2.targeted.package import TargetedPackageReader
from esg_v2.targeted.workflow import TargetedRecallWorkflow
from esg_v2.templates.registry import TemplateAdapterRegistry
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
from esg_v2.workflows.ocr_workflow import OcrWorkflow
from esg_v2.utils.sanitization import sanitize_payload, sanitize_remote_url


settings = get_settings()
settings.output_root.mkdir(parents=True, exist_ok=True)
settings.document_ir_output_root.mkdir(parents=True, exist_ok=True)
settings.evidence_output_root.mkdir(parents=True, exist_ok=True)
settings.targeted_output_root.mkdir(parents=True, exist_ok=True)
settings.upload_root.mkdir(parents=True, exist_ok=True)

ocr_store = JobStore(settings.ocr_job_state_root, OcrJobState)
ir_store = JobStore(settings.document_ir_job_state_root, DocumentIrJobState)
targeted_store = JobStore(settings.targeted_job_state_root, TargetedJobState)
ocr_workflow = OcrWorkflow(settings)
ir_workflow = DocumentIrWorkflow(settings)
revision_service = DocumentIrRevisionService(settings)
template_registry = TemplateAdapterRegistry()
evidence_builder = EvidenceInventoryBuilder(
    settings.document_ir_output_root,
    settings.evidence_output_root,
)
targeted_workflow = TargetedRecallWorkflow(
    document_ir_root=settings.document_ir_output_root,
    evidence_output_root=settings.evidence_output_root,
    targeted_output_root=settings.targeted_output_root,
    template_registry=template_registry,
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
app.mount(
    "/evidence_output",
    StaticFiles(directory=str(settings.evidence_output_root)),
    name="evidence_output",
)
app.mount(
    "/targeted_fill_output",
    StaticFiles(directory=str(settings.targeted_output_root)),
    name="targeted_fill_output",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "esg-v2-backend"}


@app.get("/api/models/status")
def get_model_status() -> dict[str, object]:
    adapter = QiniuModelAdapter(settings)
    provider_limits = ProviderRateLimitCoordinator(settings, adapter.credential_scope_id)
    try:
        payload = QiniuModelRegistry(settings, adapter).refresh()
    except Exception as exc:
        payload = {"catalog_error": str(exc), "approved_vision_models": [], "health": []}
    payload["provider_rate_limit"] = provider_limits.snapshot()
    return payload


@app.post("/api/ocr/jobs")
async def create_ocr_job(
    file: Annotated[UploadFile | None, File()] = None,
    file_url: Annotated[str | None, Form()] = None,
    token: Annotated[str | None, Form()] = None,
    model: Annotated[str, Form()] = settings.paddle_model,
    useDocOrientationClassify: Annotated[bool, Form()] = False,
    useDocUnwarping: Annotated[bool, Form()] = False,
    useChartRecognition: Annotated[bool, Form()] = False,
    poll_interval_seconds: Annotated[float, Form()] = settings.poll_interval_seconds,
) -> OcrJobState:
    if bool(file) == bool(file_url):
        raise HTTPException(status_code=400, detail="Provide exactly one of file or file_url")

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
    )
    try:
        ocr_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    thread = threading.Thread(target=_run_background_job, args=(run_id, request), daemon=True)
    thread.start()
    return state


@app.get("/api/ocr/jobs")
def list_ocr_jobs() -> list[OcrJobState]:
    states = {state.run_id: state for state in ocr_store.list()}
    for manifest_path in settings.output_root.glob("*/manifest.json"):
        run_id = manifest_path.parent.name
        if run_id not in states:
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
        if run_id not in states:
            state = _document_ir_state_from_manifest(run_id)
            if state:
                states[run_id] = state
    return sorted(states.values(), key=lambda item: item.run_id, reverse=True)


@app.get("/api/document-ir/jobs/{run_id}")
def get_document_ir_job(run_id: str) -> DocumentIrJobState:
    state = ir_store.get(run_id)
    if not state:
        state = _document_ir_state_from_manifest(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Document IR run not found")
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
        return revision_service.decide_patch(run_id, patch_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/document-ir/jobs/{run_id}/review-tasks/{task_id}/decision")
def decide_document_ir_review_task(run_id: str, task_id: str, request: PatchDecisionRequest = Body(...)) -> dict[str, object]:
    try:
        return revision_service.decide_task(run_id, task_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/document-ir/jobs/{run_id}/repair")
def repair_document_ir(run_id: str, request: DocumentIrRepairRequest = Body(...)) -> DocumentIrJobState:
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
    parent = _document_ir_output_dir(run_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent Document IR run not found")
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


@app.get("/api/evidence/runs")
def list_evidence_runs() -> list[dict[str, object]]:
    rows = []
    for manifest_path in sorted(settings.evidence_output_root.glob("*/manifest.json"), reverse=True):
        try:
            rows.append(sanitize_payload(json.loads(manifest_path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return rows


@app.post("/api/evidence/build")
def build_evidence_inventory(request: EvidenceBuildRequest = Body(...)) -> dict[str, object]:
    try:
        result = evidence_builder.build(request)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except (FileNotFoundError, EvidenceAdmissionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return result.model_dump(mode="json")


@app.get("/api/evidence/runs/{run_id}")
def get_evidence_run(run_id: str) -> dict[str, object]:
    try:
        reader = EvidencePackageReader(package_dir(settings.evidence_output_root, run_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return sanitize_payload(reader.manifest)


@app.get("/api/targeted-fill/templates")
def list_targeted_templates() -> list[dict[str, str]]:
    return template_registry.catalog()


@app.get("/api/targeted-fill/capabilities")
def get_targeted_capabilities() -> dict[str, object]:
    return {
        "retrieval": LocalEvidenceIndex.capabilities(),
        "local_semantic": {
            "sentence_transformers_installed": importlib.util.find_spec("sentence_transformers") is not None,
            "default_model": "intfloat/multilingual-e5-small",
            "automatic_download": False,
        },
        "deterministic_pipeline": {
            "requirement_execution_spec": "v2",
            "disclosure_catalog": True,
            "grouped_retrieval": True,
            "slot_verifier": True,
            "index_evidence_guard": True,
            "cross_dimension_guard": True,
        },
        "cloud_policy": {
            "enabled": False,
            "max_calls": 0,
            "max_cost_cny": 0,
        },
    }


@app.post("/api/targeted-fill/plan")
async def plan_targeted_fill(
    file: Annotated[UploadFile, File()],
    ir_run_id: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    plan_id = new_run_id("trg")
    upload_dir = settings.upload_root / f"plan-{plan_id}"
    upload_dir.mkdir(parents=True, exist_ok=False)
    template_path = upload_dir / Path(file.filename or "task-template.xlsx").name
    try:
        with template_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        payload = targeted_workflow.plan(template_path)
        if ir_run_id:
            try:
                reader = _ir_reader(ir_run_id)
                report = reader.read_json("validation_report", "validation_report.json")
                payload["ir_admission"] = {
                    "ir_run_id": ir_run_id,
                    "readiness": reader.manifest.get("readiness"),
                    "can_build_evidence": bool((report.get("checks") or {}).get("can_build_evidence")),
                }
            except HTTPException as exc:
                payload["ir_admission"] = {"ir_run_id": ir_run_id, "error": exc.detail, "can_build_evidence": False}
        return sanitize_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)


@app.post("/api/targeted-fill/jobs")
async def create_targeted_fill_job(
    file: Annotated[UploadFile, File()],
    ir_run_id: Annotated[str, Form()],
    mode: Annotated[str, Form()] = "local_strict",
    top_k: Annotated[int, Form()] = 30,
    evidence_run_id: Annotated[str | None, Form()] = None,
    local_embedding_model: Annotated[str, Form()] = "intfloat/multilingual-e5-small",
    allow_local_model_download: Annotated[bool, Form()] = False,
) -> TargetedJobState:
    try:
        require_package_dir_name(ir_run_id)
        if evidence_run_id:
            require_package_dir_name(evidence_run_id)
        request_probe = TargetedRunRequest(
            ir_run_id=ir_run_id,
            template_path=Path("task-template.xlsx"),
            evidence_run_id=evidence_run_id,
            mode=mode,
            top_k=top_k,
            local_embedding_model=local_embedding_model,
            allow_local_model_download=allow_local_model_download,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not _document_ir_output_dir(ir_run_id):
        raise HTTPException(status_code=404, detail="Document IR run not found")
    run_id = new_run_id("trg")
    upload_dir = settings.upload_root / run_id
    upload_dir.mkdir(parents=True, exist_ok=False)
    template_name = Path(file.filename or "task-template.xlsx").name
    template_path = upload_dir / template_name
    with template_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    request = request_probe.model_copy(update={"run_id": run_id, "template_path": template_path})
    state = TargetedJobState(
        run_id=run_id,
        ir_run_id=ir_run_id,
        status="queued",
        message="Targeted Recall job queued",
        output_dir=package_dir(settings.targeted_output_root, run_id),
        template_name=template_name,
        mode=request.mode,
    )
    try:
        targeted_store.create(state)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    threading.Thread(target=_run_targeted_background_job, args=(run_id, request), daemon=True).start()
    return state


@app.get("/api/targeted-fill/jobs")
def list_targeted_fill_jobs() -> list[TargetedJobState]:
    states = {state.run_id: state for state in targeted_store.list()}
    for manifest_path in settings.targeted_output_root.glob("*/manifest.json"):
        run_id = manifest_path.parent.name
        if run_id not in states:
            state = _targeted_state_from_manifest(run_id)
            if state:
                states[run_id] = state
    return sorted(states.values(), key=lambda item: item.run_id, reverse=True)


@app.get("/api/targeted-fill/jobs/{run_id}")
def get_targeted_fill_job(run_id: str) -> TargetedJobState:
    state = targeted_store.get(run_id) or _targeted_state_from_manifest(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Targeted Recall run not found")
    return state


@app.get("/api/targeted-fill/jobs/{run_id}/manifest")
def get_targeted_fill_manifest(run_id: str) -> dict[str, object]:
    return sanitize_payload(_targeted_reader(run_id).manifest)


@app.get("/api/targeted-fill/jobs/{run_id}/requirements")
def get_targeted_fill_requirements(run_id: str) -> list[dict[str, object]]:
    return sanitize_payload(_targeted_reader(run_id).profiles())


@app.get("/api/targeted-fill/jobs/{run_id}/results")
def get_targeted_fill_results(run_id: str) -> list[dict[str, object]]:
    return sanitize_payload(_targeted_reader(run_id).answers())


@app.get("/api/targeted-fill/jobs/{run_id}/requirements/{requirement_id}")
def get_targeted_requirement(run_id: str, requirement_id: str) -> dict[str, object]:
    reader = _targeted_reader(run_id)
    profile = next((row for row in reader.profiles() if row.get("requirement_id") == requirement_id), None)
    answer = next((row for row in reader.answers() if row.get("requirement_id") == requirement_id), None)
    if not profile or not answer:
        raise HTTPException(status_code=404, detail="Requirement result not found")
    hits = [row for row in reader.hits() if row.get("requirement_id") == requirement_id]
    selected = next(
        (row for row in reader.selected_evidence() if row.get("requirement_id") == requirement_id),
        {"requirement_id": requirement_id, "atoms": []},
    )
    group_ids = set(answer.get("selected_group_ids") or [])
    disclosure_groups = [
        row for row in reader.disclosure_catalog() if row.get("group_id") in group_ids
    ]
    verifications = [
        row for row in reader.verifications() if row.get("requirement_id") == requirement_id
    ]
    search_coverage = next(
        (row for row in reader.search_coverage() if row.get("requirement_id") == requirement_id),
        {},
    )
    return sanitize_payload(
        {
            "profile": profile,
            "answer": answer,
            "hits": hits,
            "selected_evidence": selected,
            "disclosure_groups": disclosure_groups,
            "verifications": verifications,
            "search_coverage": search_coverage,
        }
    )


@app.get("/api/targeted-fill/jobs/{run_id}/validation-report")
def get_targeted_fill_validation(run_id: str) -> dict[str, object]:
    return sanitize_payload(_targeted_reader(run_id).validation_report())


@app.get("/api/targeted-fill/jobs/{run_id}/artifacts")
def list_targeted_fill_artifacts(run_id: str) -> dict[str, object]:
    reader = _targeted_reader(run_id)
    files = []
    for path in sorted(reader.root.rglob("*")):
        if path.is_file() and path.name != ".DS_Store":
            relative = path.relative_to(reader.root).as_posix()
            files.append(
                {
                    "path": relative,
                    "size": path.stat().st_size,
                    "url": f"/targeted_fill_output/{run_id}/{relative}",
                }
            )
    return {"run_id": run_id, "files": files}


@app.get("/api/targeted-fill/jobs/{run_id}/export")
def download_targeted_fill_export(run_id: str) -> FileResponse:
    path = _targeted_reader(run_id).entrypoint("export")
    return FileResponse(path, filename=f"{run_id}-filled-result.xlsx")


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


def _targeted_reader(run_id: str) -> TargetedPackageReader:
    try:
        output_dir = package_dir(settings.targeted_output_root, run_id)
        return TargetedPackageReader(output_dir)
    except (FileNotFoundError, ValueError) as exc:
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
        summary={
            "readiness": manifest.get("readiness", "unknown"),
            "ir_revision": manifest.get("ir_revision", 1),
            "can_build_evidence": manifest.get("can_build_evidence", False),
            "page_count": manifest.get("page_count", 0),
            "block_count": manifest.get("block_count", 0),
            "table_count": manifest.get("table_count", 0),
            "figure_count": manifest.get("figure_count", 0),
            "spread_count": manifest.get("spread_count", 0),
            "review_task_count": manifest.get("review_task_count", 0),
        },
    )
    ir_store.put(state)
    return state


def _targeted_state_from_manifest(run_id: str) -> TargetedJobState | None:
    try:
        output_dir = package_dir(settings.targeted_output_root, run_id)
    except ValueError:
        return None
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    export_path = output_dir / str((manifest.get("entrypoints") or {}).get("export") or "exports/filled-result.xlsx")
    state = TargetedJobState(
        run_id=run_id,
        ir_run_id=str(manifest.get("source_ir_run_id") or ""),
        status="done",
        message="Targeted Recall job completed",
        output_dir=output_dir,
        template_name="task-template.xlsx",
        mode=str(manifest.get("mode_requested") or "local_strict"),
        manifest_path=manifest_path,
        export_path=export_path if export_path.exists() else None,
        summary={
            "requirement_count": manifest.get("requirement_count", 0),
            "status_counts": manifest.get("status_counts") or {},
            "cloud_call_count": manifest.get("cloud_call_count", 0),
            "cloud_cost_cny": manifest.get("cloud_cost_cny", 0),
            "local_model_inference_count": manifest.get("local_model_inference_count", 0),
            "mode_effective": manifest.get("mode_effective"),
            "can_export": manifest.get("can_export", False),
        },
    )
    targeted_store.put(state)
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
        manifest_path=manifest_path,
        summary={
            "page_count": manifest.get("page_count", 0),
            "model": manifest.get("model"),
        },
    )
    ocr_store.put(state)
    return state


def _run_background_job(run_id: str, request: OcrRunRequest) -> None:
    output_dir = package_dir(settings.output_root, run_id)
    state = OcrJobState(
        run_id=run_id,
        status="running",
        message="OCR job running",
        output_dir=output_dir,
        logs=[],
    )
    ocr_store.put(state)

    def log(message: str) -> None:
        current = ocr_store.get(run_id) or state
        current.logs.append(message)
        current.message = message
        ocr_store.put(current)

    try:
        result = ocr_workflow.run(request, run_id=run_id, log=log)
        done = ocr_store.get(run_id) or state
        done.status = "done"
        done.message = "OCR job completed"
        done.job_id = result.job_id
        done.result_json_url = result.result_json_url
        done.page_count = result.page_count
        done.manifest_path = result.manifest_path
        done.summary = {
            "page_count": result.page_count,
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
    return {
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
    }


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


def _run_targeted_background_job(run_id: str, request: TargetedRunRequest) -> None:
    queued = targeted_store.get(run_id)
    started_at = utc_now()
    state = TargetedJobState(
        run_id=run_id,
        ir_run_id=request.ir_run_id,
        status="running",
        message="Targeted Recall job running",
        output_dir=package_dir(settings.targeted_output_root, run_id),
        template_name=request.template_path.name,
        mode=request.mode,
        logs=list(queued.logs if queued else []),
        created_at=queued.created_at if queued else started_at,
        started_at=started_at,
        updated_at=started_at,
    )
    targeted_store.put(state)

    def log(message: str) -> None:
        current = targeted_store.get(run_id) or state
        current.logs.append(message)
        current.message = message
        current.updated_at = utc_now()
        targeted_store.put(current)

    try:
        result = targeted_workflow.run(request, log=log)
        done = targeted_store.get(run_id) or state
        done.status = "done"
        done.message = "Targeted Recall job completed"
        done.manifest_path = result.manifest_path
        done.export_path = result.export_path
        done.summary = {
            "requirement_count": result.requirement_count,
            "status_counts": result.status_counts,
            "cloud_call_count": result.cloud_call_count,
            "cloud_cost_cny": result.cloud_cost_cny,
            "local_model_inference_count": result.local_model_inference_count,
            "can_export": True,
        }
        done.finished_at = utc_now()
        done.updated_at = done.finished_at
        targeted_store.put(done)
        shutil.rmtree(request.template_path.parent, ignore_errors=True)
    except Exception as exc:
        failed = targeted_store.get(run_id) or state
        failed.status = "failed"
        failed.message = "Targeted Recall job failed"
        failed.error = str(exc)
        failed.logs.append(str(exc))
        failed.finished_at = utc_now()
        failed.updated_at = failed.finished_at
        targeted_store.put(failed)
