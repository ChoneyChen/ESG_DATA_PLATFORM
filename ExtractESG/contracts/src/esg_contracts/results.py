"""Extraction delivery is an independent, validated collection of raw facts and links."""

from typing import Literal
from pydantic import Field, JsonValue, model_validator
from .facts import FrozenModel, SourceFact, ClauseMapping, ClauseCoverage
from .evidence import DocumentReference


class RunError(FrozenModel):
    clause_key: str | None = None
    object_id: str | None = None
    error: str


class RunSummary(FrozenModel):
    selected_clauses: int
    processed_clauses: int
    source_facts: int
    quantitative: int
    qualitative: int
    model_calls: int
    elapsed_seconds: float
    errors: list[RunError] = Field(default_factory=list)


class FactCollection(FrozenModel):
    facts: list[SourceFact]
    clause_mappings: list[ClauseMapping]
    coverage: list[ClauseCoverage]


class OrganizationResult(FrozenModel):
    schema_version: Literal["organized-extraction-1.0"] = "organized-extraction-1.0"
    status: Literal["pending", "organized", "needs_confirmation", "partial", "failed"]
    url: str
    table_count: int | None = None
    unorganized_fact_count: int | None = None
    error: str | None = None


class FactBundle(FactCollection):
    schema_version: Literal["extraction-fact-bundle-2.1"] = "extraction-fact-bundle-2.1"
    engine_version: str
    engine_identity: dict[str, JsonValue]
    run_id: str
    document: DocumentReference
    clause_targets: dict[str, str]
    summary: RunSummary
    organization: OrganizationResult

    @model_validator(mode="after")
    def bound_collection(self):
        ids = {fact.source_fact_id for fact in self.facts}
        if len(ids) != len(self.facts):
            raise ValueError("Repeated fact identity")
        for fact in self.facts:
            if (fact.document_id, fact.ir_run_id, fact.ir_revision) != (
                self.document.document_id,
                self.document.ir_run_id,
                self.document.ir_revision,
            ):
                raise ValueError("Fact belongs to a different document revision")
            if (
                fact.report_resource
                and self.document.report_resource
                and fact.report_resource.sha256 != self.document.report_resource.sha256
            ):
                raise ValueError("Fact PDF differs from its document")
        for link in self.clause_mappings:
            if link.source_fact_id not in ids:
                raise ValueError("Mapping refers to an absent fact")
            if self.clause_targets.get(link.clause_key) != link.clause_digest:
                raise ValueError("Mapping differs from frozen target")
        for coverage in self.coverage:
            if self.clause_targets.get(coverage.clause_key) != coverage.clause_digest:
                raise ValueError("Coverage differs from frozen target")
        if (
            self.summary.source_facts != len(self.facts)
            or self.summary.quantitative
            != sum(f.kind == "quantitative" for f in self.facts)
            or self.summary.qualitative
            != sum(f.kind == "assertion" for f in self.facts)
        ):
            raise ValueError("Summary differs from raw facts")
        if self.summary.selected_clauses != len(
            self.clause_targets
        ) or self.summary.processed_clauses != len(self.coverage):
            raise ValueError(
                "Summary differs from selected clauses or processed coverage"
            )
        return self
