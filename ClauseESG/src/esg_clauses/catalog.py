"""Public clause navigation contracts contain identities, never storage paths."""

from typing import Literal
from pydantic import Field
from .base import StrictModel


class ClauseSummary(StrictModel):
    clause_key: str
    group_id: str
    title: str
    data_kind: Literal["quantitative", "qualitative"] | None
    entry_kind: Literal["value", "structure"] = "value"
    readiness: Literal["ready", "blocked", "structural"]
    clause_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ClauseCatalog(StrictModel):
    schema_version: Literal["clause-catalog-4.0"] = "clause-catalog-4.0"
    clauses: list[ClauseSummary]


class ClauseGroup(StrictModel):
    group_id: str
    title: str
    clauses: list[str]
