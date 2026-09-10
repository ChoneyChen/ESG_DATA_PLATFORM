from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from esg_v2.document.operation_registry import PATCH_OPERATION_NAMES


SchemaVersion = Literal[
    "document-ir-v0.2",
    "document-ir-v0.3",
    "document-ir-v0.4",
    "document-ir-v0.5",
    "document-ir-v0.6",
    "document-ir-v0.7",
    "document-ir-v0.8",
    "document-ir-v0.9",
    "document-ir-v0.10",
    "document-ir-v0.11",
    "document-ir-v0.12",
]
ReadinessStatus = Literal[
    "building",
    "auto_review_pending",
    "ready",
    "ready_with_warnings",
    "repair_required",
    "review_required",
    "failed",
]


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    unit: Literal["points", "pixels", "normalized", "unknown"] = "unknown"
    origin: Literal["top_left", "bottom_left", "page", "unknown"] = "top_left"
    coordinate_system_id: str | None = None


class Polygon(BaseModel):
    points: list[tuple[float, float]] = Field(default_factory=list)
    unit: Literal["points", "pixels", "normalized", "unknown"] = "unknown"
    origin: Literal["top_left", "bottom_left", "page", "unknown"] = "top_left"
    coordinate_system_id: str | None = None


class CoordinateSystem(BaseModel):
    coordinate_system_id: str
    page_index: int
    name: str
    width: float
    height: float
    unit: Literal["points", "pixels", "normalized"]
    origin: Literal["top_left", "bottom_left"] = "top_left"
    dpi: float | None = None
    rotation: int = 0
    maps_to: str | None = None
    scale_x: float | None = None
    scale_y: float | None = None


ArtifactKind = Literal[
    "source_pdf",
    "ocr_raw",
    "page_markdown",
    "page_image",
    "spread_image",
    "region_crop",
    "ocr_image",
    "figure_image",
    "other",
]


class ArtifactRef(BaseModel):
    artifact_id: str
    kind: ArtifactKind
    path: str
    remote_uri: str | None = None
    media_type: str | None = None
    page_index: int | None = None
    page_indices: list[int] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    width_pixels: int | None = None
    height_pixels: int | None = None
    page_pixel_bbox: BoundingBox | None = None
    sha256: str | None = None
    source: str | None = None


class SourceTrace(BaseModel):
    parser: str
    parser_version: str | None = None
    artifact_path: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    raw_jsonl_path: str | None = None
    raw_line_number: int | None = None
    raw_result_index: int | None = None
    raw_object_path: str | None = None
    page_markdown_path: str | None = None
    confidence: float | None = None
    notes: list[str] = Field(default_factory=list)


class LayoutObjectIR(BaseModel):
    layout_object_id: str
    page_index: int
    order: int
    label: str
    text: str = ""
    bbox: BoundingBox | None = None
    polygon: Polygon | None = None
    block_id: str | None = None
    table_id: str | None = None
    figure_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


class PageIR(BaseModel):
    page_id: str
    page_index: int
    page_number: int
    printed_page_label: str | None = None
    width: float | None = None
    height: float | None = None
    rotation: int | None = None
    markdown_path: str | None = None
    page_image_path: str | None = None
    text: str = ""
    text_length: int = 0
    coordinate_system_ids: list[str] = Field(default_factory=list)
    layout_object_ids: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    figure_ids: list[str] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    output_image_paths: list[str] = Field(default_factory=list)
    remote_image_urls: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    review_task_ids: list[str] = Field(default_factory=list)
    spread_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


class SectionIR(BaseModel):
    section_id: str
    title: str
    level: int
    start_page_index: int
    end_page_index: int | None = None
    heading_block_id: str | None = None
    parent_section_id: str | None = None
    child_section_ids: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


BlockType = Literal[
    "heading",
    "paragraph",
    "list",
    "table_markdown",
    "caption",
    "footnote",
    "header",
    "footer",
    "formula",
    "seal",
    "unknown",
]


