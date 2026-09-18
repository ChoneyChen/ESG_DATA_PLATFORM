from __future__ import annotations

import json
import re
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from esg_v2.config import Settings
from esg_v2.document.contracts import AgentModelCall, DocumentIR, VlmReviewTask
from esg_v2.document.identifiers import sequential_id
from esg_v2.document.operation_registry import OperationRegistry
from esg_v2.document.review_response_adapter import ModelOutputTruncated, ReviewResponseAdapter
from esg_v2.models.contracts import CloudChatRequest, CloudChatResult
from esg_v2.models.model_registry import ModelHealthRegistry, ModelProfile
from esg_v2.models.provider_rate_limit import ProviderRateLimitCoordinator, ProviderRateLimitOpen
from esg_v2.models.provider_error import ModelProviderError
from esg_v2.models.review_provider import ReviewModelAdapter, ReviewModelRegistry


PayloadT = TypeVar("PayloadT", bound=BaseModel)


class ReviewExecutionUnavailable(RuntimeError):
    pass


class ProviderAccountQuotaUnavailable(ReviewExecutionUnavailable):
    def __init__(self, message: str, *, provider_state: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.provider_state = provider_state or {}


class ModelRequestConfigurationError(ValueError):
    pass


class ModelRunner:
    """Capability-aware review runner with provider-neutral retry and audit output."""

    def __init__(
        self,
        settings: Settings,
        adapter: ReviewModelAdapter,
        registry: ReviewModelRegistry,
        response_adapter: ReviewResponseAdapter,
        telemetry: Callable[[dict[str, Any]], None] | None = None,
        rate_limits: ProviderRateLimitCoordinator | None = None,
        verification_policy: str = "different_model_family",
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.registry = registry
        self.response_adapter = response_adapter
        self.telemetry = telemetry
        self.rate_limits = rate_limits
        self.verification_policy = verification_policy

    def run(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        *,
        role: str,
        round_index: int,
        images: list[str],
        context: str,
        payload_type: type[PayloadT],
        alias_normalizer,
        exclude_family: str | None = None,
    ) -> tuple[PayloadT, CloudChatResult, ModelProfile]:
        profiles = self.registry.candidates(role, exclude_family=exclude_family, limit=6)
        # A repair round should prefer a different reviewer family, but must not become
        # a service failure solely because the deployment exposes one reviewer family.
        # Verifier independence remains strict and never uses this fallback.
        if not profiles and role == "reviewer" and exclude_family:
            profiles = self.registry.candidates(role, exclude_family=None, limit=6)
        if (
            not profiles
            and role == "verifier"
            and exclude_family
            and self.verification_policy == "same_model_secondary_verification"
        ):
            profiles = self.registry.candidates(role, exclude_family=None, limit=6)
        if not profiles:
            raise ReviewExecutionUnavailable(f"no_healthy_{role}_model_available")
        errors: list[str] = []
        for profile in profiles:
            output_tokens = self.output_budget(task, role, context, profile)
            attempts = profile.transient_retries + 1
            attempt_context = context
            for retry_index in range(attempts):
                try:
                    request = self.build_request(
                        task, profile, role, round_index, images, attempt_context,
                        output_tokens=output_tokens,
                    )
                except ModelRequestConfigurationError as exc:
                    self._record_call(
                        document, task, role, round_index, profile,
                        status="invalid_response", images=images, context=attempt_context,
                        error=str(exc), category="request_config", retry_index=retry_index,
                    )
                    errors.append(f"{profile.model_id}: request_config: {exc}")
                    break
                result: CloudChatResult | None = None
                try:
                    if self.rate_limits is not None:
                        try:
                            self.rate_limits.before_request(profile.model_id)
                        except ProviderRateLimitOpen as exc:
                            raise ProviderAccountQuotaUnavailable(
                                str(exc),
                                provider_state=self.rate_limits.snapshot(),
                            ) from exc
                    self._emit({
                        "event": "model_attempt_started",
                        "task_id": task.task_id,
                        "role": role,
                        "round_index": round_index,
                        "retry_index": retry_index,
                        "model_id": profile.model_id,
                        "provider": profile.provider,
                    })
                    result = self.adapter.chat_completions(request)
                    payload_dict = self.response_adapter.message_json(result.raw_response)
                    if role == "reviewer":
                        payload_dict = self.response_adapter.normalize_reviewer(
                            document,
                            payload_dict,
                            task,
                            alias_normalizer=alias_normalizer,
                        )
                    else:
                        payload_dict = self.response_adapter.normalize_verifier(payload_dict)
                    payload = payload_type.model_validate(payload_dict)
                    ModelHealthRegistry.record_success(profile.model_id, result.latency_ms)
                    self._record_call(
                        document, task, role, round_index, profile,
                        status="succeeded", images=images, context=attempt_context,
                        result=result, payload=payload_dict, retry_index=retry_index,
                        http_attempted=True,
                    )
                    return payload, result, profile
                except ModelOutputTruncated as exc:
                    self._record_call(
                        document, task, role, round_index, profile,
                        status="invalid_response", images=images, context=attempt_context,
                        result=result, error=str(exc), category="request_config", retry_index=retry_index,
                        http_attempted=True,
                    )
                    expanded = min(profile.max_output_tokens, max(output_tokens + 1024, int(output_tokens * 1.75)))
                    if retry_index + 1 < attempts and expanded > output_tokens:
                        output_tokens = expanded
                        continue
                    errors.append(f"{profile.model_id}: request_config: output_length: {exc}")
                    break
                except ModelProviderError as exc:
                    error = str(exc)[:2000]
                    if self._is_request_configuration_error(exc):
                        self._record_call(
                            document, task, role, round_index, profile,
                            status="invalid_response", images=images, context=attempt_context,
                            error=error, category="request_config", retry_index=retry_index,
                            http_attempted=True,
                        )
                        expanded = min(profile.max_output_tokens, max(output_tokens, profile.min_output_tokens, 9000))
                        if retry_index + 1 < attempts and expanded >= output_tokens:
                            output_tokens = expanded
                            continue
                        errors.append(f"{profile.model_id}: request_config: {error}")
                        break
                    category = (
                        "rate_limit_tpd"
                        if exc.account_wide_rate_limit
                        else "rate_limit_rpm"
                        if exc.minute_rate_limit
                        else "transport"
                    )
                    self._record_call(
                        document, task, role, round_index, profile,
                        status="failed", images=images, context=attempt_context,
                        error=error, category=category, retry_index=retry_index,
                        http_attempted=True,
                    )
                    if exc.account_wide_rate_limit:
                        provider_state = {}
                        if self.rate_limits is not None:
                            provider_state = self.rate_limits.record_tpd(
                                error,
                                retry_after_seconds=exc.retry_after_seconds,
                                request_id=exc.request_id,
                            )
                        ModelHealthRegistry.record_failure(
                            profile.model_id, error, category="quota", retryable=False,
                        )
                        raise ProviderAccountQuotaUnavailable(
                            f"{profile.provider}_account_rate_limited: {error}",
                            provider_state=provider_state,
                        ) from exc
                    if exc.minute_rate_limit:
                        if self.rate_limits is not None:
                            self.rate_limits.record_rpm(
                                profile.model_id,
                                error,
                                retry_index=retry_index,
                                retry_after_seconds=exc.retry_after_seconds,
                                request_id=exc.request_id,
                            )
                        ModelHealthRegistry.record_failure(
                            profile.model_id,
                            error,
                            category="rpm",
                            retryable=True,
                        )
                        if retry_index + 1 < attempts:
                            continue
                        errors.append(f"{profile.model_id}: rpm_rate_limit: {error}")
                        break
                    if exc.retryable and retry_index + 1 < attempts:
                        continue
                    ModelHealthRegistry.record_failure(
                        profile.model_id, error, category="transport", retryable=exc.retryable,
                    )
                    errors.append(f"{profile.model_id}: {error}")
                    break
                except (ValueError, ValidationError, TypeError) as exc:
                    error = str(exc)[:2000]
                    self._record_call(
                        document, task, role, round_index, profile,
                        status="invalid_response", images=images, context=attempt_context,
                        result=result, error=error, category="protocol", retry_index=retry_index,
                        http_attempted=True,
                    )
                    if retry_index + 1 < attempts:
                        try:
                            corrected = json.loads(context)
                            guidance = "Return the same visual decision as one valid JSON object."
                            if (
                                role == "reviewer" and task.review_plan
                                and task.review_plan.review_kind in {"page_text_coverage", "figure_semantic_structure"}
                            ):
                                guidance += (
                                    " Keep each chart series as an object inside series[]. "
                                    "Place visual_evidence_refs beside series, not inside it."
                                )
                            corrected["protocol_feedback"] = f"{guidance} Previous error: {error[:400]}"
                            attempt_context = json.dumps(corrected, ensure_ascii=False, separators=(",", ":"))
                        except (ValueError, TypeError):
                            attempt_context = context
                        continue
                    ModelHealthRegistry.record_failure(
                        profile.model_id, f"invalid_response:{type(exc).__name__}", category="protocol",
                    )
                    errors.append(f"{profile.model_id}: invalid_response: {error}")
                    break
        raise ReviewExecutionUnavailable(f"all_{role}_models_failed: {' | '.join(errors)}")

    def build_request(
        self,
        task: VlmReviewTask,
        profile: ModelProfile,
        role: str,
        round_index: int,
        images: list[str],
        context: str,
        *,
        output_tokens: int,
    ) -> CloudChatRequest:
        if not profile.vision and images:
            raise ModelRequestConfigurationError(f"{profile.model_id} is not declared vision-capable")
        if not profile.structured_output:
            raise ModelRequestConfigurationError(
                f"{profile.model_id} is not declared structured-output capable"
            )
        if role == "reviewer":
            operations = task.review_plan.allowed_operations if task.review_plan else []
            operation_contract = OperationRegistry.prompt_fragment(operations)
            instruction = (
                "Return strict JSON with verdict, findings, scope_decisions, patches, confidence, "
                "abstain_reason, and quality_flags. Cover every routed scope exactly once. before_value is local-only. "
                "Use only operation_contract. set_table_grid must use TableGridRepairProposal with complete cells, "
                "source_cell_ids, missing_text, visual_evidence_refs, and repair_reason. Charts must use canonical "
                "upsert_chart_spec with chart_type and series[].points[{category,value,display_value,evidence_refs}]. "
                "Do not use type, x_categories, data, or values aliases. If current chart_spec is null and the "
                "figure has visible values, propose the ChartSpec instead of confirming unchanged. operation_contract="
                + str(operation_contract)
            )
        else:
            independence = (
                "Use a fresh secondary pass and actively challenge the reviewer; this local provider uses the "
                "same model with isolated verifier context."
                if self.verification_policy == "same_model_secondary_verification"
                else "Act as an independent model-family verifier."
            )
            instruction = (
                "Return strict JSON with verdict, disagreements, confidence, and exactly one "
                f"transaction_decision per supplied transaction_id. {independence}"
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": f"{instruction}\n\n{context}"}]
        content.extend({"type": "image_url", "image_url": {"url": image}} for image in images)
        thinking = {"type": "disabled"} if profile.thinking_control == "disabled" else None
        reasoning_effort = "low" if profile.thinking_control == "reasoning_effort" else None
        return CloudChatRequest(
            request_id=f"{task.task_id}-{role}-r{round_index}",
            model_id=profile.model_id,
            max_tokens=max(profile.min_output_tokens, min(output_tokens, profile.max_output_tokens)),
            temperature=0.1,
            response_format={"type": "json_object"} if profile.structured_output and profile.supports_json_mode else None,
            reasoning_effort=reasoning_effort,
            thinking=thinking,
            timeout_seconds=(
                self.settings.nuextract_timeout_seconds
                if profile.provider == "local_nuextract"
                else self.settings.qiniu_default_timeout_seconds
            ),
            metadata={
                "task_type": task.task_type,
                "role": role,
                "round_index": round_index,
                "review_kind": task.review_plan.review_kind if task.review_plan else None,
                "provider": profile.provider,
                "verification_policy": self.verification_policy,
            },
            output_template=self._output_template(task, role, context),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a controlled Document IR visual review agent. Reconstruct only visible document "
                        "structure and OCR; never infer ESG facts. Output strict JSON only."
                    ),
                },
                {"role": "user", "content": content},
            ],
        )

    @staticmethod
    def output_budget(task: VlmReviewTask, role: str, context: str, profile: ModelProfile) -> int:
        if role == "verifier":
            base = 2600
        else:
            review_kind = task.review_plan.review_kind if task.review_plan else ""
            base = {
                "horizontal_page_spread": 5200,
                "table_structure_reconstruction": 6200,
                "table_candidate_classification": 2600,
                "page_text_coverage": 4600,
                "figure_semantic_structure": 2800,
                "figure_binding": 2200,
            }.get(review_kind, 3000)
        complexity = min(2200, len(context) // 20)
        return max(profile.min_output_tokens, min(profile.max_output_tokens, base + complexity))

    @staticmethod
    def _is_request_configuration_error(exc: ModelProviderError) -> bool:
        message = str(exc).lower()
        return exc.status_code in {400, 422} and any(
            marker in message
            for marker in ("thinking_budget", "max_completion_tokens", "max_tokens", "reasoning", "length")
        )

    @staticmethod
    def _summary(task: VlmReviewTask, images: list[str], context: str) -> dict[str, Any]:
        return {
            "task_type": task.task_type,
            "page_index": task.page_index,
            "reason_codes": task.reason_codes,
            "image_count": len(images),
            "context_characters": len(context),
            "provider": task.provider,
        }

    def _record_call(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        role: str,
        round_index: int,
        profile: ModelProfile,
        *,
        status: str,
        images: list[str],
        context: str,
        result: CloudChatResult | None = None,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
        category: str = "none",
        retry_index: int = 0,
        http_attempted: bool = False,
    ) -> None:
        call = AgentModelCall(
            call_id=sequential_id("model-call", (item.call_id for item in document.model_calls)),
            task_id=task.task_id,
            role=role,
            round_index=round_index,
            model_id=profile.model_id,
            model_family=profile.family,
            provider=profile.provider,
            status=status,
            request_summary=self._summary(task, images, context),
            response_payload=payload,
            raw_response=result.raw_response if result else None,
            usage=result.usage if result else {},
            latency_ms=result.latency_ms if result else None,
            error=error,
            failure_category=category,
            retry_index=retry_index,
        )
        document.model_calls.append(call)
        if http_attempted:
            self._emit({
                "event": "model_attempt_completed",
                "task_id": task.task_id,
                "role": role,
                "round_index": round_index,
                "retry_index": retry_index,
                "model_id": profile.model_id,
                "provider": profile.provider,
                "status": status,
                "failure_category": category,
                "latency_ms": call.latency_ms,
                "usage": call.usage,
            })

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.telemetry:
            self.telemetry(payload)

    @staticmethod
    def _output_template(task: VlmReviewTask, role: str, context: str) -> dict[str, Any]:
        target_types = list(dict.fromkeys([task.target_type, *(item.target_type for item in task.scope)]))
        target_ids = list(dict.fromkeys([task.target_id, *(item.target_id for item in task.scope)]))
        if role == "verifier":
            transaction_ids = list(dict.fromkeys(re.findall(r"transaction-[A-Za-z0-9_-]+", context)))
            return {
                "verdict": ["accept", "reject", "abstain"],
                "disagreements": ["verbatim-string"],
                "confidence": "number",
                "transaction_decisions": [
                    {
                        "transaction_id": transaction_ids or "verbatim-string",
                        "verdict": ["accept", "reject", "abstain"],
                        "disagreements": ["verbatim-string"],
                        "confidence": "number",
                    }
                ],
            }
        operations = task.review_plan.allowed_operations if task.review_plan else ["confirm"]
        return {
            "verdict": ["confirm", "propose_patch", "abstain"],
            "findings": ["verbatim-string"],
            "scope_decisions": [
                {
                    "target_type": target_types,
                    "target_id": target_ids,
                    "decision": ["confirm", "propose_patch", "abstain"],
                    "rationale": "verbatim-string",
                    "confidence": "number",
                }
            ],
            "patches": [
                {
                    "target_type": target_types,
                    "target_id": target_ids,
                    "operation": operations,
                    "field_path": "verbatim-string",
                    "proposed_value": "verbatim-json-value",
                    "evidence_refs": ["verbatim-string"],
                    "rationale": "verbatim-string",
                    "confidence": "number",
                }
            ],
            "confidence": "number",
            "abstain_reason": "verbatim-string",
            "quality_flags": ["verbatim-string"],
        }
