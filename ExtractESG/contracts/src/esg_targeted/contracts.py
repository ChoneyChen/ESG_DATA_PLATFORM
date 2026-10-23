from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer
from esg_standard_packages.extensions import ExtensionSelection


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ModelRole(StrictModel):
    provider: Literal["local_nuextract", "local_qwen", "qiniu_vlm"]
    model: str | None = None


class CloudAuthorization(StrictModel):
    allowed: bool = False
    allow_ocr_api: bool = False


class RoleConfig(StrictModel):
    semantic_brain: ModelRole = Field(default_factory=lambda: ModelRole(provider="local_qwen"))
    specialist: ModelRole = Field(default_factory=lambda: ModelRole(provider="local_nuextract"))
    cloud_authorization: CloudAuthorization = Field(default_factory=CloudAuthorization)

    @model_validator(mode="after")
    def authorize_roles(self):
        if self.semantic_brain.provider == "local_nuextract":
            raise ValueError("The report semantic brain must be local_qwen or qiniu_vlm")
        if any(role.provider == "qiniu_vlm" for role in (self.semantic_brain, self.specialist)) and not self.cloud_authorization.allowed:
            raise ValueError("Cloud semantic processing requires explicit authorization")
        return self


class ReportModelPlan(StrictModel):
    """Task preparation choices; extraction roles remain in ``role_config``."""
    ocr_provider: Literal["local_paddleocr", "local_first", "paddle_api"] = "local_paddleocr"
    ocr_model: Literal["PaddleOCR-VL-1.6"] = "PaddleOCR-VL-1.6"
    visual_review_provider: Literal["local_qwen", "local_nuextract", "qiniu"] = "local_qwen"
    execute_visual_reviews: bool = False
    execute_required_reviews: bool = False


class ReportInput(StrictModel):
    kind: Literal["pdf", "document_ir"]
    asset_id: str | None = None
    ir_run_id: str | None = None

    @model_validator(mode="after")
    def one_input(self):
        if self.kind == "pdf" and (not self.asset_id or self.ir_run_id):
            raise ValueError("PDF input requires only asset_id")
        if self.kind == "document_ir" and (not self.ir_run_id or self.asset_id):
            raise ValueError("Document IR input requires only ir_run_id")
        return self


class RequirementSelection(StrictModel):
    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    package_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    metric_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_metrics(self):
        self.metric_ids = list(dict.fromkeys(x.strip() for x in self.metric_ids if x.strip()))
        return self


class ModelCallBudget(StrictModel):
    max_model_calls: int = Field(default=128, ge=1, le=2000)
    max_format_repairs: int = Field(default=16, ge=0, le=200)
    max_input_tokens: int = Field(default=750000, ge=1000, le=20000000)
    max_output_tokens: int = Field(default=150000, ge=1000, le=4000000)
    max_consecutive_failures: int = Field(default=3, ge=1, le=20)


class EvidenceBudget(ModelCallBudget):
    retrieval_object_top_n: int = Field(default=3, ge=1, le=5)
    shared_object_budget: int = Field(default=3, ge=1, le=8)
    agent_max_steps: int = Field(default=8, ge=1, le=24)
    total_timeout_seconds: float = Field(default=14400, ge=30, le=86400)
    call_timeout_seconds: float = Field(default=600, ge=10, le=1800)


class ExtractionSelectionSnapshot(StrictModel):
    profile_id: str
    profile_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dictionary_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    dictionary_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_datapoint_ids: list[str] = Field(min_length=1)
    package_digests: dict[str, str]
    selection_kind: Literal["platform_major"] = "platform_major"


