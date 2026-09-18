from __future__ import annotations

import base64
import http.client
import json
import mimetypes
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from esg_targeted.contracts import EvidencePacket, ModelRunResult
from esg_targeted.models.decision_normalizer import SemanticDecisionNormalizer
from esg_targeted.models.errors import ModelCancelled, ModelRunFailure
from esg_targeted.models.response_adapter import DirectFillResponseAdapter
from esg_targeted.models.template import DirectFillTemplateCompiler


JsonRequester = Callable[[str, dict, dict[str, str], float], dict]


class QiniuVlmModel:
    """Cloud VLM direct semantic fill for one visual object with strict JSON."""

    provider_name = "qiniu_vlm"
    prefers_rich_visual_context = True

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model_id: str,
        timeout_seconds: float = 240.0,
        max_output_tokens: int = 32768,
        max_image_bytes: int = 7_500_000,
        requester: JsonRequester | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.max_image_bytes = max_image_bytes
        self.requester = requester or self._default_requester
        self.template_compiler = DirectFillTemplateCompiler()
        self.response_adapter = DirectFillResponseAdapter()
        self.decision_normalizer = SemanticDecisionNormalizer()

    def prefers_visual_for(self, packet: EvidencePacket) -> bool:
        return bool(packet.page_image_paths)

    def decide(
        self,
        packet: EvidencePacket,
        *,
        feedback: str | None = None,
        use_visual: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ModelRunResult:
        self._check_config()
        self._check_cancel(cancel_check)
        if len(packet.page_image_paths) > 1:
            raise ModelRunFailure(
                "one semantic-fill call may contain at most one visual object/image",
                category="request_config",
            )
        images = packet.page_image_paths if use_visual else []
        prompt = self._decision_prompt(packet, feedback)
        started = time.perf_counter()
        raw_output, telemetry = self._chat(
            prompt,
            images,
            self.max_output_tokens,
            response_format=self._decision_response_format(packet),
        )
        self._check_cancel(cancel_check)
        try:
            decision, _, adapter_actions = self.response_adapter.parse_with_diagnostics(
                raw_output,
                packet=packet,
                visual_used=bool(images),
            )
        except Exception as exc:
            category = "schema" if "contract" in str(exc) else "syntax"
            if telemetry.get("finish_reason") == "length":
                category = "output_budget"
            raise ModelRunFailure(
                str(exc),
                category=category,
                raw_output=raw_output,
                telemetry=telemetry,
            ) from exc
        normalized = self.decision_normalizer.normalize(decision, packet)
        normalized_decision = normalized.decision
        normalization_actions = [*adapter_actions, *normalized.actions]
        if telemetry.get("finish_reason") == "length":
            normalization_actions.append("generation_reached_output_budget")
            if normalized_decision.status == "found":
                normalized_decision = normalized_decision.model_copy(
                    update={
                        "status": "partial",
                        "uncertainty_code": "insufficient_evidence",
                    }
                )
        cleaned = json.dumps(
            normalized_decision.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        telemetry["timings"]["total_seconds"] = round(
            time.perf_counter() - started, 4
        )
        return ModelRunResult(
            decision=normalized_decision,
            raw_output=raw_output,
            cleaned_output=cleaned,
            visual_used=bool(images),
            image_count=len(images),
            generation_tokens=telemetry.get("generation_tokens"),
            prompt_tokens=telemetry.get("prompt_tokens"),
            finish_reason=telemetry.get("finish_reason"),
            output_token_budget=self.max_output_tokens,
            input_chars=len(prompt),
            timings=telemetry["timings"],
            normalization_actions=normalization_actions,
            provider=self.provider_name,
            model_id=self.model_id,
        )

    def close(self) -> None:
        return None

    def _decision_prompt(self, packet: EvidencePacket, feedback: str | None) -> str:
        return "\n\n".join(
            (
                "You are the direct semantic table-filling stage of an evidence-grounded ESG system.",
                self.template_compiler.instructions(feedback),
                "Cloud mode is capability-first: inspect the one supplied crop at full available resolution and use its complete bounded evidence context. Do not shorten the answer by omitting applicable rows.",
                "Return only one JSON object matching the strict response schema.",
                "EVIDENCE_CONTEXT=" + packet.model_context,
            )
        )

    def _decision_response_format(self, packet: EvidencePacket) -> dict:
        context = json.loads(packet.model_context)
        group_ids = list(packet.alias_map.get("groups", {}))
        target_cell_ids = list(packet.alias_map.get("target_cells", {}))
        group_schema: dict = {"type": "string"}
        if group_ids:
            group_schema["enum"] = group_ids
        fields = {}
        for element in context.get("elements", []):
            name = element.get("code") or element.get("id")
            if not name:
                continue
            fields[name] = {
                "anyOf": [
                    {"type": "null"},
                    {"type": "string", "minLength": 1},
                    {"type": "number"},
                    {"type": "boolean"},
                ]
            }
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task_id": {"type": "string", "const": packet.task_id},
                "status": {
                    "type": "string",
                    "enum": ["found", "partial", "not_found", "ambiguous"],
                },
                "rows": {
                    "type": "array",
                    "maxItems": int(packet.budget.get("max_output_rows", 80)),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "target_cell": (
                                {
                                    "anyOf": [
                                        {"type": "string", "enum": target_cell_ids},
                                        {"type": "null"},
                                    ]
                                }
                                if target_cell_ids
                                else {"type": "null"}
                            ),
                            "group": group_schema,
                            "fields": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": fields,
                                "required": list(fields),
                            },
                            "metric_match": {"type": "string", "enum": ["match", "uncertain", "different"]},
                            "interpretation_note": {"type": ["string", "null"]},
                            "context_refs": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["target_cell", "group", "fields", "metric_match", "interpretation_note", "context_refs"],
                    },
                },
                "uncertainty_code": {
                    "type": "string",
                    "enum": [
                        "none",
                        "insufficient_evidence",
                        "conflicting_values",
                        "scope_ambiguous",
                        "period_ambiguous",
                        "model_output_invalid",
                    ],
                },
                "skipped_targets": {
                    "type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {"target_cell": {"type": "string"}, "reason": {"type": "string"}},
                        "required": ["target_cell", "reason"],
                    },
                },
                "missing_context": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id", "status", "rows", "uncertainty_code", "skipped_targets", "missing_context"],
        }
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "targeted_direct_semantic_fill",
                "strict": True,
                "schema": schema,
            },
        }

    def _chat(
        self,
        prompt: str,
        image_paths: list[str],
        max_tokens: int,
        *,
        response_format: dict | None = None,
    ) -> tuple[str, dict]:
        user_content: list[dict] = [{"type": "text", "text": prompt}]
        for path in image_paths:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_data_url(path)},
                }
            )
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": "Perform direct ESG semantic filling. Return grounded JSON only.",
                },
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        started = time.perf_counter()
        requested_formats = [response_format, {"type": "json_object"}, None]
        unique_formats = []
        for candidate in requested_formats:
            if candidate not in unique_formats:
                unique_formats.append(candidate)
        response = None
        accepted_format = response_format
        fallback_errors = []
        for index, candidate_format in enumerate(unique_formats):
            candidate_payload = dict(payload)
            if candidate_format is None:
                candidate_payload.pop("response_format", None)
            else:
                candidate_payload["response_format"] = candidate_format
            try:
                response = self.requester(
                    f"{self.base_url}/chat/completions",
                    candidate_payload,
                    self._headers(),
                    self.timeout_seconds,
                )
                accepted_format = candidate_format
                break
            except ModelRunFailure as exc:
                if not self._response_format_rejected(exc) or index == len(unique_formats) - 1:
                    raise
                fallback_errors.append(str(exc))
        assert response is not None
        elapsed = time.perf_counter() - started
        choices = response.get("choices") or []
        if not choices:
            raise ModelRunFailure(
                "Qiniu response has no choices",
                category="schema",
                telemetry={"response": response},
            )
        choice = choices[0]
        content = choice.get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict)
            )
        usage = response.get("usage") or {}
        return str(content), {
            "provider": self.provider_name,
            "model_id": response.get("model") or self.model_id,
            "prompt_tokens": usage.get("prompt_tokens"),
            "generation_tokens": usage.get("completion_tokens"),
            "finish_reason": choice.get("finish_reason"),
            "response_format_requested": (response_format or {}).get("type"),
            "response_format_accepted": (accepted_format or {}).get("type"),
            "response_format_fallback_count": len(fallback_errors),
            "response_format_fallback_errors": fallback_errors,
            "timings": {"request_seconds": round(elapsed, 4)},
        }

    @staticmethod
    def _response_format_rejected(error: ModelRunFailure) -> bool:
        if error.category != "request_config" or error.telemetry.get("http_status") not in {400, 422}:
            return False
        detail = f"{error.telemetry.get('provider_error_code', '')} {error}".casefold()
        return "response_format" in detail or "json_schema" in detail

    def _image_data_url(self, path: str) -> str:
        source = Path(path)
        data = source.read_bytes()
        if len(data) > self.max_image_bytes:
            raise ModelRunFailure(
                f"image exceeds Qiniu request limit: {len(data)} > "
                f"{self.max_image_bytes} bytes",
                category="request_budget",
                telemetry={"image_path": str(source), "image_bytes": len(data)},
            )
        mime = mimetypes.guess_type(source.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _check_config(self) -> None:
        if not self.api_key:
            raise ModelRunFailure(
                "QINIU_API_KEY is not configured", category="request_config"
            )

    @staticmethod
    def _check_cancel(cancel_check) -> None:
        if cancel_check is not None and cancel_check():
            raise ModelCancelled()

    @staticmethod
    def _default_requester(url, payload, headers, timeout) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                provider_error = json.loads(body).get("error") or {}
            except (ValueError, TypeError, AttributeError):
                provider_error = {}
            error_code = str(provider_error.get("code") or "") if isinstance(provider_error, dict) else ""
            category = (
                "provider_account_blocked"
                if exc.code == 403 and error_code == "account_billing_suspended"
                else "request_config" if 400 <= exc.code < 500 and exc.code != 429 else "service"
            )
            raise ModelRunFailure(
                f"Qiniu HTTP {exc.code}: {body[:1000]}",
                category=category,
                raw_output=body,
                telemetry={"http_status": exc.code, "provider_error_code": error_code},
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ModelRunFailure("Qiniu request timed out", category="timeout") from exc
        except urllib.error.URLError as exc:
            category = (
                "timeout"
                if isinstance(exc.reason, (TimeoutError, socket.timeout))
                else "service"
            )
            raise ModelRunFailure(
                f"Qiniu request failed: {exc.reason}", category=category
            ) from exc
        except (
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ConnectionAbortedError,
            ssl.SSLError,
            BrokenPipeError,
        ) as exc:
            # OpenAI-compatible gateways and their TLS proxies can close a long
            # visual request without producing an HTTP response. This is a
            # recoverable service failure, not an application runtime error. The
            # workflow must retry the same visual packet instead of silently
            # downgrading to a text-only attempt.
            raise ModelRunFailure(
                f"Qiniu connection closed during request: {type(exc).__name__}: {exc}",
                category="service",
            ) from exc
