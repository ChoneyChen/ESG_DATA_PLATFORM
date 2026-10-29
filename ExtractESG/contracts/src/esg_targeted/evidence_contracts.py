"""The document service exposes evidence objects, never storage directories."""
from __future__ import annotations
from typing import Literal
from esg_reports.contract import ReportResource
from pydantic import BaseModel, ConfigDict, Field

class ProtocolModel(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)

class DocumentReference(ProtocolModel):
    document_id: str
    ir_run_id: str
    ir_revision: int
    content_digest: str
    reporting_year: int | None = None
    reporting_period_raw: str | None = None
    limited_evidence: bool = False
    label: str | None = None
    report_resource: ReportResource | None = None

class SearchIntent(ProtocolModel):
    target_id: str
    contract_digest: str
    data_kind: Literal['quantitative','qualitative']
    positive_terms: list[str] = Field(min_length=1)
    negative_terms: list[str] = Field(default_factory=list)
    exact_terms: list[str] = Field(default_factory=list)
    source_priority: list[Literal["text","table"]] = Field(default_factory=list)
    structural_anchors: list[str] = Field(default_factory=list)
    topic_term_groups: list[list[str]] = Field(default_factory=list)
    role_term_groups: list[list[str]] = Field(default_factory=list)
    context_terms: list[str] = Field(default_factory=list)
    limit: int = Field(default=8,ge=1,le=100)

class ObjectReference(ProtocolModel):
    object_id: str
    kind: Literal['table','text']
    page_index: int
    score: float
    chunk_count: int = 1

class EvidenceCell(ProtocolModel):
    page_index: int | None = Field(default=None,ge=0)
    source_object_id: str | None = None
    source_object_type: Literal["cell","text_block"] = "cell"
    char_start: int | None = None
    char_end: int | None = None
    cell_id: str
    column_id: str
    raw_text: str
    bbox: dict[str,float | None] | None = None

class EvidenceRow(ProtocolModel):
    row_role: Literal["data","header","unknown"] = "unknown"
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
    object_type: Literal["cell","text_block","table"] = "text_block"
    object_id: str
    text: str
    page_index: int
    bbox: dict[str,float | None] | None = None

class EvidenceFrame(ProtocolModel):
    object_id: str
    kind: Literal['table','text']
    page_index: int
    title: str | None = None
    columns: dict[str,list[str]] = Field(default_factory=dict)
    column_sources: dict[str,list[TextEvidence]] = Field(default_factory=dict)
    title_sources: list[TextEvidence] = Field(default_factory=list)
    rows: list[EvidenceRow] = Field(default_factory=list)
    texts: list[TextEvidence] = Field(default_factory=list)
    chunk_index: int = 0
    chunk_count: int = 1
    digest: str

EvidenceRow.model_rebuild()
EvidenceFrame.model_rebuild()