class ReportExtractionRequest(StrictModel):
    input: ReportInput
    # One report may select all 25 E1–E3 modules; model work remains bounded
    # by the report's shared call/token/time budget, not an old package count.
    standards: list[RequirementSelection] = Field(default_factory=list, max_length=128)
    selection_snapshot: ExtractionSelectionSnapshot | None = None
    extension_selections: list[ExtensionSelection] = Field(default_factory=list)
    # An expectation only: the native service must still verify every release
    # against its approved registry before accepting a new task.
    frozen_runtime_standard_snapshots: dict[str, dict[str, Any]] = Field(default_factory=dict)
    role_config: RoleConfig = Field(default_factory=RoleConfig)
    model_plan: ReportModelPlan | None = None
    # "foundation16": one bounded table-understanding call per source table for
    # the platform foundation datapoints; "standard" keeps per-package agents.
    extraction_route: Literal["standard", "foundation16"] = "standard"
    # Which foundation metric set this task runs. The link reads the set from
    # the request, so adding a set never edits the engine.
    metric_profile: Literal["base16", "priority32", "quantitative"] = "base16"
    completeness: Literal["quick", "balanced", "thorough"] = "balanced"
    budget: EvidenceBudget | None = None
    allow_limited_evidence: bool = True
    semantic_search: bool = True
    name: str = Field(default="报告提取", min_length=1, max_length=120)
    qiniu_api_key: str | None = Field(default=None, exclude=True)
    ocr_token: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def unique_packages(self):
        if self.model_plan:
            authorization = self.role_config.cloud_authorization
            if self.model_plan.ocr_provider == "paddle_api" and not authorization.allow_ocr_api:
                raise ValueError("Cloud OCR task assignment requires explicit OCR API authorization")
            if self.model_plan.visual_review_provider == "qiniu" and not authorization.allowed:
                raise ValueError("Cloud visual review task assignment requires explicit cloud authorization")
        keys = [s.package_id for s in self.standards]
        if len(set(keys)) != len(keys):
            raise ValueError("Select one version per standard package")
        if len({s.extension_id for s in self.extension_selections}) != len(self.extension_selections):
            raise ValueError("Select one version per extension identity")
        if not self.standards and not self.extension_selections:
            raise ValueError("Select at least one original requirement or approved extension")
        if any(not s.metric_ids for s in self.standards) and not self.extension_selections:
            raise ValueError("Select at least one original requirement or an explicit parent extension")
        if self.extraction_route == "foundation16" and not self.allow_limited_evidence:
            raise ValueError("The foundation16 route reads page-targeted, review-free IR and needs limited evidence")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_snapshot(self, handler):
        value = handler(self)
        if self.model_plan is None:
            value.pop("model_plan", None)
        if self.extraction_route == "standard":
            value.pop("extraction_route", None)
        if self.metric_profile == "base16":
            value.pop("metric_profile", None)
        return value

    def effective_budget(self) -> EvidenceBudget:
        settings = {"quick": (2, 2, 4), "balanced": (3, 3, 8), "thorough": (5, 5, 16)}
        top, shared, steps = settings[self.completeness]
        limits = {"quick": (64, 8, 350000, 65000), "balanced": (128, 16, 750000, 150000),
                  "thorough": (240, 24, 1500000, 300000)}
        calls, repairs, input_tokens, output_tokens = limits[self.completeness]
        preset = EvidenceBudget(retrieval_object_top_n=top, shared_object_budget=shared, agent_max_steps=steps,
            max_model_calls=calls, max_format_repairs=repairs, max_input_tokens=input_tokens, max_output_tokens=output_tokens)
        return EvidenceBudget.model_validate({**preset.model_dump(), **(self.budget.model_dump(exclude_unset=True) if self.budget else {})})


class RuntimeCredentials(StrictModel):
    qiniu_api_key: str | None = Field(default=None, exclude=True)
    ocr_token: str | None = Field(default=None, exclude=True)


class EvidenceTransitionRequest(RuntimeCredentials):
    new_ir_run_id: str
    affected_object_ids: list[str] = Field(min_length=1)
    metric_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)


class EvidenceGap(StrictModel):
    gap_id: str
    kind: Literal["source", "header", "row", "method", "mapping", "retrieval", "provider", "contract",
                  "uninterpreted_row", "column_header", "source_reference", "transport", "budget", "context",
                  "protocol", "generation_incomplete", "identity", "dependency", "not_covered",
                  "planner_stalled", "planner_round_budget", "planner_action_budget"]
    reason: str
    object_ids: list[str] = Field(default_factory=list)
    object_id: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    row_ids: list[str] = Field(default_factory=list)
    column_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    next_action: str | None = None
    topic_id: str | None = None
    attempt: int | None = None
    failure_stage: str | None = None
    status: Literal["open", "resolved"] = "open"


