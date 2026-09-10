from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from esg_v2.utils.sanitization import sanitize_remote_url


class OptionalPayload(BaseModel):
    useDocOrientationClassify: bool = False
    useDocUnwarping: bool = False
    useChartRecognition: bool = False


class OcrRunRequest(BaseModel):
    file_path: str | None = None
    file_url: str | None = None
    token: str | None = None
    model: str = "PaddleOCR-VL-1.6"
    ocr_provider: Literal["local_first", "local_paddleocr", "paddle_api"] = "local_first"
    allow_api_fallback: bool = True
    optional_payload: OptionalPayload = Field(default_factory=OptionalPayload)
    poll_interval_seconds: float = Field(default=5, ge=0.5)


class OcrJobState(BaseModel):
    run_id: str
    status: Literal["queued", "running", "done", "failed", "cancelled", "interrupted"]
    message: str
    output_dir: Path
    job_id: str | None = None
    result_json_url: str | None = None
    page_count: int = 0
    progress_current: int = 0
    progress_total: int = 0
    last_progress_at: str | None = None
    progress_detail: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    manifest_path: Path | None = None
    summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator("result_json_url")
    @classmethod
    def sanitize_result_url(cls, value: str | None) -> str | None:
        return sanitize_remote_url(value) if value else value


class OcrRunResult(BaseModel):
    run_id: str
    provider: Literal["local_paddleocr", "paddle_api"]
    job_id: str | None = None
    output_dir: Path
    manifest_path: Path
    raw_jsonl_path: Path
    page_markdown_files: list[Path]
    downloaded_files: list[Path]
    page_count: int
    result_json_url: str | None = None
    poll_history_path: Path
    submit_response_path: Path

    @field_validator("result_json_url")
    @classmethod
    def sanitize_result_url(cls, value: str | None) -> str | None:
        return sanitize_remote_url(value) if value else value
