from __future__ import annotations

from typing import Literal

from pydantic import Field

from extract_esg.contracts.base import ContractModel, SourceTrace, new_id


class DisclosureObject(ContractModel):
    id: str = Field(default_factory=lambda: new_id("disc"))
    report_id: str
    raw_label: str
    raw_text: str
    evidence_ids: list[str]
    disclosure_type: Literal["quantitative", "qualitative", "target", "policy", "risk", "governance", "methodology"]
    concept_candidates: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    source: SourceTrace


class QuantitativeObservationCandidate(ContractModel):
    id: str = Field(default_factory=lambda: new_id("obs_candidate"))
    disclosure_object_id: str
    raw_value_text: str
    parsed_value: float | None = None
    raw_unit: str | None = None
    period_text: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str]
    quality_flags: list[str] = Field(default_factory=list)


class QualitativeClaimCandidate(ContractModel):
    id: str = Field(default_factory=lambda: new_id("claim_candidate"))
    disclosure_object_id: str
    subject: str
    predicate: str
    object: str
    status: str | None = None
    qualifiers: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str]
    quality_flags: list[str] = Field(default_factory=list)


class MappingAssertion(ContractModel):
    id: str = Field(default_factory=lambda: new_id("mapping"))
    source_id: str
    target_id: str
    target_type: Literal["canonical_concept", "framework_requirement"]
    relation: Literal["exact", "broader", "narrower", "partial", "conditional", "alternative_measure", "not_equivalent"]
    rationale: str
    evidence_ids: list[str]
    source: SourceTrace
    review_state: Literal["candidate", "approved", "rejected", "review_required"] = "candidate"