class AvailableAction(StrictModel):
    action: str
    label: str
    scope: dict[str, Any] = Field(default_factory=dict)
    endpoint: str
    method: Literal["GET", "POST", "DELETE"] = "POST"
    payload: dict[str, Any] = Field(default_factory=dict)
    calls_model: bool = False
    incurs_cost: bool = False
    requires_approval: bool = False
    requires_confirmation: bool = False
    confirmation_message: str | None = None
    requires_reason: bool = False
    requires_reference_check: bool = False
    preflight_endpoint: str | None = None
    enabled: bool = True


class StageOutcome(StrictModel):
    execution_status: Literal["queued", "running", "interrupted", "failed", "cancelled", "finished"]
    result_status: Literal["usable", "partial", "needs_confirmation", "no_evidence_in_bound", "not_evaluated"]
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    valid_scope: dict[str, Any] = Field(default_factory=dict)
    gaps: list[EvidenceGap] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    available_actions: list[AvailableAction] = Field(default_factory=list)


class ExtractionRequest(StrictModel):
    selection_snapshot: ExtractionSelectionSnapshot | None = None
    extension_selections: list[ExtensionSelection] = Field(default_factory=list)
    runtime_standard_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    ir_run_id: str
    package_id: str
    package_version: str
    metric_ids: list[str] = Field(default_factory=list)
    semantic_search: bool = True
    semantic_fill: bool = True
    semantic_provider: Literal["local_nuextract", "local_qwen", "qiniu_vlm"] = "local_nuextract"
    semantic_model: str | None = None
    retrieval_object_top_n: int = Field(default=3, ge=1, le=5)
    extraction_mode: Literal["auto", "per_metric", "foundation16"] = "auto"
    shared_object_budget: int = Field(default=3, ge=1, le=8)
    agent_max_steps: int = Field(default=8, ge=1, le=24)
    visual_fallback: bool = True
    force_unready_ir: bool = False
    report_session_id: str | None = Field(default=None, pattern=r"^report-[a-f0-9]{32}$")
    roles: RoleConfig | None = None
    internal_metric_ids: list[str] = Field(default_factory=list)
    call_timeout_seconds: float | None = Field(default=None, ge=10, le=1800)
    call_budget: ModelCallBudget = Field(default_factory=ModelCallBudget)


class CatalogIrRun(StrictModel):
    run_id: str
    path: str
    document_id: str | None = None
    document_label: str | None = None
    ir_revision: int
    schema_version: str
    readiness: str
    can_build_evidence: bool
    # Keep can_build_evidence as the legacy full-or-limited projection. New
    # consumers use the separate capabilities and policy of the fixed revision.
    can_build_full_evidence: bool = False
    can_build_limited_evidence: bool = False
    evidence_policy_mode: str | None = None
    ocr_run_id: str | None = None
    source_pdf_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    page_count: int
    manifest_mtime: float


class CatalogStandardPackage(StrictModel):
    package_id: str
    package_version: str
    status: str
    disclosure_requirement: str
    path: str
    metric_count: int
    element_count: int
    concept_count: int
    source_digest: str
    format_version: str = ""
    core_schema_version: str = ""
    validation_state: str = "structurally_valid"
    real_extraction_validation: str = "not_assessed"
    business_approval: str = "not_assessed"
    core_source_datapoint_ids: list[str] = Field(default_factory=list)
    metric_summaries: list[dict[str, object]] = Field(default_factory=list)


class SourceLocator(StrictModel):
    ir_object_type: Literal["page", "section", "block", "table", "cell", "figure", "spread"]
    ir_object_id: str
    pdf_page_index: int
    printed_page_label: str | None = None
    bbox: dict[str, float | str | None] | None = None


class EvidenceSpan(StrictModel):
    span_id: str
    document_id: str
    ir_run_id: str
    ir_revision: int
    span_type: Literal[
        "block", "sentence", "table_cell", "table_row", "figure_text", "page_text"
    ]
    text: str
    context_text: str
    section_id: str | None = None
    section_title: str | None = None
    page_index: int
    printed_page_label: str | None = None
    object_ids: list[str]
    locators: list[SourceLocator]
    context_group_id: str
    quality_flags: list[str] = Field(default_factory=list)
    visual_evidence_paths: list[str] = Field(default_factory=list)
    has_table_structure: bool = False
    structural_context: dict[str, Any] = Field(default_factory=dict)


