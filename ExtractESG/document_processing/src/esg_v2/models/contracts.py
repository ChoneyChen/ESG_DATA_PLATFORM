from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CloudChatRequest(BaseModel):
    request_id: str
    model_id: str
    messages: list[dict[str, Any]]
    max_tokens: int | None = None
    temperature: float | None = None
    response_format: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    thinking: dict[str, Any] | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    output_template: dict[str, Any] | None = None


class CloudChatResult(BaseModel):
    request_id: str
    provider: str = "qiniu"
    model_id: str
    raw_response: dict[str, Any]
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float


class CloudModelInfo(BaseModel):
    id: str
    name: str | None = None
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    context_length: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