class BlockIR(BaseModel):
    block_id: str
    page_index: int
    order: int
    block_type: BlockType
    text: str
    markdown: str | None = None
    heading_level: int | None = None
    bbox: BoundingBox | None = None
    polygon: Polygon | None = None
    section_id: str | None = None
    table_id: str | None = None
    figure_id: str | None = None
    layout_object_id: str | None = None
    crop_artifact_id: str | None = None
    visual_role: Literal[
        "node_title",
        "node_body",
        "label",
        "legend",
        "value",
        "annotation",
    ] | None = None
    quality_flags: list[str] = Field(default_factory=list)
    review_task_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


class CellIR(BaseModel):
    cell_id: str
    table_id: str
    page_index: int
    row_index: int
    col_index: int
    text: str
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    column_header_path: list[str] = Field(default_factory=list)
    row_header_path: list[str] = Field(default_factory=list)
    unit_hint: str | None = None
    bbox: BoundingBox | None = None
    quality_flags: list[str] = Field(default_factory=list)
    source_cell_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


TableEdgeType = Literal[
    "right_of",
    "below",
    "column_header_for",
    "row_header_for",
    "unit_applies_to",
    "footnote_applies_to",
]


class TableGraphEdge(BaseModel):
    edge_id: str
    table_id: str
    source_cell_id: str
    target_cell_id: str
    relation: TableEdgeType
    confidence: float = 1.0


class TableCellObservation(BaseModel):
    row_index: int
    col_index: int
    text: str = ""
    bbox: BoundingBox | None = None


class TableObservationIR(BaseModel):
    observation_id: str
    source: str
    row_count: int = 0
    column_count: int = 0
    bbox: BoundingBox | None = None
    cells: list[TableCellObservation] = Field(default_factory=list)
    match_iou: float | None = None
    match_score: float | None = None
    quality_flags: list[str] = Field(default_factory=list)


class TableIR(BaseModel):
    table_id: str
    page_index: int
    page_indices: list[int] = Field(default_factory=list)
    order: int
    block_id: str | None = None
    caption: str | None = None
    markdown: str = ""
    row_count: int = 0
    column_count: int = 0
    cells: list[CellIR] = Field(default_factory=list)
    observations: list[TableObservationIR] = Field(default_factory=list)
    graph_edges: list[TableGraphEdge] = Field(default_factory=list)
    header_row_indices: list[int] = Field(default_factory=list)
    footnote_block_ids: list[str] = Field(default_factory=list)
    continuation_group_id: str | None = None
    continuation_axis: Literal["vertical", "horizontal"] | None = None
    continues_from_table_id: str | None = None
    continues_to_table_id: str | None = None
    bbox: BoundingBox | None = None
    crop_artifact_id: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    review_task_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


class LogicalTableSegmentIR(BaseModel):
    table_id: str
    page_index: int
    sequence: int
    row_offset: int = 0
    column_offset: int = 0


class LogicalCellMappingIR(BaseModel):
    source_table_id: str
    source_cell_id: str
    logical_row_index: int
    logical_col_index: int
    row_span: int = 1
    col_span: int = 1


class LogicalCellIR(BaseModel):
    row_index: int
    col_index: int
    text: str = ""
    source_cell_ids: list[str] = Field(default_factory=list)
    is_header: bool = False
    unit_hint: str | None = None


class LogicalTableIR(BaseModel):
    """Lossless derived view over physical TableIR segments."""

    logical_table_id: str
    continuation_group_id: str
    composition_axis: Literal["vertical", "horizontal"]
    composition_mode: Literal[
        "vertical_stack",
        "horizontal_append_columns",
        "horizontal_continue_last_column",
    ]
    source_table_ids: list[str]
    page_indices: list[int]
    spread_id: str | None = None
    logical_row_count: int = 0
    logical_column_count: int = 0
    segments: list[LogicalTableSegmentIR] = Field(default_factory=list)
    cell_mappings: list[LogicalCellMappingIR] = Field(default_factory=list)
    cells: list[LogicalCellIR] = Field(default_factory=list)
    status: Literal["derived", "verified", "review_required"] = "derived"
    quality_flags: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


