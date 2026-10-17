from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from esg_v2.ocr.client import PaddleOcrVlClient


LOCAL_RESOURCE_PREFIX = "local-resource://"


class OcrResourceReader(Protocol):
    def read_bytes(self, reference: str) -> bytes:
        """Resolve one provider-owned artifact without exposing provider details to the writer."""


@dataclass(frozen=True)
class HttpOcrResourceReader:
    client: PaddleOcrVlClient

    def read_bytes(self, reference: str) -> bytes:
        return self.client.download_bytes(reference)


@dataclass(frozen=True)
class LocalOcrResourceReader:
    root: Path

    def read_bytes(self, reference: str) -> bytes:
        if not reference.startswith(LOCAL_RESOURCE_PREFIX):
            raise ValueError(f"Unsupported local OCR resource reference: {reference}")
        relative = reference.removeprefix(LOCAL_RESOURCE_PREFIX)
        if not relative or Path(relative).is_absolute():
            raise ValueError(f"Invalid local OCR resource reference: {reference}")
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError(f"Local OCR resource escapes provider root: {reference}") from exc
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate.read_bytes()
