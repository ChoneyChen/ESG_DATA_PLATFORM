from __future__ import annotations

import asyncio
import csv
import hmac
import hashlib
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bridge import DatabaseBridge, DatabaseError
from .runtime import release
from .public_pdf import published_pdf
from .evidence_location import candidate_locations
from .extension_control import ExtensionControl
from esg_standard_packages.extensions import ExtensionProposal

ROOT = Path(__file__).resolve().parents[1]
Text = Annotated[str, Field(min_length=1, max_length=240)]
DatasetVersion = Annotated[int | None, Query(ge=0)]
logger = logging.getLogger("platesg")


class APIPrefixMiddleware:
    """Same API URL works behind Vite and when FastAPI serves the built SPA."""
    def __init__(self, app):
        self.app=app

    async def __call__(self, scope, receive, send):
        if scope["type"]=="http" and scope.get("path","").startswith("/api/"):
            scope={**scope,"path":scope["path"][4:],"raw_path":scope.get("raw_path",b"")[4:]}
        await self.app(scope,receive,send)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DeliveryReportIdentity(StrictRequest):
    upstream_system: Text
    organization_ref: Text
    report_ref: Text
    report_version_ref: Text
    source_artifact_ref: Text
    source_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    report_period_start: date
    report_period_end: date

    @model_validator(mode='after')
    def dates(self):
        if self.report_period_end < self.report_period_start:
            raise ValueError('报告期结束不能早于开始')
        return self


class DeliveryFixedSource(StrictRequest):
    document_id: Text
    ir_run_id: Text
    ir_revision: int = Field(strict=True, ge=1)


class DeliveryArtifactIdentity(StrictRequest):
    job_id: Annotated[str, Field(pattern=r'^tx-[a-zA-Z0-9._-]{1,150}$')]
    artifact_revision_id: Annotated[str, Field(pattern=r'^(original|partial-[a-f0-9]{32})$')]
    bundle_path: Annotated[str, Field(min_length=1, max_length=4096)]
    manifest_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class DeliveryStatusQuery(StrictRequest):
    native_session_id: Annotated[str, Field(pattern=r'^report-[a-f0-9]{32}$')]
    report_identity: DeliveryReportIdentity
    fixed_source: DeliveryFixedSource
    job_ids: list[Annotated[str, Field(pattern=r'^tx-[a-zA-Z0-9._-]{1,150}$')]] = Field(min_length=1, max_length=100)
    artifacts: list[DeliveryArtifactIdentity] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def scope(self):
        if len(set(self.job_ids)) != len(self.job_ids):
            raise ValueError('固定子任务不能重复')
        artifact_jobs = [a.job_id for a in self.artifacts]
        if len(set(artifact_jobs)) != len(artifact_jobs) or any(j not in self.job_ids for j in artifact_jobs):
            raise ValueError('当前成果须唯一属于固定子任务')
        if any(not Path(a.bundle_path).is_absolute() for a in self.artifacts):
            raise ValueError('成果目录须由服务端提供受控绝对路径')
        return self


DeliveryStatus = Literal['source_archived', 'candidates_imported', 'partial', 'not_imported', 'no_artifact',
    'committed_without_acknowledgement', 'failed', 'unknown', 'identity_mismatch', 'artifact_unavailable']
DeliveryBasis = Literal['verified_descriptor', 'sql_batch_stage_match', 'unknown']


class DeliveryCounts(BaseModel):
    archived_source_count: int | None = Field(strict=True, ge=0)
    selected_candidate_count: int | None = Field(strict=True, ge=0)
    internal_candidate_count: int | None = Field(strict=True, ge=0)
    published_observation_count: int | None = Field(strict=True, ge=0)


class DeliveryReason(BaseModel):
    code: str
    label_zh: str


class DeliveryReceipt(BaseModel):
    receipt_id: str
    current_status: Literal['imported', 'failed']
    attempt_id: str | None = None
    identity_basis: DeliveryBasis = 'unknown'


class DeliveryArtifactStatus(BaseModel):
    artifact_revision_id: str
    status: DeliveryStatus
    identity_verified: bool
    identity_basis: DeliveryBasis
    import_batch_id: str | None
    bundle_digest: str | None
    receipt: DeliveryReceipt | None
    counts: DeliveryCounts
    reason_codes: list[str]
    reason_messages: list[DeliveryReason] = Field(default_factory=list)


class DeliveryJobStatus(BaseModel):
    job_id: str
    status: DeliveryStatus
    identity_basis: DeliveryBasis
    counts: DeliveryCounts
    artifacts: list[DeliveryArtifactStatus]
    reason_codes: list[str]
    reason_messages: list[DeliveryReason] = Field(default_factory=list)


