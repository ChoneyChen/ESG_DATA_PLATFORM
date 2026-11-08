from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from urllib.parse import urlencode
from esg_contracts.results import FactPage, ResultOverview, OrganizedPage
from esg_contracts.sessions import ExtractionPlanRequest
from .monitor import MonitorSnapshot, monitor_snapshot
from .products import SessionProducts

class SessionSelection(BaseModel):
    session_ids: list[str] = Field(min_length=1,max_length=1000)


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

    @router.post("/api/report-sessions/archive")
    def archive_selected(request: SessionSelection):
        return call(coordinator.archive_many,request.session_ids)

    @router.post("/api/report-sessions/restore")
    def restore_selected(request: SessionSelection):
        return call(coordinator.archive_many,request.session_ids,False)

    @router.post("/api/report-sessions/purge")
    def purge_selected(request: SessionSelection):
        return call(coordinator.purge_many, request.session_ids)

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
    def runs(archived: bool = False):
        return call(coordinator.list,None,archived)

    @router.get("/api/report-sessions/{key}")
    def run(key: str):
        return call(coordinator.detail, call(coordinator.get, key))

    @router.post("/api/report-sessions/{key}/cancel")
    def cancel(key: str):
        return call(coordinator.cancel, key)

    @router.post("/api/report-sessions/{key}/resume")
    def resume(key: str):
        return call(coordinator.resume, key)

    def result_path(state, suffix, **filters):
        params = urlencode({key:value for key,value in filters.items() if value is not None})
        return f"/api/runs/{state['run_id']}/{suffix}" + ("?"+params if params else "")

    @router.get("/api/report-sessions/{key}/results",response_model=ResultOverview)
    def result_overview(key: str):
        state = call(coordinator.get,key)
        if not state.get("run_id"):
            return {"run_id":None,"revision":0,"clauses":[],"periods":{},"coverage":[],"summary":None}
        return call(coordinator.remote,"GET",result_path(state,"results"))

    @router.get("/api/report-sessions/{key}/facts",response_model=FactPage)
    def facts(key: str, limit: int = 50, cursor: str | None = None, clause_key: str | None = None,
              period: str | None = None, relation: str | None = None, unit: str | None = None,
              q: str | None = None, fact_id: str | None = None):
        state = call(coordinator.get,key)
        if not state.get("run_id"):
            return {"facts":[],"clause_mappings":[],"coverage":[],"total":0,"returned":0,
                "next_cursor":None,"query_digest":"pending","revision":0}
        return call(coordinator.remote,"GET",result_path(state,"facts",limit=limit,cursor=cursor,
            clause_key=clause_key,period=period,relation=relation,unit=unit,q=q,fact_id=fact_id))

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

    @router.get("/api/report-sessions/{key}/organized/{clause_key}",response_model=OrganizedPage)
    def organized(key: str, clause_key: str, limit: int = 50, cursor: str | None = None,
                  period: str | None = None, relation: str | None = None, unit: str | None = None, q: str | None = None):
        state = call(coordinator.get,key)
        if not state.get("run_id"):
            raise HTTPException(409,"文档准备完成后查看条款结果")
        return call(coordinator.remote,"GET",result_path(state,"organized/"+clause_key,
            limit=limit,cursor=cursor,period=period,relation=relation,unit=unit,q=q))

    @router.get("/api/pipeline/clause-groups")
    def groups():
        return call(coordinator.remote, "GET", "/api/clause-groups")

    @router.get("/api/pipeline/clause-selections")
    def selections():
        return call(coordinator.remote, "GET", "/api/clause-selections")

    return router
