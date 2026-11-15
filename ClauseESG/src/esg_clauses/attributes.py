"""One clause-owned definition per reported attribute, independent of display columns."""
from typing import Literal
import re
from pydantic import Field, field_validator, model_serializer
from .base import StrictModel
from esg_data.categories import VocabularyEntry, category_match


class AttributeDefinition(StrictModel):
    label: str
    source: Literal["identity", "dimension"]
    description: str | None = None
    reading_instruction: str | None = None
    vocabulary: list[VocabularyEntry] = Field(default_factory=list)
    context_terms: list[str] = Field(default_factory=list)
    context_levels: list[Literal["local", "report"]] = Field(default_factory=lambda: ["local", "report"])
    value_pattern: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("value_pattern")
    @classmethod
    def valid_pattern(cls, value):
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("Attribute value_pattern must be a valid regular expression") from exc
        return value

    @model_serializer(mode="wrap")
    def frozen_shape(self, serializer):
        body = serializer(self)
        for field in ("context_levels", "value_pattern"):
            if field not in self.model_fields_set:
                body.pop(field, None)
        return body

    def accepts_value(self, value):
        """Clause-owned constraints apply to every quoted structured leaf."""
        if self.value_pattern is None:
            return True
        if isinstance(value, dict):
            return bool(value) and all(self.accepts_value(item) for item in value.values())
        if isinstance(value, list):
            return bool(value) and all(self.accepts_value(item) for item in value)
        return isinstance(value, str) and re.fullmatch(self.value_pattern, value) is not None

    def classify(self, reported):
        return category_match(self.vocabulary, reported)

    def scope_value(self, reported):
        """Only a recognized category can establish a coded scope conflict."""
        match = self.classify(reported)
        return match.code if match and match.state == "recognized" else None

    def flags(self, axis, reported):
        match = self.classify(reported)
        return ["output_vocabulary_" + match.state + ":" + axis] if match and match.state != "recognized" else []
