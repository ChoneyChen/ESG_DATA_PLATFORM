"""Current extraction and independent clause API. No package/version routes."""

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict
from esg_clauses.contract import ClauseContract
from esg_clauses.catalog import ClauseCatalog, ClauseGroup
from esg_clauses.repository import ClauseRepository, ClauseRepositoryError
from esg_contracts.facts import RunRequest
from esg_data.organization import OrganizationSpec, OrganizedDataset
from esg_runtime.local_operator import install_loopback_marker
from .runtime import resources, engine_digest
from .engine import ENGINE_VERSION
from esg_contracts.sessions import NativeRun
from esg_contracts.results import FactBundle, FactCollection, CandidateRejection
from esg_contracts.facts import SourceFact
from typing import Literal


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
    clause: ClauseContract
    expected_digest: str | None = None


class ClauseTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clause_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    group_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    data_kind: Literal["quantitative", "qualitative"]
    title: str = Field(min_length=1, max_length=300)


def create_app(resource_factory=resources):
    app = FastAPI(title="Clause extraction backbone", version="1.1.0")
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
    from esg_runtime.layout import EXTRACT_ROOT

    release = RuntimeRelease(EXTRACT_ROOT, "extraction-backend")
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

    @app.post("/api/clauses/validate")
    def validate_clause(clause: ClauseContract):
        return {
            "valid": True,
            "scientific_acceptance": False,
            "clause": clause.seal().verify(),
        }

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
        return resource_factory()[0].get(key).reading_card()

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

    @app.get("/api/runs")
    def runs():
        store = resource_factory()[1]
        return {
            "runs": [
                NativeRun.model_validate(store.observe(state["run_id"]))
                for state in store.list()
            ]
        }

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> NativeRun:
        return resource_factory()[1].observe(run_id)

    @app.get("/api/runs/{run_id}/targets")
    def run_targets(run_id: str):
        snapshot = resource_factory()[1].get(run_id)["targets"]
        ClauseRepository.thaw(snapshot)
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
            **({"status": "cancelled"} if state["status"] == "queued" else {}),
        )

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

    @app.get("/api/runs/{run_id}/facts")
    def facts(run_id: str):
        repo, store, artifacts, documents = resource_factory()
        store.get(run_id)
        try:
            return FactBundle.model_validate(
                artifacts.read(run_id, "results/facts.json")
            )
        except FileNotFoundError:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=202,
                content=FactCollection(
                    facts=[], clause_mappings=[], coverage=[]
                ).model_dump(mode="json"),
            )

    @app.get("/api/runs/{run_id}/organized")
    def organized(run_id: str):
        repo, store, artifacts, documents = resource_factory()
        state = store.get(run_id)
        try:
            return artifacts.read(run_id, "results/organized.json")
        except FileNotFoundError:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=202,
                content={
                    "schema_version": "organized-extraction-1.0",
                    "run_id": run_id,
                    "status": "pending"
                    if state["status"] in {"queued", "running"}
                    else "unavailable",
                    "tables": [],
                },
            )

    @app.get("/api/runs/{run_id}/candidates", response_model=list[CandidateRejection])
    def candidates(run_id: str):
        _, store, artifacts, _ = resource_factory()
        store.get(run_id)
        try:
            return artifacts.read(run_id, "diagnostics/candidates.json")
        except FileNotFoundError:
            return []

    return app


app = create_app()
