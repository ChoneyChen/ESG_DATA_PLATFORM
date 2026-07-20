from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from esg_v2.config import get_settings
from esg_v2.contracts import OcrJobState, OcrRunRequest, OptionalPayload
from esg_v2.document.contracts import (
    DocumentIrBuildRequest,
    DocumentIrJobState,
    DocumentIrRepairRequest,
    PatchDecisionRequest,
)
from esg_v2.document.revision_service import DocumentIrRevisionService
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.versioning import DocumentIrVersionManager
from esg_v2.models.model_registry import QiniuModelRegistry
from esg_v2.models.qiniu_adapter import QiniuModelAdapter
from esg_v2.storage.job_store import JobStore
from esg_v2.storage.package_layout import (
    new_run_id,
    package_dir,
    require_package_dir_name,
    require_run_id,
)
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
from esg_v2.workflows.ocr_workflow import OcrWorkflow
from esg_v2.utils.sanitization import sanitize_payload, sanitize_remote_url


settings = get_settings()
settings.output_root.mkdir(parents=True, exist_ok=True)
settings.document_ir_output_root.mkdir(parents=True, exist_ok=True)
settings.upload_root.mkdir(parents=True, exist_ok=True)

ocr_store = JobStore(settings.ocr_job_state_root, OcrJobState)
ir_store = JobStore(settings.document_ir_job_state_root, DocumentIrJobState)
ocr_workflow = OcrWorkflow(settings)
ir_workflow = DocumentIrWorkflow(settings)
revision_service = DocumentIrRevisionService(settings)

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "esg-v2-backend"}


@app.get("/api/models/status")
def get_model_status() -> dict[str, object]:
    try:
        adapter = QiniuModelAdapter(settings)
        return QiniuModelRegistry(settings, adapter).refresh()
    except Exception as exc:
        return {"catalog_error": str(exc), "approved_vision_models": [], "health": []}


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
        summary={
            "readiness": manifest.get("readiness", "unknown"),
            "ir_revision": manifest.get("ir_revision", 1),
            "can_build_evidence": manifest.get("can_build_evidence", False),
            "page_count": manifest.get("page_count", 0),
            "block_count": manifest.get("block_count", 0),
            "table_count": manifest.get("table_count", 0),
            "figure_count": manifest.get("figure_count", 0),
            "review_task_count": manifest.get("review_task_count", 0),
        },
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


def _run_document_ir_background_job(run_id: str, request: DocumentIrBuildRequest) -> None:
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    state = DocumentIrJobState(
        run_id=run_id,
        ocr_run_id=request.ocr_run_id,
        status="running",
        message="Document IR job running",
        output_dir=output_dir,
        logs=[],
    )
    ir_store.put(state)

    def log(message: str) -> None:
        current = ir_store.get(run_id) or state
        current.logs.append(message)
        current.message = message
        ir_store.put(current)

    try:
        result = ir_workflow.run(request, run_id=run_id, log=log)
        done = ir_store.get(run_id) or state
        done.status = "done"
        done.message = "Document IR job completed"
        done.manifest_path = result.manifest_path
        result_manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        done.summary = {
            "readiness": result.readiness,
            "ir_revision": result_manifest.get("ir_revision", 1),
            "can_build_evidence": result_manifest.get("can_build_evidence", False),
            "page_count": result.page_count,
            "block_count": result.block_count,
            "table_count": result.table_count,
            "figure_count": result.figure_count,
            "review_task_count": result.review_task_count,
        }
        ir_store.put(done)
    except Exception as exc:
        failed = ir_store.get(run_id) or state
        failed.status = "failed"
        failed.message = "Document IR job failed"
        failed.error = str(exc)
        failed.logs.append(str(exc))
        ir_store.put(failed)


def _run_document_ir_repair_background_job(run_id: str, request: DocumentIrRepairRequest) -> None:
    output_dir = package_dir(settings.document_ir_output_root, run_id)
    ocr_run_id = ""
    try:
        ocr_run_id = _ir_reader(request.parent_ir_run_id).load_document().metadata.ocr_run_id
    except (HTTPException, FileNotFoundError, ValueError):
        pass
    state = DocumentIrJobState(
        run_id=run_id,
        ocr_run_id=ocr_run_id,
        status="running",
        message="Targeted Document IR repair running",
        output_dir=output_dir,
        logs=[f"Repair targets: {', '.join(request.target_ids)}"],
    )
    ir_store.put(state)
    try:
        manifest = revision_service.repair(request, run_id=run_id)
        state.status = "done"
        state.message = "Targeted Document IR repair completed"
        state.manifest_path = output_dir / "manifest.json"
        state.summary = {
            "readiness": manifest.get("readiness", "unknown"),
            "ir_revision": manifest.get("ir_revision", 1),
            "can_build_evidence": manifest.get("can_build_evidence", False),
            "page_count": manifest.get("page_count", 0),
            "block_count": manifest.get("block_count", 0),
            "table_count": manifest.get("table_count", 0),
            "figure_count": manifest.get("figure_count", 0),
            "review_task_count": manifest.get("review_task_count", 0),
        }
        ir_store.put(state)
    except Exception as exc:
        state.status = "failed"
        state.message = "Targeted Document IR repair failed"
        state.error = str(exc)
        state.logs.append(str(exc))
        ir_store.put(state)
