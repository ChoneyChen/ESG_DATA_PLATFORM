from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from esg_targeted.contracts import EvidencePacket, ModelRunResult
from esg_targeted.models.capabilities import NUEXTRACT3_CAPABILITIES, ModelCapabilities
from esg_targeted.models.decision_normalizer import SemanticDecisionNormalizer
from esg_targeted.models.errors import ModelCancelled, ModelRunFailure, ModelTimedOut
from esg_targeted.models.preflight import PacketPreflightGuard, PromptBudgetExceeded
from esg_targeted.models.response_adapter import DirectFillResponseAdapter
from esg_targeted.models.template import DirectFillTemplateCompiler


class NuExtractMlxModel:
    """Local NuExtract3 direct multi-row semantic fill."""

    provider_name = "local_nuextract"

    def __init__(
        self,
        model_path: Path,
        *,
        max_tokens: int = 32768,
        max_input_chars: int = 160000,
        max_input_tokens: int = 65536,
        timeout_seconds: float = 600.0,
        min_output_tokens: int = 2048,
        capabilities: ModelCapabilities = NUEXTRACT3_CAPABILITIES,
    ) -> None:
        self.model_path = model_path
        self.model_id = model_path.name
        self.max_tokens = max_tokens
        self.max_input_chars = max_input_chars
        self.max_input_tokens = max_input_tokens
        self.timeout_seconds = timeout_seconds
        self.capabilities = capabilities
        self.min_output_tokens = max(min_output_tokens, capabilities.min_output_tokens)
        self._model = None
        self._processor = None
        self.template_compiler = DirectFillTemplateCompiler()
        self.response_adapter = DirectFillResponseAdapter()
        self.decision_normalizer = SemanticDecisionNormalizer()
        self.preflight = PacketPreflightGuard(max_input_chars)

    def _ensure_loaded(self) -> float:
        if self._model is not None:
            return 0.0
        from mlx_vlm import load

        started = time.perf_counter()
        self._model, self._processor = load(str(self.model_path))
        return time.perf_counter() - started

    def decide(
        self,
        packet: EvidencePacket,
        *,
        feedback: str | None = None,
        use_visual: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ModelRunResult:
        if not self.capabilities.supports_structured_output:
            raise ModelRunFailure(
                "selected model does not support structured output",
                category="request_config",
            )
        if self.capabilities.enable_thinking and not self.capabilities.supports_thinking:
            raise ModelRunFailure(
                "thinking is enabled for an incompatible model",
                category="request_config",
            )
        if use_visual and not self.capabilities.supports_vision:
            raise ModelRunFailure(
                "selected model does not support visual input",
                category="request_config",
            )

        feedback = self.preflight.fit_feedback(packet, feedback)
        preflight = self.preflight.validate(packet, feedback=feedback)
        images = packet.page_image_paths[:1] if use_visual else []
        output_token_budget = self._output_token_budget(
            packet,
            visual_used=bool(images),
        )
        raw_output, telemetry = self._generate(
            context=packet.model_context,
            template=json.dumps(
                self.template_compiler.compile(packet),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            instructions=self.template_compiler.instructions(feedback),
            images=images,
            output_token_budget=output_token_budget,
            cancel_check=cancel_check,
            preflight=preflight.as_dict(),
        )
        try:
            decision, _, adapter_actions = self.response_adapter.parse_with_diagnostics(
                raw_output,
                packet=packet,
                visual_used=bool(images),
            )
        except Exception as exc:
            category = (
                "output_budget"
                if telemetry.get("finish_reason") == "length"
                else ("schema" if "contract" in str(exc) else "syntax")
            )
            raise ModelRunFailure(
                str(exc),
                category=category,
                raw_output=raw_output,
                telemetry=telemetry,
            ) from exc
        normalized = self.decision_normalizer.normalize(decision, packet)
        normalization_actions = [*adapter_actions, *normalized.actions]
        normalized_decision = normalized.decision
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
        return ModelRunResult(
            decision=normalized_decision,
            raw_output=raw_output,
            cleaned_output=cleaned,
            visual_used=bool(images),
            image_count=len(images),
            generation_tokens=telemetry.get("generation_tokens"),
            prompt_tokens=telemetry.get("prompt_tokens"),
            finish_reason=telemetry.get("finish_reason"),
            output_token_budget=output_token_budget,
            input_chars=telemetry.get("input_chars"),
            peak_memory_gb=telemetry.get("peak_memory_gb"),
            timings=telemetry["timings"],
            normalization_actions=list(normalization_actions),
            provider=self.provider_name,
            model_id=self.model_id,
        )

    def _generate(
        self,
        *,
        context: str,
        template: str,
        instructions: str,
        images: list[str],
        output_token_budget: int,
        cancel_check: Callable[[], bool] | None,
        preflight: dict,
    ) -> tuple[str, dict]:
        load_seconds = self._ensure_loaded()
        from mlx_vlm.generate import stream_generate
        from mlx_vlm.prompt_utils import apply_chat_template

        template_started = time.perf_counter()
        prompt = apply_chat_template(
            self._processor,
            self._model.config,
            context,
            num_images=len(images),
            template=template,
            instructions=instructions,
            enable_thinking=self.capabilities.enable_thinking,
        )
        template_seconds = time.perf_counter() - template_started
        input_chars = len(prompt)
        if input_chars > self.max_input_chars:
            raise PromptBudgetExceeded(
                f"rendered prompt exceeds hard limit: {input_chars} > "
                f"{self.max_input_chars} chars"
            )
        input_tokens = self._token_count(prompt)
        if input_tokens is not None and input_tokens > self.max_input_tokens:
            raise PromptBudgetExceeded(
                f"rendered prompt exceeds token limit: {input_tokens} > "
                f"{self.max_input_tokens}"
            )

        started = time.perf_counter()
        raw_parts: list[str] = []
        last = None
        generator = stream_generate(
            self._model,
            self._processor,
            prompt=prompt,
            image=images or None,
            max_tokens=output_token_budget,
            temperature=0.0,
            verbose=False,
        )
        try:
            for response in generator:
                if getattr(response, "is_draft", False):
                    continue
                raw_parts.append(response.text)
                last = response
                elapsed = time.perf_counter() - started
                telemetry = self._telemetry(
                    last,
                    input_chars=input_chars,
                    input_tokens=input_tokens,
                    load_seconds=load_seconds,
                    template_seconds=template_seconds,
                    generation_seconds=elapsed,
                    output_token_budget=output_token_budget,
                    preflight=preflight,
                )
                if cancel_check and cancel_check():
                    raise ModelCancelled("".join(raw_parts), telemetry)
                if elapsed > self.timeout_seconds:
                    raise ModelTimedOut(
                        self.timeout_seconds, "".join(raw_parts), telemetry
                    )
        finally:
            generator.close()

        raw_output = "".join(raw_parts)
        telemetry = self._telemetry(
            last,
            input_chars=input_chars,
            input_tokens=input_tokens,
            load_seconds=load_seconds,
            template_seconds=template_seconds,
            generation_seconds=time.perf_counter() - started,
            output_token_budget=output_token_budget,
            preflight=preflight,
        )
        return raw_output, telemetry

    def _token_count(self, prompt: str) -> int | None:
        tokenizer = getattr(self._processor, "tokenizer", self._processor)
        encode = getattr(tokenizer, "encode", None)
        if not callable(encode):
            return None
        try:
            return len(encode(prompt))
        except Exception:
            return None

    @staticmethod
    def _telemetry(
        response,
        *,
        input_chars,
        input_tokens,
        load_seconds,
        template_seconds,
        generation_seconds,
        output_token_budget,
        preflight,
    ) -> dict:
        return {
            "input_chars": input_chars,
            "input_tokens": input_tokens,
            "prompt_tokens": getattr(response, "prompt_tokens", input_tokens),
            "generation_tokens": getattr(response, "generation_tokens", None),
            "finish_reason": getattr(response, "finish_reason", None),
            "output_token_budget": output_token_budget,
            "peak_memory_gb": getattr(response, "peak_memory", None),
            "prompt_tps": getattr(response, "prompt_tps", None),
            "generation_tps": getattr(response, "generation_tps", None),
            "preflight": preflight,
            "timings": {
                "model_load_seconds": round(load_seconds, 3),
                "template_seconds": round(template_seconds, 3),
                "generation_seconds": round(generation_seconds, 3),
            },
        }

    def _output_token_budget(
        self,
        packet: EvidencePacket,
        *,
        visual_used: bool = False,
    ) -> int:
        if visual_used:
            # A full table/figure can contain substantially more applicable rows
            # than OCR discovered. max_tokens is only a ceiling; models that
            # finish normally still stop early. Do not constrain visual reading
            # with a local row-count estimate.
            return self.max_tokens
        context = json.loads(packet.model_context)
        target_cells = len((context.get("region") or {}).get("target_value_cells") or [])
        rows = max(
            1,
            target_cells
            or min(24, int(packet.budget.get("max_output_rows", 1))),
        )
        fields = max(1, len(context.get("elements", [])))
        # Complete-table rows repeat the field keys, so output grows almost
        # linearly with both row and field count. Keep a generous reserve for
        # CJK tokenisation and do not artificially clamp visual-table answers to
        # the old eight-row budget.
        estimated = 1024 + rows * (120 + min(fields, 16) * 24)
        if rows >= 40:
            estimated = max(estimated, 12_288)
        return min(self.max_tokens, max(self.min_output_tokens, estimated))

    def close(self) -> None:
        if self._model is None:
            return
        import mlx.core as mx

        self._model = None
        self._processor = None
        mx.clear_cache()
