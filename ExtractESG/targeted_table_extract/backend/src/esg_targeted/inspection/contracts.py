from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from esg_targeted.contracts import StrictModel


class InspectionIssue(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    artifact_path: str | None = None
    record_id: str | None = None


class InspectionEvidence(StrictModel):
    evidence_id: str
    evidence_set_id: str
    evidence_role: str
    excerpt: str
    document_id: str
    ir_run_id: str
    ir_revision: int
    ir_object_type: str
    ir_object_id: str
    pdf_page_index: int
    pdf_page_number: int
    printed_page_label: str | None = None
    section_id: str | None = None
    section_title: str | None = None
    bbox: dict[str, Any] | None = None
    quote_sha256: str
    review_status: str


class InspectionValue(StrictModel):
    element_id: str
    label: dict[str, str] = Field(default_factory=dict)
    value: Any = None
    value_type: str | None = None
    unit_id: str | None = None
    raw_record: dict[str, Any]


class InspectionElementColumn(StrictModel):
    element_id: str
    element_code: str
    label: dict[str, str] = Field(default_factory=dict)
    value_type: str
    required: bool
    nullable: bool
    binding_target: str
    semantic_role: str
    unit_dimension: str | None = None
    fixed_value: Any = None


class InspectionElementCell(InspectionElementColumn):
    value: Any = None
    source: Literal["core_field", "attribute", "dimension", "fixed", "missing"]
    provenance_mode: Literal["candidate", "span", "visual"] | None = None


class InspectionFact(StrictModel):
    organization: dict[str, Any] = Field(default_factory=dict)
    fact_id: str
    fact_type: Literal["quantitative_observation", "qualitative_assertion"]
    metric_id: str
    display_value: str
    value_raw: str | None = None
    value_numeric: str | int | float | None = None
    comparator: str | None = None
    unit_raw: str | None = None
    unit_id: str | None = None
    statement_raw: str | None = None
    statement_summary: str | None = None
    reporting_period_raw: str | None = None
    reporting_year: int | None = None
    period_start: str | None = None
    period_end: str | None = None
    reporting_boundary: str | None = None
    confidence: float | None = None
    review_status: str
    source_kind: Literal["ir_table", "visual_table", "text", "unknown"] = "unknown"
    provenance_modes: list[Literal["candidate", "span", "visual"]] = Field(
        default_factory=list
    )
    quality_issues: list[InspectionIssue] = Field(default_factory=list)
    attributes: list[InspectionValue]
    dimensions: list[InspectionValue]
    element_cells: list[InspectionElementCell] = Field(default_factory=list)
    evidence: list[InspectionEvidence]
    raw_record: dict[str, Any]


class InspectionMetric(StrictModel):
    unmapped_measurements: list[dict[str, Any]] = Field(default_factory=list)
    logical_measurement_count: int = 0
    task_id: str | None = None
    metric_id: str
    source_datapoint_id: str
    labels: dict[str, str]
    data_class: str
    description: str
    status: str
    found_status: str
    review_status: str
    guard_accepted: bool | None = None
    attempts: int
    uncertainty_reason: str | None = None
    contract_validation_status: Literal["passed", "failed"] | None = None
    contract_error: str | None = None
    result_counts: dict[str, int]
    element_columns: list[InspectionElementColumn] = Field(default_factory=list)
    facts: list[InspectionFact]
    task_attributes: list[InspectionValue]
    task_dimensions: list[InspectionValue]
    guard: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    artifact_paths: dict[str, str]


class InspectionOverview(StrictModel):
    metric_count: int
    found_count: int
    partial_count: int
    not_found_count: int
    ambiguous_count: int
    pending_count: int
    guard_accepted_count: int
    human_review_count: int
    quantitative_fact_count: int
    qualitative_fact_count: int
    evidence_count: int


class ResultInspection(StrictModel):
    schema_version: str
    result_schema_version: str | None = None
    availability: Literal["building", "ready", "unavailable"]
    message: str
    job: dict[str, Any]
    model_execution: dict[str, Any] = Field(default_factory=dict)
    document: dict[str, Any]
    standard: dict[str, Any]
    overview: InspectionOverview
    integrity_status: Literal["passed", "warning", "failed", "pending"]
    integrity_issues: list[InspectionIssue]
    metrics: list[InspectionMetric]
    downloads: dict[str, str]
