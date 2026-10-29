"""Pipeline records: evidence/facts/goals are independent contracts."""
from __future__ import annotations
from typing import Literal, Annotated
from esg_reports.contract import ReportResource
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from .data_kinds import extensible_metadata
from .data_kinds.quantitative import QuantitativeValue
from .data_kinds.qualitative import QualitativeValue

class FrozenModel(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)

class EvidenceLocation(FrozenModel):
    object_id: str = Field(min_length=1)
    object_type: Literal['cell','text_block','table','report']
    page_index: int | None = Field(default=None,ge=0)
    excerpt: str = Field(min_length=1)
    role: Literal['value','statement','row','column_header','unit','table','footnote','report']
    bbox: dict[str,float | None] | None = None
    row_id: str | None = None
    column_id: str | None = None
    char_start: int | None = None
    char_end: int | None = None

    @model_validator(mode='after')
    def physical_location(self):
        if self.object_type!='report' and self.page_index is None:
            raise ValueError('Physical evidence needs its actual PDF page')
        if (self.char_start is None)!=(self.char_end is None):
            raise ValueError('Character offsets must be supplied together')
        if self.char_start is not None and (self.char_start<0 or self.char_end-self.char_start!=len(self.excerpt)):
            raise ValueError('Character offsets must bind the exact source excerpt')
        return self

class ReportedField(FrozenModel):
    state: Literal['stated','inherited','not_stated','not_read','conflict'] = 'not_read'
    raw_text: str | None = None
    value: JsonValue = None
    evidence: list[EvidenceLocation] = Field(default_factory=list)

    @model_validator(mode='after')
    def supported_identity(self):
        if self.state in {'stated','inherited'} and (not self.raw_text or self.value is None or not self.evidence):
            raise ValueError('A stated/inherited field needs its literal source and evidence')
        if self.state in {'not_stated','not_read'} and (self.raw_text is not None or self.value is not None):
            raise ValueError('Unknown identity cannot carry an invented value')
        return self

class ReportedContext(FrozenModel):
    reporting_period: ReportedField = Field(default_factory=ReportedField)
    reporting_entity: ReportedField = Field(default_factory=ReportedField)
    entity_aggregation_role: ReportedField = Field(default_factory=ReportedField)
    reporting_boundary: ReportedField = Field(default_factory=ReportedField)
    measurement_method: ReportedField = Field(default_factory=ReportedField)
    value_role: ReportedField = Field(default_factory=ReportedField)
    aggregation_role: ReportedField = Field(default_factory=ReportedField)
    denominator: ReportedField = Field(default_factory=ReportedField)
    dimensions: dict[str,ReportedField] = Field(default_factory=dict)

class QuantitativePayload(QuantitativeValue):
    kind: Literal['quantitative']='quantitative'

class QualitativePayload(QualitativeValue):
    kind: Literal['assertion']='assertion'

class SourceFact(FrozenModel):
    schema_version: Literal['source-fact-2.0']='source-fact-2.0'
    source_fact_id: str
    source_anchor_id: str
    document_id: str
    ir_run_id: str
    ir_revision: int = Field(ge=1)
    interpretation_id: str
    report_resource: ReportResource | None = None
    value: Annotated[QuantitativePayload | QualitativePayload,Field(discriminator='kind')]
    source_row_label: str | None = None
    identity: ReportedContext = Field(default_factory=ReportedContext)
    evidence: list[EvidenceLocation] = Field(min_length=1)
    quality: Literal['grounded','needs_confirmation']='needs_confirmation'
    flags: list[str] = Field(default_factory=list)
    extensions: dict[str,JsonValue] = Field(default_factory=dict)

    @model_validator(mode='before')
    @classmethod
    def extensible_annotations(cls,data):
        return extensible_metadata(cls,data)

    @model_validator(mode='after')
    def literal_evidence(self):
        role='value' if self.value.kind=='quantitative' else 'statement'
        raw=self.value.value_raw if role=='value' else self.value.statement_raw
        if not any(item.role==role and item.object_type!='report' and item.excerpt==raw for item in self.evidence):
            raise ValueError('A source fact needs physical evidence for its exact raw value')
        return self

    @property
    def kind(self):
        return self.value.kind

class ClauseMapping(FrozenModel):
    clause_key: str
    clause_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_fact_id: str
    match: Literal['match','uncertain','different']
    reason: str
    scope_ids: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

class ClauseCoverage(FrozenModel):
    clause_key: str
    clause_digest: str
    state: Literal['covered','unconfirmed','not_found','not_covered','failed','cancelled']
    candidate_objects: int = 0
    inspected_objects: int = 0
    fact_count: int = 0
    required_scopes: list[str] = Field(default_factory=list)
    covered_scopes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

class PipelineBudget(FrozenModel):
    max_model_calls: int = Field(default=128,ge=1,le=2000)
    max_objects_per_clause: int = Field(default=8,ge=1,le=100)
    max_output_tokens: int = Field(default=4096,ge=128,le=8192)
    timeout_seconds: float = Field(default=14400,ge=1,le=86400)

class RunRequest(FrozenModel):
    ir_run_id: str = Field(min_length=1)
    clause_keys: list[str] = Field(default_factory=list)
    selection: str | None = None
    name: str = Field(default='报告提取',min_length=1,max_length=120)
    budget: PipelineBudget = Field(default_factory=PipelineBudget)

    @model_validator(mode='after')
    def one_selection(self):
        if bool(self.clause_keys)==bool(self.selection):
            raise ValueError('Provide clause identities or one named selection')
        return self
