"""The document service exposes evidence objects, never storage directories."""

from __future__ import annotations
from typing import Literal
from esg_reports.contract import ReportResource
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from esg_data.periods import ReportedPeriod


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportingPeriodContext(ProtocolModel):
    basis: str
    evidence: str | None = None
    source_id: str | None = None
    page_index: int | None = Field(default=None, ge=0)
    inheritance: Literal["explicit_reference_only"] = "explicit_reference_only"


class DocumentReference(ProtocolModel):
    document_id: str
    ir_run_id: str
    ir_revision: int
    content_digest: str
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reporting_year: int | None = None
    reporting_period_raw: str | None = None
    limited_evidence: bool = False
    label: str | None = None
    report_resource: ReportResource | None = None
    search_scope: dict | None = None
    period_context: ReportingPeriodContext | None = None

    @model_serializer(mode="wrap")
    def preserve_frozen_shape(self, serializer):
        body = serializer(self)
        if self.period_context is None:
            body.pop("period_context", None)
        return body

    def evidence_identity(self):
        """Mutable report labels are provenance, separate from the fixed physical input."""
        return (
            self.document_id,
            self.ir_run_id,
            self.ir_revision,
            self.content_digest,
            self.limited_evidence,
        )


class SearchIntent(ProtocolModel):
    target_id: str
    contract_digest: str
    data_kind: Literal["quantitative", "qualitative"]
    positive_terms: list[str] = Field(min_length=1)
    negative_terms: list[str] = Field(default_factory=list)
    exact_terms: list[str] = Field(default_factory=list)
    source_priority: list[Literal["text", "table"]] = Field(default_factory=list)
    structural_anchors: list[str] = Field(default_factory=list)
    topic_term_groups: list[list[str]] = Field(default_factory=list)
    role_term_groups: list[list[str]] = Field(default_factory=list)
    required_term_groups: list[list[str]] = Field(default_factory=list)
    excluded_object_terms: list[str] = Field(default_factory=list)
    context_terms: list[str] = Field(default_factory=list)
    expected_candidate_types: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)


class ObjectReference(ProtocolModel):
    object_id: str
    kind: Literal["table", "text"]
    page_index: int
    score: float
    chunk_count: int = 1


class ObjectSearchPage(ProtocolModel):
    schema_version: Literal["object-search-page-1.0"] = "object-search-page-1.0"
    objects: list[ObjectReference]
    total: int = Field(ge=0)
    returned: int = Field(ge=0)
    next_cursor: str | None = None
    query_digest: str
    scope: dict

    @model_validator(mode="after")
    def bounded_page(self):
        if self.returned != len(self.objects) or self.returned > self.total:
            raise ValueError("Search page counts disagree")
        return self


class EvidenceCell(ProtocolModel):
    candidate_kind: Literal[
        "measurement", "formula", "identifier", "footnote", "date", "unresolved"
    ] = "measurement"
    page_index: int | None = Field(default=None, ge=0)
    source_object_id: str | None = None
    source_object_type: Literal["cell", "text_block"] = "cell"
    char_start: int | None = None
    char_end: int | None = None
    cell_id: str
    column_id: str
    raw_text: str
    bbox: dict[str, float | None] | None = None


class EvidenceRow(ProtocolModel):
    row_role: Literal["data", "header", "unknown"] = "unknown"
    label_sources: list[TextEvidence] = Field(default_factory=list)
    unit_sources: list[TextEvidence] = Field(default_factory=list)
    row_id: str
    label: str
    section: str | None = None
    unit_raw: str | None = None
    cells: list[EvidenceCell]


class TextEvidence(ProtocolModel):
    span_id: str | None = None
    char_start: int = 0
    object_type: Literal["cell", "text_block", "table"] = "text_block"
    object_id: str
    text: str
    page_index: int
    bbox: dict[str, float | None] | None = None


class FrameFragment(ProtocolModel):
    parent_digest: str
    reason: Literal["input_budget", "generation_incomplete"]
    completeness: Literal["fragment"] = "fragment"


class EvidenceFrame(ProtocolModel):
    object_id: str
    kind: Literal["table", "text"]
    page_index: int
    title: str | None = None
    columns: dict[str, list[str]] = Field(default_factory=dict)
    column_sources: dict[str, list[TextEvidence]] = Field(default_factory=dict)
    title_sources: list[TextEvidence] = Field(default_factory=list)
    rows: list[EvidenceRow] = Field(default_factory=list)
    texts: list[TextEvidence] = Field(default_factory=list)
    chunk_index: int = 0
    chunk_count: int = 1
    digest: str
    fragment: FrameFragment | None = None

    @model_serializer(mode="wrap")
    def preserve_frozen_shape(self, serializer):
        body = serializer(self)
        if self.fragment is None:
            body.pop("fragment", None)
        return body


class ColumnSemantics(ProtocolModel):
    column_id: str
    header_path: list[str]
    period: ReportedPeriod | None = None
    period_only: bool = False
    requires_identity_reading: bool = False


class EvidenceStructure(ProtocolModel):
    schema_version: Literal["evidence-structure-1.0"] = "evidence-structure-1.0"
    period_policy: Literal["all_reported"] = "all_reported"
    columns: list[ColumnSemantics]
    unresolved_columns: list[str] = Field(default_factory=list)


EvidenceRow.model_rebuild()
EvidenceFrame.model_rebuild()
