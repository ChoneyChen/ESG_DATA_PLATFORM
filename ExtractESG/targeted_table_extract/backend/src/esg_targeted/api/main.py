from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from esg_targeted import __version__
from esg_targeted.config import settings
from esg_targeted.contracts import ExtractionRequest, JobStatus, ReportExtractionRequest, EvidenceTransitionRequest, RuntimeCredentials
from esg_targeted.report_session import ReportSessionService
from esg_targeted.queue_client import enqueue_existing, cancel_existing
from esg_targeted.ids import new_job_id
from esg_targeted.inspection import ResultInspectionService
from esg_targeted.inspection.contracts import ResultInspection
from esg_targeted.ir.catalog import DocumentIrCatalog
from esg_targeted.models.capabilities import NUEXTRACT3_CAPABILITIES
from esg_targeted.results.spec import result_contract
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.clause_lifecycle_service import ClauseLifecycleService
from esg_standard_packages.clause_package import ClausePackageCatalog
from esg_targeted.standards.runtime_snapshot import registered_extensions, freeze_extraction_request
from esg_targeted.runtime_compatibility import compatibility_snapshot, require_runtime_ready, RuntimeCompatibilityError
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.index_assets import SemanticIndexAssetCatalog
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.lifecycle import ExtractionLifecycle
from esg_targeted.storage.intermediate_cleanup import IntermediateCleanup, IntermediateCleanupPlan
from esg_runtime.lifecycle import LifecycleBlocked
from esg_runtime.progress import progress_from_event, classify_error
from esg_runtime.local_operator import install_loopback_marker, operator_headers
from types import SimpleNamespace
from esg_targeted.results.stages import collect_stage
from pydantic import BaseModel, ConfigDict, Field
from esg_targeted.storage.recovery import recover_result_jobs, result_catalog_status
from esg_targeted.storage.record_deletion import ExtractionRecordDeletion
from esg_targeted.api.record_deletion import extraction_deletion_router
from esg_targeted.workflow import TargetedExtractionWorkflow


settings.ensure_runtime_dirs()
job_store = JobStore(settings.state_db)
recovered_job_ids = job_store.recover_abandoned()
artifact_store = ArtifactStore(settings.output_root)
restored_result_job_ids = recover_result_jobs(job_store, artifact_store)
semantic_index_assets = SemanticIndexAssetCatalog(settings.index_cache_root)
ir_catalog = DocumentIrCatalog(settings.ir_roots)
standard_catalog = StandardPackageCatalog(settings.standard_dist_root)
clause_lifecycle = ClauseLifecycleService(standard_catalog)
clause_profiles = ClausePackageCatalog(settings.standard_dist_root.parent)
workflow = TargetedExtractionWorkflow(settings, job_store, artifact_store)
inspection_service = ResultInspectionService(artifact_store)
report_sessions = ReportSessionService(settings, job_store, artifact_store)
lifecycle = ExtractionLifecycle(job_store, artifact_store, report_sessions)
intermediate_cleanup = IntermediateCleanup(lifecycle)


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=4000)
    expected_revision: int | None = Field(default=None, ge=0)


class TerminalMaterializationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=2000)
    job_ids: list[str] | None = None


class IntermediateExecuteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    plan_id: str = Field(pattern=r'^intermediate-plan-[a-f0-9]{64}$')
    expected_revision: int = Field(strict=True, ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class IntermediateRestoreRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    cleanup_id: str = Field(pattern=r'^intermediate-[a-f0-9]{64}$')
    expected_revision: int = Field(strict=True, ge=0)
    reason: str = Field(min_length=1, max_length=2000)


def _intermediate_operator(authorization, *, mutation=False):
    headers = operator_headers(authorization)
    if headers is None:
        raise HTTPException(401, '核对或恢复中间文件需要工作台操作员凭据')
    base = os.getenv('ESG_PUBLICATION_API_URL', 'http://127.0.0.1:8765').rstrip('/')
    try:
        request = Request(base + '/internal/v1/me', headers=headers)
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            principal = json.load(response)
    except HTTPError as exc:
        raise HTTPException(exc.code if exc.code in {401, 403} else 503, '操作员凭据未获授权或授权服务不可读') from exc
    except (URLError, OSError, ValueError) as exc:
        raise HTTPException(503, '操作员授权服务不可读，中间文件未操作') from exc
    allowed = {'admin'} if mutation else {'read', 'admin'}
    if not isinstance(principal, dict) or not isinstance(principal.get('roles'), list) \
            or any(not isinstance(role, str) for role in principal['roles']) \
            or not set(principal['roles']) & allowed \
            or not isinstance(principal.get('operator_ref'), str) or not principal['operator_ref'].strip():
        raise HTTPException(403, '当前操作员没有中间文件清理权限')
    return principal['operator_ref']


def _materialization_operator(authorization):
    headers = operator_headers(authorization)
    if headers is None:
        raise HTTPException(401, '保存交付成果需要工作台操作员凭据')
    base = os.getenv('ESG_PUBLICATION_API_URL', 'http://127.0.0.1:8765').rstrip('/')
    try:
        request = Request(base + '/internal/v1/me', headers=headers)
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            principal = json.load(response)
    except HTTPError as exc:
        raise HTTPException(exc.code if exc.code in {401, 403} else 503,
            '操作员凭据未获授权' if exc.code in {401, 403} else '操作员授权服务暂不可用') from exc
    except (URLError, OSError, ValueError) as exc:
        raise HTTPException(503, '操作员授权服务暂不可用；已保存成果保留') from exc
    if not set(principal.get('roles', [])) & {'review', 'admin'} or not principal.get('operator_ref'):
        raise HTTPException(403, '当前操作员没有保存交付成果权限')
    return principal['operator_ref']


def _session_with_lifecycle(session):
    description = lifecycle.describe_session(session["session_id"])
    session["lifecycle"] = description
    if description["state"] != "active":
        session["available_actions"] = []
    session["available_actions"] = [action for action in session.get("available_actions", [])
        if action.get("action") not in {"archive", "restore", "discard", "delete"}]
    session["available_actions"].extend(execution_deletion.describe("session", session["session_id"])["available_actions"])
    session["deletion_pending"] = execution_deletion.describe("session", session["session_id"])["deletion_pending"]
    if session.get('error'):
        session['error_detail'] = session.get('error_detail') or classify_error(str(session['error']), task_type='targeted_extraction').model_dump(mode='json')
    return session


def _job_with_lifecycle(job):
    result = job.model_dump(mode="json")
    result["lifecycle"] = lifecycle.describe_job(job.job_id)
    result["lifecycle"]["available_actions"] = [action for action in result["lifecycle"].get("available_actions", [])
        if action.get("action") not in {"archive", "restore", "discard", "delete"}]
    try:
        deletion = execution_deletion.describe("job", job.job_id)
        result["lifecycle"]["available_actions"].extend(deletion["available_actions"])
        result["deletion_pending"] = deletion["deletion_pending"]
    except LifecycleBlocked as exc:
        # A projection is deleted with its report-session owner. Keep the reason
        # so the workbench can explain why instead of hiding the command.
        result["deletion_blockers"] = exc.blockers
    result["available_actions"] = result["lifecycle"]["available_actions"]
    try:
        counts = collect_stage(artifact_store, job.job_id, request=job.request)['summary']['counts']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A missing or corrupt historical checkpoint affects this record, not
        # the entire workbench. Legacy counters are not completion evidence.
        counts = {}
        result['progress_error'] = {'category': 'source', 'code': 'progress_artifact_unreadable',
            'message': '阶段成果暂时无法读取，请检查此任务的成果文件。',
            'technical_detail': str(exc), 'recoverable': True, 'next_action': 'inspect'}
    subject = SimpleNamespace(stage=job.stage, progress=None, status=job.status,
        task_type=SimpleNamespace(value='targeted_extraction'), operation='run')
    progress = progress_from_event(subject, {'kind':'progress','counts':counts,
        'current_call': (job.summary or {}).get('agent', {}).get('current_call')})
    # This is a read projection, not a newly committed business event. A
    # heartbeat or page refresh must not fabricate a last-progress timestamp.
    progress.last_business_progress_at = (job.summary or {}).get('last_business_progress_at')
    result['progress'] = progress.model_dump(mode='json')
    if job.error:
        producer = (job.summary or {}).get('error_detail')
        call = (job.summary or {}).get('agent', {}).get('current_call') or {}
        if not producer and call.get('failure_category'):
            producer = {'category': call['failure_category']}
        result['error_detail'] = classify_error(job.error, task_type='targeted_extraction', producer_error=producer).model_dump(mode='json')
    return result


def submit_queue(kind, identity, **credentials):
    try:
        return enqueue_existing(kind, identity, **credentials)
    except RuntimeError as exc:
        raise HTTPException(503, {"message": str(exc), "native_id": identity,
            "retry_endpoint": f"/api/pipeline/tasks/{kind}/{identity}/run-existing"}) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

app = FastAPI(
    title="ExtractESG Targeted Table Extraction",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
install_loopback_marker(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{settings.frontend_port}",
        f"http://localhost:{settings.frontend_port}",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


def _cancel_deleted_execution(kind, identity):
    if kind == "session":
        report_sessions.cancel(identity)
    else:
        parent = job_store.get(identity).request.report_session_id
        if parent:
            try:
                report_sessions.cancel(parent)
                cancel_existing(parent)
            except KeyError:
                pass
        job = job_store.request_cancel(identity)
        if job.status == JobStatus.QUEUED and not job_store.has_live_worker(identity):
            job_store.update(identity, status=JobStatus.CANCELLED, stage="cancelled")
    cancel_existing(identity)


execution_deletion = ExtractionRecordDeletion(job_store, artifact_store, report_sessions,
    _cancel_deleted_execution, guard=lifecycle.guard)
app.include_router(extraction_deletion_router(execution_deletion,
    lambda authorization: _intermediate_operator(authorization, mutation=True)))


@app.on_event("startup")
def start_execution_deletion():
    if not os.getenv("PYTEST_CURRENT_TEST"):
        execution_deletion.start()


@app.on_event("shutdown")
def stop_execution_deletion():
    execution_deletion.stop()


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
            "local_qwen": {
                "path": str(settings.qwen_model_path),
                "available": settings.qwen_model_path.is_dir()
                and any(settings.qwen_model_path.glob("*.safetensors")),
                "profile": "general_visual_semantic_reasoning",
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
        "compatibility": compatibility_snapshot(standard_catalog),
        "capabilities": {
            "delete_terminal_job": True,
            "report_sessions_v1": True,
            "model_roles_v1": True,
            "unified_queue_only": True,
            "compatibility_v1": True,
            "standard_catalog_v1": True,
            "platform_major_profile_v1": True,
        },
    }


@app.get("/api/compatibility")
def get_compatibility() -> dict:
    return compatibility_snapshot(standard_catalog)


def _require_runtime_ready():
    try:
        require_runtime_ready(standard_catalog)
    except RuntimeCompatibilityError as exc:
        raise HTTPException(409, exc.detail) from exc


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
            "unit": "retrieval_candidate_object",
        },
        "agent": {"default_max_steps": 8, "minimum": 1, "maximum": 24},
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
                "role": "小输出合同与成熟定向填表路径",
            },
            {
                "id": "local_qwen",
                "label": "本地 Qwen3.5（通用视觉语义）",
                "profile": "general_visual_semantic_reasoning",
                "default_model": settings.qwen_model_path.name,
                "role": "源表理解、上下文补全与指标语义映射",
            },
            {
                "id": "qiniu_vlm",
                "label": "七牛云视觉语言模型",
                "profile": "direct_multi_row_semantic_fill_single_visual_object",
                "configured": bool(settings.qiniu_api_key),
                "default_model": settings.qiniu_model,
                "role": "用户配置的云视觉模型；能力依赖实际模型与服务配置",
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


@app.get("/api/extraction-profiles/platform-major")
def extraction_platform_major(topics: str | None = None):
    from esg_targeted.standards.profiles import platform_major_profile
    try:
        return platform_major_profile(settings.standard_dist_root.parent,
            topics=[t.strip() for t in topics.split(",") if t.strip()] if topics else None)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/extraction-profiles/foundation")
def extraction_foundation(profile: str = "base16"):
    """A foundation metric set with its immutable package identities.

    The set is data (catalog.PROFILES); this endpoint only resolves each
    package's active version, so a new set needs no new route.
    """
    from esg_targeted.foundation.catalog import metrics_for
    from esg_targeted.standards.catalog import StandardPackageCatalog
    try:
        metrics = metrics_for(profile)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    packages, _ = StandardPackageCatalog(settings.standard_dist_root).scan()
    versions = {p.disclosure_requirement: p.package_version for p in packages}
    digests = {p.package_id: getattr(p, "source_digest", None) for p in packages}
    grouped: dict[str, list[str]] = {}
    for metric in metrics:
        grouped.setdefault(metric.package_id, []).append(metric.metric_id)
    standards = []
    for package_id, metric_ids in grouped.items():
        requirement = package_id.rsplit(".", 1)[-1].upper()
        if requirement not in versions:
            raise HTTPException(409, f"标准包未加载：{package_id}")
        standards.append({"package_id": package_id, "package_version": versions[requirement],
                          "metric_ids": metric_ids})
    return {"profile": profile, "metric_count": len(metrics), "standards": standards,
            "package_digests": {item["package_id"]: digests.get(item["package_id"]) for item in standards},
            "metrics": [{"code": m.code, "metric_id": m.metric_id, "package_id": m.package_id,
                         "label": m.label, "kind": m.kind, "dimension": m.dimension,
                         "parent_code": m.parent_code} for m in metrics]}


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


@app.get("/api/standards/catalog")
def get_standard_catalog() -> dict:
    return standard_catalog.snapshot()


class ClauseAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    bundle_path: str
    package_id: str | None = None
    package_version: str | None = None
    source_datapoint_ids: list[str] = Field(default_factory=list)
    extension_id: str | None = None
    definition_version: str | None = None
    gold: dict | None = None
    recorded_by: str
    reason: str


class ClauseRetirementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    package_id: str | None = None
    package_version: str | None = None
    source_datapoint_id: str | None = None
    extension_id: str | None = None
    definition_version: str | None = None
    reason: str
    retired_by: str
    published_observations: int = Field(default=0, ge=0)
    active_tasks: int = Field(default=0, ge=0)
    dependent_extensions: list[str] = Field(default_factory=list)
    force: bool = False


@app.get("/api/clause-lifecycle")
def get_clause_lifecycle() -> dict:
    return clause_lifecycle.view()


@app.post("/api/clause-lifecycle/acceptances")
def record_clause_acceptance(request: ClauseAcceptanceRequest) -> dict:
    try:
        return clause_lifecycle.record(
            bundle_path=request.bundle_path, allowed_roots=[settings.output_root],
            package_id=request.package_id, package_version=request.package_version,
            source_datapoint_ids=request.source_datapoint_ids, extension_id=request.extension_id,
            definition_version=request.definition_version, gold=request.gold,
            recorded_by=request.recorded_by, reason=request.reason)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/clause-lifecycle/retirements")
def retire_clause_version(request: ClauseRetirementRequest) -> dict:
    try:
        return clause_lifecycle.retire(
            package_id=request.package_id, package_version=request.package_version,
            source_datapoint_id=request.source_datapoint_id, extension_id=request.extension_id,
            definition_version=request.definition_version, reason=request.reason,
            retired_by=request.retired_by, published_observations=request.published_observations,
            active_tasks=request.active_tasks, dependent_extensions=request.dependent_extensions,
            force=request.force)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/api/extensions')
def list_approved_extensions() -> dict:
    try:
        releases = registered_extensions(settings.standard_dist_root.parent, standard_catalog)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    from esg_standard_packages.clause_lifecycle import load_ledger
    ledger = load_ledger(clause_lifecycle.ledger_path)
    states = {row["clause"]: row for row in clause_lifecycle.extension_states(releases, ledger)}
    return {'schema_version':'approved-extension-catalog-1.0',
            'items':[{**release.model_dump(mode='json'),
                      'lifecycle':states.get(clause_lifecycle.extension_ref(release).key())} for release in releases]}


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
        "format_version": package.format_version,
        "manifest": package.manifest.model_dump(mode="json"),
        "compilation": package.compilation.model_dump(mode="json"),
        "metrics": [item.model_dump(mode="json") for item in package.metrics],
        "concepts": [item.model_dump(mode="json") for item in package.concepts],
        "elements_by_metric": package.elements_by_metric,
        "elements": [item.model_dump(mode="json") for item in package.elements],
        "relations": [item.model_dump(mode="json") for item in package.relations],
        "derivations": [item.model_dump(mode="json") for item in package.derivations],
        "semantic_diagnostics": standard_catalog.compiler.semantic_diagnostics(package),
        "lifecycle": clause_lifecycle.package_lifecycle(package_id, package_version, package),
        "profiles": [profile.model_dump(mode="json")
                     for profile in clause_profiles.for_package(package_id, package_version).values()],
        "profile_skipped": clause_profiles.skipped(package_id, package_version),
    }


@app.get("/api/clause-catalog")
def get_clause_catalog() -> dict:
    """One clause is one directory; this index is what the console and editor read."""
    return {"schema_version": "clause-catalog-view-1.0", "packages": clause_profiles.list_packages()}


@app.get("/api/clause-catalog/{package_id}/{package_version}/{source_datapoint_id}")
def get_clause_package(package_id: str, package_version: str, source_datapoint_id: str) -> dict:
    try:
        parts = clause_profiles.clause_parts(package_id, package_version, source_datapoint_id)
        profile = clause_profiles.profile(package_id, package_version, source_datapoint_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"schema_version": "clause-package-view-1.0", **parts,
            "profile": profile.model_dump(mode="json")}


@app.get("/api/clause-profiles/{package_id}/{package_version}")
def get_clause_profiles(package_id: str, package_version: str) -> dict:
    profiles = clause_profiles.for_package(package_id, package_version)
    return {"schema_version": "clause-profile-set-view-1.0", "package_id": package_id,
            "package_version": package_version,
            "profiles": [profile.model_dump(mode="json") for profile in profiles.values()],
            "skipped": clause_profiles.skipped(package_id, package_version)}


@app.get("/api/standards/{package_id}/{package_version}/diff/{other_version}")
def standard_version_diff(package_id: str, package_version: str, other_version: str) -> dict:
    try:
        return clause_lifecycle.diff(package_id, package_version, other_version)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/standards/{package_id}/{package_version}/dependents/{source_datapoint_id}")
def standard_clause_dependents(package_id: str, package_version: str, source_datapoint_id: str) -> dict:
    try:
        return clause_lifecycle.dependents(package_id, package_version, source_datapoint_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/jobs", status_code=202)
def create_job(request: ExtractionRequest) -> dict:
    _require_runtime_ready()
    try:
        package = standard_catalog.load(request.package_id, request.package_version)
        request, runtime_snapshot = freeze_extraction_request(package, request, settings.standard_dist_root.parent)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    job_id = new_job_id()
    output_dir = str(artifact_store.job_dir(job_id))
    record = job_store.create(job_id, request, output_dir)
    artifact_store.write_json(job_id, "input/standard-package.json", package.model_dump(mode="json"))
    if runtime_snapshot is not None:
        artifact_store.write_json(job_id, 'input/runtime-standard-snapshot.json', runtime_snapshot.model_dump(mode='json'))
    artifact_store.write_json(job_id, "input/request.json", request.model_dump(mode="json"))
    submit_queue("targeted-extraction", job_id)
    return record.model_dump(mode="json")


@app.post("/api/report-sessions/preview")
def preview_report_session(request: ReportExtractionRequest) -> dict:
    try:
        return report_sessions.preview(request)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/report-sessions", status_code=202)
def create_report_session(request: ReportExtractionRequest, enqueue: bool = Query(True), idempotency_key: str | None = Query(None, max_length=200)) -> dict:
    _require_runtime_ready()
    try:
        session = report_sessions.create(request, idempotency_key=idempotency_key)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if enqueue:
        submit_queue("report-session", session["session_id"], qiniu_api_key=request.qiniu_api_key, ocr_token=request.ocr_token)
    return session


@app.get("/api/report-sessions")
def list_report_sessions(limit: int = Query(100, ge=1, le=500), include_archived: bool = Query(False)) -> list[dict]:
    return [_session_with_lifecycle(session) for session in report_sessions.list(limit, include_archived=include_archived)]


@app.get("/api/report-sessions/{session_id}")
def get_report_session(session_id: str) -> dict:
    try:
        return _session_with_lifecycle(report_sessions.detail(session_id))
    except KeyError as exc:
        raise HTTPException(404, "Report session not found") from exc


@app.get("/api/report-sessions/{session_id}/foundation16")
def get_foundation16_result(session_id: str) -> dict:
    """The 16 foundation conclusions of a foundation16 session, with readable flags."""
    from esg_targeted.foundation.catalog import BY_CODE
    from esg_targeted.foundation.labels import DEFINITIONS_ZH, GROUP_LABELS, STATUS_LABELS, flag_label

    try:
        session = report_sessions.detail(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Report session not found") from exc
    root = artifact_store.report_session_dir(session_id) / "foundation16"
    result_path, plan_path = root / "result.json", root / "plan.json"
    if not result_path.is_file():
        return {"status": "running" if plan_path.is_file() else "not_started", "session_id": session_id,
                "plan": json.loads(plan_path.read_text()) if plan_path.is_file() else None,
                "execution_status": session.get("execution_status")}
    result = json.loads(result_path.read_text())
    from esg_targeted.foundation.engine import ENGINE_VERSION
    result["engine_version"] = result.get("engine_version") or ENGINE_VERSION
    result["stale_engine"] = result["engine_version"] != ENGINE_VERSION
    result["current_engine_version"] = ENGINE_VERSION
    by_code = {item["code"]: item for item in result["conclusions"]}
    for conclusion in result["conclusions"]:
        metric = BY_CODE[conclusion["code"]]
        children = [item for item in result["conclusions"] if item.get("parent_code") == metric.code]
        # Keep the authored child breakdown discovered by consolidation; the
        # catalog's own sub-clauses are added alongside it.
        stored_children = [item for item in conclusion.get("children") or [] if item.get("relation")]
        conclusion.update(parent_code=metric.parent_code,
                          children=[{"code": item["code"], "label": item.get("label"),
                                     "status": item["status"],
                                     "values": [{"member": m.get("member"), "value_raw": m.get("value_raw"),
                                                 "unit_raw": m.get("unit_raw")} for m in item.get("members") or []]}
                                    for item in children] + stored_children,
                          group=metric.group, group_label=GROUP_LABELS.get(metric.group, metric.group),
                          status_label=STATUS_LABELS.get(conclusion["status"], conclusion["status"]),
                          definition=metric.definition, definition_zh=DEFINITIONS_ZH.get(metric.code), kind=metric.kind)
        sources = list(conclusion.get("candidates") or [])
        for member in conclusion.get("members") or []:
            sources += [member, member["source"]]
            for key in ("agreeing_sources", "conflicting_sources", "alternative_unit_sources",
                        "related_subset_sources", "component_sources"):
                sources += member.get(key) or []
        for source in sources:
            source["flag_labels"] = [flag_label(flag) for flag in source.get("flags") or []]
    try:
        manifest = json.loads((ir_catalog.resolve(result["ir_run_id"]) / "manifest.json").read_text())
        coverage = manifest.get("page_coverage") or {}
        result["document_coverage"] = {"mode": coverage.get("mode", "all"),
                                       "covered_pages": manifest.get("page_count"),
                                       "source_pages": coverage.get("source_page_count") or manifest.get("page_count"),
                                       "text_source": (coverage.get("selector") or {}).get("text_source"),
                                       "reading": coverage.get("reading")}
    except (KeyError, OSError, ValueError):
        result["document_coverage"] = None
    return {"status": "available", "session_id": session_id, "execution_status": session.get("execution_status"),
            "result": result}


@app.get("/api/report-sessions/{session_id}/facts")
def get_report_facts(session_id: str) -> dict:
    try:
        return report_sessions.facts(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Report session not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/report-sessions/{session_id}/resume", status_code=202)
def resume_report_session(session_id: str, enqueue: bool = Query(True), request: RuntimeCredentials | None = Body(default=None)) -> dict:
    try:
        current = report_sessions.detail(session_id)
        session = current if current["execution_status"] == "queued" else report_sessions.resume(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Report session not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if enqueue:
        submit_queue("report-session", session_id, qiniu_api_key=request.qiniu_api_key if request else None,
            ocr_token=request.ocr_token if request else None)
    return session


@app.post("/api/report-sessions/{session_id}/cancel")
def cancel_report_session(session_id: str, terminal: bool = Query(False)) -> dict:
    try:
        result = report_sessions.cancel(session_id)
        if not terminal:
            cancel_existing(session_id)
        return result
    except KeyError as exc:
        raise HTTPException(404, "Report session not found") from exc


@app.post('/api/report-sessions/{session_id}/materialize')
def materialize_report_session(session_id: str, request: TerminalMaterializationRequest,
                               authorization: str | None = Header(default=None)) -> dict:
    actor = _materialization_operator(authorization)
    try:
        return report_sessions.materialize(session_id, reason=request.reason, requested_by=actor, job_ids=request.job_ids)
    except KeyError as exc:
        raise HTTPException(404, 'Report session not found') from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/report-sessions/{session_id}/evidence-transition", status_code=202)
def transition_report_evidence(session_id: str, request: EvidenceTransitionRequest, enqueue: bool = Query(True)) -> dict:
    try:
        session = report_sessions.transition(session_id, request)
    except KeyError as exc:
        raise HTTPException(404, "Report session not found") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if enqueue:
        submit_queue("report-session", session_id, qiniu_api_key=request.qiniu_api_key, ocr_token=request.ocr_token)
    return session


@app.get("/api/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=500), include_archived: bool = Query(False)) -> list[dict]:
    return [_job_with_lifecycle(item) for item in job_store.list(limit, include_archived=include_archived)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return _job_with_lifecycle(job_store.get(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


def _lifecycle_error(exc):
    if isinstance(exc, KeyError):
        return HTTPException(404, "生命周期对象不存在")
    return HTTPException(409, {"code":"lifecycle_blocked", "message":str(exc),
        "blockers":exc.blockers if isinstance(exc,LifecycleBlocked) else []})


@app.get("/api/jobs/{job_id}/lifecycle")
def job_lifecycle(job_id: str):
    try:
        return lifecycle.describe_job(job_id, check_references=True)
    except (KeyError,ValueError) as exc:
        raise _lifecycle_error(exc) from exc


def job_lifecycle_action(job_id: str, action: str, request: LifecycleCommand):
    if action not in {"archive","restore","discard"}:
        raise HTTPException(404,"生命周期操作不存在")
    try:
        return lifecycle.job(job_id,action,**request.model_dump())
    except (KeyError,ValueError) as exc:
        raise _lifecycle_error(exc) from exc


@app.get("/api/report-sessions/{session_id}/lifecycle")
def session_lifecycle(session_id: str):
    try:
        return lifecycle.describe_session(session_id, check_references=True)
    except (KeyError,ValueError) as exc:
        raise _lifecycle_error(exc) from exc


@app.get('/api/report-sessions/{session_id}/intermediates/plan', response_model=IntermediateCleanupPlan)
def intermediate_plan(session_id: str, authorization: str | None = Header(default=None)):
    _intermediate_operator(authorization)
    try:
        return intermediate_cleanup.plan(session_id)
    except (KeyError, ValueError, OSError) as exc:
        raise _lifecycle_error(exc) from exc


@app.get('/api/report-sessions/{session_id}/intermediates/receipts')
def intermediate_receipts(session_id: str, authorization: str | None = Header(default=None)):
    _intermediate_operator(authorization)
    try:
        return intermediate_cleanup.receipts(session_id)
    except (KeyError, ValueError, OSError) as exc:
        raise _lifecycle_error(exc) from exc


@app.post('/api/report-sessions/{session_id}/intermediates/execute')
def execute_intermediates(session_id: str, request: IntermediateExecuteRequest,
                          authorization: str | None = Header(default=None)):
    actor = _intermediate_operator(authorization, mutation=True)
    try:
        return intermediate_cleanup.execute(session_id, operator_ref=actor, **request.model_dump())
    except (KeyError, ValueError, OSError) as exc:
        raise _lifecycle_error(exc) from exc


@app.post('/api/report-sessions/{session_id}/intermediates/restore')
def restore_intermediates(session_id: str, request: IntermediateRestoreRequest,
                          authorization: str | None = Header(default=None)):
    actor = _intermediate_operator(authorization, mutation=True)
    try:
        return intermediate_cleanup.restore(session_id, operator_ref=actor, **request.model_dump())
    except (KeyError, ValueError, OSError) as exc:
        raise _lifecycle_error(exc) from exc


def session_lifecycle_action(session_id: str, action: str, request: LifecycleCommand):
    if action not in {"archive","restore","discard"}:
        raise HTTPException(404,"生命周期操作不存在")
    try:
        return lifecycle.session(session_id,action,**request.model_dump())
    except (KeyError,ValueError) as exc:
        raise _lifecycle_error(exc) from exc


def _register_lifecycle_actions():
    def job_endpoint(action):
        def endpoint(job_id: str, request: LifecycleCommand):
            return job_lifecycle_action(job_id,action,request)
        return endpoint
    def session_endpoint(action):
        def endpoint(session_id: str, request: LifecycleCommand):
            return session_lifecycle_action(session_id,action,request)
        return endpoint
    for action in ("archive","restore","discard"):
        app.add_api_route(f"/api/jobs/{{job_id}}/{action}",job_endpoint(action),methods=["POST"],name=f"job_{action}")
        app.add_api_route(f"/api/report-sessions/{{session_id}}/{action}",session_endpoint(action),methods=["POST"],name=f"session_{action}")


_register_lifecycle_actions()


@app.get("/api/lifecycle/trash")
def list_lifecycle_trash():
    return {"items":lifecycle.trash.list()}


@app.post("/api/lifecycle/trash/{cleanup_id}/restore")
def restore_lifecycle_trash(cleanup_id: str, request: LifecycleCommand):
    try:
        return lifecycle.restore_receipt(cleanup_id,reason=request.reason)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise _lifecycle_error(exc) from exc


@app.post("/api/lifecycle/trash/{cleanup_id}/purge")
def purge_lifecycle_trash(cleanup_id: str, request: LifecycleCommand):
    try:
        return lifecycle.trash.purge(cleanup_id,reason=request.reason)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise _lifecycle_error(exc) from exc


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    terminal: bool = Query(False),
    terminal_status: str = Query("cancelled", pattern="^(cancelled|interrupted|failed)$"),
) -> dict:
    try:
        record = job_store.request_cancel(job_id)
        if not terminal:
            cancel_existing(job_id)
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
def resume_job(job_id: str, request: RuntimeCredentials | None = Body(default=None)) -> dict:
    try:
        current = job_store.get(job_id)
        record = current if current.status == JobStatus.QUEUED else job_store.prepare_resume(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    submit_queue("targeted-extraction", job_id, qiniu_api_key=request.qiniu_api_key if request else None,
        ocr_token=request.ocr_token if request else None)
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
