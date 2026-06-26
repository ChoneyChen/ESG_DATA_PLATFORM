from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from extract_esg.contracts.base import ContractModel, SourceTrace, new_id


class PageRef(ContractModel):
    page_index: int = Field(ge=0)
    printed_label: str | None = None


class BoundingBox(ContractModel):
    """PDF coordinate box in points unless explicitly stated otherwise."""

    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_system: Literal["pdf_points", "normalized_0_1000", "image_pixels"] = "pdf_points"

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class LocalTextBlock(ContractModel):
    id: str = Field(default_factory=lambda: new_id("block"))
    page_ref: PageRef
    text: str
    bbox: BoundingBox | None = None
    role: Literal["title", "paragraph", "table_text", "footnote", "header", "footer", "unknown"] = "unknown"
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: SourceTrace
    quality_flags: list[str] = Field(default_factory=list)


class LocalTableCandidate(ContractModel):
    id: str = Field(default_factory=lambda: new_id("table"))
    page_ref: PageRef
    bbox: BoundingBox | None = None
    extracted_text: str | None = None
    row_count: int | None = Field(default=None, ge=0)
    column_count: int | None = Field(default=None, ge=0)
    source: SourceTrace
    quality_flags: list[str] = Field(default_factory=list)


class LocalPdfPage(ContractModel):
    page_ref: PageRef
    width: float
    height: float
    rotation: int = 0
    native_text: str = ""
    text_blocks: list[LocalTextBlock] = Field(default_factory=list)
    table_candidates: list[LocalTableCandidate] = Field(default_factory=list)
    page_image_path: Path | None = None
    quality_flags: list[str] = Field(default_factory=list)


class DocumentIR(ContractModel):
    report_id: str
    artifact_path: Path
    sha256: str
    pages: list[LocalPdfPage]
    source_traces: list[SourceTrace] = Field(default_factory=list)
    conflict_flags: list[str] = Field(default_factory=list)
