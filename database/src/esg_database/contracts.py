"""Database-owned query contracts. Producer facts are consumed, not redefined."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from esg_contracts.facts import SourceFact


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PeriodFacet(Model):
    period: str
    facts: int = Field(ge=0)


class CaptureFact(Model):
    record: SourceFact
    exact_numeric: str | None = None


class CaptureFactPage(Model):
    schema_version: Literal["capture-fact-page-1.0"] = "capture-fact-page-1.0"
    export_id: str
    query_digest: str
    total: int = Field(ge=0)
    returned: int = Field(ge=0)
    periods: list[PeriodFacet]
    items: list[CaptureFact]
    next_cursor: str | None = None

    @model_validator(mode="after")
    def page_counts(self):
        if self.returned != len(self.items) or self.returned > self.total:
            raise ValueError("Capture page counts disagree")
        return self
