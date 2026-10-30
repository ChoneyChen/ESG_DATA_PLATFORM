"""Public clause navigation contracts contain identities, never storage paths."""

from typing import Literal
from pydantic import Field
from .base import StrictModel


class ClauseSummary(StrictModel):
    clause_key: str
    group_id: str
    title: str
    data_kind: Literal["quantitative", "qualitative"]
    readiness: Literal["ready", "blocked"]
    clause_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ClauseCatalog(StrictModel):
    schema_version: Literal["clause-catalog-3.0"] = "clause-catalog-3.0"
    clauses: list[ClauseSummary]


class ClauseGroup(StrictModel):
    group_id: str
    title: str
    clauses: list[str]
