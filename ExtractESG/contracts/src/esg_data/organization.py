"""General extraction table contract; domain fields belong to independent clauses.

This module knows neither clause IDs nor pollutant/energy/employee vocabularies.
A table is a referenced view of raw facts, never a second value authority.
"""

from __future__ import annotations
from typing import Literal
import re, unicodedata
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from esg_reports.contract import ReportResource


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


FieldRole = Literal[
    "raw_value",
    "parsed_value",
    "unit",
    "reported_identity",
    "reported_dimension",
    "target_definition",
    "resource_identity",
    "source_label",
    "provenance",
    "match_state",
    "annotation",
]
FieldSource = Literal[
    "value",
    "identity",
    "dimension",
    "target",
    "resource",
    "fact",
    "evidence",
    "mapping",
    "extension",
]


class FieldBinding(Model):
    source: FieldSource
    key: str = Field(min_length=1)
    attribute: str | None = Field(default=None, min_length=1)


class VocabularyEntry(Model):
    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class CategoryMatch(Model):
    state: Literal["recognized", "unlisted", "unproven", "conflict"]
    code: str | None = None
    label: str | None = None
    definition_source: Literal["clause_vocabulary"] = "clause_vocabulary"

    @model_validator(mode="after")
    def verified_code_only(self):
        if self.state == "recognized" and (not self.code or not self.label):
            raise ValueError("A recognized category needs its declared code and label")
        if self.state != "recognized" and (
            self.code is not None or self.label is not None
        ):
            raise ValueError("Unverified categories cannot advertise an accepted code")
        return self


def _normalized(text):
    return unicodedata.normalize("NFKC", str(text)).casefold().strip()


def _literal_match(alias, text):
    alias = _normalized(alias)
    text = _normalized(text)
    if not alias:
        return False
    if alias.isascii() and re.fullmatch(r"[a-z0-9]+", alias):
        return bool(
            re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text)
        )
    return alias in text


class OrganizationField(Model):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
    label: str = Field(min_length=1)
    role: FieldRole
    binding: FieldBinding
    type_hint: Literal["text", "decimal", "integer", "boolean", "date", "json"] = "text"
    description: str | None = None
    required_for_confirmation: bool = False
    vocabulary: list[VocabularyEntry] = Field(default_factory=list)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def honest_role(self):
        allowed = {
            "value": {"raw_value", "parsed_value", "unit", "annotation"},
            "identity": {"reported_identity"},
            "dimension": {"reported_dimension"},
            "target": {"target_definition"},
            "resource": {"resource_identity", "provenance"},
            "fact": {"source_label", "provenance", "annotation"},
            "evidence": {"provenance"},
            "mapping": {"match_state", "provenance"},
            "extension": {"annotation"},
        }
        if self.role not in allowed[self.binding.source]:
            raise ValueError(
                "A field role cannot disguise target/resource metadata as reported identity"
            )
        if self.role == "raw_value" and (
            self.binding.attribute
            or self.binding.key not in {"value_raw", "statement_raw"}
        ):
            raise ValueError(
                "The raw-value column must bind the original produced value"
            )
        if self.required_for_confirmation and self.role not in {
            "raw_value",
            "reported_identity",
            "reported_dimension",
        }:
            raise ValueError(
                "Only source value and reported identity can be required for extraction confirmation"
            )
        if self.vocabulary and self.role not in {
            "reported_identity",
            "reported_dimension",
        }:
            raise ValueError(
                "Reported vocabularies cannot be used as target/resource defaults"
            )
        if len({entry.code for entry in self.vocabulary}) != len(self.vocabulary):
            raise ValueError("Repeated vocabulary code")
        return self

    def identity_flags(self, reported):
        if not reported or reported.get("state") not in {"stated", "inherited"}:
            return (
                ["output_identity_missing:" + self.key]
                if self.required_for_confirmation
                else []
            )
        match = self.category_match(reported)
        if match is None or match.state == "recognized":
            return []
        return ["output_vocabulary_" + match.state + ":" + self.key]

    def category_match(self, reported):
        if (
            not self.vocabulary
            or not reported
            or reported.get("state") not in {"stated", "inherited"}
        ):
            return None
        value = reported.get("value")
        raw = reported.get("raw_text") or ""
        entries = [
            item
            for item in self.vocabulary
            if any(
                _normalized(alias) == _normalized(value)
                for alias in [item.code, item.label, *item.aliases]
            )
        ]
        if len(entries) != 1:
            return CategoryMatch(state="unlisted" if not entries else "unproven")
        entry = entries[0]
        own = any(
            _literal_match(alias, raw)
            for alias in [entry.code, entry.label, *entry.aliases]
        )
        other = any(
            _literal_match(alias, raw)
            for item in self.vocabulary
            if item.code != entry.code
            for alias in [item.code, item.label, *item.aliases]
        )
        if own and not other:
            return CategoryMatch(state="recognized", code=entry.code, label=entry.label)
        return CategoryMatch(state="conflict" if other and not own else "unproven")


class SortField(Model):
    field: str
    direction: Literal["ascending", "descending"] = "ascending"


class MatrixDefinition(Model):
    row_axes: list[str] = Field(min_length=1)
    column_axes: list[str] = Field(min_length=1)
    value_fields: list[str] = Field(min_length=1)


class Presentation(Model):
    view: Literal["table", "statements", "matrix"] = "table"
    columns: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    sort_by: list[SortField] = Field(default_factory=list)
    matrix: MatrixDefinition | None = None