class ChartDataPoint(BaseModel):
    category: str
    value: float | str | None = None
    display_value: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class ChartSeries(BaseModel):
    name: str
    unit: str | None = None
    points: list[ChartDataPoint] = Field(default_factory=list)


class ChartSpec(BaseModel):
    chart_type: Literal[
        "bar", "column", "line", "area", "pie", "donut", "scatter", "bubble",
        "waterfall", "radar", "heatmap", "mixed", "other",
    ]
    title: str | None = None
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    legend: list[str] = Field(default_factory=list)
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    notes: list[str] = Field(default_factory=list)
    visual_evidence_refs: list[str] = Field(default_factory=list)


class FigureIR(BaseModel):
    figure_id: str
    page_index: int
    order: int
    image_path: str | None = None
    caption: str | None = None
    legend_text: list[str] = Field(default_factory=list)
    element_block_ids: list[str] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    crop_artifact_id: str | None = None
    visual_type: Literal["unknown", "chart", "diagram", "illustration", "photo", "icon", "decoration", "composite"] = "unknown"
    visual_status: Literal["candidate", "reviewed", "unresolved"] = "candidate"
    chart_spec: ChartSpec | None = None
    quality_flags: list[str] = Field(default_factory=list)
    review_task_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


SpreadStatus = Literal[
    "candidate",
    "confirmed",
    "rejected",
    "ambiguous",
    "visual_continuity",
]
SpreadClassification = Literal[
    "content_crossing",
    "visual_continuity",
    "standalone_pages",
    "uncertain",
]
SpreadResolutionSource = Literal[
    "unresolved",
    "local_preflight",
    "agent_review",
    "human_review",
    "inherited",
]


class SpreadPagePlacement(BaseModel):
    page_id: str
    page_index: int
    side: Literal["left", "right"]
    x_offset_pixels: int
    y_offset_pixels: int = 0
    width_pixels: int
    height_pixels: int
    source_coordinate_system_id: str | None = None


class SpreadEntityLink(BaseModel):
    source_id: str
    target_id: str
    relation: Literal["horizontal_continuation"] = "horizontal_continuation"
    confidence: float = Field(default=1.0, ge=0, le=1)


class SpreadIR(BaseModel):
    spread_id: str
    page_ids: list[str]
    page_indices: list[int]
    reading_direction: Literal["left_to_right", "right_to_left"] = "left_to_right"
    status: SpreadStatus = "candidate"
    confidence: float = Field(default=0.0, ge=0, le=1)
    classification: SpreadClassification = "uncertain"
    content_dependency: bool = False
    requires_detailed_review: bool = True
    resolution_source: SpreadResolutionSource = "unresolved"
    preflight_confidence: float = Field(default=0.0, ge=0, le=1)
    preflight_signals: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    composite_artifact_id: str
    placements: list[SpreadPagePlacement] = Field(default_factory=list)
    seam_metrics: dict[str, float] = Field(default_factory=dict)
    member_entity_ids: list[str] = Field(default_factory=list)
    linked_entities: list[SpreadEntityLink] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    review_task_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


StructureRelation = Literal[
    "contains",
    "reading_next",
    "caption_of",
    "footnote_of",
    "continues",
    "horizontal_continuation",
    "part_of_spread",
    "references",
    "visual_contains",
    "visual_parent_of",
    "visual_connected_to",
    "visual_flow_to",
]


class StructureEdge(BaseModel):
    edge_id: str
    source_id: str
    target_id: str
    relation: StructureRelation
    confidence: float = 1.0
    source: str = "deterministic"


ReviewTaskType = Literal[
    "horizontal_spread_review",
    "page_compound_review",
    "page_visual_read",
    "table_structure_review",
    "figure_chart_review",
    "ocr_native_conflict_review",
    "low_confidence_region_review",
]

