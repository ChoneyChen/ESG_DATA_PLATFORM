"""DeepSeek transport for existing text-only extraction decisions."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from jsonschema import Draft202012Validator, ValidationError

from esg_contracts.model_providers import DEEPSEEK_MODELS
from esg_extraction.models.errors import ModelCancelled, ModelRunFailure, ModelTimedOut
from esg_extraction.models.structured_template import json_schema, structured_prompt
from esg_runtime.model_calls import (
    ModelCallRequest,
    ModelCapabilities,
    content_digest,
    finish_call,
    prepare_call,
)


_ENDPOINT = "https://api.deepseek.com/chat/completions"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_HTTP_ERRORS = {
    401: ("provider_authentication", "DeepSeek API key was rejected"),
    402: ("provider_balance", "DeepSeek account balance is insufficient"),
    403: ("provider_authentication", "DeepSeek access was denied"),
    413: ("input_budget", "DeepSeek request exceeds its input limit"),
    429: ("provider_rate_limit", "DeepSeek rate limit was reached"),
}


def _reject_non_json_constant(_value):
    raise ValueError("Invalid JSON constant")


class DeepSeekModel:
    provider_name = "deepseek"
    progress_callback = None
    max_input_chars = 160000
    max_input_tokens = 65536

    def __init__(self, model_id: str, api_key: str, *, max_tokens=8192, timeout_seconds=600):
        if model_id not in DEEPSEEK_MODELS or not api_key.strip():
            raise ModelRunFailure("DeepSeek model or API key is missing or invalid", category="request_config")
        self.model_id = model_id
        self._api_key = api_key
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    async def _post(self, client, payload, timeout_seconds):
        async with client.stream("POST", _ENDPOINT, json=payload, headers={
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }) as response:
            status = response.status_code
            if status in {408, 504}:
                raise ModelTimedOut(timeout_seconds)
            if not response.is_success:
                category, message = _HTTP_ERRORS.get(status, (
                    ("provider_unavailable", "DeepSeek service is unavailable")
                    if status >= 500 else ("provider_request", "DeepSeek refused the request")
                ))
                raise ModelRunFailure(f"{message} (HTTP {status})", category=category)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise ModelRunFailure("DeepSeek response exceeds the read limit", category="provider_protocol")
                body.extend(chunk)
        try:
            return json.loads(body, parse_constant=_reject_non_json_constant)
        except ValueError:
            raise ModelRunFailure("DeepSeek returned an invalid response", category="provider_protocol") from None

    async def _complete_http(self, payload, timeout_seconds, cancel_check):
        timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            task = asyncio.create_task(self._post(client, payload, timeout_seconds))
            try:
                async with asyncio.timeout(timeout_seconds):
                    while not task.done():
                        if cancel_check and cancel_check():
                            raise ModelCancelled()
                        await asyncio.wait({task}, timeout=0.25)
                    if cancel_check and cancel_check():
                        raise ModelCancelled()
                    return task.result()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def _decision(self, response, schema, telemetry):
        if not isinstance(response, dict):
            raise ModelRunFailure("DeepSeek response has no decisions", category="provider_protocol")
        usage = response.get("usage")
        if isinstance(usage, dict):
            for provider_field, receipt_field in (("prompt_tokens", "input_tokens"),
                                                 ("completion_tokens", "generation_tokens")):
                count = usage.get(provider_field)
                if type(count) is int and count >= 0:
                    telemetry[receipt_field] = count
        if telemetry.get("input_tokens", 0) > self.max_input_tokens:
            raise ModelRunFailure("DeepSeek prompt exceeds the input token budget", category="input_budget")
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ModelRunFailure("DeepSeek response has no single decision", category="provider_protocol")
        choice = choices[0]
        reason = choice.get("finish_reason")
        if reason in ("stop", "length", "aborted", "content_filter", "tool_calls", "insufficient_system_resource"):
            telemetry["finish_reason"] = reason
        if reason == "length":
            raise ModelRunFailure("DeepSeek reached the output token limit", category="generation_incomplete")
        if reason != "stop":
            raise ModelRunFailure("DeepSeek stopped without a complete decision", category="provider_protocol")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ModelRunFailure("DeepSeek returned empty decision content", category="generation_incomplete")
        try:
            result = json.loads(content, parse_constant=_reject_non_json_constant)
        except ValueError:
            raise ModelRunFailure("DeepSeek decision is not valid JSON", category="invalid_model_output") from None
        if self._api_key in json.dumps(result, ensure_ascii=False):
            raise ModelRunFailure("DeepSeek decision includes sensitive content", category="invalid_model_output")
        try:
            Draft202012Validator(schema).validate(result)
        except ValidationError:
            # The schema error may carry full provider content. Only its category is retained.
            raise ModelRunFailure("DeepSeek decision does not match the output schema", category="invalid_model_output") from None
        return result

    def complete(self, *, instructions, context, schema, max_tokens, cancel_check):
        started = time.perf_counter()
        execution = context.get("execution", {})
        evidence = json.dumps({key: value for key, value in context.items() if key != "execution"},
                              ensure_ascii=False, separators=(",", ":"))
        schema = json_schema(schema)
        receipt = prepare_call(ModelCallRequest(
            run_id=execution.get("run_id"), evidence_ids=execution.get("evidence_ids", []),
            attempt=execution.get("attempt", 0), parent_task_id=execution.get("parent_task_id"),
            task_type=execution.get("task_type", "structured_reading"), input_modalities=["text"],
            prompt_digest=content_digest([instructions, evidence]), schema_digest=content_digest(schema),
            requested_output_tokens=max_tokens,
            requested_timeout_seconds=execution.get("remaining_seconds", self.timeout_seconds),
        ), ModelCapabilities(
            provider=self.provider_name, model=self.model_id, input_modalities=["text"],
            max_output_tokens=self.max_tokens, timeout_seconds=self.timeout_seconds,
            output_constraint="prompt_schema",
        ))
        prompt = structured_prompt(instructions, evidence, schema)
        telemetry = {
            "provider": self.provider_name, "model_id": self.model_id,
            "input_chars": len(prompt), "output_token_budget": receipt.effective_output_tokens,
            "schema_enforcement": "prompt_schema", "generation_started": False,
        }
        try:
            if cancel_check and cancel_check():
                raise ModelCancelled()
            if len(prompt) > self.max_input_chars:
                raise ModelRunFailure("DeepSeek prompt exceeds the input character limit", category="input_budget")
            if self.progress_callback:
                self.progress_callback({"phase": "prefill", "input_chars": len(prompt), "generation_tokens": 0,
                                        "output_token_budget": receipt.effective_output_tokens})
            payload = {
                "model": self.model_id, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": receipt.effective_output_tokens, "temperature": 0.0,
                "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
            }
            telemetry["generation_started"] = True
            remaining = receipt.effective_timeout_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                raise ModelTimedOut(receipt.effective_timeout_seconds)
            try:
                response = asyncio.run(self._complete_http(payload, remaining, cancel_check))
            except (TimeoutError, httpx.TimeoutException):
                raise ModelTimedOut(receipt.effective_timeout_seconds) from None
            except httpx.RequestError:
                raise ModelRunFailure("DeepSeek connection failed", category="provider_unavailable") from None
            result = self._decision(response, schema, telemetry)
        except ModelRunFailure as exc:
            finished = finish_call(receipt, started, telemetry, error=exc)
            if exc.category == "invalid_model_output":
                finished["output_validation"] = "failed"
            exc.telemetry = {**telemetry, "execution": finished}
            raise
        telemetry["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        telemetry["execution"] = finish_call(receipt, started, telemetry)
        return result, telemetry

    def close(self):
        """The HTTP client is closed at the end of each call."""