class DeliveryStatusResponse(BaseModel):
    schema_version: Literal['delivery-status-v1']
    status: DeliveryStatus
    checked_at: str
    native_session_id: str
    report_ref: str
    report_counts_scope: Literal['fixed_report_version']
    identity_basis: DeliveryBasis
    publication_dataset_version: int | None = Field(strict=True, ge=0)
    report_counts: DeliveryCounts
    session_counts: DeliveryCounts
    current_revision_counts: DeliveryCounts
    jobs: list[DeliveryJobStatus]
    reason_codes: list[str]
    reason_messages: list[DeliveryReason] = Field(default_factory=list)


class EntityRequest(StrictRequest):
    entity_ref: Text
    name_zh: Text
    entity_kind: Literal["group", "listed_company", "subsidiary", "other"]
    industry_code: Text
    country_code: Annotated[str, Field(min_length=2, max_length=3)]
    is_demo: bool = False
    metadata: dict = Field(default_factory=dict)


class ReviewRequest(StrictRequest):
    disclosure_id: UUID | None = None
    mapping_id: UUID | None = None
    decision: Literal["approved", "rejected", "review_required"]
    operator_ref: Text | None = None
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    mapping_relation: Literal["exact", "broader", "narrower", "partial", "conditional", "alternative_measure", "not_equivalent"] | None = None
    satisfaction_status: Literal["supports", "partially_supports", "not_sufficient", "not_applicable", "unknown"] | None = None

    @model_validator(mode="after")
    def target(self):
        if bool(self.disclosure_id) == bool(self.mapping_id):
            raise ValueError("Review exactly one fact or one mapping")
        if self.disclosure_id and (self.mapping_relation or self.satisfaction_status):
            raise ValueError("Mapping decisions belong to a mapping review")
        if self.mapping_id and self.decision == "approved" and (not self.mapping_relation or not self.satisfaction_status):
            raise ValueError("Approved mapping requires an explicit equivalence and satisfaction decision")
        return self


class PropertySelectionRequest(StrictRequest):
    source_field: Text
    ordinal: Annotated[int, Field(ge=1, le=1000)] = 1
    label_zh: Annotated[str, Field(min_length=1, max_length=160)]


class ContextRequest(StrictRequest):
    mapping_id: UUID
    entity_ref: Text
    period_start: date
    period_end: date
    boundary_code: Text
    value_role: Literal["actual", "restated", "context"] = "actual"
    measurement_method: Text | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    property_selections: Annotated[list[PropertySelectionRequest], Field(max_length=64)] = Field(default_factory=list)
    reported_unit: Text | None = None
    operator_ref: Text | None = None
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    supersedes_observation_id: UUID | None = None

    @model_validator(mode="after")
    def interval(self):
        if self.period_end < self.period_start:
            raise ValueError("Reporting period ends before it starts")
        return self


class ReportMetadata(StrictRequest):
    upstream_system: Text
    organization_ref: Text
    report_ref: Text
    report_version_ref: Text
    source_artifact_ref: Text
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]
    report_title: Text
    language_code: Text = "zh"
    report_period_start: date | None = None
    report_period_end: date | None = None
    publication_date: date | None = None


class ImportRequest(StrictRequest):
    bundle_path: Annotated[str, Field(min_length=1, max_length=4096)]
    report_metadata: ReportMetadata
    selection_snapshot: dict | None = None


class CompareRequest(StrictRequest):
    metric_id: Text
    entity_refs: list[Text] = Field(min_length=1, max_length=100)
    dataset_version: int | None = Field(default=None, ge=0)
    period_start: date | None = None
    period_end: date | None = None
    boundary_code: Text | None = None
    measurement_method: Text | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def entities_unique(self):
        if len(set(self.entity_refs)) != len(self.entity_refs):
            raise ValueError("Duplicate entity in comparison request")
        return self


class CoverageRequest(StrictRequest):
    entity_ref: Text
    source_datapoint_id: Text
    status: Literal["not_selected", "not_extracted", "source_unreadable", "not_disclosed_in_reviewed_scope", "not_applicable", "review_required"]
    period_start: date
    period_end: date
    evidence_scope: dict
    operator_ref: Text | None = None
    reason: Annotated[str, Field(min_length=1, max_length=4000)]


class RetryRequest(StrictRequest):
    operator_ref: Text | None = None
    reason: Annotated[str, Field(min_length=1, max_length=4000)]


class ImportCatalogRegistrationRequest(StrictRequest):
    contract_id: Text
    expected_release_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    reason: Annotated[str, Field(min_length=1, max_length=4000)]


