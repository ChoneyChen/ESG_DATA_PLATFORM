from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from esg_contracts.search import PageSearchIntent

from esg_contracts.document import (
    OptionalPayload,
    PageSelectionMode,
    validate_page_indices,
)
from esg_pipeline.progress import BusinessProgress, ExecutionTiming, TaskError
from esg_pipeline.shared_contracts import AvailableAction, StageOutcome
from esg_document.document.contracts import (
    DocumentIrBuildRequest,
    DocumentIrRepairRequest,
    ReviewRetryRequest,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PipelineTaskType(str, Enum):
    OCR = "ocr"
    DOCUMENT_IR = "document_ir"
    TARGETED_EXTRACTION = "targeted_extraction"


class PipelineTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class PipelineTask(BaseModel):
    task_id: str
    task_type: PipelineTaskType
    operation: str
    title: str
    status: PipelineTaskStatus
    position: int
    payload: dict[str, Any]
    native_job_id: str | None = None
    stage: str = "queued"
    message: str = "Task queued"
    progress_current: int = 0
    progress_total: int = 0
    cancel_requested: bool = False
    requires_runtime_secret: bool = False
    worker_pid: int | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    outcome: StageOutcome | None = None
    available_actions: list[AvailableAction] = Field(default_factory=list)
    source_references: dict[str, Any] | None = None
    progress: BusinessProgress | None = None
    timing: ExecutionTiming = Field(default_factory=ExecutionTiming)
    error_detail: TaskError | None = None
    revision: int = 0
    archived_at: str | None = None
    archive_reason: str | None = None


class OcrPipelineTaskRequest(BaseModel):
    asset_id: str | None = None
    file_url: str | None = None
    token: str | None = Field(default=None, exclude=True)
    model: str = "PaddleOCR-VL-1.6"
    ocr_provider: Literal["local_first", "local_paddleocr", "paddle_api"] = (
        "local_paddleocr"
    )
    allow_api_fallback: bool = False
    optional_payload: OptionalPayload = Field(default_factory=OptionalPayload)
    poll_interval_seconds: float = Field(default=5, ge=0.5)
    page_selection: PageSelectionMode = "all"
    page_search_targets: list[PageSearchIntent] = Field(default_factory=list)
    page_indices: list[int] | None = None

    @field_validator("page_indices")
    @classmethod
    def page_indices_are_ordered(cls, value: list[int] | None) -> list[int] | None:
        return validate_page_indices(value)

    @model_validator(mode="after")
    def source_is_exclusive(self) -> "OcrPipelineTaskRequest":
        if bool(self.asset_id) == bool(self.file_url):
            raise ValueError("Provide exactly one of asset_id or file_url")
        if self.file_url and self.ocr_provider == "local_paddleocr":
            raise ValueError("Local PaddleOCR-VL requires a report asset")
        if self.page_indices is not None and self.page_selection != "all":
            raise ValueError("Provide either page_indices or page_selection, not both")
        if self.file_url and (
            self.page_indices is not None or self.page_selection != "all"
        ):
            raise ValueError("Page-targeted OCR requires a report asset")
        return self


class DocumentIrPipelineTaskRequest(BaseModel):
    operation: Literal["build", "repair", "review_retry"] = "build"
    parent_run_id: str | None = None
    build_request: DocumentIrBuildRequest | None = None
    repair_request: DocumentIrRepairRequest | None = None
    review_retry_request: ReviewRetryRequest | None = None

    @model_validator(mode="after")
    def operation_payload_matches(self) -> "DocumentIrPipelineTaskRequest":
        selected = {
            "build": self.build_request,
            "repair": self.repair_request,
            "review_retry": self.review_retry_request,
        }[self.operation]
        if selected is None:
            raise ValueError(f"{self.operation} request payload is required")
        if self.operation == "review_retry" and not self.parent_run_id:
            raise ValueError("parent_run_id is required for review_retry")
        return self


class PipelineOrderRequest(BaseModel):
    task_ids: list[str]