class OrganizationSpec(Model):
    schema_version: Literal["clause-organization-1.0"] = "clause-organization-1.0"
    title: str = Field(min_length=1)
    description: str | None = None
    fields: list[OrganizationField] = Field(min_length=1)
    row_axes: list[str] = Field(default_factory=list)
    presentation: Presentation = Field(default_factory=Presentation)
    include_additional_dimensions: bool = True
    include_extensions: bool = True
    curation: Literal["template", "sample"] = "template"
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def local_structure(self):
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Organization field identities must be unique")
        references = [
            *self.row_axes,
            *self.presentation.columns,
            *self.presentation.group_by,
            *[sort.field for sort in self.presentation.sort_by],
        ]
        if self.presentation.matrix:
            matrix = self.presentation.matrix
            references.extend(
                [*matrix.row_axes, *matrix.column_axes, *matrix.value_fields]
            )
            if len(set(matrix.row_axes + matrix.column_axes)) != len(
                matrix.row_axes + matrix.column_axes
            ):
                raise ValueError("Matrix row and column axes must be distinct")
        if self.presentation.view == "matrix" and self.presentation.matrix is None:
            raise ValueError(
                "A matrix view needs its local axes and value-field declaration"
            )
        if set(references) - set(keys):
            raise ValueError(
                "Table axes and presentation can refer only to this clause's own fields"
            )
        if len(set(self.row_axes)) != len(self.row_axes):
            raise ValueError("Repeated table row axis")
        return self

    def validate_kind(self, data_kind):
        expected = "value_raw" if data_kind == "quantitative" else "statement_raw"
        raw = [field for field in self.fields if field.role == "raw_value"]
        if len(raw) != 1 or raw[0].binding.key != expected:
            raise ValueError(
                "Each clause table requires exactly its own kind of original-value column"
            )
        if (
            self.presentation.matrix
            and raw[0].key not in self.presentation.matrix.value_fields
        ):
            raise ValueError("A matrix must keep the original-value field visible")
        return self

    def reading_fields(self):
        return [
            {
                "axis": field.binding.key,
                "label": field.label,
                "description": field.description,
                "source": field.binding.source,
                "required_for_confirmation": field.required_for_confirmation,
                "vocabulary": [
                    entry.model_dump(mode="json") for entry in field.vocabulary
                ],
            }
            for field in self.fields
            if field.binding.source in {"identity", "dimension"}
        ]

    def confirmation_flags(self, identity):
        flags = []
        for field in self.fields:
            if field.binding.source == "identity":
                reported = identity.get(field.binding.key)
            elif field.binding.source == "dimension":
                reported = identity.get("dimensions", {}).get(field.binding.key)
            else:
                continue
            flags.extend(field.identity_flags(reported))
        return list(dict.fromkeys(flags))


class EvidenceReference(Model):
    source_fact_id: str = Field(min_length=1)
    container: Literal["fact", "identity", "dimension"]
    field_key: str | None = None
    index: int = Field(ge=0)


class OrganizedCell(Model):
    state: Literal[
        "available", "not_stated", "not_read", "conflict", "not_applicable"
    ] = "not_read"
    origin: FieldSource
    value: JsonValue = None
    raw_text: str | None = None
    reported_state: (
        Literal["stated", "inherited", "not_stated", "not_read", "conflict"] | None
    ) = None
    classification: CategoryMatch | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class OrganizedRow(Model):
    row_id: str
    source_fact_id: str
    match: Literal["match", "uncertain", "different", "unmapped"]
    cells: dict[str, OrganizedCell]
    flags: list[str] = Field(default_factory=list)
    result_class: Literal["target", "other", "unresolved"] = "unresolved"


class RowGroup(Model):
    group_id: str
    keys: dict[str, OrganizedCell]
    row_ids: list[str]


class AxisCoordinate(Model):
    coordinate_id: str
    keys: dict[str, OrganizedCell]


class MatrixCell(Model):
    row_coordinate_id: str
    column_coordinate_id: str
    row_ids: list[str] = Field(min_length=1)
    state: Literal["single", "multiple"]


class MatrixView(Model):
    rows: list[AxisCoordinate]
    columns: list[AxisCoordinate]
    value_fields: list[str]
    cells: list[MatrixCell]
    aggregation_performed: Literal[False] = False


class OrganizedTable(Model):
    table_id: str
    clause_key: str
    clause_digest: str
    data_kind: Literal["quantitative", "qualitative"]
    definition: OrganizationSpec
    rows: list[OrganizedRow] = Field(default_factory=list)
    groups: list[RowGroup] = Field(default_factory=list)
    matrix: MatrixView | None = None
    status: Literal["empty", "organized", "needs_confirmation", "failed"] = "empty"
    flags: list[str] = Field(default_factory=list)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class OrganizedDataset(Model):
    schema_version: Literal["organized-extraction-1.0", "organized-extraction-1.1"] = "organized-extraction-1.1"
    run_id: str
    document_id: str
    ir_run_id: str
    ir_revision: int = Field(ge=1)
    report_resource: ReportResource | None = None
    tables: list[OrganizedTable] = Field(default_factory=list)
    unorganized_fact_ids: list[str] = Field(default_factory=list)
    status: Literal["pending", "organized", "needs_confirmation", "partial"] = "pending"
    extensions: dict[str, JsonValue] = Field(default_factory=dict)