class ExtensionDecisionRequest(StrictRequest):
    proposal_id: Text
    decision: Literal['approved','rejected']
    reason: Annotated[str, Field(min_length=1, max_length=4000)]


class ExtensionRegistrationRequest(StrictRequest):
    proposal_id: Text
    approval_id: Text


class ExtensionPlatformSelection(StrictRequest):
    dictionary_version: Annotated[str, Field(pattern=r'^\d+\.\d+\.\d+$')]
    extension_ids: list[Text] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.extension_ids)) != len(self.extension_ids):
            raise ValueError('Duplicate platform extension selection')
        return self


class DerivationOperandRequest(StrictRequest):
    operand_id: Text
    observation_id: UUID
    context_id: UUID
    fact_review_decision_id: UUID
    mapping_review_decision_id: UUID


class ExtensionDerivationRequest(StrictRequest):
    idempotency_key: Text
    input_dataset_version: int = Field(ge=0)
    extension_id: Text
    definition_version: Annotated[str, Field(pattern=r'^\d+\.\d+\.\d+$')]
    release_digest: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    rule_id: Text
    target_metric_id: Text
    target_definition_version: Annotated[str, Field(pattern=r'^\d+\.\d+\.\d+$')]
    operands: list[DerivationOperandRequest] = Field(min_length=2, max_length=32)


class CalculationReviewRequest(StrictRequest):
    candidate_id: UUID
    decision: Literal['approved','rejected','review_required']
    reason: Annotated[str, Field(min_length=1,max_length=4000)]


