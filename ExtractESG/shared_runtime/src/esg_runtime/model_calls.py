"""Shared execution envelope; consumers retain ownership of output semantics.

No provider switching, prompt storage, credentials or repeated verification lives
here. Budgets are negotiated once before a call and recorded as actually used.
"""
from typing import Literal
from uuid import uuid4
from hashlib import sha256
import json
import time
from pydantic import BaseModel, Field


def content_digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class ModelCapabilities(BaseModel):
    provider: str
    model: str
    input_modalities: list[Literal["text", "image"]]
    max_output_tokens: int | None = None
    timeout_seconds: float = Field(gt=0)
    output_constraint: str


class ModelCallRequest(BaseModel):
    call_id: str = Field(default_factory=lambda: "call-" + uuid4().hex)
    run_id: str | None = None
    task_type: str
    attempt: int = 0
    parent_task_id: str | None = None
    input_modalities: list[Literal["text", "image"]]
    evidence_ids: list[str] = Field(default_factory=list)
    prompt_digest: str
    schema_digest: str | None = None
    requested_output_tokens: int | None = Field(default=None, ge=1)
    requested_timeout_seconds: float = Field(gt=0)


class ModelCallReceipt(BaseModel):
    schema_version: Literal["model-call-1.0"] = "model-call-1.0"
    request: ModelCallRequest
    capabilities: ModelCapabilities
    effective_output_tokens: int | None = None
    effective_timeout_seconds: float
    status: Literal["prepared", "succeeded", "failed", "cancelled"] = "prepared"
    elapsed_seconds: float = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    failure_category: str | None = None
    output_constraint: str
    output_validation: Literal["passed", "failed", "not_evaluated"] = "not_evaluated"


def prepare_call(request: ModelCallRequest, capabilities: ModelCapabilities):
    if set(request.input_modalities) - set(capabilities.input_modalities):
        raise ValueError("Requested input modality is unsupported")
    tokens = request.requested_output_tokens
    if tokens is not None and capabilities.max_output_tokens is not None:
        tokens = min(tokens, capabilities.max_output_tokens)
    return ModelCallReceipt(
        request=request, capabilities=capabilities, effective_output_tokens=tokens,
        effective_timeout_seconds=min(request.requested_timeout_seconds, capabilities.timeout_seconds),
        output_constraint=capabilities.output_constraint,
    )


def finish_call(receipt, started, telemetry=None, *, error=None):
    telemetry = telemetry or {}
    category = getattr(error, "category", None)
    return receipt.model_copy(update={
        "status": "cancelled" if category == "cancelled" else "failed" if error else "succeeded",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "input_tokens": telemetry.get("input_tokens", telemetry.get("prompt_tokens")),
        "output_tokens": telemetry.get("generation_tokens", telemetry.get("completion_tokens")),
        "failure_category": category or (type(error).__name__ if error else None),
        "output_constraint": telemetry.get("schema_enforcement", receipt.output_constraint),
    }).model_dump(mode="json")
