"""Sealed producer-to-database handoff; no service paths or presentation objects."""
from typing import Literal
from pydantic import Field, model_validator, model_serializer
from .facts import FrozenModel
from .evidence import DocumentReference
from pydantic import JsonValue


class ExportFile(FrozenModel):
    name: str = Field(pattern=r"^[a-z-]+\.json$")
    role: Literal["facts", "properties", "evidence", "relations", "coverage", "targets", "document", "diagnostics"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0)
    records: int = Field(ge=0)


class ExportManifest(FrozenModel):
    schema_version: Literal["extraction-export-1.0", "extraction-export-1.1"] = "extraction-export-1.1"
    authority: Literal["facts"] | None = None
    export_id: str = Field(pattern=r"^export-[a-f0-9]{64}$")
    run_id: str
    producer: str = "esg-extraction"
    engine_version: str
    engine_identity: dict[str, JsonValue]
    document: DocumentReference
    clause_targets: dict[str, str]
    execution_state: Literal["completed", "interrupted"]
    sealed_at: str
    files: list[ExportFile] = Field(min_length=1)
    publication_status: Literal["not_evaluated"] = "not_evaluated"

    @model_serializer(mode="wrap")
    def preserve_frozen_shape(self, serializer):
        body = serializer(self)
        if self.authority is None:
            body.pop("authority", None)
        return body

    @model_validator(mode="after")
    def complete_members(self):
        if self.schema_version == "extraction-export-1.1" and self.authority != "facts":
            raise ValueError("Current exports declare facts as their sole data authority")
        required = {"facts", "properties", "evidence", "relations", "coverage", "targets", "document", "diagnostics"}
        if {f.role for f in self.files} != required or len(self.files) != len(required) or len({f.name for f in self.files}) != len(self.files):
            raise ValueError("An export needs exactly its declared data members")
        return self


class DeliveryStatus(FrozenModel):
    export_id: str | None = None
    state: Literal["prepared", "delivered", "partial", "failed", "unknown"] = "prepared"
    accepted_facts: int | None = None
    rejected_facts: int | None = None
    message: str | None = None
    retryable: bool = True


class IngestionReceipt(FrozenModel):
    schema_version: Literal["extraction-receipt-1.0"] = "extraction-receipt-1.0"
    export_id: str
    input_digest: str
    receipt_id: str
    status: Literal["accepted", "partial", "rejected"]
    accepted_facts: int = Field(ge=0)
    rejected_facts: int = Field(ge=0)
    dispositions: list[dict[str, JsonValue]]
    publication_status: Literal["not_evaluated"]
    received_at: str
