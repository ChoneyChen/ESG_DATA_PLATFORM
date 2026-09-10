from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from esg_targeted import __version__
from esg_targeted.config import settings
from esg_targeted.contracts import ExtractionRequest, JobStatus
from esg_targeted.ids import new_job_id
from esg_targeted.inspection import ResultInspectionService
from esg_targeted.inspection.contracts import ResultInspection
from esg_targeted.ir.catalog import DocumentIrCatalog
from esg_targeted.models.capabilities import NUEXTRACT3_CAPABILITIES
from esg_targeted.results.spec import result_contract
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.index_assets import SemanticIndexAssetCatalog
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.recovery import recover_result_jobs, result_catalog_status
from esg_targeted.workflow import TargetedExtractionWorkflow


settings.ensure_runtime_dirs()
job_store = JobStore(settings.state_db)
recovered_job_ids = job_store.recover_abandoned()
artifact_store = ArtifactStore(settings.output_root)
restored_result_job_ids = recover_result_jobs(job_store, artifact_store)
semantic_index_assets = SemanticIndexAssetCatalog(settings.index_cache_root)
ir_catalog = DocumentIrCatalog(settings.ir_roots)
standard_catalog = StandardPackageCatalog(settings.standard_dist_root)
workflow = TargetedExtractionWorkflow(settings, job_store, artifact_store)
inspection_service = ResultInspectionService(artifact_store)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="targeted-extraction")

app = FastAPI(
    title="ExtractESG Targeted Table Extraction",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{settings.frontend_port}",
        f"http://localhost:{settings.frontend_port}",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health() -> dict:
    ir_runs = ir_catalog.list()
    packages, package_issues = standard_catalog.scan()
    results = result_catalog_status(job_store, artifact_store)
    return {
        "status": "ok",
        "service": "targeted-table-extraction",
        "version": __version__,
        "ports": {"backend": settings.backend_port, "frontend": settings.frontend_port},
        "models": {
            "nuextract": {
                "path": str(settings.nuextract_model_path),
                "available": (settings.nuextract_model_path / "model.safetensors").is_file(),
                "capabilities": NUEXTRACT3_CAPABILITIES.as_dict(),
            },
            "embedding": {
                "path": str(settings.embedding_model_path),
                "available": (settings.embedding_model_path / "model.safetensors").is_file(),
            },
            "qiniu_vlm": {
                "configured": bool(settings.qiniu_api_key),
                "default_model": settings.qiniu_model,
                "base_url": settings.qiniu_base_url,
                "profile": "bounded_single_visual_object",
            },
        },
        "catalog": {
            "ir_run_count": len(ir_runs),
            "evidence_ready_ir_count": sum(item.can_build_evidence for item in ir_runs),
            "standard_package_count": len(packages),
            "invalid_standard_package_count": len(package_issues),
            "semantic_index_asset_count": len(semantic_index_assets.list()),
            "indexed_result_job_count": results["indexed_job_count"],
            "result_bundle_directory_count": results["artifact_job_count"],
        },
        "runtime": {
            "recovered_interrupted_job_count": len(recovered_job_ids),
            "restored_result_job_count": len(restored_result_job_ids),
        },
        "capabilities": {
            "delete_terminal_job": True,
        },
    }


@app.get("/api/config")
def config() -> dict:
    return {
        "ir_roots": [str(path) for path in settings.ir_roots],
        "standard_dist_root": str(settings.standard_dist_root),
        "output_root": str(settings.output_root),
        "require_ready_ir": settings.require_ready_ir,
        "retrieval_top_k": settings.retrieval_top_k,
        "retrieval_objects": {
            "default_top_n": settings.retrieval_object_top_n,
            "minimum": 1,
            "maximum": 5,
            "unit": "independent_evidence_object",
        },
        "packet_max_chars": settings.packet_max_chars,
        "model_max_tokens": settings.model_max_tokens,
        "model_retries": settings.model_retries,
        "packet_max_spans": settings.packet_max_spans,
        "packet_max_groups": settings.packet_max_groups,
        "packet_max_candidates": settings.packet_max_candidates,
        "packet_max_images": settings.packet_max_images,
        "model_max_input_tokens": settings.model_max_input_tokens,
        "model_timeout_seconds": settings.model_timeout_seconds,
        "model_min_output_tokens": settings.model_min_output_tokens,
        "semantic_providers": [
            {
                "id": "local_nuextract",
                "label": "本地 NuExtract3",
                "profile": "direct_multi_row_semantic_fill",
                "default_model": settings.nuextract_model_path.name,
            },
            {
                "id": "qiniu_vlm",
                "label": "七牛云视觉语言模型",
                "profile": "direct_multi_row_semantic_fill_single_visual_object",
                "configured": bool(settings.qiniu_api_key),
                "default_model": settings.qiniu_model,
            },
        ],
        "cloud_packet": {
            "max_chars": settings.cloud_packet_max_chars,
            "max_spans": settings.cloud_packet_max_spans,
            "max_groups": settings.cloud_packet_max_groups,
            "max_candidates": settings.cloud_packet_max_candidates,
            "max_images": settings.cloud_packet_max_images,
        },
    }


@app.get("/api/contracts/result-bundle")
def get_result_bundle_contract() -> dict:
    return result_contract()


@app.get("/api/semantic-indexes")
def list_semantic_indexes() -> list[dict]:
    return semantic_index_assets.list()


@app.delete("/api/semantic-indexes/{inventory_id}")
def delete_semantic_index(inventory_id: str) -> dict:
    active_jobs = [
        item.job_id
        for item in job_store.list(500)
        if item.status in {JobStatus.QUEUED, JobStatus.RUNNING}
    ]
    if active_jobs:
        raise HTTPException(
            status_code=409,
            detail=(
                "A targeted extraction job is queued or running; semantic indexes "
                "cannot be removed until the extraction worker is idle."
            ),
        )
    try:
        return semantic_index_assets.delete(inventory_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="semantic index not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ir-runs")
def list_ir_runs() -> list[dict]:
    return [item.model_dump(mode="json") for item in ir_catalog.list()]


@app.get("/api/standards")
def list_standards() -> list[dict]:
    return [item.model_dump(mode="json") for item in standard_catalog.list()]


@app.get("/api/standards/diagnostics")
def standard_diagnostics() -> dict:
    return standard_catalog.diagnostics()


@app.get("/api/result-catalog")
def get_result_catalog_status() -> dict:
    return result_catalog_status(job_store, artifact_store)


@app.get("/api/standards/{package_id}/{package_version}")
def get_standard(package_id: str, package_version: str) -> dict:
    try:
        package = standard_catalog.load(package_id, package_version)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "manifest": package.manifest.model_dump(mode="json"),
        "compilation": package.compilation.model_dump(mode="json"),
        "metrics": [item.model_dump(mode="json") for item in package.metrics],
        "concepts": [item.model_dump(mode="json") for item in package.concepts],
        "elements_by_metric": package.elements_by_metric,
    }


