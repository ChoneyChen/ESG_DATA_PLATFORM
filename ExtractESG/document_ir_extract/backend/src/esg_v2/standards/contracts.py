from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RequirementKind = Literal[
    "conditional_absence",
    "table_bundle",
    "scalar",
    "narrative",
]

DisclosureType = Literal[
    "quantitative_table",
    "quantitative_scalar",
    "policy",
    "action",
    "target",
    "narrative",
]

MultiplicityPolicy = Literal[
    "single_best",
    "all_instances",
    "group_by_dimension",
    "table_bundle",
]

DimensionRelation = Literal["co_present", "same_table", "cross_tab"]


class RequirementSlot(BaseModel):
    slot_id: str
    role: Literal["concept", "value", "unit", "period", "scope", "dimension", "statement"]
    required: bool = True
    aliases: list[str] = Field(default_factory=list)
    value_types: list[str] = Field(default_factory=list)
    minimum_distinct: int = Field(default=1, ge=1)
    relation: DimensionRelation = "co_present"
    description: str = ""


class StandardTaskSpec(BaseModel):
    task_id: str
    sequence: int
    framework: str
    framework_version: str = "source-template"
    esg_dimension: str | None = None
    topic_standard: str | None = None
    topic: str
    disclosure_requirement: str | None = None
    data_point_id: str
    paragraph: str | None = None
    related_ar: str | None = None
    official_text: str
    data_type: str | None = None
    rationale: str | None = None
    source_sheet: str
    source_row: int
    source_fields: dict[str, Any] = Field(default_factory=dict)


class RequirementExecutionSpec(BaseModel):
    profile_schema_version: str = "requirement-execution-spec-v2"
    requirement_id: str
    sequence: int
    framework: str
    framework_version: str
    topic_standard: str | None = None
    topic: str
    data_point_id: str
    requirement_kind: RequirementKind
    execution_strategy: Literal["conditional_rule", "specialized_rule", "generic_local_rule"] = "generic_local_rule"
    compiler_notes: list[str] = Field(default_factory=list)
    disclosure_type: DisclosureType = "narrative"
    concept_id: str = "unclassified"
    official_text: str
    query_text: str
    exact_terms: list[str] = Field(default_factory=list)
    alias_terms: list[str] = Field(default_factory=list)
    topic_terms: list[str] = Field(default_factory=list)
    topic_anchor_terms: list[str] = Field(default_factory=list)
    required_dimensions: dict[str, list[str]] = Field(default_factory=dict)
    optional_dimensions: dict[str, list[str]] = Field(default_factory=dict)
    required_slots: list[RequirementSlot] = Field(default_factory=list)
    measure_aliases: list[str] = Field(default_factory=list)
    negative_aliases: list[str] = Field(default_factory=list)
    unit_families: list[str] = Field(default_factory=list)
    multiplicity: MultiplicityPolicy = "single_best"
    requires_dimension_intersection: bool = False
    allow_explicit_zero_statement: bool = False
    period_policy: Literal["reporting_period_preferred", "any_period", "not_applicable"] = "any_period"
    scope_policy: Literal["preserve_all", "reporting_boundary_preferred", "not_applicable"] = "preserve_all"
    evidence_types: list[str] = Field(default_factory=list)
    excluded_evidence_types: list[str] = Field(default_factory=lambda: ["index_atom"])
    excluded_section_terms: list[str] = Field(default_factory=lambda: ["指標索引", "指标索引", "standards index"])
    absence_patterns: list[str] = Field(default_factory=list)
    positive_patterns: list[str] = Field(default_factory=list)
    applicability_rule: str | None = None
    source_sheet: str
    source_row: int
    source_fields: dict[str, Any] = Field(default_factory=dict)
    rulepack_version: str


# Backward-compatible import name. The workflow now compiles an execution contract,
# while existing adapters and API callers may still refer to RequirementProfile.
RequirementProfile = RequirementExecutionSpec


class CompiledTaskSet(BaseModel):
    schema_version: str = "targeted-task-set-v2"
    template_name: str
    template_sha256: str
    source_sheet: str
    requirements: list[RequirementExecutionSpec]
    warnings: list[str] = Field(default_factory=list)
