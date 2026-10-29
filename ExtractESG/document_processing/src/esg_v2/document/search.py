"""Document-service search input. No standard, selection-set or package imports."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class PageSearchIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1)
    contract_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_kind: Literal["quantitative", "qualitative"]
    positive_terms: list[str] = Field(min_length=1)
    negative_terms: list[str] = Field(default_factory=list)
    supporting_terms: list[str] = Field(default_factory=list)
    method_terms: list[str] = Field(default_factory=list)
    required_term_groups: list[list[str]] = Field(default_factory=list)
