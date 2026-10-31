from fastapi import APIRouter, HTTPException
from esg_contracts.sessions import ExtractionPlanRequest
from .monitor import MonitorSnapshot, monitor_snapshot
from .products import SessionProducts


def extraction_router(coordinator, require_ready=lambda: None,products=None):
    router = APIRouter()

    def call(fn, *args):
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, "Current extraction does not exist") from exc
        except ConnectionError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/console/monitor", response_model=MonitorSnapshot)
    def monitor():
        return monitor_snapshot(coordinator.list(terminal_limit=3),products)

    @router.get("/api/report-sessions/{key}/products",response_model=SessionProducts)
    def product_state(key: str):
        state=call(coordinator.detail,call(coordinator.get,key))
        return products.for_session(state) if products else SessionProducts(session_id=key,observation="unavailable",stage_label="数据服务未配置")

    @router.post("/api/report-sessions/{key}/archive")
    def archive(key: str):
        return call(coordinator.archive, key)

    @router.post("/api/report-sessions/{key}/restore")
    def restore(key: str):
        return call(coordinator.restore, key)

    @router.get("/api/report-sessions/{key}/candidates")
    def candidates(key: str):
        state = call(coordinator.get, key)
        if not state.get("run_id"):
            return []
        return call(coordinator.remote, "GET", f"/api/runs/{state['run_id']}/candidates")

    @router.post("/api/report-sessions/preview")
    def preview(request: ExtractionPlanRequest):
        require_ready()
        return call(coordinator.preview, request)

    @router.post("/api/report-sessions", status_code=202)
    def create(request: ExtractionPlanRequest):
        require_ready()
        return call(coordinator.create, request)

    @router.get("/api/report-sessions")
    def runs():
        return call(coordinator.list)

    @router.get("/api/report-sessions/{key}")
    def run(key: str):
        return call(coordinator.detail, call(coordinator.get, key))

    @router.post("/api/report-sessions/{key}/cancel")
    def cancel(key: str):
        return call(coordinator.cancel, key)

    @router.get("/api/report-sessions/{key}/facts")
    def facts(key: str):
        state = call(coordinator.get, key)
        if not state.get("run_id"):
            return {"facts": [], "clause_mappings": [], "coverage": []}
        return call(coordinator.remote, "GET", f"/api/runs/{state['run_id']}/facts")

    @router.get("/api/pipeline/clauses")
    def clauses():
        return call(coordinator.remote, "GET", "/api/clauses")

    @router.get("/api/pipeline/clauses/{key}")
    def clause(key: str):
        return call(coordinator.remote, "GET", f"/api/clauses/{key}")

    @router.get("/api/pipeline/clauses/{key}/organization")
    def organization(key: str):
        return call(coordinator.remote, "GET", f"/api/clauses/{key}/organization")

    @router.get("/api/pipeline/organization-contract")
    def organization_contract():
        return call(coordinator.remote, "GET", "/api/organization-contract")

    @router.get("/api/report-sessions/{key}/organized")
    def organized(key: str):
        state = call(coordinator.get, key)
        if not state.get("run_id"):
            return {
                "schema_version": "organized-extraction-1.0",
                "status": "pending",
                "tables": [],
            }
        return call(coordinator.remote, "GET", f"/api/runs/{state['run_id']}/organized")

    @router.get("/api/pipeline/clause-groups")
    def groups():
        return call(coordinator.remote, "GET", "/api/clause-groups")

    @router.get("/api/pipeline/clause-selections")
    def selections():
        return call(coordinator.remote, "GET", "/api/clause-selections")

    return router
