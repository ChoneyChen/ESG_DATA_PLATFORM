"""Navigable standard structure; deliberately not an executable value contract."""
from typing import Literal
from pydantic import Field, RootModel
from .base import StrictModel, digest
from .parts import ClauseRelationships
from .contract import ClauseContract


class ClauseDescriptor(StrictModel):
    schema_version: Literal["clause-descriptor-1.0"] = "clause-descriptor-1.0"
    clause_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    group_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    labels: dict[str, str]
    definition: str = Field(min_length=1)
    disposition_reason: str = Field(min_length=1)
    optional_axes: list[str] = Field(default_factory=list)
    member_clause_keys: list[str] = Field(default_factory=list)
    relationships: ClauseRelationships = Field(default_factory=ClauseRelationships)
    provenance: dict = Field(default_factory=dict)
    clause_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @property
    def title(self):
        return self.labels.get("zh") or self.labels.get("en") or self.clause_key

    @property
    def readiness(self):
        return "structural"

    @property
    def data_kind(self):
        return None

    def semantic_digest(self):
        return digest(self.model_dump(mode="json", exclude={"clause_digest"}))

    def verify(self):
        if self.clause_digest != self.semantic_digest():
            raise ValueError(f"Descriptor content digest differs: {self.clause_key}")
        return self

    def seal(self):
        return self.model_copy(update={"clause_digest": self.semantic_digest()})


def parse_record(body):
    model = ClauseDescriptor if body.get("schema_version") == "clause-descriptor-1.0" else ClauseContract
    return model.model_validate(body).verify()


class ClauseRecord(RootModel[ClauseContract | ClauseDescriptor]):
    """The navigation/edit port. Extraction continues to consume ClauseContract only."""
