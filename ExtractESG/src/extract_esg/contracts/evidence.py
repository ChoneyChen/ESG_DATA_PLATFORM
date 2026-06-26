from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from extract_esg.contracts.base import ContractModel, SourceTrace, new_id
from extract_esg.contracts.document import BoundingBox, PageRef


class EvidenceFragment(ContractModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    report_id: str
    page_ref: PageRef
    kind: Literal["text_block", "table_candidate", "table_region", "figure", "page_crop", "cloud_ocr", "note"]
    text: str | None = None
    bbox: BoundingBox | None = None
    artifact_path: Path | None = None
    source: SourceTrace
    quality_flags: list[str] = Field(default_factory=list)


class EvidencePacket(ContractModel):
    id: str = Field(default_factory=lambda: new_id("packet"))
    report_id: str
    task_type: Literal[
        "page_classification",
        "vision_ocr",
        "full_harvest",
        "quant_table_mining",
        "qualitative_claim_mining",
        "targeted_recall",
        "mapping_review",
        "verification",
    ]
    primary_evidence: list[EvidenceFragment]
    context_evidence: list[EvidenceFragment] = Field(default_factory=list)
    instructions: str | None = None
    max_model_cost_cny: float | None = Field(default=None, ge=0)