ReviewTaskStatus = Literal[
    "pending",
    "queued",
    "running",
    "reviewed",
    "auto_resolved",
    "human_required",
    "deferred",
    "failed",
    "done",
    "skipped",
]


class ReviewScopeItem(BaseModel):
    target_type: Literal["page", "spread", "block", "table", "figure", "cell", "section"]
    target_id: str
    reason_codes: list[str] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    blocking: bool = True


ReviewKind = Literal[
    "horizontal_page_spread",
    "page_text_coverage",
    "table_candidate_classification",
    "table_structure_reconstruction",
    "figure_binding",
    "figure_semantic_structure",
    "generic_document_ir_review",
]


class ReviewPlan(BaseModel):
    compiler_version: str = "review-plan-v1"
    contract_hash: str = ""
    review_kind: ReviewKind = "generic_document_ir_review"
    question: str
    current_risk: str
    evidence_checklist: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)
    automation_strategy: Literal[
        "deterministic_first",
        "classify_and_link",
        "classify_then_reconstruct",
        "review_verify_repair",
        "optional_enrichment",
    ] = "review_verify_repair"
    human_boundary: str = (
        "Escalate only when readable evidence supports more than one materially different result "
        "after a guard-passing proposal reaches an independent verifier."
    )
    context_target_ids: list[str] = Field(default_factory=list)
    mutable_target_ids: list[str] = Field(default_factory=list)
    required_decision_target_ids: list[str] = Field(default_factory=list)


class VlmReviewTask(BaseModel):
    task_id: str
    task_type: ReviewTaskType
    target_type: Literal["page", "spread", "block", "table", "figure"]
    target_id: str
    page_index: int
    bbox: BoundingBox | None = None
    provider: Literal["qiniu", "local_nuextract"] = "qiniu"
    model_name: str | None = None
    status: ReviewTaskStatus = "pending"
    priority: Literal["critical", "high", "normal", "low"] = "normal"
    blocking: bool = True
    compound_group_id: str | None = None
    scope: list[ReviewScopeItem] = Field(default_factory=list)
    max_attempts: int = Field(default=2, ge=1, le=3)
    attempt_count: int = 0
    execution_count: int = 0
    reviewer_call_count: int = 0
    verifier_call_count: int = 0
    resume_stage: Literal[
        "reviewer_pending",
        "verifier_pending",
        "repair_pending",
        "complete",
    ] = "reviewer_pending"
    last_attempt_run_id: str | None = None
    last_attempt_at: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    prompt_intent: str
    review_plan: ReviewPlan | None = None
    input_refs: list[str] = Field(default_factory=list)
    expected_schema: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    reviewer_result_ids: list[str] = Field(default_factory=list)
    guard_result_ids: list[str] = Field(default_factory=list)
    verifier_result_ids: list[str] = Field(default_factory=list)
    final_decision_id: str | None = None
    failure_class: Literal[
        "none",
        "model_protocol",
        "system_contract",
        "evidence_missing",
        "verifier_disagreement",
        "semantic_ambiguity",
        "model_service",
        "rate_limit",
        "scheduler_deferred",
        "repeated_failure",
    ] = "none"
    failure_owner: Literal["none", "model", "system", "evidence", "service"] = "none"
    retryable: bool = True
    failure_fingerprint: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VlmReviewPayload(BaseModel):
    findings: list[str] = Field(default_factory=list)
    corrected_text_or_table: Any = None
    confidence: float = Field(ge=0, le=1)
    needs_human_review: bool
    quality_flags: list[str] = Field(default_factory=list)

    @field_validator("findings", "quality_flags", mode="before")
    @classmethod
    def normalize_string_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


ReviewVerdict = Literal["confirm", "propose_patch", "abstain"]
StoredReviewVerdict = Literal["confirm", "propose_patch", "abstain", "correct"]
PatchOperation = Literal[*PATCH_OPERATION_NAMES]


