"""Qualitative value contract: the literal statement is mandatory; annotations extend it."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_serializer, model_validator

from . import extensible_metadata
from .quantitative import QuantitativeValue


class QualitativeStructure(BaseModel):
    """Optional parts of the original assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    topic: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    action: str | None = Field(default=None, min_length=1)


class QualitativeValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    statement_raw: str = Field(min_length=1)
    structure: QualitativeStructure | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def optional_annotations(cls, data):
        prepared = extensible_metadata(
            cls, data, forbidden=set(QuantitativeValue.model_fields) - {"extensions"}
        )
        if isinstance(prepared, dict) and prepared.get("structure") is not None:
            try:
                prepared["structure"] = QualitativeStructure.model_validate(prepared["structure"])
            except ValidationError:
                prepared["extensions"]["_unparsed_structure"] = prepared.pop("structure")
        return prepared

    @model_serializer(mode="wrap")
    def preserve_frozen_shape(self, serializer):
        body = serializer(self)
        if self.structure is None:
            body.pop("structure", None)
        return body

    @model_validator(mode="after")
    def nonblank(self):
        if not self.statement_raw.strip():
            raise ValueError("A statement requires its exact source text")
        return self
