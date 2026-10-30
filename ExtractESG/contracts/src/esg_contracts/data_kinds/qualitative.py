"""Qualitative value contract: the literal statement is mandatory; annotations extend it."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from . import extensible_metadata
from .quantitative import QuantitativeValue


class QualitativeValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    statement_raw: str = Field(min_length=1)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def optional_annotations(cls, data):
        return extensible_metadata(
            cls, data, forbidden=set(QuantitativeValue.model_fields) - {"extensions"}
        )

    @model_validator(mode="after")
    def nonblank(self):
        if not self.statement_raw.strip():
            raise ValueError("A statement requires its exact source text")
        return self
