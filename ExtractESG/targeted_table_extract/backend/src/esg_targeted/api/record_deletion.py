from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from esg_runtime.lifecycle import LifecycleBlocked


class ExtractionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def extraction_deletion_router(service, authorize):
    router = APIRouter()
    def description(kind):
        def get(identity: str, authorization: str | None = Header(default=None)):
            authorize(authorization)
            try: return service.describe(kind, identity)
            except KeyError as exc: raise HTTPException(404, "提取任务不存在。") from exc
            except LifecycleBlocked as exc: raise HTTPException(409, {"code": "deletion_blocked", "message": str(exc), "blockers": exc.blockers}) from exc
        return get
    def command(kind):
        def delete(identity: str, request: ExtractionDeleteRequest, response: Response, authorization: str | None = Header(default=None)):
            authorize(authorization)
            try:
                result = service.request(kind, identity, **request.model_dump())
                if result.get("status") == "deletion_pending": response.status_code = 202
                return result
            except KeyError as exc: raise HTTPException(404, "提取任务不存在。") from exc
            except LifecycleBlocked as exc: raise HTTPException(409, {"code": "deletion_blocked", "message": str(exc), "blockers": exc.blockers}) from exc
            except ValueError as exc: raise HTTPException(409, {"code": "deletion_conflict", "message": str(exc)}) from exc
            except OSError as exc: raise HTTPException(500, {"code": "deletion_incomplete", "message": "执行记录删除未完整完成，请刷新核对。"}) from exc
        return delete
    for kind, prefix in (("session", "report-sessions"), ("job", "jobs")):
        router.add_api_route(f"/api/{prefix}/{{identity}}/deletion", description(kind), methods=["GET"], name=f"{kind}_deletion_description")
        router.add_api_route(f"/api/{prefix}/{{identity}}", command(kind), methods=["DELETE"], name=f"{kind}_permanent_delete")
    return router
