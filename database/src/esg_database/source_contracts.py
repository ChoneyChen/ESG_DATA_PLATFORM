"""Reference IDs are provenance; only the PDF blob remains an external dependency."""
from typing import Literal
from pydantic import Field,JsonValue,model_validator
from .contracts import Model


class SourceScope(Model):
    source_sha256: str | None=Field(default=None,pattern=r"^[a-f0-9]{64}$")
    job_ids: list[str]=Field(default_factory=list,max_length=1000)
    ir_run_ids: list[str]=Field(default_factory=list,max_length=1000)
    target: Literal["auto","pdf","cache"]="auto"

    @model_validator(mode="after")
    def identified(self):
        if not self.source_sha256 and not self.job_ids and not self.ir_run_ids:
            raise ValueError("Select a physical source or cache identity")
        if self.target=="pdf" and not self.source_sha256:
            raise ValueError("PDF source cleanup needs its actual SHA-256")
        if self.target=="cache" and not (self.job_ids or self.ir_run_ids):
            raise ValueError("Cache cleanup needs its own job or IR identity")
        if any(not value or len(value)>240 for value in [*self.job_ids,*self.ir_run_ids]):
            raise ValueError("Invalid source identity")
        return self


class SourceReferences(Model):
    schema_version: Literal["source-references-1.0"]="source-references-1.0"
    verified: bool
    scope: SourceScope
    dependencies: list[dict[str,JsonValue]]
    blockers: list[dict[str,JsonValue]]


class ReserveSource(SourceScope):
    reason: str=Field(min_length=1,max_length=4000)
    reservation_id: str | None=Field(default=None,max_length=160)
    local_files_only: bool=False


class UpdateSourceReservation(Model):
    state: Literal["quarantined","released","purged"]
    reason: str=Field(min_length=1,max_length=4000)
