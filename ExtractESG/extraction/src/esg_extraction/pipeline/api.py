"""Current extraction and independent clause API. No package/version routes."""

from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict
from esg_clauses.contract import ClauseContract
from esg_clauses.descriptor import ClauseDescriptor
from esg_clauses.catalog import ClauseCatalog, ClauseGroup
from esg_clauses.repository import ClauseRepository, ClauseRepositoryError
from esg_contracts.facts import RunRequest
from esg_data.organization import OrganizationSpec, OrganizedDataset
from esg_runtime.local_operator import install_loopback_marker
from .runtime import resources, engine_digest
from .engine import ENGINE_VERSION
from .result_store import ResultStore
from esg_contracts.sessions import NativeRun
from esg_contracts.results import FactPage, ResultOverview, OrganizedPage, CandidateRejection
from esg_contracts.facts import SourceFact
from typing import Literal
from contextlib import asynccontextmanager


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: RunRequest
    target_snapshot: dict | None = None
    submission_key: str | None = Field(default=None, min_length=1, max_length=160)
    expected_engine_fingerprint: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )


class SaveClause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clause: ClauseContract | ClauseDescriptor
    expected_digest: str | None = None


class ClauseTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clause_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    group_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    data_kind: Literal["quantitative", "qualitative"]
    title: str = Field(min_length=1, max_length=300)


class ExecutionTerminal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["failed", "interrupted"]
    error: str = Field(max_length=1000)


class ResumeRun(BaseModel):
    ir_run_id: str | None = None
    reason: str | None = None


class PurgeRuns(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=1000)


