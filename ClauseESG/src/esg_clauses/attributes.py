"""One clause-owned definition per reported attribute, independent of display columns."""
from typing import Literal
from pydantic import Field
from .base import StrictModel
from esg_data.categories import VocabularyEntry, category_match


class AttributeDefinition(StrictModel):
    label: str
    source: Literal["identity", "dimension"]
    description: str | None = None
    reading_instruction: str | None = None
    vocabulary: list[VocabularyEntry] = Field(default_factory=list)
    context_terms: list[str] = Field(default_factory=list)

    def classify(self, reported):
        return category_match(self.vocabulary, reported)

    def flags(self, axis, reported):
        match = self.classify(reported)
        return ["output_vocabulary_" + match.state + ":" + axis] if match and match.state != "recognized" else []
