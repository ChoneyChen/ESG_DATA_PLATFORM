"""The independent clause contract; no extraction engine or aggregate package dependency."""
from __future__ import annotations
from typing import Literal
from pydantic import Field, model_validator
from .base import StrictModel, digest
from .parts import (ReadingContract, RetrievalProfile, EvidenceRequirement,
                    AcceptanceProfile, ClauseRelationships)
from .scope import OutputScope

class ValueContract(StrictModel):
    canonical_unit: str | None = None
    unit_dimension: str | None = None
    value_origin: Literal['reported','derived','either'] = 'reported'
    value_role: str = 'actual'
    identity_axes: list[str] = Field(default_factory=list)
    fixed_axes: dict[str,str] = Field(default_factory=dict)
    comparison_axes: list[str] = Field(default_factory=list)
    comparison_requirements: list[str] = Field(default_factory=list)
    aggregation: Literal['none','sum_disjoint_members','ratio_recompute','do_not_sum'] = 'none'

class ClauseContract(StrictModel):
    schema_version: Literal['clause-contract-1.0'] = 'clause-contract-1.0'
    clause_key: str = Field(min_length=1, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    group_id: str = Field(min_length=1, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    labels: dict[str,str]
    definition: str = Field(min_length=1)
    distinguish_from: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(min_length=1)
    data_kind: Literal['quantitative','qualitative']
    value: ValueContract
    output_scopes: list[OutputScope] = Field(min_length=1)
    reading: ReadingContract
    retrieval: RetrievalProfile
    evidence: list[EvidenceRequirement] = Field(min_length=1)
    acceptance: AcceptanceProfile = Field(default_factory=AcceptanceProfile)
    relationships: ClauseRelationships = Field(default_factory=ClauseRelationships)
    exact_labels: list[str] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
    readiness: Literal['ready','blocked'] = 'ready'
    blocking_reasons: list[str] = Field(default_factory=list)
    clause_digest: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def coherent(self):
        if (self.readiness=='blocked') != bool(self.blocking_reasons):
            raise ValueError('Blocked clauses require explicit unresolved requirements')
        if self.data_kind=='qualitative':
            if self.value.canonical_unit or self.reading.operator!='statement':
                raise ValueError('A qualitative clause requires statement reading and no numerical unit')
        elif self.reading.operator=='statement':
            raise ValueError('A quantitative clause requires a quantitative reading operator')
        if self.data_kind=='quantitative' and self.readiness=='ready' and not self.value.canonical_unit:
            raise ValueError('A ready quantitative clause requires its declared unit')
        if len({scope.scope_id for scope in self.output_scopes})!=len(self.output_scopes):
            raise ValueError('Repeated output scope identity')
        if not any(scope.required for scope in self.output_scopes):
            raise ValueError('At least one output scope must be required')
        return self

    @property
    def title(self):
        return self.labels.get('zh') or self.labels.get('en') or self.clause_key

    @property
    def dimension(self):
        return self.value.unit_dimension

    def semantic_digest(self):
        return digest(self.model_dump(mode='json',exclude={'clause_digest'}))

    def verify(self):
        if self.clause_digest!=self.semantic_digest():
            raise ValueError(f'Clause content digest differs: {self.clause_key}')
        return self

    def seal(self):
        return self.model_copy(update={'clause_digest':self.semantic_digest()})
