from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

import requests

from esg_v2.config import Settings, get_settings
from esg_v2.models.contracts import CloudChatRequest, CloudChatResult, CloudModelInfo


class QiniuApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return not self.account_wide_rate_limit and (
            self.status_code is None or self.status_code == 429 or self.status_code >= 500
        )

    @property
    def account_wide_rate_limit(self) -> bool:
        message = str(self).lower()
        markers = ("uid rate limit reached for tpd", "daily token limit", "insufficient_quota")
        return self.status_code in {402, 429} and any(marker in message for marker in markers)

    @property
    def minute_rate_limit(self) -> bool:
        if self.account_wide_rate_limit or self.status_code != 429:
            return False
        message = str(self).lower()
        markers = ("rate limit reached for rpm", "requests per minute", "rpm limit")
        return any(marker in message for marker in markers)

    @property
    def rate_limit_scope(self) -> str | None:
        if self.account_wide_rate_limit:
            return "tpd"
        if self.minute_rate_limit:
            return "rpm"
        return None

    @property
    def retry_after_seconds(self) -> float | None:
        raw = self.headers.get("retry-after")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                value = parsedate_to_datetime(raw)
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return max(0.0, (value - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None


class QiniuModelAdapter:
    """Thin Qiniu OpenAI-compatible API adapter.

    This adapter is deliberately model-agnostic. ESG semantics, routing, and
    evidence rules must live in workflow code, not in this HTTP client.
    """

    def __init__(self, settings: Settings | None = None, *, api_key: str | None = None):
        self.settings = settings or get_settings()
        self.api_key = api_key or self.settings.qiniu_api_key

    @property
    def credential_scope_id(self) -> str:
        host = urlparse(self.settings.qiniu_base_url).netloc or "qiniu"
        digest = sha256((self.api_key or "missing").encode("utf-8")).hexdigest()[:16]
        return f"{host}:{digest}"

    def list_models(self) -> list[CloudModelInfo]:
        raw = self._request("GET", "/models")
        items = raw.get("data") or raw.get("models") or []
        return [self._parse_model(item) for item in items]

    def chat_completions(self, request: CloudChatRequest) -> CloudChatResult:
        payload: dict[str, Any] = {
            "model": request.model_id,
            "messages": request.messages,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        if request.thinking is not None:
            payload["thinking"] = request.thinking
        payload.update(request.extra_body)

        started = time.perf_counter()
        raw = self._request(
            "POST",
            "/chat/completions",
            json_payload=payload,
            timeout=request.timeout_seconds,
        )
        return CloudChatResult(
            request_id=request.request_id,
            model_id=request.model_id,
            raw_response=raw,
            usage=raw.get("usage") or {},
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise QiniuApiError("QINIU_API_KEY is required for Qiniu model calls")

        url = f"{self.settings.qiniu_base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json_payload,
                timeout=timeout or self.settings.qiniu_default_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise QiniuApiError(f"Qiniu request failed: {exc}") from exc
        if response.status_code >= 400:
            request_id = (
                response.headers.get("X-Reqid")
                or response.headers.get("X-Request-Id")
                or response.headers.get("Request-Id")
            )
            raise QiniuApiError(
                f"Qiniu request failed: HTTP {response.status_code} {response.text}",
                status_code=response.status_code,
                headers=dict(response.headers),
                request_id=request_id,
            )
        return response.json()

    @staticmethod
    def _parse_model(item: dict[str, Any]) -> CloudModelInfo:
        architecture = item.get("architecture") or {}
        constraints = item.get("model_constraints") or {}
        return CloudModelInfo(
            id=item["id"],
            name=item.get("name"),
            input_modalities=list(architecture.get("input_modalities") or item.get("input_modalities") or []),
            output_modalities=list(architecture.get("output_modalities") or item.get("output_modalities") or []),
            context_length=constraints.get("context_length") or item.get("context_length"),
            raw=item,
        )
