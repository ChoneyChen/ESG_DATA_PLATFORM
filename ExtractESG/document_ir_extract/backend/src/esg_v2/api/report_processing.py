"""Report-processing routes, separate from extraction and service readiness."""
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from esg_v2.control_plane.report_processing import (
    EvidenceTransitionRequest, ProcessingActionRequest, ProcessingRequest,
    ReportMetadataPatch, ReportVersionRequest,
)
from esg_v2.document.reader import DocumentIrPackageReader


class RegisterReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=160)


def processing_router(coordinator, require_ready=None):
    router = APIRouter()

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "报告或处理任务不存在") from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "报告来源或处理成果不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, {"code": "report_processing_conflict", "message": str(exc),
                "layer": "document_processing", "retryable": True, "artifacts_retained": True}) from exc

    def preflight():
        if require_ready:
            require_ready()

    @router.get("/api/reports")
    def reports():
        return {"schema_version": "report-registry-v1", "reports": coordinator.reports()}

    @router.post("/api/reports", status_code=201)
    def register(request: RegisterReportRequest):
        return call(coordinator.register_asset, call(coordinator.assets.get, request.asset_id))

    @router.get("/api/reports/{report_id}")
    def report(report_id: str):
        return call(coordinator.report, report_id)

    @router.patch("/api/reports/{report_id}")
    def update_report(report_id: str, request: ReportMetadataPatch):
        return call(coordinator.update_metadata, report_id, request)

    @router.post("/api/reports/{report_id}/versions", status_code=201)
    def attach_version(report_id: str, request: ReportVersionRequest):
        return call(coordinator.register_asset, call(coordinator.assets.get, request.asset_id), report_id, request.expected_revision)

    @router.get("/api/report-processing/runs")
    def runs(include_archived: bool = False):
        return {"schema_version": "report-processing-v1", "runs": [coordinator.detail(s) for s in coordinator.list(include_archived)]}

    @router.post("/api/report-processing/runs", status_code=202)
    def create(request: ProcessingRequest):
        preflight()
        return coordinator.detail(call(coordinator.create, request))

    @router.get("/api/report-processing/runs/{processing_id}")
    def run(processing_id: str):
        return coordinator.detail(call(coordinator.get, processing_id))

    @router.get("/api/report-processing/runs/{processing_id}/events")
    def events(processing_id: str, after: int = Query(default=0, ge=0)):
        return {"events": call(coordinator.events, processing_id, after)}

    def evidence_reader(processing_id):
        state = call(coordinator.get, processing_id)
        if not state.get("ir_run_id"):
            raise HTTPException(409, "结构证据尚未产生")
        manifest, _ = call(coordinator._manifest, "ir", state["ir_run_id"])
        call(coordinator._verify_manifest, state, "ir", manifest)
        return DocumentIrPackageReader(coordinator.roots["ir"] / state["ir_run_id"])

    @router.get("/api/report-processing/runs/{processing_id}/pages")
    def pages(processing_id: str):
        return call(evidence_reader(processing_id).list_pages)

    @router.get("/api/report-processing/runs/{processing_id}/pages/{page_index}")
    def page(processing_id: str, page_index: int):
        return call(evidence_reader(processing_id).page, page_index)

    @router.post("/api/report-processing/runs/{processing_id}/cancel")
    def cancel(processing_id: str, request: ProcessingActionRequest | None = Body(default=None)):
        return coordinator.detail(call(coordinator.action, processing_id, "cancel", request))

    @router.post("/api/report-processing/runs/{processing_id}/resume", status_code=202)
    def resume(processing_id: str, request: ProcessingActionRequest | None = Body(default=None)):
        preflight()
        return coordinator.detail(call(coordinator.action, processing_id, "resume", request))

    @router.post("/api/report-processing/runs/{processing_id}/evidence")
    def transition(processing_id: str, request: EvidenceTransitionRequest):
        return coordinator.detail(call(coordinator.transition_evidence, processing_id, request))

    @router.get("/api/report-processing/runs/{processing_id}/evidence-versions")
    def versions(processing_id: str):
        return {"versions": call(coordinator.evidence_versions, processing_id)}

    return router
