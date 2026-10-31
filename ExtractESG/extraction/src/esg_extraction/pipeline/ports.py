"""Backbone dependencies are value interfaces; implementations own I/O."""

from typing import Protocol
from esg_contracts.evidence import (
    DocumentReference,
    SearchIntent,
    ObjectReference,
    EvidenceFrame,
    ObjectSearchPage,
)


class DocumentPort(Protocol):
    def document(self, ir_run_id: str) -> DocumentReference: ...
    def search_page(self, ir_run_id: str, intent: SearchIntent) -> ObjectSearchPage: ...
    def read(
        self, ir_run_id: str, reference: ObjectReference, chunk_index: int = 0
    ) -> EvidenceFrame: ...


class ModelPort(Protocol):
    def complete(
        self,
        *,
        instructions: str,
        context: dict,
        schema: dict,
        max_tokens: int,
        cancel_check,
    ): ...
    def close(self): ...


class ArtifactPort(Protocol):
    def write(self, run_id: str, name: str, value): ...
