"""Cloud adapter contract at the HTTP boundary, without external calls."""

import asyncio
import json

import httpx
import pytest

from esg_extraction.models.errors import ModelCancelled, ModelRunFailure, ModelTimedOut
from esg_extraction.models.structured_template import structured_prompt
from esg_extraction.pipeline.adapters import chat_completion_model, dashscope_model, deepseek_model


FAKE_KEY = "test-only-authorization-value"
SCHEMA = {
    "type": "object", "required": ["matches"], "additionalProperties": False,
    "properties": {"matches": {"type": "array", "items": {"enum": ["source-1"]}}},
}


def mock_cloud(monkeypatch, handle):
    async_client = httpx.AsyncClient
    monkeypatch.setattr(chat_completion_model.httpx, 'AsyncClient', lambda **options: async_client(
        transport=httpx.MockTransport(handle), **options,
    ))


def complete(model, *, cancel_check=None, remaining=5, images=()):
    return model.complete(instructions="Read the selected source.", context={
        "row": "披露", "execution": {"run_id": "run-1", "task_type": "clause_context",
                                 "evidence_ids": ["source-1"], "remaining_seconds": remaining},
    }, schema=SCHEMA, max_tokens=128, cancel_check=cancel_check, images=images)


def reply(content='{"matches":["source-1"]}', *, finish_reason="stop"):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 116, "completion_tokens": 12},
    })


@pytest.mark.parametrize("model_id", ["deepseek-flash", "deepseek-v4-pro"])
def test_uses_official_api_and_preserves_bounded_input(monkeypatch, model_id):
    def handle(request):
        assert request.method == "POST" and str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        assert FAKE_KEY.encode() not in request.content
        assert json.loads(request.content) == {
            "model": model_id, "messages": [{"role": "user", "content": structured_prompt(
                "Read the selected source.", '{"row":"披露"}', SCHEMA,
            )}], "max_tokens": 64, "temperature": 0.0, "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
        return reply()

    mock_cloud(monkeypatch, handle)
    result, telemetry = complete(deepseek_model.DeepSeekModel(model_id, FAKE_KEY, max_tokens=64))
    assert result == {"matches": ["source-1"]}
    assert telemetry["provider"] == "deepseek" and telemetry["model_id"] == model_id
    receipt = telemetry["execution"]
    assert receipt["request"]["evidence_ids"] == ["source-1"] and receipt["request"]["run_id"] == "run-1"
    assert receipt["effective_output_tokens"] == 64 and receipt["effective_timeout_seconds"] == 5
    assert receipt["input_tokens"] == 116 and receipt["output_tokens"] == 12
    assert receipt["output_constraint"] == "prompt_schema" and receipt["status"] == "succeeded"
    assert FAKE_KEY not in json.dumps(telemetry)


def test_flash_sends_pdf_page_as_inline_image(monkeypatch):
    def handle(request):
        content = json.loads(request.content)["messages"][0]["content"]
        assert content[1]["type"] == "text"
        assert content[0] == {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,cGFnZQ==", "detail": "high"},
        }
        return reply()

    mock_cloud(monkeypatch, handle)
    _, telemetry = complete(deepseek_model.DeepSeekModel("deepseek-flash", FAKE_KEY),
                            images=(b"page",))
    assert telemetry["execution"]["request"]["input_modalities"] == ["text", "image"]


@pytest.mark.parametrize("status,category", [(302, "provider_request"), (401, "provider_authentication"),
    (402, "provider_balance"), (429, "provider_rate_limit"), (503, "provider_unavailable")])
def test_http_failures_are_sanitized_without_fallback(monkeypatch, status, category):
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(status, headers={"Location": "https://example.invalid/"}, json={"error": FAKE_KEY})

    mock_cloud(monkeypatch, handle)
    with pytest.raises(ModelRunFailure) as exc:
        complete(deepseek_model.DeepSeekModel("deepseek-flash", FAKE_KEY))
    assert calls == ["POST"] and exc.value.category == category
    assert exc.value.telemetry["execution"]["failure_category"] == category
    assert FAKE_KEY not in str(exc.value) + json.dumps(exc.value.telemetry)


@pytest.mark.parametrize("content,reason,category", [
    ('{"matches":["unknown-source"]}', "stop", "invalid_model_output"),
    ('{"matches":[' + json.dumps(FAKE_KEY) + ']}', "stop", "invalid_model_output"),
    ('{"matches":[NaN]}', "stop", "invalid_model_output"),
    (' ', "stop", "generation_incomplete"),
    ('{"matches":[]}', "length", "generation_incomplete"),
])
def test_schema_validation_and_incomplete_generation(monkeypatch, content, reason, category):
    mock_cloud(monkeypatch, lambda _: reply(content, finish_reason=reason))
    with pytest.raises(ModelRunFailure) as exc:
        complete(deepseek_model.DeepSeekModel("deepseek-flash", FAKE_KEY))
    assert exc.value.category == category
    assert FAKE_KEY not in str(exc.value) + json.dumps(exc.value.telemetry)
    assert exc.value.telemetry["execution"]["input_tokens"] == 116
    if category == "invalid_model_output":
        assert exc.value.telemetry["execution"]["output_validation"] == "failed"


@pytest.mark.parametrize("cancel_in_flight", [True, False])
def test_cancel_or_deadline_releases_in_flight_request(monkeypatch, cancel_in_flight):
    started = closed = False

    async def handle(_request):
        nonlocal started, closed
        started = True
        try:
            await asyncio.Future()
        finally:
            closed = True

    mock_cloud(monkeypatch, handle)
    with pytest.raises(ModelCancelled if cancel_in_flight else ModelTimedOut) as exc:
        complete(deepseek_model.DeepSeekModel("deepseek-flash", FAKE_KEY),
                 cancel_check=(lambda: started) if cancel_in_flight else None,
                 remaining=2 if cancel_in_flight else 0.2)
    assert started and closed
    assert exc.value.telemetry["execution"]["status"] == ("cancelled" if cancel_in_flight else "failed")


def streamed_reply(*contents, finish_reason="stop"):
    events = [{"choices": [{"delta": {"content": part}, "finish_reason": None}]} for part in contents]
    events.extend([
        {"choices": [{"delta": {}, "finish_reason": finish_reason}]},
        {"choices": [], "usage": {"prompt_tokens": 36, "completion_tokens": 9}},
    ])
    lines = [f"data: {json.dumps(event)}\r\n\r\n" for event in events]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join([": ping\r\n\r\n", *lines, "data: [DONE]\r\n\r\n"]))