class LiteralCandidate(StrictModel):
    candidate_id: str
    span_id: str
    candidate_type: Literal[
        "number", "quantity", "percentage", "year", "date", "date_range", "unit", "boolean", "text"
    ]
    raw_value: str
    normalized_value: str | None = None
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    unit_raw: str | None = None
    unit_id: str | None = None
    comparator: str = "exact"
    context_group_id: str

    @model_validator(mode="after")
    def check_offsets(self) -> "LiteralCandidate":
        if self.char_end <= self.char_start:
            raise ValueError("candidate end offset must be greater than start")
        return self


class EvidenceInventory(StrictModel):
    inventory_id: str
    document_id: str
    ir_run_id: str
    ir_revision: int
    spans: list[EvidenceSpan]
    candidates: list[LiteralCandidate]
    page_images: dict[int, str]
    visual_artifacts: dict[str, str] = Field(default_factory=dict)
    visual_artifact_pages: dict[str, int] = Field(default_factory=dict)
    stats: dict[str, int]


class QueryIntent(StrictModel):
    topic_term_groups: list[list[str]] = Field(default_factory=list)
    role_term_groups: list[list[str]] = Field(default_factory=list)
    must_term_groups: list[list[str]] = Field(default_factory=list)
    should_terms: list[str] = Field(default_factory=list)
    must_not_terms: list[str] = Field(default_factory=list)
    context_terms: list[str] = Field(default_factory=list)
    expected_candidate_types: list[str] = Field(default_factory=list)


class MetricQuery(StrictModel):
    metric_id: str
    query_text: str
    lexical_text: str
    semantic_text: str
    lexical_terms: list[str]
    data_class: str
    element_ids: list[str]
    intent: QueryIntent


class RetrievalHit(StrictModel):
    span_id: str
    lexical_rank: int | None = None
    lexical_score: float | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None
    rrf_score: float
    structural_boost: float
    intent_boost: float = 0.0
    conflict_penalty: float = 0.0
    intent_coverage: float = 0.0
    topic_coverage: float = 0.0
    role_coverage: float = 0.0
    compatible_candidate: bool = False
    combination_boost: float = 0.0
    final_score: float
    reasons: list[str] = Field(default_factory=list)


class EvidenceSufficiency(StrictModel):
    status: Literal["sufficient", "uncertain", "insufficient"]
    retrieval_complete: bool
    auto_not_found_allowed: bool
    required_group_count: int
    best_group_coverage: float
    best_topic_coverage: float = 0.0
    best_role_coverage: float = 0.0
    qualifying_group_ids: list[str] = Field(default_factory=list)
    conflict_group_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    group_summaries: list[dict[str, Any]] = Field(default_factory=list)


class EvidencePacket(StrictModel):
    packet_id: str
    task_id: str
    metric: dict[str, Any]
    elements: list[dict[str, Any]]
    query: MetricQuery
    spans: list[EvidenceSpan]
    candidates: list[LiteralCandidate]
    retrieval_hits: list[RetrievalHit]
    allowed_group_ids: list[str]
    context_only_span_ids: list[str] = Field(default_factory=list)
    page_image_paths: list[str]
    retrieval_complete: bool
    truncated: bool
    model_context: str
    alias_map: dict[str, dict[str, str]] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    source_document_id: str | None = None
    source_ir_run_id: str | None = None
    source_ir_revision: int | None = None


class SupportFragment(StrictModel):
    span_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class SemanticAssignment(StrictModel):
    element_id: str
    source_ref_id: str
    evidence_span_ids: list[str]
    value_raw: str | None = None
    source_mode: Literal["candidate", "span", "visual"] = "span"
    confidence: float | None = Field(default=None, ge=0, le=1)
    support_fragments: list[SupportFragment] = Field(default_factory=list)
    support_axis: str | None = None


