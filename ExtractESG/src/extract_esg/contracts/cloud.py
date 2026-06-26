from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from extract_esg.contracts.base import ContractModel, SourceTrace, new_id


class CloudModelInfo(ContractModel):
    id: str
    name: str | None = None
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    context_length: int | None = None
    max_tokens: int | None = None
    supports_schema_output: bool = False
    supports_function_calling: bool = False
    supports_reasoning: bool = False
    support_api_protocols: list[str] = Field(default_factory=list)
    retirement_at: str | None = None
    suggested_model: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CloudTaskRequest(ContractModel):
    id: str = Field(default_factory=lambda: new_id("cloud_task"))
    task_type: Literal[
        "vision_ocr",
        "file_recognition",
        "structured_extract",
        "long_context_understanding",
        "mapping_review",
        "verification",
    ]
    model_id: str
    input_refs: list[str]
    messages: list[dict[str, Any]]
    schema_name: str | None = None
    prompt_version: str
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CloudTaskResult(ContractModel):
    request_id: str
    model_id: str
    raw_response: dict[str, Any]
    parsed_response: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost_cny: float | None = None
    latency_ms: float | None = None
    source: SourceTrace
    quality_flags: list[str] = Field(default_factory=list)

