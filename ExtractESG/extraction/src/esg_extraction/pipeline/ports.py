"""Backbone dependencies are value interfaces; implementations own I/O."""

from typing import Protocol
from esg_contracts.evidence import DocumentReference, PDFPageText


class DocumentUnavailable(RuntimeError):
    """The document service could not supply the PDF source."""


class ProviderInterruption(RuntimeError):
    """The current page can be retried after the selected provider recovers."""

    def __init__(self, provider: str, category: str, message: str, phase: str):
        super().__init__(message)
        self.provider = provider
        self.category = category
        self.phase = phase


class DocumentPort(Protocol):
    def pdf(self, asset_id: str) -> DocumentReference: ...
    def document(self, ir_run_id: str) -> DocumentReference: ...
    def pages(self, asset_id: str) -> list[PDFPageText]: ...
    def image(self, asset_id: str, page_index: int, *, thumbnail: bool = False) -> bytes: ...


class ModelPort(Protocol):
    def complete(
        self,
        *,
        instructions: str,
        context: dict,
        schema: dict,
        max_tokens: int,
        cancel_check,
        images: tuple[bytes, ...] = (),
    ): ...
    def close(self): ...


class ArtifactPort(Protocol):
    def write(self, run_id: str, name: str, value): ...
    def read(self, run_id: str, name: str): ...


class ResultPort(Protocol):
    def put(self, fact, mapping): ...
    def flush(self): ...
    def status(self, body=None): ...
    def checkpoint(self, body=None): ...
    def collection(self, *, clause_key=None): ...
