"""DashScope text and page image input with streamed text output."""

import json

from esg_contracts.model_providers import DASHSCOPE_MODELS
from esg_extraction.models.errors import ModelRunFailure

from .chat_completion_model import ChatCompletionModel, MAX_RESPONSE_BYTES


_QUOTA_CODES = {"arrearage", "insufficient_quota", "servicequotaexhausted"}


class DashScopeModel(ChatCompletionModel):
    provider_name = "dashscope"
    display_name = "DashScope"
    endpoint = "https://cn-beijing.maas.qianwenaiapi.com/compatible-mode/v1/chat/completions"
    models = vision_models = DASHSCOPE_MODELS
    response_type = "text/event-stream"
    request_options = {
        "modalities": ["text"],
        "reasoning_effort": "none",
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    async def _read_failure(self, response):
        if response.status_code in {403, 429}:
            try:
                body = await super()._read_response(response)
            except ModelRunFailure:
                body = None
            if _quota_exhausted(body):
                return ModelRunFailure("DashScope account quota is exhausted", category="provider_balance")
        return await super()._read_failure(response)

    async def _read_response(self, response):
        chunks = []
        usage = None
        reason = None
        size = 0
        event_data = []

        async for line in response.aiter_lines():
            size += len(line.encode("utf-8")) + 1
            if size > MAX_RESPONSE_BYTES:
                raise ModelRunFailure("DashScope response exceeds the read limit", category="provider_protocol")
            if line.startswith("data:"):
                event_data.append(line[5:].lstrip(" "))
            if line or not event_data:
                continue
            event = "\n".join(event_data)
            event_data.clear()
            if event == "[DONE]":
                break
            try:
                record = json.loads(event)
            except ValueError:
                raise ModelRunFailure("DashScope returned an invalid stream event", category="provider_protocol") from None
            if not isinstance(record, dict):
                raise ModelRunFailure("DashScope returned an invalid stream event", category="provider_protocol")
            if "error" in record:
                if _quota_exhausted(record):
                    raise ModelRunFailure("DashScope account quota is exhausted", category="provider_balance")
                raise ModelRunFailure("DashScope stream reported an error", category="provider_protocol")
            if isinstance(record.get("usage"), dict):
                usage = record["usage"]
            choices = record.get("choices") or []
            if not choices:
                continue
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ModelRunFailure("DashScope stream has no single decision", category="provider_protocol")
            choice = choices[0]
            reason = choice.get("finish_reason") or reason
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                chunks.append(delta["content"])

        return {"choices": [{"message": {"content": "".join(chunks)}, "finish_reason": reason}], "usage": usage}


def _quota_exhausted(record):
    if not isinstance(record, dict):
        return False
    error = record.get("error", record)
    code = error.get("code") if isinstance(error, dict) else None
    return isinstance(code, str) and code.casefold() in _QUOTA_CODES
