from __future__ import annotations

from enum import Enum
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


StableId = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", min_length=3),
]
SemanticVersion = Annotated[
    str,
    Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
PackageFormatVersion = Literal["1.0", "1.1"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValueType(str, Enum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    YEAR = "year"
    DATE_RANGE = "date_range"
    DECIMAL_RANGE = "decimal_range"
    ENUM = "enum"
    IDENTIFIER = "identifier"
    URI = "uri"
    HASH = "hash"


class RequirementLevel(str, Enum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    CONDITIONAL = "conditional"


class BindingStorage(str, Enum):
    CORE_FIELD = "core_field"
    ATTRIBUTE = "attribute"
    DIMENSION = "dimension"


class RecordClass(str, Enum):
    TASK = "reporting_task"
    QUANTITATIVE = "quantitative_observation"
    QUALITATIVE = "qualitative_assertion"
    ATTRIBUTE = "attribute_value"
    DIMENSION = "dimension_value"
    EVIDENCE = "evidence_reference"


class MetricDataClass(str, Enum):
    STRUCTURE = "structure"
    QUANTITATIVE = "quantitative"
    QUALITATIVE = "qualitative"
    MIXED = "mixed"


class ObligationClass(str, Enum):
    MANDATORY_IF_MATERIAL = "mandatory_if_material"
    ALTERNATIVE_IF_MATERIAL = "alternative_if_material"
    VOLUNTARY_IF_MATERIAL = "voluntary_if_material"
    CONDITIONAL_IF_MATERIAL = "conditional_if_material"
    CONDITIONAL_VOLUNTARY_IF_MATERIAL = "conditional_voluntary_if_material"


class RelationType(str, Enum):
    ALTERNATIVE_GROUP = "alternative_group"
    CONDITIONAL_ACTIVATION = "conditional_activation"
    REQUIRES = "requires"
    CONTEXTUALIZES = "contextualizes"


class PredicateOperator(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


class FieldDefinition(StrictModel):
    field_id: StableId
    value_type: ValueType
    required: bool
    nullable: bool = False
    code_set_id: StableId | None = None
    description: str


class RecordTypeDefinition(StrictModel):
    record_type: RecordClass
    primary_key: StableId
    description: str
    fields: list[FieldDefinition]

    @model_validator(mode="after")
    def check_fields(self) -> "RecordTypeDefinition":
        ids = [field.field_id for field in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate field in {self.record_type}")
        if self.primary_key not in ids:
            raise ValueError(f"primary key {self.primary_key} is not a field")
        return self


class CodeValue(StrictModel):
    code: StableId
    labels: dict[str, str]
    description: str | None = None


class CodeSetDefinition(StrictModel):
    code_set_id: StableId
    extensible: bool = False
    values: list[CodeValue]

    @model_validator(mode="after")
    def check_codes(self) -> "CodeSetDefinition":
        codes = [value.code for value in self.values]
        if not codes or len(codes) != len(set(codes)):
            raise ValueError(f"invalid or duplicate codes in {self.code_set_id}")
        return self


class DimensionDefinition(StrictModel):
    dimension_id: StableId
    value_type: ValueType
    labels: dict[str, str]
    code_set_id: StableId | None = None
    hierarchical: bool = False
    description: str


class UnitDefinition(StrictModel):
    unit_id: StableId
    symbol: str
    dimension: StableId
    labels: dict[str, str]
    scale_to_si: str | None = None


class CoreSchemaPackage(StrictModel):
    format_version: Literal["1.0"]
    core_schema_id: StableId
    core_schema_version: SemanticVersion
    record_types: list[RecordTypeDefinition]
    common_code_sets: list[CodeSetDefinition]
    common_dimensions: list[DimensionDefinition]
    common_units: list[UnitDefinition]

    @model_validator(mode="after")
    def check_uniqueness(self) -> "CoreSchemaPackage":
        for label, values in (
            ("record type", [item.record_type for item in self.record_types]),
            ("code set", [item.code_set_id for item in self.common_code_sets]),
            ("dimension", [item.dimension_id for item in self.common_dimensions]),
            ("unit", [item.unit_id for item in self.common_units]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label}")
        code_set_ids = {item.code_set_id for item in self.common_code_sets}
        for record in self.record_types:
            for field in record.fields:
                if field.code_set_id and field.code_set_id not in code_set_ids:
                    raise ValueError(
                        f"unknown core code set {field.code_set_id} on {record.record_type}.{field.field_id}"
                    )
        return self


class StandardIdentity(StrictModel):
    framework_id: StableId
    framework_version: str
    jurisdiction: str
    module_id: StableId
    disclosure_requirement: str
    effective_from: str
    effective_to: str | None = None


class PackageFileSet(StrictModel):
    metrics: str
    elements: str
    relations: str
    code_sets: str
    dimensions: str
    derivations: str
    validation_rules: str
    sources: str
    concepts: str | None = None


class PackageManifest(StrictModel):
    format_version: PackageFormatVersion
    package_id: StableId
    package_version: SemanticVersion
    status: Literal["draft", "released", "deprecated"]
    standard: StandardIdentity
    core_schema_id: StableId
    core_schema_version: SemanticVersion
    default_language: str
    supported_languages: list[str]
    files: PackageFileSet

    @model_validator(mode="after")
    def check_format_files(self) -> "PackageManifest":
        if self.format_version == "1.1" and not self.files.concepts:
            raise ValueError("format 1.1 packages require concepts.json")
        return self


class SourceReference(StrictModel):
    source_id: StableId
    title: str
    authority: Literal[
        "authoritative_law",
        "official_digital_taxonomy",
        "official_reproduction",
        "official_explanation",
        "non_authoritative_implementation_guidance",
    ]
    version_or_date: str
    locator: str
    url: str
    notes: str | None = None


class Cardinality(StrictModel):
    minimum: int = Field(ge=0)
    maximum: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_range(self) -> "Cardinality":
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum cardinality is lower than minimum")
        return self


class SemanticConcept(StrictModel):
    concept_id: StableId
    labels: dict[str, str]
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    abbreviations: list[str] = Field(default_factory=list)
    formulas: list[str] = Field(default_factory=list)
    broader_concept_ids: list[StableId] = Field(default_factory=list)
    related_concept_ids: list[StableId] = Field(default_factory=list)
    excluded_concept_ids: list[StableId] = Field(default_factory=list)
    applicable_context_concept_ids: list[StableId] = Field(default_factory=list)
    description: str
    source_refs: list[StableId]

    @model_validator(mode="after")
    def check_lexical_and_relational_shape(self) -> "SemanticConcept":
        if not self.labels or any(not value.strip() for value in self.labels.values()):
            raise ValueError("semantic concepts require non-empty preferred labels")
        lexical_values = [
            *self.labels.values(),
            *(value for values in self.aliases.values() for value in values),
            *self.abbreviations,
            *self.formulas,
        ]
        normalized = [" ".join(value.split()).casefold() for value in lexical_values]
        if any(not value for value in normalized):
            raise ValueError("semantic concept lexical variants cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"duplicate lexical variant in {self.concept_id}")
        relation_sets = [
            set(self.broader_concept_ids),
            set(self.related_concept_ids),
            set(self.excluded_concept_ids),
            set(self.applicable_context_concept_ids),
        ]
        if any(self.concept_id in values for values in relation_sets):
            raise ValueError("semantic concept cannot reference itself")
        if relation_sets[0] & relation_sets[2]:
            raise ValueError("a broader concept cannot also be excluded")
        return self


class MetricDefinition(StrictModel):
    metric_id: StableId
    source_datapoint_id: str
    disclosure_requirement: str
    paragraph: str
    application_requirements: list[str]
    labels: dict[str, str]
    official_data_type: str
    data_class: MetricDataClass
    value_family: StableId
    obligation_class: ObligationClass
    allowed_value_origins: list[Literal["reported", "derived"]]
    cardinality: Cardinality
    required_dimension_ids: list[StableId] = Field(default_factory=list)
    optional_dimension_ids: list[StableId] = Field(default_factory=list)
    description: str
    reporting_requirement: str
    source_refs: list[StableId]
    subject_concept_groups: list[list[StableId]] = Field(default_factory=list)
    context_concept_ids: list[StableId] = Field(default_factory=list)
    excluded_concept_ids: list[StableId] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_dimensions_and_origins(self) -> "MetricDefinition":
        if not self.allowed_value_origins:
            raise ValueError("a metric must allow at least one value origin")
        overlap = set(self.required_dimension_ids) & set(self.optional_dimension_ids)
        if overlap:
            raise ValueError(f"dimensions cannot be both required and optional: {sorted(overlap)}")
        if any(not group for group in self.subject_concept_groups):
            raise ValueError("subject concept groups cannot be empty")
        return self


class ValueContract(StrictModel):
    primary_type: ValueType
    fallback_types: list[ValueType] = Field(default_factory=list)
    code_set_id: StableId | None = None
    unit_dimension: StableId | None = None
    fixed_value: str | int | float | bool | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def check_contract(self) -> "ValueContract":
        if self.primary_type is ValueType.ENUM and self.code_set_id is None:
            raise ValueError("enum value contracts require a code set")
        if self.maximum is not None and self.minimum is not None and self.maximum < self.minimum:
            raise ValueError("maximum value is lower than minimum value")
        if self.primary_type in self.fallback_types:
            raise ValueError("primary value type cannot also be a fallback")
        return self


class ElementBinding(StrictModel):
    record_type: RecordClass
    storage: BindingStorage
    target: StableId


class ElementDefinition(StrictModel):
    element_id: StableId
    metric_id: StableId
    element_code: StableId
    labels: dict[str, str]
    description: str
    semantic_role: StableId
    requirement_level: RequirementLevel
    cardinality: Cardinality
    null_allowed: bool
    value_contract: ValueContract
    binding: ElementBinding
    source_refs: list[StableId]
    concept_ids: list[StableId] = Field(default_factory=list)
    value_concept_root_ids: list[StableId] = Field(default_factory=list)


class Predicate(StrictModel):
    path: str
    operator: PredicateOperator
    value: Any | None = None

    @model_validator(mode="after")
    def check_value(self) -> "Predicate":
        if self.operator in {PredicateOperator.EXISTS, PredicateOperator.NOT_EXISTS}:
            if self.value is not None:
                raise ValueError("exists predicates cannot carry a value")
        elif self.value is None:
            raise ValueError("comparison predicates require a value")
        return self


class MetricRelation(StrictModel):
    relation_id: StableId
    relation_type: RelationType
    source_metric_ids: list[StableId] = Field(default_factory=list)
    target_metric_ids: list[StableId] = Field(default_factory=list)
    member_metric_ids: list[StableId] = Field(default_factory=list)
    activation_predicate: Predicate | None = None
    policy: StableId
    description: str
    source_refs: list[StableId]


class DerivationOperand(StrictModel):
    operand_id: StableId
    role: StableId
    description: str
    source_metric_id: StableId | None = None


class DerivationRule(StrictModel):
    rule_id: StableId
    target_metric_id: StableId
    operation: Literal["ratio", "sum"]
    operands: list[DerivationOperand]
    required_match_dimensions: list[StableId]
    unit_policy: StableId
    guards: list[Predicate]
    execution_stage: Literal["data_processing"]
    reported_value_priority: bool
    source_refs: list[StableId]


class RequiredElementParameters(StrictModel):
    element_id: StableId


class FixedValueParameters(StrictModel):
    element_id: StableId
    expected: str | int | float | bool


class NumericRangeParameters(StrictModel):
    element_id: StableId
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def check_bounds(self) -> "NumericRangeParameters":
        if self.minimum is None and self.maximum is None:
            raise ValueError("a numeric range needs at least one bound")
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum value is lower than minimum value")
        return self


class CodeSetMembershipParameters(StrictModel):
    element_id: StableId
    code_set_id: StableId


class EvidenceRequiredParameters(StrictModel):
    when_value_origin: Literal["reported", "derived", "any"]


class MatchingContextParameters(StrictModel):
    fields: list[StableId] = Field(min_length=1)


class ValidationRule(StrictModel):
    rule_id: StableId
    target: StableId
    assertion: Literal[
        "required_element",
        "fixed_value",
        "numeric_range",
        "code_set_membership",
        "evidence_required",
        "matching_context",
    ]
    severity: Literal["error", "warning"]
    parameters: (
        RequiredElementParameters
        | FixedValueParameters
        | NumericRangeParameters
        | CodeSetMembershipParameters
        | EvidenceRequiredParameters
        | MatchingContextParameters
    )
    message: str

    @model_validator(mode="after")
    def check_parameter_type(self) -> "ValidationRule":
        expected = {
            "required_element": RequiredElementParameters,
            "fixed_value": FixedValueParameters,
            "numeric_range": NumericRangeParameters,
            "code_set_membership": CodeSetMembershipParameters,
            "evidence_required": EvidenceRequiredParameters,
            "matching_context": MatchingContextParameters,
        }[self.assertion]
        if not isinstance(self.parameters, expected):
            raise ValueError(f"parameters do not match assertion {self.assertion}")
        return self


class ModuleSource(StrictModel):
    manifest: PackageManifest
    metrics: list[MetricDefinition]
    elements: list[ElementDefinition]
    relations: list[MetricRelation]
    code_sets: list[CodeSetDefinition]
    dimensions: list[DimensionDefinition]
    derivations: list[DerivationRule]
    validation_rules: list[ValidationRule]
    sources: list[SourceReference]
    concepts: list[SemanticConcept] = Field(default_factory=list)


class SourceFileDigest(StrictModel):
    path: str
    sha256: Sha256


class CompilationMetadata(StrictModel):
    compiler_id: StableId
    compiler_version: SemanticVersion
    source_digest: Sha256
    source_files: list[SourceFileDigest]


class CompiledStandardPackage(StrictModel):
    format_version: PackageFormatVersion
    compilation: CompilationMetadata
    core: CoreSchemaPackage
    manifest: PackageManifest
    metrics: list[MetricDefinition]
    elements: list[ElementDefinition]
    elements_by_metric: dict[str, list[StableId]]
    relations: list[MetricRelation]
    code_sets: list[CodeSetDefinition]
    dimensions: list[DimensionDefinition]
    derivations: list[DerivationRule]
    validation_rules: list[ValidationRule]
    sources: list[SourceReference]
    concepts: list[SemanticConcept] = Field(default_factory=list)
