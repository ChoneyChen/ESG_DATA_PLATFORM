from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from esg_runtime.lifecycle import LifecycleBlocked


class RecordDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def record_deletion_router(service, authorize):
    router = APIRouter()
    def endpoint(kind):
        def delete(identity: str, request: RecordDeleteRequest, response: Response, authorization: str | None = Header(default=None)):
            authorize(authorization, "admin")
            try:
                result = service.request(kind, identity, **request.model_dump(), authorization=authorization)
                if result.get("status") == "deletion_pending": response.status_code = 202
                return result
            except KeyError as exc:
                if kind == "session":
                    result = service.native_request("DELETE", f"/api/report-sessions/{identity}", request.model_dump(), authorization=authorization)
                    if result.get("status") == "deletion_pending": response.status_code = 202
                    return result
                raise HTTPException(404, "任务不存在，请刷新列表。") from exc
            except LifecycleBlocked as exc: raise HTTPException(409, {"code": "deletion_blocked", "message": str(exc), "blockers": exc.blockers}) from exc
            except ValueError as exc: raise HTTPException(409, {"code": "deletion_conflict", "message": str(exc)}) from exc
        return delete
    for kind, prefix in (("processing", "report-processing/runs"), ("session", "report-sessions"), ("queue", "pipeline/tasks")):
        router.add_api_route(f"/api/{prefix}/{{identity}}", endpoint(kind), methods=["DELETE"], name=f"delete_{kind}_record")
    @router.delete("/api/jobs/{identity}")
    def delete_native_job(identity: str, request: RecordDeleteRequest, response: Response, authorization: str | None = Header(default=None)):
        authorize(authorization, "admin")
        result = service.native_request("DELETE", f"/api/jobs/{identity}", request.model_dump(), authorization=authorization)
        if result.get("status") == "deletion_pending": response.status_code = 202
        return result
    return router