class SemanticFactGroup(StrictModel):
    group_ref_id: str
    assignments: list[SemanticAssignment]
    metric_match: Literal["match", "uncertain", "different"] = "match"
    interpretation_note: str | None = None
    context_span_ids: list[str] = Field(default_factory=list)


class SemanticDecision(StrictModel):
    task_id: str
    status: Literal["found", "partial", "not_found", "ambiguous"]
    fact_groups: list[SemanticFactGroup] = Field(default_factory=list)
    selected_evidence_span_ids: list[str] = Field(default_factory=list)
    skipped_targets: dict[str, str] = Field(default_factory=dict)
    missing_context: list[str] = Field(default_factory=list)
    uncertainty_code: Literal[
        "none",
        "insufficient_evidence",
        "conflicting_values",
        "scope_ambiguous",
        "period_ambiguous",
        "model_output_invalid",
    ] = "none"

    @model_validator(mode="before")
    @classmethod
    def normalize_nullable_uncertainty(cls, value):
        if isinstance(value, dict) and value.get("uncertainty_code") is None:
            value = dict(value)
            value["uncertainty_code"] = (
                "none" if value.get("status") == "found" else "insufficient_evidence"
            )
        return value


class ModelRunResult(StrictModel):
    decision: SemanticDecision
    raw_output: str
    cleaned_output: str
    visual_used: bool
    image_count: int
    generation_tokens: int | None = None
    prompt_tokens: int | None = None
    finish_reason: str | None = None
    output_token_budget: int | None = None
    input_chars: int | None = None
    peak_memory_gb: float | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    normalization_actions: list[str] = Field(default_factory=list)
    provider: str | None = None
    model_id: str | None = None


class GuardIssue(StrictModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    group_ref_id: str | None = None
    element_id: str | None = None
    source_ref_id: str | None = None
    recommended_action: str | None = None


class GuardResult(StrictModel):
    task_id: str
    accepted: bool
    recoverable: bool
    issues: list[GuardIssue]
    accepted_group_count: int
    accepted_assignment_count: int
    feedback: str


class TaskOutcome(StrictModel):
    task_id: str
    metric_id: str
    status: str
    found_status: str
    review_status: str
    guard_accepted: bool
    attempts: int
    result_counts: dict[str, int]
    uncertainty_reason: str | None = None
    source_validation_status: Literal["passed", "failed", "not_run"] = "not_run"
    semantic_decision_status: Literal["decided", "unresolved", "not_run"] = "not_run"
    display_readiness_status: Literal[
        "ready", "needs_semantic_completion", "not_applicable"
    ] = "not_applicable"
    contract_validation_status: Literal["passed", "failed"] | None = None
    contract_error: str | None = None
    search_resolution_status: Literal[
        "not_applicable",
        "not_found_after_complete_search",
        "not_found_after_bounded_search",
        "retrieval_uncertain",
    ] = "not_applicable"
    failure_category: str | None = None
    source_checked_fact_count: int = 0
    dependency_status: Literal["not_applicable", "ready", "blocked"] = "not_applicable"
    dependency_blocks: list[dict[str, Any]] = Field(default_factory=list)


class MaterializedTaskResult(StrictModel):
    outcome: TaskOutcome
    reporting_tasks: list[dict[str, Any]]
    quantitative_observations: list[dict[str, Any]]
    qualitative_assertions: list[dict[str, Any]]
    attribute_values: list[dict[str, Any]]
    dimension_values: list[dict[str, Any]]
    evidence_references: list[dict[str, Any]]


class JobRecord(StrictModel):
    job_id: str
    status: JobStatus
    stage: str
    progress_current: int
    progress_total: int
    created_at: datetime
    updated_at: datetime
    request: ExtractionRequest
    output_dir: str
    error: str | None = None
    summary: dict[str, Any] | None = None
    cancel_requested: bool = False
    worker_id: str | None = None
    heartbeat_at: datetime | None = None


class QuantitativeValue(StrictModel):
    raw: str
    numeric: Decimal | None = None
    lower: Decimal | None = None
    upper: Decimal | None = None
    comparator: str = "exact"


def path_string(path: Path) -> str:
    return str(path.expanduser().resolve())