class TableGridCellProposal(BaseModel):
    row_index: int = Field(ge=0)
    col_index: int = Field(ge=0)
    text: str = ""
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    is_header: bool = False
    source_cell_ids: list[str] = Field(default_factory=list)
    visual_evidence_refs: list[str] = Field(default_factory=list)


class MissingTableText(BaseModel):
    text: str
    visual_evidence_refs: list[str] = Field(default_factory=list)
    intended_row_index: int | None = Field(default=None, ge=0)
    intended_col_index: int | None = Field(default=None, ge=0)


class TableGridRepairProposal(BaseModel):
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    cells: list[TableGridCellProposal]
    missing_text: list[MissingTableText] = Field(default_factory=list)
    visual_evidence_refs: list[str] = Field(default_factory=list)
    repair_reason: str = "Complete evidence-backed page-local grid reconstruction."


class AtomicPatchProposal(BaseModel):
    target_type: Literal["page", "spread", "block", "table", "figure", "cell", "section"]
    target_id: str
    operation: PatchOperation
    field_path: str | None = None
    before_value: Any = None
    proposed_value: Any = None
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class ScopeReviewDecision(BaseModel):
    target_type: Literal["page", "spread", "block", "table", "figure", "cell", "section"]
    target_id: str
    decision: Literal["confirm", "propose_patch", "abstain"]
    rationale: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class ReviewerPayload(BaseModel):
    verdict: ReviewVerdict
    findings: list[str] = Field(default_factory=list)
    scope_decisions: list[ScopeReviewDecision] = Field(default_factory=list)
    patches: list[AtomicPatchProposal] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    abstain_reason: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    @field_validator("findings", mode="before")
    @classmethod
    def normalize_findings(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
        return [str(value)]

    @field_validator("quality_flags", mode="before")
    @classmethod
    def normalize_lists(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class ReviewerResult(BaseModel):
    reviewer_result_id: str
    task_id: str
    model_id: str
    model_family: str
    provider: Literal["qiniu", "local_nuextract"] = "qiniu"
    attempt: int
    verdict: StoredReviewVerdict
    findings: list[str] = Field(default_factory=list)
    patch_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    abstain_reason: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentModelCall(BaseModel):
    call_id: str
    task_id: str
    role: Literal["reviewer", "verifier"]
    round_index: int
    model_id: str
    model_family: str
    provider: Literal["qiniu", "local_nuextract"] = "qiniu"
    status: Literal["succeeded", "failed", "invalid_response"]
    request_summary: dict[str, Any] = Field(default_factory=dict)
    response_payload: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    error: str | None = None
    failure_category: Literal[
        "none",
        "transport",
        "quota",
        "rate_limit_rpm",
        "rate_limit_tpd",
        "protocol",
        "request_config",
        "output_length",
    ] = "none"
    retry_index: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AtomicPatch(BaseModel):
    patch_id: str
    source_task_id: str
    source_reviewer_result_id: str | None = None
    transaction_id: str | None = None
    target_type: Literal["page", "spread", "block", "table", "figure", "cell", "section"]
    target_id: str
    operation: PatchOperation
    field_path: str | None = None
    before_value: Any = None
    proposed_value: Any = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = None
    risk_level: Literal["low", "medium", "high"] = "medium"
    status: Literal["proposed", "guard_passed", "guard_failed", "accepted", "rejected", "human_required"] = "proposed"
    rationale: str | None = None


class CorrectionPatch(AtomicPatch):
    """Backward-compatible name for pre-v0.3 artifact readers."""


class GuardCheck(BaseModel):
    code: str
    passed: bool
    severity: Literal["info", "warning", "error", "blocking"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class GuardResult(BaseModel):
    guard_result_id: str
    task_id: str
    transaction_id: str | None = None
    patch_ids: list[str] = Field(default_factory=list)
    passed: bool
    requires_independent_verifier: bool = True
    checks: list[GuardCheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VerifierTransactionDecision(BaseModel):
    transaction_id: str
    verdict: Literal["accept", "reject", "abstain"]
    disagreements: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @field_validator("disagreements", mode="before")
    @classmethod
    def normalize_disagreements(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
        if isinstance(value, (list, tuple)):
            return [
                item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in value
            ]
        return [str(value)]


class VerifierResult(BaseModel):
    verifier_result_id: str
    task_id: str
    model_id: str
    model_family: str
    provider: Literal["qiniu", "local_nuextract"] = "qiniu"
    verification_policy: Literal[
        "different_model_family",
        "same_model_secondary_verification",
    ] = "different_model_family"
    independent_model_family: bool = True
    reviewer_result_id: str
    transaction_ids: list[str] = Field(default_factory=list)
    transaction_decisions: list[VerifierTransactionDecision] = Field(default_factory=list)
    patch_ids: list[str] = Field(default_factory=list)
    verdict: Literal["accept", "reject", "abstain"]
    disagreements: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VerifierPayload(BaseModel):
    verdict: Literal["accept", "reject", "abstain"]
    disagreements: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    transaction_decisions: list[VerifierTransactionDecision] = Field(default_factory=list)

    @field_validator("disagreements", mode="before")
    @classmethod
    def normalize_disagreements(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
        if isinstance(value, (list, tuple)):
            return [
                item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in value
            ]
        return [str(value)]


class FinalReviewDecision(BaseModel):
    decision_id: str
    task_id: str
    outcome: Literal["auto_confirmed", "auto_corrected", "human_resolved", "human_required", "deferred", "rejected"]
    blocking_resolved: bool
    accepted_patch_ids: list[str] = Field(default_factory=list)
    reviewer_result_ids: list[str] = Field(default_factory=list)
    guard_result_ids: list[str] = Field(default_factory=list)
    verifier_result_ids: list[str] = Field(default_factory=list)
    reason: str
    decided_by: Literal["deterministic", "agent_consensus", "human"]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CandidateRevision(BaseModel):
    candidate_id: str
    task_id: str
    transaction_id: str | None = None
    based_on_run_id: str
    patch_ids: list[str] = Field(default_factory=list)
    target_snapshots: dict[str, Any] = Field(default_factory=dict)
    before_snapshots: dict[str, Any] = Field(default_factory=dict)
    after_snapshots: dict[str, Any] = Field(default_factory=dict)
    diffs: list[dict[str, Any]] = Field(default_factory=list)
    created_entity_ids: list[str] = Field(default_factory=list)
    retired_entity_ids: list[str] = Field(default_factory=list)
    created_structure_edge_ids: list[str] = Field(default_factory=list)
    status: Literal["proposed", "guard_failed", "verified", "rejected", "accepted"] = "proposed"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PatchTransactionIR(BaseModel):
    transaction_id: str
    task_id: str
    reviewer_result_id: str | None = None
    patch_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    required_target_ids: list[str] = Field(default_factory=list)
    status: Literal[
        "proposed",
        "guard_passed",
        "guard_failed",
        "verified",
        "accepted",
        "rejected",
    ] = "proposed"
    guard_result_id: str | None = None
    verifier_result_id: str | None = None
    failure_class: Literal[
        "none",
        "model_protocol",
        "system_contract",
        "evidence_missing",
        "verifier_disagreement",
        "semantic_ambiguity",
        "model_service",
        "rate_limit",
        "repeated_failure",
    ] = "none"
    failure_owner: Literal["none", "model", "system", "evidence", "service"] = "none"
    retryable: bool = True
    failure_fingerprint: str | None = None
    failure_messages: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ConflictGroup(BaseModel):
    conflict_id: str
    target_id: str
    conflict_type: str
    observation_refs: list[str] = Field(default_factory=list)
    patch_ids: list[str] = Field(default_factory=list)
    blocking: bool = True
    status: Literal["open", "auto_resolved", "resolved", "human_required"] = "open"
    routing_disposition: Literal[
        "unrouted",
        "blocking_task",
        "optional_task",
        "deterministically_resolved",
        "accepted_nonmaterial_difference",
        "human_required",
    ] = "unrouted"
    resolution: str | None = None


class RetiredEntityIR(BaseModel):
    entity_id: str
    entity_type: Literal["block", "table"]
    page_index: int
    disposition: Literal[
        "non_table_visual",
        "decoration",
        "duplicate_fragment",
        "duplicate_fallback",
    ]
    snapshot: dict[str, Any]
    canonical_target_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str
    source_task_id: str | None = None
    retired_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LocalTextBlock(BaseModel):
    text: str
    bbox: BoundingBox


class NativeTextExclusionIR(BaseModel):
    exclusion_id: str
    page_index: int
    bbox: BoundingBox
    reason: Literal["embedded_document_preview_microtext"]
    native_block_indices: list[int] = Field(default_factory=list)
    excluded_character_count: int = 0
    matched_layout_object_ids: list[str] = Field(default_factory=list)
    matched_figure_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class LocalTableCandidate(BaseModel):
    candidate_id: str | None = None
    bbox: BoundingBox
    row_count: int | None = None
    column_count: int | None = None
    cells: list[TableCellObservation] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


class LocalPageForensics(BaseModel):
    page_index: int
    width_points: float | None = None
    height_points: float | None = None
    native_text_length: int | None = None
    native_text: str | None = None
    native_text_preview: str | None = None
    native_text_for_comparison: str | None = None
    native_text_for_comparison_length: int | None = None
    native_text_excluded_figure_ids: list[str] = Field(default_factory=list)
    native_text_excluded_block_count: int = 0
    native_text_exclusions: list[NativeTextExclusionIR] = Field(default_factory=list)
    native_text_blocks: list[LocalTextBlock] = Field(default_factory=list)
    table_candidates: list[LocalTableCandidate] = Field(default_factory=list)
    page_image_path: str | None = None
    quality_flags: list[str] = Field(default_factory=list)


class LocalPdfForensics(BaseModel):
    pdf_path: str | None = None
    exists: bool = False
    page_count: int | None = None
    pages: list[LocalPageForensics] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "error", "blocking"]
    message: str
    target_id: str | None = None
    page_index: int | None = None


class ValidationReport(BaseModel):
    readiness: ReadinessStatus = "building"
    checks: dict[str, bool] = Field(default_factory=dict)
    metrics: dict[str, float | int] = Field(default_factory=dict)
    issues: list[ValidationIssue] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DocumentIRMetadata(BaseModel):
    run_id: str
    ocr_run_id: str
    document_id: str | None = Field(
        default=None,
        pattern=r"^(?:doc-sha256-[0-9a-f]{64}|doc-legacy-[0-9a-f]{24})$",
    )
    document_label: str | None = Field(default=None, max_length=240)
    external_document_id: str | None = Field(default=None, max_length=256)
    lineage_id: str | None = Field(default=None, pattern=r"^irl-[0-9a-f]{24}$")
    ir_revision: int = 1
    parent_ir_run_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    parser_adapter: str = "PaddleOCRVLApiAdapter"
    fusion_adapter: str = "ParserFusion-v0.2"
    pipeline_version: str = "document-pipeline-v0.12.1"
    source_pdf_path: str | None = None
    source_pdf_sha256: str | None = None
    ocr_manifest_path: str | None = None
    raw_jsonl_path: str | None = None
    local_forensics: LocalPdfForensics | None = None
    source_artifacts: dict[str, Any] = Field(default_factory=dict)


class DocumentIR(BaseModel):
    schema_version: SchemaVersion = "document-ir-v0.12"
    metadata: DocumentIRMetadata
    readiness: ReadinessStatus = "building"
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    coordinate_systems: list[CoordinateSystem] = Field(default_factory=list)
    pages: list[PageIR] = Field(default_factory=list)
    sections: list[SectionIR] = Field(default_factory=list)
    blocks: list[BlockIR] = Field(default_factory=list)
    layout_objects: list[LayoutObjectIR] = Field(default_factory=list)
    tables: list[TableIR] = Field(default_factory=list)
    logical_tables: list[LogicalTableIR] = Field(default_factory=list)
    figures: list[FigureIR] = Field(default_factory=list)
    spreads: list[SpreadIR] = Field(default_factory=list)
    structure_edges: list[StructureEdge] = Field(default_factory=list)
    review_tasks: list[VlmReviewTask] = Field(default_factory=list)
    correction_patches: list[CorrectionPatch] = Field(default_factory=list)
    reviewer_results: list[ReviewerResult] = Field(default_factory=list)
    model_calls: list[AgentModelCall] = Field(default_factory=list)
    atomic_patches: list[AtomicPatch] = Field(default_factory=list)
    guard_results: list[GuardResult] = Field(default_factory=list)
    verifier_results: list[VerifierResult] = Field(default_factory=list)
    final_decisions: list[FinalReviewDecision] = Field(default_factory=list)
    candidate_revisions: list[CandidateRevision] = Field(default_factory=list)
    patch_transactions: list[PatchTransactionIR] = Field(default_factory=list)
    conflict_groups: list[ConflictGroup] = Field(default_factory=list)
    retired_entities: list[RetiredEntityIR] = Field(default_factory=list)
    validation_report: ValidationReport = Field(default_factory=ValidationReport)
    quality_report: dict[str, Any] = Field(default_factory=dict)


class DocumentIrBuildRequest(BaseModel):
    ocr_run_id: str
    pdf_path: str | None = None
    run_id: str | None = None
    parent_ir_run_id: str | None = None
    document_label: str | None = Field(default=None, max_length=240)
    external_document_id: str | None = Field(default=None, max_length=256)
    render_dpi: int = Field(default=144, ge=72, le=300)
    execute_vlm_reviews: bool = False
    review_provider: Literal["qiniu", "local_nuextract"] = "qiniu"
    qiniu_api_key: str | None = None
    review_target_ids: list[str] = Field(default_factory=list)
    max_auto_review_rounds: int = Field(default=3, ge=1, le=3)
    repair_reason_code: str | None = None
    repair_requested_by: str | None = None
    repair_notes: str | None = None


class DocumentIrRepairRequest(BaseModel):
    parent_ir_run_id: str
    target_ids: list[str]
    reason_code: str
    requested_by: str
    notes: str | None = None
    execute_vlm_reviews: bool = True
    review_provider: Literal["qiniu", "local_nuextract"] = "qiniu"
    qiniu_api_key: str | None = None


class ReviewRetryRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    include_optional: bool = False
    max_auto_review_rounds: int = Field(default=3, ge=1, le=3)
    requested_by: str
    notes: str | None = None
    review_provider: Literal["qiniu", "local_nuextract"] = "qiniu"
    qiniu_api_key: str | None = None


class PatchDecisionRequest(BaseModel):
    action: Literal[
        "accept",
        "reject",
        "keep_current",
        "accept_current_nonmaterial",
        "continue_limited",
        "confirm_spread",
        "reject_spread",
    ]
    decided_by: str
    notes: str | None = None


class DocumentIrJobState(BaseModel):
    run_id: str
    ocr_run_id: str
    status: Literal["queued", "running", "done", "failed", "cancelled", "interrupted"]
    message: str
    output_dir: Path
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    manifest_path: Path | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    telemetry: dict[str, Any] = Field(default_factory=dict)


class DocumentIrBuildResult(BaseModel):
    run_id: str
    ocr_run_id: str
    output_dir: Path
    manifest_path: Path
    document_ir_path: Path
    quality_report_path: Path
    validation_report_path: Path
    review_tasks_path: Path
    page_count: int
    block_count: int
    table_count: int
    figure_count: int
    spread_count: int
    review_task_count: int
    readiness: ReadinessStatus
    retention: dict[str, Any] = Field(default_factory=dict)
