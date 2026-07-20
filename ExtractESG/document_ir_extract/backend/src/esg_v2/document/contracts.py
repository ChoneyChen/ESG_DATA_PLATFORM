from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


SchemaVersion = Literal["document-ir-v0.2", "document-ir-v0.3", "document-ir-v0.4"]
ReadinessStatus = Literal[
    "building",
    "auto_review_pending",
    "ready",
    "ready_with_warnings",
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
    bbox: BoundingBox | None = None
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
    continues_from_table_id: str | None = None
    continues_to_table_id: str | None = None
    bbox: BoundingBox | None = None
    crop_artifact_id: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    review_task_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


class FigureIR(BaseModel):
    figure_id: str
    page_index: int
    order: int
    image_path: str | None = None
    caption: str | None = None
    legend_text: list[str] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    crop_artifact_id: str | None = None
    visual_type: Literal["unknown", "chart", "diagram", "illustration", "photo", "icon", "decoration", "composite"] = "unknown"
    visual_status: Literal["candidate", "reviewed", "unresolved"] = "candidate"
    quality_flags: list[str] = Field(default_factory=list)
    review_task_ids: list[str] = Field(default_factory=list)
    source_trace: SourceTrace


StructureRelation = Literal[
    "contains",
    "reading_next",
    "caption_of",
    "footnote_of",
    "continues",
    "references",
]


class StructureEdge(BaseModel):
    edge_id: str
    source_id: str
    target_id: str
    relation: StructureRelation
    confidence: float = 1.0
    source: str = "deterministic"


ReviewTaskType = Literal[
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
    target_type: Literal["page", "block", "table", "figure", "cell", "section"]
    target_id: str
    reason_codes: list[str] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    blocking: bool = True


class VlmReviewTask(BaseModel):
    task_id: str
    task_type: ReviewTaskType
    target_type: Literal["page", "block", "table", "figure"]
    target_id: str
    page_index: int
    bbox: BoundingBox | None = None
    provider: str = "qiniu"
    model_name: str | None = None
    status: ReviewTaskStatus = "pending"
    priority: Literal["critical", "high", "normal", "low"] = "normal"
    blocking: bool = True
    compound_group_id: str | None = None
    scope: list[ReviewScopeItem] = Field(default_factory=list)
    max_attempts: int = Field(default=2, ge=1, le=3)
    attempt_count: int = 0
    reason_codes: list[str] = Field(default_factory=list)
    prompt_intent: str
    input_refs: list[str] = Field(default_factory=list)
    expected_schema: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    reviewer_result_ids: list[str] = Field(default_factory=list)
    guard_result_ids: list[str] = Field(default_factory=list)
    verifier_result_ids: list[str] = Field(default_factory=list)
    final_decision_id: str | None = None
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


ReviewVerdict = Literal["confirm", "correct", "abstain"]
PatchOperation = Literal[
    "confirm",
    "replace_block_text",
    "replace_cell_text",
    "set_table_grid",
    "insert_table_row",
    "set_bbox",
    "set_printed_page_label",
    "set_visual_type",
    "set_caption",
    "link_continuation",
    "merge_blocks",
    "split_block",
    "add_quality_flags",
    "replace_text",
    "replace_table",
]


class AtomicPatchProposal(BaseModel):
    target_type: Literal["page", "block", "table", "figure", "cell", "section"]
    target_id: str
    operation: PatchOperation
    field_path: str | None = None
    before_value: Any = None
    proposed_value: Any = None
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class ReviewerPayload(BaseModel):
    verdict: ReviewVerdict
    findings: list[str] = Field(default_factory=list)
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
    attempt: int
    verdict: ReviewVerdict
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
    status: Literal["succeeded", "failed", "invalid_response"]
    request_summary: dict[str, Any] = Field(default_factory=dict)
    response_payload: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    error: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AtomicPatch(BaseModel):
    patch_id: str
    source_task_id: str
    source_reviewer_result_id: str | None = None
    target_type: Literal["page", "block", "table", "figure", "cell", "section"]
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


class GuardResult(BaseModel):
    guard_result_id: str
    task_id: str
    patch_ids: list[str] = Field(default_factory=list)
    passed: bool
    requires_independent_verifier: bool = True
    checks: list[GuardCheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VerifierResult(BaseModel):
    verifier_result_id: str
    task_id: str
    model_id: str
    model_family: str
    reviewer_result_id: str
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

    @field_validator("disagreements", mode="before")
    @classmethod
    def normalize_disagreements(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


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
    based_on_run_id: str
    patch_ids: list[str] = Field(default_factory=list)
    target_snapshots: dict[str, Any] = Field(default_factory=dict)
    before_snapshots: dict[str, Any] = Field(default_factory=dict)
    after_snapshots: dict[str, Any] = Field(default_factory=dict)
    diffs: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["proposed", "guard_failed", "verified", "rejected", "accepted"] = "proposed"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ConflictGroup(BaseModel):
    conflict_id: str
    target_id: str
    conflict_type: str
    observation_refs: list[str] = Field(default_factory=list)
    patch_ids: list[str] = Field(default_factory=list)
    blocking: bool = True
    status: Literal["open", "auto_resolved", "resolved", "human_required"] = "open"
    resolution: str | None = None


class LocalTextBlock(BaseModel):
    text: str
    bbox: BoundingBox


class LocalTableCandidate(BaseModel):
    candidate_id: str | None = None
    bbox: BoundingBox
    row_count: int | None = None
    column_count: int | None = None
    cells: list[TableCellObservation] = Field(default_factory=list)


class LocalPageForensics(BaseModel):
    page_index: int
    width_points: float | None = None
    height_points: float | None = None
    native_text_length: int | None = None
    native_text: str | None = None
    native_text_preview: str | None = None
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
    ir_revision: int = 1
    parent_ir_run_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    parser_adapter: str = "PaddleOCRVLApiAdapter"
    fusion_adapter: str = "ParserFusion-v0.2"
    pipeline_version: str = "document-pipeline-v0.4.0"
    source_pdf_path: str | None = None
    source_pdf_sha256: str | None = None
    ocr_manifest_path: str | None = None
    raw_jsonl_path: str | None = None
    local_forensics: LocalPdfForensics | None = None
    source_artifacts: dict[str, Any] = Field(default_factory=dict)


class DocumentIR(BaseModel):
    schema_version: SchemaVersion = "document-ir-v0.4"
    metadata: DocumentIRMetadata
    readiness: ReadinessStatus = "building"
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    coordinate_systems: list[CoordinateSystem] = Field(default_factory=list)
    pages: list[PageIR] = Field(default_factory=list)
    sections: list[SectionIR] = Field(default_factory=list)
    blocks: list[BlockIR] = Field(default_factory=list)
    layout_objects: list[LayoutObjectIR] = Field(default_factory=list)
    tables: list[TableIR] = Field(default_factory=list)
    figures: list[FigureIR] = Field(default_factory=list)
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
    conflict_groups: list[ConflictGroup] = Field(default_factory=list)
    validation_report: ValidationReport = Field(default_factory=ValidationReport)
    quality_report: dict[str, Any] = Field(default_factory=dict)


class DocumentIrBuildRequest(BaseModel):
    ocr_run_id: str
    pdf_path: str | None = None
    run_id: str | None = None
    parent_ir_run_id: str | None = None
    render_dpi: int = Field(default=144, ge=72, le=300)
    execute_vlm_reviews: bool = False
    qiniu_api_key: str | None = None
    review_target_ids: list[str] = Field(default_factory=list)
    max_auto_review_rounds: int = Field(default=2, ge=1, le=2)
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
    qiniu_api_key: str | None = None


class PatchDecisionRequest(BaseModel):
    action: Literal["accept", "reject"]
    decided_by: str
    notes: str | None = None


class DocumentIrJobState(BaseModel):
    run_id: str
    ocr_run_id: str
    status: Literal["queued", "running", "done", "failed"]
    message: str
    output_dir: Path
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    manifest_path: Path | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


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
    review_task_count: int
    readiness: ReadinessStatus
