"""Clause extraction profile: one declaration that drives retrieval, extraction, review and publication.

The profile never contains executable code. It composes controlled vocabularies,
declared evidence requirements and acceptance samples, so a new clause can be
added without changing the pipeline.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from .clause_scope import (AGGREGATION_CODES, ENTITY_CODES, PERIOD_CODES, QUALIFIER_CODES,
                           VALUE_ROLE_CODES, OutputScope, make_scope)
from .contracts import SemanticVersion, StableId, StrictModel

EVIDENCE_LEVELS = ("cell", "row", "column_header", "table", "footnote", "text", "report")


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()


class EvidenceRequirement(StrictModel):
    field: str = Field(min_length=1)
    level: Literal["cell", "row", "column_header", "table", "footnote", "text", "report"]
    required: bool = True
    note: str | None = None


class RetrievalProfile(StrictModel):
    positive_terms: list[str] = Field(min_length=1)
    negative_terms: list[str] = Field(default_factory=list)
    structural_anchors: list[str] = Field(default_factory=list)

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


class ClauseRelationships(StrictModel):
    parent_clause: str | None = None
    share_partner: str | None = None
    siblings: list[str] = Field(default_factory=list)
    child_terms: list[str] = Field(default_factory=list)
    child_excludes: list[str] = Field(default_factory=list)


class ClauseExtractionProfile(StrictModel):
    schema_version: Literal["clause-extraction-profile-2.0"] = "clause-extraction-profile-2.0"
    clause_id: str = Field(min_length=3)
    clause_key: str = Field(min_length=1)
    clause_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_id: StableId
    package_version: SemanticVersion
    source_datapoint_id: str = Field(min_length=1)
    metric_id: StableId
    labels: dict[str, str]
    description: str
    display_label: str | None = None
    display_definition: str | None = None
    positive_definition: str | None = None
    distinguish_from: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(min_length=1)
    data_kind: Literal["quantitative", "qualitative"]
    value_type: Literal["numeric", "statement"]
    group: str | None = None
    text_group: str | None = None
    group_terms: list[str] = Field(default_factory=list)
    group_unit_terms: list[str] = Field(default_factory=list)
    relationships: ClauseRelationships = Field(default_factory=ClauseRelationships)
    exact_labels: list[str] = Field(default_factory=list)
    reading: ReadingContract = Field(default_factory=ReadingContract)
    canonical_unit: str | None = None
    unit_dimension: str | None = None
    value_origin: Literal["reported", "derived", "either"] = "reported"
    output_scopes: list[OutputScope] = Field(min_length=1)
    evidence: list[EvidenceRequirement] = Field(min_length=1)
    retrieval: RetrievalProfile
    acceptance: AcceptanceProfile = Field(default_factory=AcceptanceProfile)
    publication: PublicationProfile = Field(default_factory=PublicationProfile)
    contract_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def coherent(self):
        if not any(scope.required for scope in self.output_scopes):
            raise ValueError("A clause profile needs at least one required output scope")
        ids = [scope.scope_id for scope in self.output_scopes]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate output scope identity")
        if self.data_kind == "quantitative" and self.value_type != "numeric":
            raise ValueError("A quantitative clause must declare a numeric value type")
        if self.data_kind == "qualitative" and self.value_type != "statement":
            raise ValueError("A qualitative clause must declare a statement value type")
        if self.value_type == "statement" and self.canonical_unit:
            raise ValueError("A statement clause cannot declare a numeric unit")
        if self.value_type == "numeric" and not self.canonical_unit:
            raise ValueError("A numeric clause requires its canonical unit")
        if self.clause_key != self.source_datapoint_id:
            raise ValueError("Clause key must be the stable source datapoint identity")
        if self.reading.operator == "statement" and self.data_kind != "qualitative":
            raise ValueError("The statement reading operator requires a qualitative clause")
        if self.data_kind == "qualitative" and self.reading.operator != "statement":
            raise ValueError("A qualitative clause must use the statement reading operator")
        declared = {axis for scope in self.output_scopes for axis in scope.axes}
        for axis in self.publication.fixed_axes:
            if axis not in declared and axis not in {"value_role", "entity", "aggregation", "period", "method"}:
                raise ValueError(f"Publication fixes an axis no scope declares: {axis}")
        return self

    def contract_payload(self) -> dict:
        return {"clause_id": self.clause_id, "value_type": self.value_type,
                "canonical_unit": self.canonical_unit, "unit_dimension": self.unit_dimension,
                "exclusions": self.exclusions, "positive_definition": self.positive_definition,
                "output_scopes": [scope.canonical() for scope in self.output_scopes]}


def clause_id(package_id: str, package_version: str, source_datapoint_id: str) -> str:
    return f"{package_id}@{package_version}:{source_datapoint_id}"


def clause_content_digest(profile: ClauseExtractionProfile) -> str:
    """Stable identity of one clause's meaning, independent of its package version.

    Editing one clause changes only that clause's digest. Package version and
    the catalogue's own digest fields are provenance, not clause semantics.
    """
    payload = profile.model_dump(
        mode="json",
        exclude={"clause_id", "package_version", "clause_digest", "contract_digest"},
    )
    return _digest(payload)


def profile_digest(profile: ClauseExtractionProfile) -> str:
    return _digest(profile.model_dump(mode="json", exclude={"contract_digest"}))


def _scope_for_contract(contract: dict, *, fixed_axes: dict[str, str],
                        aggregation: str | None = None) -> list[OutputScope]:
    role = contract.get("value_role", "actual")
    # ``do_not_sum`` says the clause must not be aggregated across members; it
    # never says the clause's own value is a subtotal.
    aggregation = aggregation or "total"
    comparison = set(contract.get("comparison_axes") or [])
    period = {"target": "target_period", "baseline": "baseline_period"}.get(role, "annual")
    entity = "group"
    # Fixed values are part of the output scope: a clause fixed to
    # energy_source=renewable must declare that in the scope it emits, or the
    # publication contract would fix an axis no scope carries.
    base_axes = dict(fixed_axes)
    scope = make_scope(_scope_label(period, entity, aggregation, base_axes), period=period, entity=entity,
                       value_role="context" if role == "context" else role, aggregation=aggregation,
                       axes=base_axes)
    scopes = [scope]
    # A clause that compares Scope 2 by method needs both methods to be findable.
    # A clause already fixed to one method must not receive a scope for the
    # opposite method: that would declare two contradictory readings.
    method_fixed = "measurement_method" in fixed_axes or "scope_2_method" in fixed_axes
    if not method_fixed and ("scope_2_method" in comparison or "scope2_method" in comparison):
        for method in ("location_based", "market_based"):
            scopes.append(make_scope(f"{_method_label(method)}口径", period=period, entity=entity,
                                     value_role="actual", aggregation=aggregation, method=method,
                                     axes=base_axes, required=False))
    return scopes


def _scope_label(period: str, entity: str, aggregation: str, axes: dict[str, str]) -> str:
    parts = [PERIOD_CODES.get(period, period), ENTITY_CODES.get(entity, entity), AGGREGATION_CODES.get(aggregation, aggregation)]
    parts.extend(f"{key}={value}" for key, value in axes.items())
    return " · ".join(parts)


def _method_label(method: str) -> str:
    return {"location_based": "位置法", "market_based": "市场法"}.get(method, method)


def _evidence_requirements(metric: dict, value_type: str) -> list[EvidenceRequirement]:
    requirements = [
        EvidenceRequirement(field="value", level="cell", required=value_type == "numeric",
                            note="原值来自真实单元格" if value_type == "numeric" else "陈述文本必须可回读"),
        EvidenceRequirement(field="source_row_label", level="row"),
        EvidenceRequirement(field="reporting_period", level="column_header"),
        EvidenceRequirement(field="reporting_boundary", level="row", required=False,
                            note="边界可由表题或章节说明给出"),
    ]
    contract = metric.get("measurement_contract") or {}
    if value_type == "numeric":
        requirements.append(EvidenceRequirement(field="unit", level="row", required=True))
    for axis in (contract.get("required_dimensions") or []):
        requirements.append(EvidenceRequirement(field=axis, level="row", required=False))
    return requirements


def compile_clause_profile(metric: dict, *, package_id: str, package_version: str,
                           retrieval_terms: list[str] | None = None,
                           fixed_axes: dict[str, str] | None = None,
                           value_type: str | None = None,
                           aggregation: str | None = None,
                           data_kind: str | None = None,
                           reading: ReadingContract | None = None,
                           display_label: str | None = None,
                           display_definition: str | None = None,
                           group: str | None = None,
                           text_group: str | None = None,
                           group_terms: list[str] | None = None,
                           group_unit_terms: list[str] | None = None,
                           relationships: ClauseRelationships | None = None,
                           exact_labels: list[str] | None = None) -> ClauseExtractionProfile:
    """Compile one profile from an immutable package metric plus explicit policy.

    Everything the pipeline needs is derived here; nothing is inferred later from
    a free-text description. ``reading`` is the clause's own declared reading
    semantics; a clause without a declaration gets a conservative default.
    """
    contract = metric.get("measurement_contract") or {}
    if not contract:
        raise ValueError(f"Metric has no scientific contract: {metric.get('source_datapoint_id')}")
    kind = value_type or ("numeric" if metric.get("data_class") == "quantitative" else "statement")
    resolved_kind = data_kind or ("quantitative" if kind == "numeric" else "qualitative")
    if (resolved_kind == "quantitative") != (kind == "numeric"):
        raise ValueError(f"Clause {metric['source_datapoint_id']}: data kind and value type disagree")
    if reading is None:
        reading = _default_reading(contract, resolved_kind)
    if reading.operator == "statement" and resolved_kind != "qualitative":
        raise ValueError(f"Clause {metric['source_datapoint_id']}: statement reading requires qualitative")
    terms = [term for term in (retrieval_terms or []) if term and term.strip()]
    if not terms:
        terms = [metric.get("labels", {}).get("zh", ""), metric.get("source_datapoint_id", "")]
        terms = [term for term in terms if term.strip()]
    if not terms:
        raise ValueError("A clause profile needs at least one retrieval term")
    canonical_unit = metric.get("canonical_unit")
    source_id = metric["source_datapoint_id"]
    body = dict(
        clause_id=clause_id(package_id, package_version, source_id),
        clause_key=source_id, clause_digest="0" * 64,
        package_id=package_id, package_version=package_version,
        source_datapoint_id=source_id, metric_id=metric["metric_id"],
        labels=metric.get("labels") or {}, description=metric.get("description") or "",
        display_label=display_label, display_definition=display_definition,
        positive_definition=contract.get("positive_definition"),
        distinguish_from=list(contract.get("distinguish_from") or []),
        exclusions=list(contract.get("exclusions") or []) or ["标题、目录、空白、未披露状态不能作为带值事实"],
        data_kind=resolved_kind,
        value_type="numeric" if kind == "numeric" else "statement",
        group=group,
        text_group=text_group,
        group_terms=list(group_terms or []),
        group_unit_terms=list(group_unit_terms or []),
        relationships=relationships or ClauseRelationships(),
        exact_labels=list(exact_labels or []),
        reading=reading,
        canonical_unit=canonical_unit if kind == "numeric" else None,
        unit_dimension=(metric.get("unit_dimension") or contract.get("unit_dimension")) if kind == "numeric" else None,
        value_origin="reported" if "reported" in (metric.get("allowed_value_origins") or ["reported"]) else "derived",
        output_scopes=_scope_for_contract(contract, fixed_axes=fixed_axes or {}, aggregation=aggregation),
        evidence=_evidence_requirements(metric, "numeric" if kind == "numeric" else "statement"),
        retrieval=RetrievalProfile(positive_terms=terms),
        publication=PublicationProfile(aggregation=contract.get("aggregation", "none"),
                                       comparison_axes=list(contract.get("comparison_axes") or []),
                                       fixed_axes=fixed_axes or {}),
        contract_digest="0" * 64,
    )
    draft = ClauseExtractionProfile(**body)
    draft = draft.model_copy(update={"clause_digest": clause_content_digest(draft)})
    return draft.model_copy(update={"contract_digest": profile_digest(draft)})


def _default_reading(contract: dict, data_kind: str) -> ReadingContract:
    """A clause without an authored reading still declares its unit contract."""
    if data_kind == "qualitative":
        return ReadingContract(operator="statement")
    dimension = (contract.get("unit_dimension") or "").rsplit(".", 1)[-1]
    rules = []
    if dimension:
        rules.append(ReadingRule(rule="unit_dimension", allowed=[dimension],
                                 params={"allow_missing": True}))
    return ReadingContract(operator="numeric_label", rules=rules)


def validate_profile(profile: ClauseExtractionProfile) -> None:
    expected = profile_digest(profile)
    if profile.contract_digest != expected:
        raise ValueError("Clause profile digest differs from its content")


# Re-exported so a reader sees the whole vocabulary in one place.
TAXONOMY_AXES = {"period": PERIOD_CODES, "entity": ENTITY_CODES, "value_role": VALUE_ROLE_CODES,
                 "aggregation": AGGREGATION_CODES, "qualifier": QUALIFIER_CODES}
