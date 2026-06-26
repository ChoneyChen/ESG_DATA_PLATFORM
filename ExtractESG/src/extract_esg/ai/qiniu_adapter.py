from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from extract_esg.config import Settings
from extract_esg.contracts.cloud import CloudTaskRequest, CloudTaskResult
from extract_esg.contracts.base import SourceTrace


class QiniuApiError(RuntimeError):
    pass


class QiniuModelAdapter:
    """Thin stdlib adapter for Qiniu's OpenAI-compatible API.

    This class intentionally does not know ESG business semantics. It only
    handles authenticated HTTP calls, timeouts, and response capture.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def _headers(self) -> dict[str, str]:
        if not self.settings.qiniu_api_key:
            raise QiniuApiError("QINIU_API_KEY is required for cloud model calls")
        return {
            "Authorization": f"Bearer {self.settings.qiniu_api_key}",
            "Content-Type": "application/json",
        }

    def list_models(self) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.settings.qiniu_base_url}/models",
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.qiniu_default_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise QiniuApiError(f"Qiniu /models failed: {exc.code} {exc.read().decode('utf-8', 'ignore')}") from exc
        except urllib.error.URLError as exc:
            raise QiniuApiError(f"Qiniu /models failed: {exc}") from exc

    def chat_completions(self, task: CloudTaskRequest) -> CloudTaskResult:
        payload: dict[str, Any] = {
            "model": task.model_id,
            "messages": task.messages,
        }
        if task.max_tokens is not None:
            payload["max_tokens"] = task.max_tokens

        started = time.perf_counter()
        request = urllib.request.Request(
            f"{self.settings.qiniu_base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=task.timeout_seconds or self.settings.qiniu_default_timeout_seconds,
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise QiniuApiError(
                f"Qiniu chat/completions failed for {task.model_id}: "
                f"{exc.code} {exc.read().decode('utf-8', 'ignore')}"
            ) from exc
        except urllib.error.URLError as exc:
            raise QiniuApiError(f"Qiniu chat/completions failed for {task.model_id}: {exc}") from exc

        return CloudTaskResult(
            request_id=task.id,
            model_id=task.model_id,
            raw_response=raw,
            usage=raw.get("usage") or {},
            latency_ms=(time.perf_counter() - started) * 1000,
            source=SourceTrace(run_id=task.id, producer="qiniu", version=task.model_id),
        )

