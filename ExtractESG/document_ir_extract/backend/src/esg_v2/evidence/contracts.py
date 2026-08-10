from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


EvidenceAtomType = Literal[
    "heading_atom",
    "paragraph_atom",
    "list_atom",
    "list_item_atom",
    "caption_atom",
    "footnote_atom",
    "table_cell_atom",
    "table_row_atom",
    "table_region_atom",
    "logical_table_row_atom",
    "logical_table_region_atom",
    "figure_atom",
    "index_atom",
    "other_atom",
]


class EvidenceLocation(BaseModel):
    page_indices: list[int] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)
    printed_page_labels: list[str] = Field(default_factory=list)
    section_id: str | None = None
    section_path: list[str] = Field(default_factory=list)
    bbox: dict[str, Any] | None = None


class EvidenceAtom(BaseModel):
    atom_id: str
    atom_type: EvidenceAtomType
    source_text: str
    search_text: str
    location: EvidenceLocation
    source_node_ids: list[str]
    source_block_ids: list[str] = Field(default_factory=list)
    source_table_ids: list[str] = Field(default_factory=list)
    source_cell_ids: list[str] = Field(default_factory=list)
    source_figure_ids: list[str] = Field(default_factory=list)
    logical_table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    header_paths: list[str] = Field(default_factory=list)
    unit_hint: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    source_trace: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str


class EvidenceBuildRequest(BaseModel):
    ir_run_id: str
    run_id: str | None = None


class EvidenceBuildResult(BaseModel):
    run_id: str
    ir_run_id: str
    ir_revision: int
    output_dir: Path
    manifest_path: Path
    atom_count: int
    counts_by_type: dict[str, int]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EvidenceValidationReport(BaseModel):
    valid: bool
    can_run_targeted_recall: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
