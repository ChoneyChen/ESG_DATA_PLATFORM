"""Authenticated batch production controls, separate from ordinary report APIs."""
from fastapi import APIRouter, Header, HTTPException, Query

from esg_v2.control_plane.production_automation import (
    AutomationRequest, AutomationCreateRequest, AutomationAction, AutomationOrder,
)


def production_automation_router(coordinator):
    router = APIRouter(prefix="/api/production-automations", tags=["批量自动生产"])

    def call(fn, *args):
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, "自动化计划或报告来源不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, {"code": "production_automation_conflict", "message": str(exc),
                "layer": "production_automation", "artifacts_retained": True}) from exc

    def read(authorization):
        call(coordinator._principal, authorization, "read")

    @router.get("")
    def plans(include_archived: bool = False, authorization: str | None = Header(default=None)):
        read(authorization)
        return {"schema_version": "production-automation-v1", "plans": [coordinator.detail(s) for s in coordinator.list(include_archived)]}

    @router.post("/preview")
    def preview(request: AutomationRequest, authorization: str | None = Header(default=None)):
        read(authorization)
        return call(coordinator.preview, request)

    @router.post("", status_code=202)
    def create(request: AutomationCreateRequest, authorization: str | None = Header(default=None)):
        return call(coordinator.create, request, authorization)

    @router.get("/{automation_id}")
    def detail(automation_id: str, authorization: str | None = Header(default=None)):
        read(authorization)
        return coordinator.detail(call(coordinator.get, automation_id))

    @router.get("/{automation_id}/events")
    def events(automation_id: str, after: int = Query(default=0, ge=0), authorization: str | None = Header(default=None)):
        read(authorization)
        return {"events": call(coordinator.events, automation_id, after)}

    @router.post("/{automation_id}/order")
    def order(automation_id: str, request: AutomationOrder, authorization: str | None = Header(default=None)):
        return call(coordinator.reorder, automation_id, request, authorization)

    def register(action):
        def operate(automation_id: str, request: AutomationAction, authorization: str | None = Header(default=None)):
            return call(coordinator.action, automation_id, action, request, authorization)
        router.add_api_route("/{automation_id}/" + action, operate, methods=["POST"], name="automation_" + action)

    for action in ("start", "pause", "resume", "cancel", "retry_failed", "archive", "restore"):
        register(action)
    return router
