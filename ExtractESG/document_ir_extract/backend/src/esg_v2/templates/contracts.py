from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from esg_v2.standards.contracts import CompiledTaskSet


class TemplateAnswer(BaseModel):
    requirement_id: str
    source_row: int
    status: Literal["found", "not_found", "not_applicable", "uncertain", "system_failed"]
    location: str = ""
    quote: str = ""
    value: str = ""
    notes: str = ""


class TemplateExportResult(BaseModel):
    output_path: Path
    updated_cells: list[str] = Field(default_factory=list)
    preserved_cell_count: int = 0


class TemplateAdapter(Protocol):
    adapter_id: str

    def supports(self, path: Path) -> bool: ...

    def compile(self, path: Path) -> CompiledTaskSet: ...

    def export(
        self,
        source_path: Path,
        output_path: Path,
        answers: list[TemplateAnswer],
    ) -> TemplateExportResult: ...