class LifecycleScope(StrictRequest):
    job_ids: list[Text] = Field(default_factory=list, max_length=1000)
    ir_run_ids: list[Text] = Field(default_factory=list, max_length=1000)
    source_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-fA-F]{64}$")] = None
    source_sha256s: list[Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def identity(self):
        if not self.job_ids and not self.ir_run_ids and not self.source_sha256 and not self.source_sha256s:
            raise ValueError("来源引用检查需要确定身份")
        return self


class LifecycleReservationRequest(LifecycleScope):
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    reservation_id: UUID | None = None
    local_files_only: bool = False


class LifecycleReservationUpdate(StrictRequest):
    state: Literal["quarantined", "released", "purged"]
    reason: Annotated[str, Field(min_length=1, max_length=4000)]


class PropertyEvidenceResponse(BaseModel):
    page_number: int | None = None
    source_quote: str | None = None
    bbox: dict | None = None


class ReportedPropertyResponse(BaseModel):
    field_key: str
    label_zh: str
    value_raw: str | list[str]
    value_type: Literal["text", "number", "boolean", "text_array"]
    unit_raw: str | None = None
    evidence_scope: Literal["field", "source"]
    evidence: list[PropertyEvidenceResponse] = Field(default_factory=list)
    report_title: str | None = None
    source_role: str | None = None
    source_label: str | None = None


class Observation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    observation_id: str
    entity_ref: str
    metric_id: str
    source_datapoint_id: str | None = None
    clause_kind: Literal['original_datapoint','project_extension'] = 'original_datapoint'
    extension_id: str | None = None
    related_source_datapoint_ids: list[str] = Field(default_factory=list)
    value_origin: Literal['reported','derived'] = 'reported'
    definition_version: str
    period_start: str
    period_end: str
    boundary_code: str
    value_role: str
    measurement_method: str | None = None
    dimensions: dict
    reported_properties: list[ReportedPropertyResponse] = Field(default_factory=list)
    canonical_unit: str | None = None
    value_numeric: str | None = None
    lower_bound: str | None = None
    upper_bound: str | None = None
    statement_text: str | None = None
    comparison_eligibility: str
    comparison_reasons: list[str]
    publication_status: Literal["published"]
    dataset_version: int
    entity_name: str | None = None
    entity_kind: str | None = None
    is_demo: bool = False
    label_zh: str | None = None
    topic: str | None = None
    precision_qualifier: str | None = None
    raw_value_text: str | None = None
    raw_unit: str | None = None
    source_count: int = 0
    published_at: str | None = None


class ObservationDetail(Observation):
    sources: list[dict] = Field(default_factory=list)
    report: dict | None = None
    public_revisions: list[dict] = Field(default_factory=list)
    calculation: dict | None = None


class ObservationList(BaseModel):
    model_config = ConfigDict(extra="ignore")
    dataset_version: int
    dictionary_version: str
    items: list[Observation]
    next_cursor: str | None = None
    as_of: str | None = None


class ComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    dataset_version: int
    dictionary_version: str
    contract_version: str
    metric_id: str
    rows: list[Observation]
    excluded: list[dict]
    available_cohorts: list[dict]
    equivalent_alternatives: list[dict] = Field(default_factory=list)
    source_datapoint_id: str | None = None


def create_app(environment: dict[str, str] | None = None):
    config = {**os.environ, **(environment or {})}
    bridge = DatabaseBridge(config)
    extensions = ExtensionControl(bridge, standards_root=ROOT.parent/'ExtractESG/standards',
        runtime_root=Path(config.get('ESG_EXTENSION_ROOT',str(ROOT.parent/'ExtractESG/.local/extensions'))))
    import_roots=[Path(value) for value in config.get('PLATESG_IMPORT_ROOTS',os.pathsep.join([str(ROOT.parent/'ExtractESG'),str(ROOT/'.local')])).split(os.pathsep) if value]
    running_release=release()
    token = config.get("PLATESG_REVIEW_TOKEN")
    operators = json.loads(config.get("PLATESG_OPERATORS_JSON", "[]"))
    if token:
        operators.append({"token":token,"operator_ref":"local-reviewer","roles":["read","review","publish","admin"]})
    if any(not p.get("token") or not p.get("operator_ref") or not set(p.get("roles",[])) <= {"read","review","publish","admin"} for p in operators):
        raise ValueError("Invalid server operator configuration")

    def process_inbox():
        inbox = Path(config.get("PLATESG_INBOX", ROOT / ".local/inbox"))
        inbox.mkdir(parents=True, exist_ok=True)
        new_count=0
        for file in sorted(inbox.glob("*.json")):
            raw = file.read_bytes()
            if len(raw) > 100000:
                continue
            receipt_id = hashlib.sha256(raw).hexdigest()
            if bridge.call("receipt", {"receipt_id": receipt_id}):
                continue
            new_count+=1
            try:
                request = ImportRequest.model_validate_json(raw)
                extensions.validate_bundle(request.bundle_path,import_roots)
                result = bridge.call("import", request.model_dump(mode="json"))
                bridge.call("receipt", {"receipt_id": receipt_id, "status": "imported", "import_batch_id": result["import_batch_id"], "detail": result})
            except (ValueError, DatabaseError) as exc:
                detail={"message":str(exc),"file":file.name}
                try:
                    detail["request"]=ImportRequest.model_validate_json(raw).model_dump(mode="json")
                except ValueError:
                    pass
                bridge.call("receipt", {"receipt_id": receipt_id, "status": "failed", "detail":detail})
            if new_count>=20:
                break

    async def publisher():
        while True:
            try:
                await asyncio.to_thread(bridge.call, "publish", {"limit": 100})
                if config.get("PLATESG_AUTO_IMPORT", "1") == "1":
                    await asyncio.to_thread(process_inbox)
            except DatabaseError as exc:
                logger.error("Publication processor failed: %s", exc.code)
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(bridge.start)
        task = asyncio.create_task(publisher()) if config.get("PLATESG_AUTO_PUBLISH", "1") == "1" else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await asyncio.to_thread(bridge.close)

    app = FastAPI(title="PlatESG 数据发布与比较 API", version="1.0.0", lifespan=lifespan,
                  description="只读接口使用正式数据集版本。候选、事实审核、标准映射和正式发布分开保存。精确数值以字符串传输。")
    app.state.bridge = bridge
    app.add_middleware(APIPrefixMiddleware)

    def authorized(authorization: Annotated[str | None, Header()] = None):
        if not operators:
            raise HTTPException(503, "内部写入接口未启用；请配置审核凭据")
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        for principal in operators:
            if hmac.compare_digest(supplied.encode(), principal["token"].encode()):
                return {"operator_ref":principal["operator_ref"],"roles":principal["roles"]}
        raise HTTPException(401, "需要有效的审核凭据", headers={"WWW-Authenticate": "Bearer"})

    def permission(role):
        def check(principal=Depends(authorized)):
            if role not in principal["roles"] and "admin" not in principal["roles"]:
                raise HTTPException(403,"当前操作员没有此操作权限")
            if role!="read" and running_release.snapshot()["restart_required"]:
                raise HTTPException(503,{"code":"runtime_requires_restart","message":"发布服务的运行版本已过期；请协调重启后继续，历史数据保留"})
            return principal
        return check

    def operator_payload(request, principal, **options):
        return {**request.model_dump(mode="json",**options),"operator_ref":principal["operator_ref"]}

    def lifecycle_store_coverage():
        """An empty preview database is not proof about older fact stores.

        Check only filesystem presence; never open another historical database
        from a request or mutate it to make cleanup possible. Isolated fixture
        databases declare that boundary explicitly in their test environment.
        """
        active=Path(config.get("PLATESG_DATA_DIR",ROOT/".local/data")).expanduser().resolve()
        known=[ROOT.parent/"ExtractESG/esg_database/.local/pglite",ROOT/".local/data",ROOT/".local/demo-data"]
        unverified=[] if config.get("PLATESG_LIFECYCLE_STORE_SCOPE")=="isolated" else [str(path.resolve()) for path in known
            if path.is_dir() and (config.get("PLATESG_DATABASE_URL") or path.resolve()!=active)]
        identity="postgresql" if config.get("PLATESG_DATABASE_URL") else str(active)
        return {"verified_database":identity,"unverified_store_roots":unverified,"scope":"isolated_fixture" if config.get("PLATESG_LIFECYCLE_STORE_SCOPE")=="isolated" else "all_known_local_stores"}

    def lifecycle_result(result):
        coverage=lifecycle_store_coverage()
        if coverage["unverified_store_roots"] and not result.get('scope',{}).get('local_files_only'):
            result={**result,"verified":False,"blockers":[*result.get("blockers",[]),{
                "code":"additional_source_store_unverified","message":"还有历史或其他数据目录未完成来源核查；可以归档，暂不能清理来源。",
                "store_roots":coverage["unverified_store_roots"]}]}
        return {**result,"store_coverage":coverage}

    @app.exception_handler(DatabaseError)
    async def database_error(_, exc):
        status = 404 if exc.code == "not_found" else 503 if exc.code.startswith("adapter_") else 409 if "conflict" in exc.code else 422
        return JSONResponse(status_code=status, content={"detail": str(exc), "code": exc.code})

    @app.get("/health")
    def health():
        result = bridge.call("health")
        return {"status": result["status"], "service": "platesg", "database": result["database"], "dataset_version": result["latest"]}

    @app.get("/v1/entities")
    def entities(dataset_version: DatasetVersion = None, search: str | None = Query(None, max_length=240)):
        return bridge.call("entities", {"dataset_version": dataset_version, "search": search})

    @app.get("/v1/metrics")
    def metrics(dataset_version: DatasetVersion = None, topic: str | None = Query(None, max_length=12)):
        return bridge.call("metrics", {"dataset_version": dataset_version, "topic": topic})

    @app.get("/v1/observations", response_model=ObservationList)
    def observations(dataset_version: DatasetVersion = None, entity_ref: str | None = Query(None, max_length=240),
                     metric_id: str | None = Query(None, max_length=240), period_start: date | None = None,
                     period_end: date | None = None, limit: int = Query(200, ge=1, le=1000), cursor: str | None = Query(None, max_length=3000)):
        return bridge.call("observations", {"dataset_version": dataset_version, "entity_ref": entity_ref,
            "metric_id": metric_id, "period_start": str(period_start) if period_start else None,
            "period_end": str(period_end) if period_end else None, "limit": limit, "cursor": cursor})

    @app.get("/v1/observations/{observation_id}", response_model=ObservationDetail)
    def observation(observation_id: UUID, dataset_version: DatasetVersion = None):
        return bridge.call("detail", {"observation_id": str(observation_id), "dataset_version": dataset_version})

    @app.get("/v1/coverage")
    def coverage(dataset_version: DatasetVersion = None, entity_ref: str | None = Query(None, max_length=240),
                 period_start: date | None = None, period_end: date | None = None):
        return bridge.call("coverage", {"dataset_version": dataset_version, "entity_ref": entity_ref,
            "period_start":str(period_start) if period_start else None,"period_end":str(period_end) if period_end else None})

    @app.post("/v1/comparisons", response_model=ComparisonResponse)
    def comparison(request: CompareRequest):
        return bridge.call("comparisons", request.model_dump(mode="json"))

    @app.get("/v1/datasets")
    def datasets():
        return bridge.call("datasets")

    @app.get("/v1/datasets/{dataset_version}")
    def dataset(dataset_version: int):
        versions = bridge.call("datasets")["items"]
        result = next((v for v in versions if v["dataset_version"] == dataset_version), None)
        if result is None:
            raise HTTPException(404, "数据集版本不存在")
        return result

    @app.get("/v1/sources")
    def sources(dataset_version: DatasetVersion = None):
        return bridge.call("sources", {"dataset_version":dataset_version})

    @app.get("/v1/sources/{source_id}/document")
    def source_document(source_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], dataset_version: DatasetVersion = None):
        descriptor=bridge.call("source_document",{"source_id":source_id,"dataset_version":dataset_version})
        roots=[Path(value) for value in config.get("PLATESG_PUBLIC_PDF_ROOTS",str(ROOT.parent.parent/"pdf")).split(os.pathsep) if value]
        try:
            document=published_pdf(descriptor,roots)
        except FileNotFoundError as exc:
            raise HTTPException(404,{"code":"published_source_unavailable","message":"此发布版本的原始报告暂不可访问，来源摘录仍保留"}) from exc
        except (OSError,ValueError) as exc:
            raise HTTPException(409,{"code":"published_source_integrity_error","message":"原始报告未通过发布来源核对，已停止访问"}) from exc
        return FileResponse(document,media_type="application/pdf",filename=document.name,content_disposition_type="inline")

    @app.get("/internal/v1/extraction-profiles", dependencies=[Depends(permission("read"))])
    def profile():
        return json.loads((ROOT / "catalog/extraction-profile.e1-e3.json").read_text(encoding="utf-8"))

    @app.get('/internal/v1/import-catalogs', dependencies=[Depends(permission('read'))])
    def import_catalogs():
        return bridge.call('import_catalogs', {})

    @app.post('/internal/v1/import-catalogs/register')
    def register_import_catalog(request: ImportCatalogRegistrationRequest, principal=Depends(permission('admin'))):
        return bridge.call('register_import_catalog',operator_payload(request,principal))

    @app.get("/v1/exports")
    def export(format: Literal["csv", "json"] = "csv", dataset_version: DatasetVersion = None,
               entity_ref: str | None = Query(None, max_length=240), metric_id: str | None = Query(None, max_length=240)):
        params = {"dataset_version": dataset_version, "entity_ref": entity_ref, "metric_id": metric_id, "limit": 1000}
        result = bridge.call("observations", params)
        # Reuse the same public DTO as /v1/observations. Decimal values remain
        # strings; bridge-only metadata cannot escape via the export route.
        rows = [Observation.model_validate(row).model_dump(mode="json") for row in result["items"]]
        # Freeze the first resolved version before fetching the next page.
        params["dataset_version"] = result["dataset_version"]
        while result["next_cursor"]:
            params["cursor"] = result["next_cursor"]
            result = bridge.call("observations", params)
            rows.extend(Observation.model_validate(row).model_dump(mode="json") for row in result["items"])
            if len(rows) > 100000:
                raise HTTPException(413, "请缩小导出范围；批量数据集导出需使用离线任务")
        version = result["dataset_version"]
        if format == "json":
            return JSONResponse({"schema_version": "platesg-export-v1", "dataset_version": version,
                "dictionary_version": result["dictionary_version"], "as_of": result.get("as_of"), "items": rows},
                headers={"Content-Disposition": f'attachment; filename="platesg-v{version}.json"'})
        fields = ["dataset_version","observation_id","entity_ref","entity_name","is_demo","metric_id","source_datapoint_id",
                  "clause_kind","extension_id","related_source_datapoint_ids","value_origin",
                  "definition_version","period_start","period_end","boundary_code","measurement_method","value_role",
                  "value_numeric","lower_bound","upper_bound","canonical_unit","statement_text","dimensions","reported_properties",
                  "comparison_eligibility","comparison_reasons"]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            values = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()}
            # Protect text cells opened by spreadsheet applications; numeric decimal columns remain exact.
            for key in fields:
                if key not in {"value_numeric","lower_bound","upper_bound"} and isinstance(values.get(key), str) and values[key].startswith(("=", "+", "-", "@")):
                    values[key] = "'" + values[key]
            writer.writerow(values)
        return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="platesg-v{version}.csv"'})

    @app.get("/internal/v1/me")
    def me(principal=Depends(authorized)):
        return principal

    @app.get("/internal/v1/compatibility",dependencies=[Depends(permission("read"))])
    def compatibility():
        payload=running_release.snapshot()
        payload["readiness"]={"ready":not payload["restart_required"],"blockers":["发布服务需协调重启"] if payload["restart_required"] else []}
        return payload

    @app.get("/internal/v1/metrics", dependencies=[Depends(permission("read"))])
    def internal_metrics(dataset_version: DatasetVersion = None):
        return bridge.call("internal_metrics",{"dataset_version":dataset_version})

    def extension_action(callback):
        try:
            return callback()
        except (ValueError, OSError) as exc:
            raise HTTPException(409, {'code':'extension_contract_error','message':str(exc)}) from exc

    @app.get('/internal/v1/extensions', dependencies=[Depends(permission('read'))])
    def extension_catalog():
        return extension_action(extensions.catalog_view)

    @app.post('/internal/v1/extensions/proposals')
    def extension_propose(request: ExtensionProposal, principal=Depends(permission('review'))):
        return extension_action(lambda: extensions.propose(request, principal))

    @app.post('/internal/v1/extensions/decisions')
    def extension_decide(request: ExtensionDecisionRequest, principal=Depends(permission('admin'))):
        return extension_action(lambda: extensions.decide(request.proposal_id,decision=request.decision,reason=request.reason,principal=principal))

    @app.post('/internal/v1/extensions/register', dependencies=[Depends(permission('admin'))])
    def extension_register(request: ExtensionRegistrationRequest):
        return extension_action(lambda: extensions.register(request.proposal_id,request.approval_id))

    @app.post('/internal/v1/extensions/sync', dependencies=[Depends(permission('admin'))])
    def extension_sync():
        return extension_action(extensions.sync)

    @app.post('/internal/v1/extensions/platform-selection', dependencies=[Depends(permission('admin'))])
    def extension_platform_selection(request: ExtensionPlatformSelection):
        from .extension_dictionary import build_extension_dictionary
        return extension_action(lambda: bridge.call('extension_platform_selection',{
            'dictionary':build_extension_dictionary(json.loads((ROOT/'catalog/metric-dictionary.v1.json').read_text()),
                extensions.catalog_view()['releases'], request.extension_ids, request.dictionary_version)}))

    @app.get('/internal/v1/extensions/calculations', dependencies=[Depends(permission('read'))])
    def extension_calculations():
        return bridge.call('extension_calculations',{})

    @app.post('/internal/v1/extensions/calculations')
    def extension_derive(request: ExtensionDerivationRequest, principal=Depends(permission('review'))):
        return bridge.call('extension_derive',{'payload':request.model_dump(mode='json'),'operator_ref':principal['operator_ref']})

    @app.post('/internal/v1/extensions/calculations/{request_id}/retry', dependencies=[Depends(permission('review'))])
    def extension_retry_calculation(request_id: UUID):
        return bridge.call('extension_retry_derivation',{'request_id':str(request_id)})

    @app.post('/internal/v1/extensions/calculation-reviews')
    def extension_review_calculation(request: CalculationReviewRequest, principal=Depends(permission('review'))):
        return bridge.call('extension_review_calculation',operator_payload(request,principal))

    @app.get("/internal/v1/coverage", dependencies=[Depends(permission("read"))])
    def internal_coverage(entity_ref: str | None = None):
        return bridge.call("internal_coverage",{"entity_ref":entity_ref})

    @app.get("/internal/v1/observations/{observation_id}", dependencies=[Depends(permission("read"))])
    def internal_observation(observation_id: UUID, dataset_version: DatasetVersion = None):
        return bridge.call("internal_detail",{"observation_id":str(observation_id),"dataset_version":dataset_version})

    @app.get("/internal/v1/publication-status", dependencies=[Depends(permission("read"))])
    def publication_status(limit: int = Query(200,ge=1,le=1000)):
        return {**bridge.call("publication_status",{"limit":limit}),"processor":{"automatic":config.get("PLATESG_AUTO_PUBLISH","1")=="1"}}

    @app.get("/internal/v1/report-summaries", dependencies=[Depends(permission("read"))])
    def report_summaries():
        return bridge.call("report_summaries",{})

    @app.post("/internal/v1/entities", dependencies=[Depends(permission("admin"))])
    def register_entity(request: EntityRequest):
        return bridge.call("register_entity", request.model_dump(mode="json"))

    @app.get("/internal/v1/entities", dependencies=[Depends(permission("read"))])
    def internal_entities():
        return bridge.call("internal_entities",{})

    @app.post("/internal/v1/lifecycle/references", dependencies=[Depends(permission("read"))])
    def lifecycle_references(request: LifecycleScope):
        return lifecycle_result(bridge.call("lifecycle_references", request.model_dump(mode="json")))

    @app.post("/internal/v1/lifecycle/reserve", dependencies=[Depends(permission("admin"))])
    def lifecycle_reserve(request: LifecycleReservationRequest):
        coverage=lifecycle_store_coverage()
        if coverage["unverified_store_roots"] and not request.local_files_only:
            return lifecycle_result(bridge.call("lifecycle_references", request.model_dump(mode="json",exclude={"reason"})))
        return lifecycle_result(bridge.call("lifecycle_reserve", request.model_dump(mode="json")))

    @app.post("/internal/v1/lifecycle/reservations/{reservation_id}", dependencies=[Depends(permission("admin"))])
    def lifecycle_reservation(reservation_id: UUID, request: LifecycleReservationUpdate):
        if request.state=="purged" and lifecycle_store_coverage()["unverified_store_roots"]:
            raise HTTPException(409,{"code":"additional_source_store_unverified","message":"其他来源目录尚未核查，不能彻底清除。"})
        return bridge.call("lifecycle_reservation", {"reservation_id":str(reservation_id), **request.model_dump(mode="json")})

    @app.post("/internal/v1/import-batches", dependencies=[Depends(permission("review"))])
    def import_batch(request: ImportRequest):
        extension_action(lambda: extensions.validate_bundle(request.bundle_path,import_roots))
        return bridge.call("import", request.model_dump(mode="json"))

    @app.post("/internal/v1/deliveries", dependencies=[Depends(permission("review"))])
    def deliver_batch(request: ImportRequest):
        extension_action(lambda: extensions.validate_bundle(request.bundle_path,import_roots))
        return bridge.call("deliver", request.model_dump(mode="json"))

    @app.get("/internal/v1/candidates", dependencies=[Depends(permission("read"))])
    def candidates(entity_ref: str | None = Query(None, max_length=240), report_ref: str | None = Query(None,max_length=240),
                   source_datapoint_id: str | None = Query(None,max_length=100), limit: int = Query(200, ge=1, le=1000)):
        result = bridge.call("candidates", {"entity_ref": entity_ref,"report_ref":report_ref,"source_datapoint_id":source_datapoint_id,"limit": limit})
        pdf_roots = [Path(value) for value in config.get("PLATESG_PUBLIC_PDF_ROOTS", str(ROOT.parent.parent/"pdf")).split(os.pathsep) if value]
        ir_root = Path(config.get("PLATESG_IR_ROOT", str(ROOT.parent/"ExtractESG/document_ir_extract/document_ir_output")))
        candidate_locations(result["items"], pdf_roots, ir_root)
        return result

    @app.get("/internal/v1/source-facts", dependencies=[Depends(permission("read"))])
    def report_source_facts(report_ref: str | None = Query(None, max_length=240),
                            limit: int = Query(200, ge=1, le=1000),
                            cursor: str | None = Query(None,min_length=1,max_length=2048)):
        result = bridge.call("source_facts", {"report_ref": report_ref, "limit": limit, "cursor": cursor})
        pdf_roots = [Path(value) for value in config.get("PLATESG_PUBLIC_PDF_ROOTS", str(ROOT.parent.parent/"pdf")).split(os.pathsep) if value]
        ir_root = Path(config.get("PLATESG_IR_ROOT", str(ROOT.parent/"ExtractESG/document_ir_extract/document_ir_output")))
        candidate_locations(result["items"], pdf_roots, ir_root)
        return result

    @app.post("/internal/v1/reviews")
    def review(request: ReviewRequest, principal=Depends(permission("review"))):
        return bridge.call("review", operator_payload(request,principal,exclude_none=True))

    @app.post("/internal/v1/measurement-contexts")
    def context(request: ContextRequest, principal=Depends(permission("review"))):
        return bridge.call("context", operator_payload(request,principal))

    @app.post("/internal/v1/coverage-decisions")
    def coverage_decision(request: CoverageRequest, principal=Depends(permission("review"))):
        return bridge.call("coverage_decision", operator_payload(request,principal))

    @app.post("/internal/v1/publication-process", dependencies=[Depends(permission("publish"))])
    def process(limit: int = Query(100, ge=1, le=1000)):
        return bridge.call("publish", {"limit": limit})

    @app.get("/internal/v1/import-receipts", dependencies=[Depends(permission("read"))])
    def import_receipts(receipt_id: str = Query(..., pattern=r"^[0-9a-f]{64}$")):
        return bridge.call("receipt", {"receipt_id":receipt_id})

    @app.post('/internal/v1/delivery-status/query', response_model=DeliveryStatusResponse, dependencies=[Depends(permission('read'))])
    def delivery_status(request: DeliveryStatusQuery):
        # POST carries a bounded immutable descriptor. This operation performs
        # a read-only repeatable SQL snapshot and never calls import or publish.
        return bridge.call('delivery_status', request.model_dump(mode='json'))

    @app.post("/internal/v1/import-receipts/{receipt_id}/retry")
    def retry_import(receipt_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],request:RetryRequest, principal=Depends(permission("review"))):
        return bridge.call("retry_receipt",{"receipt_id":receipt_id,**operator_payload(request,principal)})

    app.mount("/review",StaticFiles(directory=ROOT / "review",html=True),name="review-workbench")

    if (ROOT / "frontend/dist").exists():
        app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="platform")
    return app


app = create_app()