@app.post("/api/jobs", status_code=202)
def create_job(request: ExtractionRequest) -> dict:
    try:
        package = standard_catalog.load(request.package_id, request.package_version)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    job_id = new_job_id()
    output_dir = str(artifact_store.job_dir(job_id))
    record = job_store.create(job_id, request, output_dir)
    artifact_store.write_json(job_id, "input/standard-package.json", package.model_dump(mode="json"))
    executor.submit(workflow.run, job_id)
    return record.model_dump(mode="json")


@app.get("/api/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return [item.model_dump(mode="json") for item in job_store.list(limit)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return job_store.get(job_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    try:
        job = job_store.get(job_id)
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise ValueError(
                f"job status {job.status.value} cannot be deleted; cancel it first"
            )
        deleted_artifacts = artifact_store.delete_job(job_id)
        job_store.delete(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "deleted": True,
        "deleted_artifacts": deleted_artifacts,
        "preserved": [
            "document_ir",
            "ocr_output",
            "standard_packages",
            "model_weights",
            "shared_semantic_index_cache",
        ],
    }


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    terminal: bool = Query(False),
    terminal_status: str = Query("cancelled", pattern="^(cancelled|interrupted|failed)$"),
) -> dict:
    try:
        record = job_store.request_cancel(job_id)
        if terminal and record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            final_status = {
                "cancelled": JobStatus.CANCELLED,
                "interrupted": JobStatus.INTERRUPTED,
                "failed": JobStatus.FAILED,
            }[terminal_status]
            record = job_store.update(
                job_id,
                status=final_status,
                stage=final_status.value,
                error=f"{final_status.value.title()} by the unified pipeline scheduler",
            )
            job_store.add_event(
                job_id,
                final_status.value,
                "warning",
                f"Process group marked {final_status.value} by the unified pipeline scheduler",
            )
        return record.model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@app.post("/api/jobs/{job_id}/resume", status_code=202)
def resume_job(job_id: str) -> dict:
    try:
        record = job_store.prepare_resume(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    executor.submit(workflow.run, job_id)
    return record.model_dump(mode="json")


@app.get("/api/jobs/{job_id}/events")
def get_events(job_id: str, after: int = Query(0, ge=0)) -> list[dict]:
    try:
        job_store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    return job_store.events(job_id, after)


@app.get("/api/jobs/{job_id}/inspection", response_model=ResultInspection)
def inspect_job_results(job_id: str) -> ResultInspection:
    try:
        job = job_store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    return inspection_service.build(job)


@app.get("/api/jobs/{job_id}/artifacts")
def get_artifacts(job_id: str) -> list[dict]:
    try:
        job_store.get(job_id)
        return artifact_store.list(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@app.get("/api/jobs/{job_id}/artifact")
def preview_artifact(job_id: str, path: str = Query(...)):
    try:
        job_store.get(job_id)
        artifact = artifact_store.resolve(job_id, path)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if artifact.suffix == ".json":
        return json.loads(artifact.read_text(encoding="utf-8"))
    if artifact.suffix in {".jsonl", ".csv", ".txt", ".md"}:
        return PlainTextResponse(artifact.read_text(encoding="utf-8"))
    raise HTTPException(status_code=415, detail="artifact is binary; use the download endpoint")


@app.get("/api/jobs/{job_id}/download")
def download_artifact(job_id: str, path: str = Query(...)) -> FileResponse:
    try:
        job_store.get(job_id)
        artifact = artifact_store.resolve(job_id, path)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(artifact, filename=artifact.name)
