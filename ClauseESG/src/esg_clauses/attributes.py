"""One clause-owned definition per reported attribute, independent of display columns."""
from typing import Literal
from pydantic import Field, model_serializer
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

    @model_serializer(mode="wrap")
    def frozen_shape(self, serializer):
        body = serializer(self)
        if "context_levels" not in self.model_fields_set:
            body.pop("context_levels", None)
        return body

    def classify(self, reported):
        return category_match(self.vocabulary, reported)

    def flags(self, axis, reported):
        match = self.classify(reported)
        return ["output_vocabulary_" + match.state + ":" + axis] if match and match.state != "recognized" else []