def create_app(resource_factory=resources):
    from .delivery import DeliveryOutbox
    outbox = DeliveryOutbox(resource_factory)
    @asynccontextmanager
    async def lifespan(app):
        outbox.start()
        yield
        outbox.stop()
    app = FastAPI(title="Clause extraction backbone", version="1.3.0", lifespan=lifespan)
    install_loopback_marker(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:18081", "http://localhost:18081"],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {k: error[k] for k in ("loc", "type", "msg") if k in error}
                    for error in exc.errors()
                ]
            },
        )

    @app.exception_handler(ClauseRepositoryError)
    async def clause_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=404
            if request.method == "GET" and str(exc).startswith("Unknown clause ")
            else 409,
            content={"detail": str(exc)},
        )

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=404, content={"detail": "Current resource does not exist"}
        )

    from esg_runtime.release import RuntimeRelease

    release = RuntimeRelease(None, "extraction-backend")
    started_engine = engine_digest()

    def require_current_engine():
        if release.snapshot()["restart_required"]:
            raise HTTPException(
                503,
                "Extraction service code changed; restart before creating a new task",
            )

    @app.get("/api/compatibility")
    def compatibility():
        body = release.snapshot()
        body["readiness"] = {
            "ready": not body["restart_required"],
            "blockers": ["Extraction service requires restart"]
            if body["restart_required"]
            else [],
        }
        return body

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "service": "clause-backbone",
            "engine_version": ENGINE_VERSION,
            "capabilities": {
                "independent_clauses": True,
                "raw_values_first": True,
                "data_contract": "extraction-data-2.0",
                "cross_clause_reading_reuse": False,
            },
        }

    @app.get("/api/engine")
    def engine():
        return {
            "version": ENGINE_VERSION,
            "fingerprint": started_engine,
            "ready": not release.snapshot()["restart_required"],
        }

    @app.get("/api/organization-contract")
    def organization_contract():
        return {
            "definition": OrganizationSpec.model_json_schema(),
            "dataset": OrganizedDataset.model_json_schema(),
            "facts_contract": "source-fact-2.0",
            "aggregation_performed": False,
        }

    @app.get("/api/clauses")
    def clauses() -> ClauseCatalog:
        body = resource_factory()[0].catalog()
        return ClauseCatalog(
            schema_version=body["schema_version"],
            clauses=[
                {k: v for k, v in row.items() if k != "path"} for row in body["clauses"]
            ],
        )

    @app.get("/api/clause-contract")
    def clause_contract():
        return ClauseContract.model_json_schema()

    @app.post("/api/clauses/template")
    def clause_template(request: ClauseTemplate):
        from esg_clauses.templates import new_clause

        return new_clause(
            request.clause_key, request.group_id, request.data_kind, request.title
        )

    @app.get("/api/clause-groups")
    def groups():
        return {
            "groups": [
                ClauseGroup.model_validate(row)
                for row in resource_factory()[0].groups()
            ]
        }

    @app.get("/api/clause-selections")
    def selections():
        return {"selections": resource_factory()[0].selections()}

    @app.get("/api/clauses/{key}")
    def clause(key: str):
        return resource_factory()[0].get(key)

    @app.get("/api/clauses/{key}/organization")
    def clause_organization(key: str):
        clause = resource_factory()[0].get(key)
        if isinstance(clause, ClauseDescriptor):
            raise HTTPException(409, "Structure descriptors have no value output; select independent member clauses")
        return {
            "clause_key": key,
            "clause_digest": clause.clause_digest,
            "data_kind": clause.data_kind,
            "organization": clause.organization,
            "relationships": clause.relationships,
            "relationships_are_execution_dependencies": False,
        }

    @app.get("/api/clauses/{key}/reading-card")
    def reading_card(key: str):
        clause = resource_factory()[0].get(key)
        if isinstance(clause, ClauseDescriptor):
            raise HTTPException(409, "Structure descriptors are not reading targets")
        return clause.reading_card()

    @app.post("/api/clause-selection-options")
    def selection_options(keys: list[str] = Body(..., min_length=1, max_length=2000)):
        return resource_factory()[0].selection_options(keys)

    @app.put("/api/clauses/{key}")
    def save(key: str, request: SaveClause):
        require_current_engine()
        if key != request.clause.clause_key:
            raise HTTPException(409, "Clause identity differs from its URL")
        return resource_factory()[0].save(
            request.clause, expected_digest=request.expected_digest
        )

    def selection(request, repo):
        return repo.select(request.clause_keys, selection=request.selection)

    @app.post("/api/targets/freeze")
    def freeze(request: RunRequest):
        require_current_engine()
        repo, *_ = resource_factory()
        return repo.freeze(selection(request, repo))

    @app.post("/api/runs/preview")
    def preview(request: RunRequest):
        require_current_engine()
        repo, store, artifacts, documents = resource_factory()
        targets = selection(request, repo)
        document = documents.document(request.ir_run_id)
        return {
            "engine_version": ENGINE_VERSION,
            "document": document,
            "targets": repo.freeze(targets),
            "clause_count": len(targets),
            "budget": request.budget,
            "calls_model": False,
        }

    @app.post("/api/runs", status_code=201, response_model=NativeRun)
    def create(request: CreateRun):
        require_current_engine()
        if (
            request.expected_engine_fingerprint
            and request.expected_engine_fingerprint != started_engine
        ):
            raise HTTPException(
                409,
                "Extraction engine changed after preview; reload before creating a task",
            )
        repo, store, artifacts, documents = resource_factory()
        targets = (
            repo.thaw(request.target_snapshot)
            if request.target_snapshot
            else selection(request.request, repo)
        )
        wanted = set(
            request.request.clause_keys
            or repo.selections().get(request.request.selection, [])
        )
        if set(targets) != wanted or any(
            c.readiness != "ready" for c in targets.values()
        ):
            raise HTTPException(
                409, "Frozen targets differ from selected independent clauses"
            )
        document = documents.document(request.request.ir_run_id)
        return store.create(
            request.request,
            repo.freeze(targets),
            document.model_dump(mode="json"),
            request.submission_key,
            engine_fingerprint=engine_digest(),
        )

    def run_view(state):
        return NativeRun.model_validate({**state,"resumable": state["status"] in {"cancelled","failed","interrupted"}
            and state.get("engine_fingerprint") == started_engine and not (state.get("delivery") or {}).get("export_id")})

    @app.get("/api/runs")
    def runs():
        store = resource_factory()[1]
        return {"runs":[run_view(store.observe(state["run_id"])) for state in store.list()]}

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> NativeRun:
        return run_view(resource_factory()[1].observe(run_id))

    @app.post("/api/runs/purge")
    def purge_runs(request: PurgeRuns):
        _, store, artifacts, _ = resource_factory()
        try:
            with outbox.lock:
                return store.purge(request.run_ids, artifacts.remove_run)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs/{run_id}/targets")
    def run_targets(run_id: str):
        snapshot = resource_factory()[1].get(run_id)["targets"]
        return snapshot

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> NativeRun:
        store = resource_factory()[1]
        state = store.get(run_id)
        if state["status"] not in {"queued", "running"}:
            raise HTTPException(409, "Run is already terminal")
        return store.update(
            run_id,
            cancel_requested=True,
            **({"status": "cancelled", "evidence_request":None} if state["status"] == "queued" or (state.get("evidence_request") and not state.get("worker_pid")) else {}),
        )

    @app.post("/api/runs/{run_id}/resume", response_model=NativeRun)
    def resume(run_id: str, request: ResumeRun):
        require_current_engine()
        _, store, _, documents = resource_factory()
        state = store.get(run_id)
        if state["status"] == "queued":
            return store.public(state)
        if state.get("engine_fingerprint") != started_engine:
            raise HTTPException(409, "The extraction engine changed; create a new extraction")
        if (state.get("delivery") or {}).get("export_id"):
            raise HTTPException(409, "This extraction is sealed; create a new extraction")
        need = state.get("evidence_request")
        if not need and state["status"] not in {"interrupted", "failed", "cancelled"}:
            raise HTTPException(409, "Extraction is already running")
        sources = dict(state.get("evidence_sources") or {})
        from esg_contracts.evidence import DocumentReference
        primary = DocumentReference.model_validate(state["document"])
        sources.setdefault(primary.snapshot_id, primary.model_dump(mode="json"))
        exhausted = dict(state.get("exhausted_queries") or {})
        if request.ir_run_id:
            additional = documents.document(request.ir_run_id)
            if additional.source_sha256 != primary.source_sha256:
                raise HTTPException(409, "Additional evidence belongs to a different PDF")
            sources[additional.snapshot_id] = additional.model_dump(mode="json")
        elif need:
            exhausted[need["request_id"]] = request.reason or "No additional pages were located"
        context_attempted = set(state.get("context_attempted",[]))
        if need and need["phase"] == "context":
            context_attempted.add(need["clause_key"])
        store.progress(run_id, current_call=None, progress={"stage":"queued", "current":0,
            "model_calls":(state.get("summary") or {}).get("model_calls",0),
            "total":len(state["targets"]["clauses"]), "fact_count":(state.get("summary") or {}).get("source_facts",0)})
        return store.update(run_id, status="queued", worker_pid=None, worker_started=None,
            evidence_request=None, evidence_sources=sources, exhausted_queries=exhausted, context_attempted=sorted(context_attempted),
            cancel_requested=False, error=None, finished_at=None)

    @app.post("/api/runs/{run_id}/execution-terminal")
    def execution_terminal(run_id: str, request: ExecutionTerminal) -> NativeRun:
        store = resource_factory()[1]
        state = store.get(run_id)
        if state["status"] not in {"queued", "running"}:
            return store.public(state)
        from datetime import datetime, timezone
        return store.update(run_id, status=request.status, error=request.error,
            finished_at=datetime.now(timezone.utc).isoformat())

    @app.get("/api/runs/{run_id}/facts/{fact_id}")
    def fact(run_id: str, fact_id: str):
        import re

        if not re.fullmatch("[a-f0-9]{64}", fact_id):
            raise HTTPException(404, "Unknown fact identity")
        repo, store, artifacts, documents = resource_factory()
        store.get(run_id)
        try:
            return SourceFact.model_validate(
                artifacts.read(run_id, f"facts/{fact_id}.json")
            )
        except FileNotFoundError:
            raise HTTPException(404, "Unknown fact identity")

    @app.get("/api/runs/{run_id}/results", response_model=ResultOverview)
    def result_overview(run_id: str):
        repo, store, artifacts, _ = resource_factory()
        state = store.get(run_id)
        result = ResultStore(run_id,artifacts)
        overview = result.overview()
        by_clause = {}
        for row in overview["clauses"]:
            by_clause.setdefault(row["clause"],{})[row["relation"]] = row["facts"]
        clauses = state["targets"]["clauses"]
        return {"run_id":run_id,"revision":overview["revision"],"periods":overview["periods"],
            "coverage":overview.get("coverage",[]),"summary":overview.get("summary"),
            "clauses":[{"clause_key":key,"title":clause["organization"]["title"],"data_kind":clause["data_kind"],
                "relations":by_clause.get(key,{}),"facts":sum(by_clause.get(key,{}).values())} for key,clause in clauses.items()]}

    @app.get("/api/runs/{run_id}/facts", response_model=FactPage)
    def facts(run_id: str, limit: int = Query(50,ge=1,le=500), cursor: str | None = None,
              clause_key: str | None = None, period: str | None = None, relation: str | None = None,
              unit: str | None = None, q: str | None = None, fact_id: str | None = None):
        _, store, artifacts, _ = resource_factory()
        store.get(run_id)
        try:
            page = ResultStore(run_id,artifacts).page(limit=limit,cursor=cursor,clause_key=clause_key,
                period=period,relation=relation,unit=unit,q=q,fact_id=fact_id)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        return {**page,"coverage":[]}

    @app.get("/api/runs/{run_id}/organized/{clause_key}", response_model=OrganizedPage)
    def organized(run_id: str, clause_key: str, limit: int = Query(50,ge=1,le=500),
                  cursor: str | None = None, period: str | None = None, relation: str | None = None,
                  unit: str | None = None, q: str | None = None):
        from .organization import build_organized_dataset
        from esg_contracts.evidence import DocumentReference
        repo, store, artifacts, _ = resource_factory()
        state = store.get(run_id)
        clauses = state["targets"]["clauses"]
        if clause_key not in clauses:
            raise HTTPException(404,"Clause was not selected in this extraction")
        result = ResultStore(run_id,artifacts)
        try:
            page = result.page(limit=limit,cursor=cursor,clause_key=clause_key,period=period,relation=relation,unit=unit,q=q)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        if clauses[clause_key]["schema_version"] == "clause-contract-1.3":
            table = result.frozen_table(clause_key,[fact["source_fact_id"] for fact in page["facts"]])
        else:
            table = build_organized_dataset(run_id,DocumentReference.model_validate(state["document"]),
                {clause_key:ClauseContract.model_validate(clauses[clause_key])},page["facts"],page["clause_mappings"]).tables[0]
        return {**page,"table":table,"coverage":[],**result.facets(clause_key=clause_key)}

    @app.get("/api/runs/{run_id}/candidates", response_model=list[CandidateRejection])
    def candidates(run_id: str):
        _, store, artifacts, _ = resource_factory()
        store.get(run_id)
        try:
            return artifacts.read(run_id, "diagnostics/candidates.json")
        except FileNotFoundError:
            return []

    @app.post("/api/runs/{run_id}/exports")
    def export(run_id: str):
        from .exporting import seal_export
        _, store, artifacts, _ = resource_factory()
        try:
            return seal_export(run_id, store, artifacts)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs/{run_id}/exports/{export_id}")
    def download_export(run_id: str, export_id: str):
        from esg_contracts.exports import ExportManifest
        from .exporting import export_archive
        from fastapi.responses import Response
        _, store, artifacts, _ = resource_factory()
        store.get(run_id)
        if not __import__("re").fullmatch(r"export-[a-f0-9]{64}", export_id):
            raise HTTPException(404, "Export does not exist")
        try:
            manifest = ExportManifest.model_validate(artifacts.read(run_id, f"exports/{export_id}/manifest.json"))
            return Response(export_archive(run_id, manifest, artifacts), media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{export_id}.zip"'})
        except FileNotFoundError:
            raise HTTPException(404, "Export does not exist")

    return app


app = create_app()
