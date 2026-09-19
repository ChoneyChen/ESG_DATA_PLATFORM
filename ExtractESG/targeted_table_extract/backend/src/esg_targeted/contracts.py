from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ExtractionRequest(StrictModel):
    ir_run_id: str
    package_id: str
    package_version: str
    metric_ids: list[str] = Field(default_factory=list)
    semantic_search: bool = True
    semantic_fill: bool = True
    semantic_provider: Literal["local_nuextract", "qiniu_vlm"] = "local_nuextract"
    semantic_model: str | None = None
    retrieval_object_top_n: int = Field(default=3, ge=1, le=5)
    visual_fallback: bool = True
    force_unready_ir: bool = False


class CatalogIrRun(StrictModel):
    run_id: str
    path: str
    document_id: str | None = None
    document_label: str | None = None
    ir_revision: int
    schema_version: str
    readiness: str
    can_build_evidence: bool
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


class SemanticAssignment(StrictModel):
    element_id: str
    source_ref_id: str
    evidence_span_ids: list[str]
    value_raw: str | None = None
    source_mode: Literal["candidate", "span", "visual"] = "span"
    confidence: float | None = Field(default=None, ge=0, le=1)


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