def test_dashscope_stream_reads_text_deltas_and_final_usage(monkeypatch):
    def handle(request):
        assert str(request.url) == "https://cn-beijing.maas.qianwenaiapi.com/compatible-mode/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        assert request.headers["accept"] == "text/event-stream"
        assert FAKE_KEY.encode() not in request.content
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.8-omni-flash"
        assert payload["stream"] is True and payload["stream_options"] == {"include_usage": True}
        assert payload["modalities"] == ["text"] and "audio" not in payload
        assert payload["reasoning_effort"] == "none" and "thinking" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        content = payload["messages"][0]["content"]
        assert content[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,cGFnZQ=="}}
        assert content[1]["type"] == "text" and "EVIDENCE=" in content[1]["text"]
        return streamed_reply('{"matches":', '["source-1"]}')

    mock_cloud(monkeypatch, handle)
    result, telemetry = complete(dashscope_model.DashScopeModel("qwen3.8-omni-flash", FAKE_KEY), images=(b"page",))
    assert result == {"matches": ["source-1"]}
    assert telemetry["provider"] == "dashscope" and telemetry["execution"]["status"] == "succeeded"
    assert telemetry["execution"]["input_tokens"] == 36 and telemetry["execution"]["output_tokens"] == 9
    assert FAKE_KEY not in json.dumps(telemetry)


def test_dashscope_distinguishes_quota_from_rate_limit_and_keeps_truncated_content(monkeypatch):
    model = dashscope_model.DashScopeModel("qwen3.8-omni-flash", FAKE_KEY)
    response = None
    mock_cloud(monkeypatch, lambda _: response)
    for code, category in [("insufficient_quota", "provider_balance"), ("limit_requests", "provider_rate_limit")]:
        response = httpx.Response(429, json={"error": {"code": code, "message": FAKE_KEY}})
        with pytest.raises(ModelRunFailure) as exc:
            complete(model)
        assert exc.value.category == category and FAKE_KEY not in str(exc.value)

    response = streamed_reply('{"matches":["source-1"', finish_reason="length")
    with pytest.raises(ModelRunFailure) as exc:
        complete(model)
    assert exc.value.category == "generation_incomplete"
    assert exc.value.cleaned_output == '{"matches":["source-1"'


@pytest.mark.parametrize("successful_retry", [True, False])
def test_cloud_retries_only_once_on_short_retry_after(monkeypatch, successful_retry):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 2 and successful_retry:
            return reply()
        return httpx.Response(429, headers={"Retry-After": "0"})

    mock_cloud(monkeypatch, handle)
    model = deepseek_model.DeepSeekModel("deepseek-flash", FAKE_KEY)
    if successful_retry:
        result, telemetry = complete(model)
        assert result == {"matches": ["source-1"]} and telemetry["http_attempts"] == 2
    else:
        with pytest.raises(ModelRunFailure) as exc:
            complete(model)
        assert exc.value.category == "provider_rate_limit"
        assert exc.value.telemetry["http_attempts"] == 2
    assert len(requests) == 2


def test_cloud_rate_limit_backoff_is_cancellable(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "2"})

    mock_cloud(monkeypatch, handle)
    with pytest.raises(ModelCancelled):
        complete(deepseek_model.DeepSeekModel("deepseek-flash", FAKE_KEY), cancel_check=lambda: bool(requests))
    assert len(requests) == 1
