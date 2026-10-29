from __future__ import annotations
from typing import Any, Literal
from pydantic import Field, model_validator
from .base import StrictModel

class EvidenceRequirement(StrictModel):
    field: str = Field(min_length=1)
    level: Literal["cell", "row", "column_header", "table", "footnote", "text", "report"]
    required: bool = True
    note: str | None = None
    purpose: Literal["target_confirmation"] = "target_confirmation"


class RetrievalProfile(StrictModel):
    source_priority: list[Literal["text","table"]] = Field(default_factory=list)
    positive_terms: list[str] = Field(min_length=1)
    negative_terms: list[str] = Field(default_factory=list)
    structural_anchors: list[str] = Field(default_factory=list)
    topic_term_groups: list[list[str]] = Field(default_factory=list)
    role_term_groups: list[list[str]] = Field(default_factory=list)
    context_terms: list[str] = Field(default_factory=list)
    expected_candidate_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def usable_terms(self):
        if any(not term.strip() for term in self.positive_terms):
            raise ValueError("Retrieval terms cannot be blank")
        if set(self.positive_terms) & set(self.negative_terms):
            raise ValueError("A term cannot be both required and excluded")
        return self


class AcceptanceSample(StrictModel):
    """An expectation bound to a real run; it is evidence of behaviour, not a value source."""
    run_ref: str = Field(min_length=1)
    report_label: str | None = None
    scope_id: str | None = None
    expected_value_raw: str | None = None
    expected_absent: bool = False

    @model_validator(mode="after")
    def one_expectation(self):
        if self.expected_absent and self.expected_value_raw is not None:
            raise ValueError("An absence sample cannot also carry a value")
        if not self.expected_absent and self.expected_value_raw is None:
            raise ValueError("A value sample requires its expected printed value")
        return self


class AcceptanceProfile(StrictModel):
    minimum_reports: int = Field(default=1, ge=1)
    samples: list[AcceptanceSample] = Field(default_factory=list)


class PublicationProfile(StrictModel):
    aggregation: Literal["none", "sum_disjoint_members", "ratio_recompute", "do_not_sum"] = "none"
    comparison_axes: list[str] = Field(default_factory=list)
    fixed_axes: dict[str, str] = Field(default_factory=dict)


READING_OPERATORS = ("numeric_label", "ghg_scope", "pollutant", "water", "waste", "statement")
READING_RULES = (
    "require_any", "require_all", "forbid_any", "forbid_any_unless", "forbid_if_all",
    "reject_component", "flag_review", "unit_dimension", "ratio_required", "ghg_unit",
    "ghg_scope", "ghg_scope_composition", "pollutant_medium", "water_concept", "waste_concept",
)
READING_SCOPES = ("text", "subject", "scope_text", "evidence", "unit", "all")


class ReadingRule(StrictModel):
    """One declarative concept rule; all clause-specific language lives in terms."""

    rule: str = Field(min_length=1)
    terms: list[str] = Field(default_factory=list)
    unless_terms: list[str] = Field(default_factory=list)
    if_terms: list[str] = Field(default_factory=list)
    scope: Literal["text", "subject", "scope_text", "evidence", "unit", "all"] = "text"
    reason: str | None = None
    flag: str | None = None
    allowed: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def known_rule(self):
        if self.rule not in READING_RULES:
            raise ValueError(f"Unknown reading rule: {self.rule}")
        if self.rule in {"require_any", "require_all", "forbid_any", "forbid_any_unless",
                         "flag_review"} and not self.terms:
            raise ValueError(f"Reading rule {self.rule} requires terms")
        if self.rule == "forbid_any_unless" and not self.unless_terms:
            raise ValueError("forbid_any_unless requires unless_terms")
        return self


class ReadingContract(StrictModel):
    """The clause's reading semantics: operator, rules and resolved vocabulary."""

    schema_version: Literal["clause-reading-1.0"] = "clause-reading-1.0"
    operator: Literal["numeric_label", "ghg_scope", "pollutant", "water", "waste", "statement"] = "numeric_label"
    rules: list[ReadingRule] = Field(default_factory=list)
    vocabulary: dict[str, list[str]] = Field(default_factory=dict)
    allow_regulatory_limit: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class ClauseRelationships(StrictModel):
    parent_clause: str | None = None
    share_partner: str | None = None
    siblings: list[str] = Field(default_factory=list)
    child_terms: list[str] = Field(default_factory=list)
    child_excludes: list[str] = Field(default_factory=list)
