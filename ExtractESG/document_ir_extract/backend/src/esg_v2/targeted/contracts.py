from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


TargetedRunMode = Literal["local_strict", "local_semantic"]
AnswerStatus = Literal["found", "not_found", "not_applicable", "uncertain", "system_failed"]
InternalDecision = Literal["complete", "partial", "irrelevant", "not_applicable", "ambiguous", "system_failed"]


class CloudPolicy(BaseModel):
    enabled: bool = False
    max_calls: int = 0
    max_cost_cny: float = 0
    escalation: Literal["disabled", "manual_only"] = "disabled"


class TargetedRunRequest(BaseModel):
    ir_run_id: str
    template_path: Path
    run_id: str | None = None
    evidence_run_id: str | None = None
    mode: TargetedRunMode = "local_strict"
    top_k: int = Field(default=30, ge=5, le=100)
    local_embedding_model: str = "intfloat/multilingual-e5-small"
    allow_local_model_download: bool = False
    cloud_policy: CloudPolicy = Field(default_factory=CloudPolicy)


class CandidateEvidence(BaseModel):
    requirement_id: str
    atom_id: str
    atom_type: str
    group_id: str = ""
    representative_atom_ids: list[str] = Field(default_factory=list)
    fused_score: float
    lane_ranks: dict[str, int] = Field(default_factory=dict)
    matched_terms: list[str] = Field(default_factory=list)
    matched_dimensions: dict[str, list[str]] = Field(default_factory=dict)
    matched_slots: dict[str, list[str]] = Field(default_factory=dict)
    score_components: dict[str, float] = Field(default_factory=dict)
    penalties: list[str] = Field(default_factory=list)
    index_like: bool = False
    coverage_ratio: float = Field(default=0, ge=0, le=1)
    page_indices: list[int] = Field(default_factory=list)
    source_node_ids: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


class RequirementSearchTrace(BaseModel):
    requirement_id: str
    query_terms: list[str]
    mode_requested: TargetedRunMode
    mode_effective: TargetedRunMode
    lane_counts: dict[str, int] = Field(default_factory=dict)
    semantic_status: str = "not_requested"
    candidate_count: int = 0
    returned_candidate_count: int = 0
    candidate_limit: int = 0
    candidate_limit_reached: bool = False
    top_candidate_ids: list[str] = Field(default_factory=list)


class TargetedAnswer(BaseModel):
    requirement_id: str
    sequence: int
    source_sheet: str
    source_row: int
    data_point_id: str
    status: AnswerStatus
    internal_decision: InternalDecision = "ambiguous"
    location: str = ""
    quote: str = ""
    value: str = ""
    easy_to_judge: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    reasoning: str
    selected_atom_ids: list[str] = Field(default_factory=list)
    selected_group_ids: list[str] = Field(default_factory=list)
    selected_source_node_ids: list[str] = Field(default_factory=list)
    matched_dimensions: dict[str, list[str]] = Field(default_factory=dict)
    decision_routes: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    slot_coverage: list["SlotCoverage"] = Field(default_factory=list)
    facts: list["FactInstance"] = Field(default_factory=list)
    rejected_candidate_reasons: list[str] = Field(default_factory=list)


class DisclosureFeatures(BaseModel):
    numeric_values: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    unit_families: list[str] = Field(default_factory=list)
    statement_types: list[str] = Field(default_factory=list)
    explicit_zero: bool = False
    index_like: bool = False
    table_like: bool = False


class DisclosureGroup(BaseModel):
    group_id: str
    group_type: Literal["table", "logical_table", "paragraph", "figure", "other"]
    atom_ids: list[str]
    representative_atom_id: str
    source_text: str
    search_text: str
    page_indices: list[int] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)
    section_path: list[str] = Field(default_factory=list)
    source_table_ids: list[str] = Field(default_factory=list)
    row_texts: list[str] = Field(default_factory=list)
    header_texts: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    features: DisclosureFeatures = Field(default_factory=DisclosureFeatures)


class SlotCoverage(BaseModel):
    slot_id: str
    required: bool = True
    satisfied: bool
    matched_values: list[str] = Field(default_factory=list)
    evidence_atom_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class FactInstance(BaseModel):
    fact_id: str = ""
    concept_id: str
    value: str
    numeric_values: list[str] = Field(default_factory=list)
    raw_text: str = ""
    unit_family: str | None = None
    period: int | None = None
    dimensions: dict[str, list[str]] = Field(default_factory=dict)
    entity_scope: str | None = None
    source_group_id: str = ""
    evidence_atom_ids: list[str] = Field(default_factory=list)


class CandidateVerification(BaseModel):
    requirement_id: str
    group_id: str
    decision: InternalDecision
    coverage_ratio: float = Field(default=0, ge=0, le=1)
    slot_coverage: list[SlotCoverage] = Field(default_factory=list)
    facts: list[FactInstance] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    verifier_score: float = 0


class SearchCoverageRecord(BaseModel):
    requirement_id: str
    candidate_group_count: int = 0
    prelimit_candidate_group_count: int = 0
    candidate_limit: int = 0
    candidate_limit_reached: bool = False
    verified_group_count: int = 0
    complete_group_count: int = 0
    partial_group_count: int = 0
    irrelevant_group_count: int = 0
    not_applicable_group_count: int = 0
    ambiguous_group_count: int = 0
    index_rejected_count: int = 0
    selected_group_ids: list[str] = Field(default_factory=list)
    terminal_status: AnswerStatus
    all_retrieved_candidates_verified: bool = False
    search_exhausted: bool = False


class LocalInferenceRecord(BaseModel):
    component: str
    backend: str
    model_id: str | None = None
    status: Literal["not_requested", "succeeded", "unavailable", "failed", "fallback"]
    input_count: int = 0
    duration_ms: float = 0
    details: dict[str, Any] = Field(default_factory=dict)


class TargetedValidationReport(BaseModel):
    valid: bool
    can_export: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TargetedRunResult(BaseModel):
    run_id: str
    ir_run_id: str
    evidence_run_id: str
    status: Literal["done", "failed"]
    output_dir: Path
    manifest_path: Path
    export_path: Path | None = None
    requirement_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    cloud_call_count: int = 0
    cloud_cost_cny: float = 0
    local_model_inference_count: int = 0


class TargetedJobState(BaseModel):
    run_id: str
    ir_run_id: str
    status: Literal["queued", "running", "done", "failed"]
    message: str
    output_dir: Path
    template_name: str
    mode: TargetedRunMode
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    manifest_path: Path | None = None
    export_path: Path | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
