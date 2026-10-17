"""Optional project clauses, separately versioned from immutable ESRS originals.

This module defines and compiles contracts. It neither changes a base package
nor evaluates arbitrary expressions or grants an HTTP caller review authority.
An authenticated application must supply ``ReviewAuthority`` from its own
principal, never from a submitted proposal. Model suggestions remain proposals.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path, PurePosixPath
from typing import Any, Literal
import tempfile
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .contracts import SemanticVersion, Sha256, StableId, StrictModel
from .errors import PackageIntegrityError
from .io import canonical_json_bytes, load_json, pretty_json_bytes, sha256_bytes


def _digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


class OriginalClauseLink(StrictModel):
    source_datapoint_id: str = Field(min_length=1)
    relationship: Literal["aggregate_component", "calculation_operand", "context"]


class OperandSelector(StrictModel):
    operand_id: StableId
    source_datapoint_id: str = Field(min_length=1)
    source_metric_id: StableId
    # These are predicates over reviewed normalized fields, not default values.
    required_axes: dict[str, list[str]] = Field(min_length=1)

    @model_validator(mode="after")
    def explicit_axes(self):
        for key, values in self.required_axes.items():
            if not key.strip() or not values or any(not v.strip() for v in values):
                raise ValueError("Selectors require explicit axis values")
            if set(values) & {"unknown", "unspecified", "not_reported", "unverified"}:
                raise ValueError("Unknown values cannot satisfy a calculation selector")
            _unique(values, "selector value")
        return self


class TargetAxisProjection(StrictModel):
    target_axis: str = Field(min_length=1)
    from_operand_id: StableId | None = None
    source_axis: str | None = None
    fixed_value: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if self.fixed_value is not None:
            if not self.fixed_value.strip() or self.from_operand_id or self.source_axis:
                raise ValueError("A fixed target axis cannot also copy an operand")
        elif not self.from_operand_id or not self.source_axis or not self.source_axis.strip():
            raise ValueError("A copied target axis requires an operand and source axis")
        return self


class ExtensionDerivationRule(StrictModel):
    rule_id: StableId
    rule_version: SemanticVersion
    operation: Literal["sum"]  # Bounded registry; no expression/eval interface.
    operands: list[OperandSelector] = Field(min_length=2)
    shared_match_axes: list[str] = Field(min_length=1)
    target_axis_projections: list[TargetAxisProjection] = Field(min_length=1)
    unit_policy: Literal["reviewed_versioned_conversion_to_target_unit"]
    arithmetic: Literal["exact_decimal_sum"]
    require_approved_operands: Literal[True] = True
    require_explicit_match_values: Literal[True] = True
    require_point_values: Literal[True] = True
    output_review_status: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def references(self):
        _unique([o.operand_id for o in self.operands], "operand ID")
        _unique(self.shared_match_axes, "shared match axis")
        _unique([p.target_axis for p in self.target_axis_projections], "target axis")
        if not {"entity_ref", "period_start", "period_end", "boundary_code", "value_role"} <= set(self.shared_match_axes):
            raise ValueError("Calculation must match entity, ISO period, boundary and value role")
        by_id = {o.operand_id: o for o in self.operands}
        for projection in self.target_axis_projections:
            if projection.from_operand_id:
                operand = by_id.get(projection.from_operand_id)
                if operand is None or projection.source_axis not in operand.required_axes:
                    raise ValueError("A projected axis must be constrained by its source operand")
        return self


class ComparisonContract(StrictModel):
    required_axes: list[str] = Field(min_length=1)
    allowed_values: dict[str, list[str]] = Field(default_factory=dict)
    missing_or_unknown_policy: Literal["display_only"] = "display_only"

    @model_validator(mode="after")
    def known_only(self):
        _unique(self.required_axes, "comparison axis")
        if not set(self.allowed_values) <= set(self.required_axes):
            raise ValueError("Comparison predicates must refer to required axes")
        for values in self.allowed_values.values():
            if not values or set(values) & {"unknown", "unspecified", "not_reported", "unverified"}:
                raise ValueError("Unknown values cannot establish comparability")
        return self


class NumericConstraints(StrictModel):
    minimum: str | None = Field(default=None, pattern=r"^[+-]?\d+(?:\.\d+)?$")
    maximum: str | None = Field(default=None, pattern=r"^[+-]?\d+(?:\.\d+)?$")

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum is not None and self.maximum is not None and Decimal(self.minimum) > Decimal(self.maximum):
            raise ValueError("Extension numeric bounds are reversed")
        return self


class ExtensionDefinition(StrictModel):
    schema_version: Literal["project-extension-definition-1.0"]
    extension_id: StableId
    definition_version: SemanticVersion
    clause_kind: Literal["project_extension"] = "project_extension"
    parent_package_id: StableId
    parent_package_version: SemanticVersion
    parent_source_digest: Sha256
    original_clause_links: list[OriginalClauseLink] = Field(min_length=1)
    labels: dict[str, str]
    description: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    package_core: bool = False
    core_selection_reason: str | None = None
    value_type: Literal["numeric"]
    value_constraints: NumericConstraints = Field(default_factory=NumericConstraints)
    canonical_unit: str = Field(min_length=1)
    canonical_unit_id: StableId
    unit_dimension: StableId
    allowed_value_origins: list[Literal["reported", "derived"]] = Field(min_length=1)
    identity_axes: list[str] = Field(min_length=1)
    reported_required_axes: dict[str, list[str]] = Field(min_length=1)
    reported_context_axes: list[str] = Field(min_length=1)
    # Missing context is preserved. These codes describe missingness, never
    # supply evidence for scope, amount basis, entity or accounting method.
    context_unknown_policy: Literal["preserve_missing_no_imputation"]
    comparison_contract: ComparisonContract
    derivation_rules: list[ExtensionDerivationRule] = Field(default_factory=list)
    exclusions: list[str] = Field(min_length=1)
    output_policy: Literal["explicit_selection_only"] = "explicit_selection_only"
    reported_calculated_reconciliation: Literal["retain_both_review_conflict"] = "retain_both_review_conflict"

    @model_validator(mode="after")
    def identity_and_references(self):
        if ".extension." not in self.extension_id:
            raise ValueError("Project extension IDs require an explicit extension namespace")
        if self.package_core and (not self.core_selection_reason or not self.core_selection_reason.strip()):
            raise ValueError("A package-core extension requires an authored selection reason")
        if not {"zh", "en"} <= set(self.labels) or any(not s.strip() for s in self.labels.values()):
            raise ValueError("Extension labels require nonempty Chinese and English text")
        _unique([r.source_datapoint_id for r in self.original_clause_links], "original clause link")
        _unique(self.allowed_value_origins, "value origin")
        _unique(self.identity_axes, "identity axis")
        _unique([r.rule_id for r in self.derivation_rules], "derivation rule")
        if not set(self.comparison_contract.required_axes) <= set(self.identity_axes):
            raise ValueError("Comparison axes must belong to the extension identity")
        for axis, values in self.reported_required_axes.items():
            if axis not in self.identity_axes or not values or any(not v.strip() for v in values):
                raise ValueError("Reported predicates must use declared identity axes")
        if not set(self.reported_context_axes) <= set(self.identity_axes):
            raise ValueError("Reported context axes must belong to the extension identity")
        if self.derivation_rules and "derived" not in self.allowed_value_origins:
            raise ValueError("Calculation rules require a derived value origin")
        for rule in self.derivation_rules:
            if not rule.rule_id.startswith(self.extension_id + ".derivation."):
                raise ValueError("Calculation rule IDs must belong to the extension namespace")
            if not set(rule.shared_match_axes) <= set(self.identity_axes):
                raise ValueError("Shared calculation axes must belong to the target identity")
            if not {p.target_axis for p in rule.target_axis_projections} <= set(self.identity_axes):
                raise ValueError("Projected axes must belong to the target identity")
            projected = {p.target_axis: p for p in rule.target_axis_projections}
            if set(self.identity_axes) - set(rule.shared_match_axes) - set(projected):
                raise ValueError("Calculation must provide every declared target identity axis")
            for axis, values in self.reported_required_axes.items():
                if axis in projected and projected[axis].fixed_value is not None and projected[axis].fixed_value not in values:
                    raise ValueError("A calculated target contradicts the extension's reported meaning")
        return self


class ExtensionProposal(StrictModel):
    schema_version: Literal["project-extension-proposal-1.0"]
    proposal_id: str = Field(min_length=1)
    proposer_kind: Literal["human", "model"]
    proposer_ref: str = Field(min_length=1)
    definition: ExtensionDefinition
    source_fact_refs: list[str] = Field(default_factory=list)
    status: Literal["proposed"] = "proposed"

    @model_validator(mode="after")
    def source_references(self):
        _unique(self.source_fact_refs, "proposal source fact reference")
        if any(not r.strip() for r in self.source_fact_refs):
            raise ValueError("Proposal source fact references cannot be empty")
        return self


class ReviewAuthority(StrictModel):
    """Trusted handler argument, not a field accepted in a proposal HTTP body."""
    actor_ref: str = Field(min_length=1)
    actor_kind: Literal["human"]
    verified_by: Literal["server_authentication"]
    capabilities: list[Literal["approve_extension_definition"]]


class ExtensionApproval(StrictModel):
    schema_version: Literal["project-extension-approval-1.0"]
    approval_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    definition_digest: Sha256
    decision: Literal["approved", "rejected"]
    actor_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decided_at: datetime

    @model_validator(mode="after")
    def aware_time(self):
        if self.decided_at.tzinfo is None:
            raise ValueError("Approval time requires a timezone")
        return self


class CompiledExtension(StrictModel):
    schema_version: Literal["project-extension-release-1.0"]
    definition: ExtensionDefinition
    definition_digest: Sha256
    approval: ExtensionApproval
    release_digest: Sha256


class ExtensionRegistration(StrictModel):
    extension_id: StableId
    definition_version: SemanticVersion
    parent_package_id: StableId
    parent_package_version: SemanticVersion
    parent_source_digest: Sha256
    definition_digest: Sha256
    release_digest: Sha256
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def relative_release_path(self):
        p = PurePosixPath(self.path)
        if p.is_absolute() or ".." in p.parts or "\\" in self.path or ":" in self.path or p.suffix != ".json":
            raise ValueError("Registered extension paths must stay inside their release root")
        return self


class ExtensionRegistry(StrictModel):
    schema_version: Literal["project-extension-registry-1.0"]
    registrations: list[ExtensionRegistration] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_versions(self):
        _unique([(r.extension_id, r.definition_version) for r in self.registrations], "extension release identity")
        _unique([r.path for r in self.registrations], "extension release path")
        return self


class ExtensionSelection(StrictModel):
    extension_id: StableId
    definition_version: SemanticVersion
    release_digest: Sha256
    selection_kind: Literal["custom_extension", "package_core_extension", "platform_major_extension"] = "custom_extension"
    value_origins: list[Literal["reported", "derived"]] = Field(min_length=1)
    derivation_rule_ids: list[StableId] = Field(default_factory=list)

    @model_validator(mode="after")
    def selection_unique(self):
        _unique(self.value_origins, "selected value origin")
        _unique(self.derivation_rule_ids, "selected derivation rule")
        if self.derivation_rule_ids and "derived" not in self.value_origins:
            raise ValueError("Selecting calculation rules requires the derived origin")
        return self


class ReviewedOperandLineage(StrictModel):
    """Immutable published input reference; review IDs must be checked by DB."""
    operand_id: StableId
    observation_id: UUID
    # Platform concepts and standard metrics have different ID namespaces.
    metric_id: StableId
    standard_metric_id: StableId
    metric_definition_version: SemanticVersion
    source_datapoint_id: str = Field(min_length=1)
    context_id: UUID
    context_revision: int = Field(ge=1)
    fact_review_decision_id: UUID
    mapping_review_decision_id: UUID
    numeric_value: str = Field(pattern=r"^[+-]?\d+(?:\.\d+)?$")
    canonical_unit: str = Field(min_length=1)
    normalization_rule_version: StableId
    normalization_rule_digest: Sha256
    reviewed_axes: dict[str, str]


class DerivedExtensionLineage(StrictModel):
    schema_version: Literal["project-extension-lineage-1.0"]
    value_origin: Literal["derived"] = "derived"
    extension_id: StableId
    definition_version: SemanticVersion
    release_digest: Sha256
    rule_id: StableId
    rule_version: SemanticVersion
    rule_digest: Sha256
    input_dataset_version: int = Field(ge=0)
    operands: list[ReviewedOperandLineage] = Field(min_length=2)
    output_numeric_value: str = Field(pattern=r"^[+-]?\d+(?:\.\d+)?$")
    canonical_unit: str = Field(min_length=1)
    arithmetic: Literal["exact_decimal_sum"] = "exact_decimal_sum"

    @model_validator(mode="after")
    def exact_inputs(self):
        _unique([o.operand_id for o in self.operands], "lineage operand ID")
        _unique([o.observation_id for o in self.operands], "lineage observation")
        if any(o.canonical_unit != self.canonical_unit for o in self.operands):
            raise ValueError("Lineage inputs must already use the reviewed target canonical unit")
        # Decimal arithmetic must remain exact for long disclosed coefficients.
        # This check validates a submitted receipt; production calculation and
        # review authority still belong to the publication transaction.
        from decimal import localcontext
        precision = max(len(o.numeric_value.replace(".", "").lstrip("+-")) for o in self.operands)
        scale = max(len(o.numeric_value.partition(".")[2]) for o in self.operands)
        with localcontext() as ctx:
            ctx.prec = precision + scale + len(str(len(self.operands))) + 2
            if sum((Decimal(o.numeric_value) for o in self.operands), Decimal(0)) != Decimal(self.output_numeric_value):
                raise ValueError("Derived lineage result differs from its exact decimal operands")
        return self


def decide_proposal(proposal: ExtensionProposal, *, authority: ReviewAuthority,
                    decision: Literal["approved", "rejected"], reason: str) -> ExtensionApproval:
    if "approve_extension_definition" not in authority.capabilities:
        raise PermissionError("Authenticated actor cannot approve extension definitions")
    if not reason.strip() or not authority.actor_ref.strip():
        raise ValueError("Extension review requires an identified actor and a substantive reason")
    return ExtensionApproval(schema_version="project-extension-approval-1.0", approval_id=str(uuid4()),
        proposal_id=proposal.proposal_id, definition_digest=_digest(proposal.definition.model_dump(mode="json")),
        decision=decision, actor_ref=authority.actor_ref, reason=reason, decided_at=datetime.now(timezone.utc))


def definition_digest(definition: ExtensionDefinition) -> str:
    return _digest(definition.model_dump(mode="json"))


def derivation_rule_digest(rule: ExtensionDerivationRule) -> str:
    return _digest(rule.model_dump(mode="json"))


def validate_parent(definition: ExtensionDefinition, parent) -> None:
    actual = (parent.manifest.package_id, parent.manifest.package_version, parent.compilation.source_digest)
    expected = (definition.parent_package_id, definition.parent_package_version, definition.parent_source_digest)
    if actual != expected:
        raise PackageIntegrityError("Extension parent package identity/version/digest mismatch")
    originals = {m.source_datapoint_id: m.metric_id for m in parent.metrics}
    metrics = {m.source_datapoint_id: m for m in parent.metrics}
    units = {u.unit_id: u for u in parent.core.common_units}
    if (definition.canonical_unit_id not in units or units[definition.canonical_unit_id].dimension != definition.unit_dimension
            or units[definition.canonical_unit_id].symbol != definition.canonical_unit):
        raise PackageIntegrityError("Extension target unit does not match the parent's Core unit dimension")
    links = {r.source_datapoint_id for r in definition.original_clause_links}
    if not links <= set(originals):
        raise PackageIntegrityError("Extension links refer to unknown original clauses")
    for rule in definition.derivation_rules:
        for operand in rule.operands:
            if operand.source_datapoint_id not in links or originals.get(operand.source_datapoint_id) != operand.source_metric_id:
                raise PackageIntegrityError("Extension operand differs from linked original metric")
            contract = metrics[operand.source_datapoint_id].measurement_contract
            if not contract or contract.unit_dimension != definition.unit_dimension:
                raise PackageIntegrityError("Sum operand and extension target have different unit dimensions")


def compile_extension(proposal: ExtensionProposal, *, parent, approval: ExtensionApproval) -> CompiledExtension:
    validate_parent(proposal.definition, parent)
    definition_digest = _digest(proposal.definition.model_dump(mode="json"))
    if approval.decision != "approved" or approval.proposal_id != proposal.proposal_id or approval.definition_digest != definition_digest:
        raise PackageIntegrityError("Compilation requires approval of this exact proposal definition")
    body = dict(schema_version="project-extension-release-1.0", definition=proposal.definition.model_dump(mode="json"),
                definition_digest=definition_digest, approval=approval.model_dump(mode="json"))
    return CompiledExtension(**body, release_digest=_digest(body))


def validate_release(value: dict | CompiledExtension, *, parent) -> CompiledExtension:
    release = value if isinstance(value, CompiledExtension) else CompiledExtension.model_validate(value)
    definition_digest = _digest(release.definition.model_dump(mode="json"))
    body = release.model_dump(mode="json", exclude={"release_digest"})
    if (release.definition_digest != definition_digest or release.approval.definition_digest != definition_digest
            or release.approval.decision != "approved" or release.release_digest != _digest(body)):
        raise PackageIntegrityError("Immutable extension release or approval digest differs")
    validate_parent(release.definition, parent)
    return release


def register_extension(registry: ExtensionRegistry, release: CompiledExtension, *, path: str, parent) -> ExtensionRegistry:
    validate_release(release, parent=parent)
    definition = release.definition
    registration = ExtensionRegistration(extension_id=definition.extension_id, definition_version=definition.definition_version,
        parent_package_id=definition.parent_package_id, parent_package_version=definition.parent_package_version,
        parent_source_digest=definition.parent_source_digest, definition_digest=release.definition_digest,
        release_digest=release.release_digest, path=path)
    existing = next((r for r in registry.registrations if (r.extension_id, r.definition_version) ==
                     (registration.extension_id, registration.definition_version)), None)
    if existing:
        if existing != registration:
            raise PackageIntegrityError("Immutable registered extension version differs")
        return registry
    return ExtensionRegistry(schema_version=registry.schema_version, registrations=[*registry.registrations, registration])


def write_extension_release(root: Path, registration: ExtensionRegistration, release: CompiledExtension, *, parent) -> Path:
    validate_release(release, parent=parent)
    # Compare every registration field before saving; a digest alone is not a
    # licence to store a release under another parent's display identity.
    registered = register_extension(ExtensionRegistry(schema_version="project-extension-registry-1.0"),
                                    release, path=registration.path, parent=parent).registrations[0]
    if registered != registration:
        raise PackageIntegrityError("Extension registration and release disagree")
    root = root.resolve()
    path = (root / registration.path).resolve()
    if not path.is_relative_to(root):
        raise PackageIntegrityError("Extension release path escapes its root")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = pretty_json_bytes(release.model_dump(mode="json"))
    fd, name = tempfile.mkstemp(prefix=".extension-compile-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Linking is atomic and refuses an existing immutable version. A failed
        # write cannot leave a partially written release at its registered path.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise PackageIntegrityError("Cannot overwrite an immutable extension release")
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_registered_extension(root: Path, registration: ExtensionRegistration, *, parent) -> CompiledExtension:
    root = root.resolve()
    path = (root / registration.path).resolve()
    if not path.is_relative_to(root):
        raise PackageIntegrityError("Extension release path escapes its root")
    release = validate_release(load_json(path), parent=parent)
    check = register_extension(ExtensionRegistry(schema_version="project-extension-registry-1.0"), release,
                               path=registration.path, parent=parent).registrations[0]
    if check != registration:
        raise PackageIntegrityError("Registered extension identity differs from release")
    return release


def validate_selection(selection: ExtensionSelection, release: CompiledExtension, *, parent) -> dict:
    validate_release(release, parent=parent)
    d = release.definition
    if (selection.extension_id, selection.definition_version, selection.release_digest) != (d.extension_id, d.definition_version, release.release_digest):
        raise PackageIntegrityError("Selected extension release snapshot differs")
    if not set(selection.value_origins) <= set(d.allowed_value_origins):
        raise PackageIntegrityError("Selected value origins are not supported")
    if selection.selection_kind in {"package_core_extension", "platform_major_extension"} and not d.package_core:
        raise PackageIntegrityError("A major/core extension must be a package-core extension")
    if not set(selection.derivation_rule_ids) <= {r.rule_id for r in d.derivation_rules}:
        raise PackageIntegrityError("Selected extension calculation rule does not exist")
    if "derived" in selection.value_origins and not selection.derivation_rule_ids:
        raise PackageIntegrityError("Derived selection must explicitly choose its calculation rules")
    rules = [r for r in d.derivation_rules if r.rule_id in selection.derivation_rule_ids]
    return {"extension_id": d.extension_id, "definition_version": d.definition_version,
            "release_digest": release.release_digest, "parent_package_id": d.parent_package_id,
            "package_core": d.package_core, "selection_kind": selection.selection_kind,
            "parent_package_version": d.parent_package_version, "parent_source_digest": d.parent_source_digest,
            "original_clause_links": [r.model_dump(mode="json") for r in d.original_clause_links],
            "selected_value_origins": selection.value_origins,
            "selected_derivation_rule_ids": selection.derivation_rule_ids,
            "internal_operand_metric_ids": sorted({o.source_metric_id for r in rules for o in r.operands}),
            "original_output_datapoint_ids": [], "extension_output_count": 1}


def comparison_verdict(definition: ExtensionDefinition, reviewed_axes: dict[str, Any]) -> dict:
    """Explain display eligibility; never mutate or impute reviewed evidence."""
    reasons = []
    for axis in definition.comparison_contract.required_axes:
        value = reviewed_axes.get(axis)
        if value is None or value == "" or value in ("unknown", "unspecified", "not_reported", "unverified"):
            reasons.append(f"missing_comparison_axis:{axis}")
        elif axis in definition.comparison_contract.allowed_values and value not in definition.comparison_contract.allowed_values[axis]:
            reasons.append(f"comparison_axis_not_allowed:{axis}")
    return {"comparison_eligibility": "not_comparable" if reasons else "comparable", "reason_codes": reasons}


def validate_lineage(lineage: DerivedExtensionLineage, release: CompiledExtension, *, parent) -> dict:
    """Validate rule/identity arithmetic against DB-resolved immutable inputs.

    The publication service must first resolve the observation, review and
    context IDs in ``input_dataset_version`` and compare these supplied fields
    to authoritative rows. A schema receipt is not proof of database approval.
    """
    validate_release(release, parent=parent)
    d = release.definition
    if (lineage.extension_id, lineage.definition_version, lineage.release_digest, lineage.canonical_unit) != (
            d.extension_id, d.definition_version, release.release_digest, d.canonical_unit):
        raise PackageIntegrityError("Lineage target differs from the compiled extension")
    value = Decimal(lineage.output_numeric_value)
    if ((d.value_constraints.minimum is not None and value < Decimal(d.value_constraints.minimum)) or
            (d.value_constraints.maximum is not None and value > Decimal(d.value_constraints.maximum))):
        raise PackageIntegrityError("Lineage result violates the extension value constraints")
    rule = next((r for r in d.derivation_rules if r.rule_id == lineage.rule_id), None)
    if rule is None or lineage.rule_version != rule.rule_version or lineage.rule_digest != _digest(rule.model_dump(mode="json")):
        raise PackageIntegrityError("Lineage calculation rule identity/digest differs")
    by_id = {o.operand_id: o for o in lineage.operands}
    if set(by_id) != {o.operand_id for o in rule.operands}:
        raise PackageIntegrityError("Lineage must contain exactly the declared operands")
    unknown = {"unknown", "unspecified", "not_reported", "unverified", ""}
    for selector in rule.operands:
        operand = by_id[selector.operand_id]
        if (operand.source_datapoint_id, operand.standard_metric_id) != (selector.source_datapoint_id, selector.source_metric_id):
            raise PackageIntegrityError("Lineage operand metric differs from its selector")
        for axis, allowed in selector.required_axes.items():
            if operand.reviewed_axes.get(axis) not in allowed:
                raise PackageIntegrityError(f"Lineage operand selector failed: {selector.operand_id}:{axis}")
    target_axes = {}
    for axis in rule.shared_match_axes:
        values = [o.reviewed_axes.get(axis) for o in lineage.operands]
        if any(v is None or v in unknown for v in values) or len(set(values)) != 1:
            raise PackageIntegrityError(f"Lineage shared identity mismatch or unknown: {axis}")
        target_axes[axis] = values[0]
    for projection in rule.target_axis_projections:
        target_axes[projection.target_axis] = projection.fixed_value if projection.fixed_value is not None else (
            by_id[projection.from_operand_id].reviewed_axes[projection.source_axis])
    try:
        start, end = date.fromisoformat(target_axes["period_start"]), date.fromisoformat(target_axes["period_end"])
        if start.isoformat() != target_axes["period_start"] or end.isoformat() != target_axes["period_end"] or start > end:
            raise ValueError("Noncanonical or reversed ISO dates")
    except ValueError as error:
        raise PackageIntegrityError("Lineage period must be ordered canonical ISO dates") from error
    return {"value_origin": "derived", "review_status": "pending", "reviewed_axes": target_axes,
            **comparison_verdict(d, target_axes)}
