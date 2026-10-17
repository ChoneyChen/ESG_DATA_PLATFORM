from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from esg_targeted.contracts import EvidencePacket, ModelRunResult
from esg_targeted.models.capabilities import NUEXTRACT3_CAPABILITIES, ModelCapabilities
from esg_targeted.models.decision_normalizer import SemanticDecisionNormalizer
from esg_targeted.models.errors import ModelCancelled, ModelRunFailure, ModelTimedOut
from esg_targeted.models.family_adjudication import (
    FamilyAdjudicationPacket,
    FamilyModelRunResult,
    family_context_json,
    family_instructions,
    family_template,
    parse_family_decision,
)
from esg_targeted.models.preflight import PacketPreflightGuard, PromptBudgetExceeded
from esg_targeted.models.response_adapter import DirectFillResponseAdapter
from esg_targeted.models.template import DirectFillTemplateCompiler
from esg_targeted.models.mlx_runtime import MlxVlmRuntime


class DirectFillModelAdapter:
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




    def readjudicate_family(
        self,
        packet: FamilyAdjudicationPacket,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> FamilyModelRunResult:
        if not self.capabilities.supports_structured_output:
            raise ModelRunFailure(
                "selected model does not support structured output",
                category="request_config",
            )
        context = family_context_json(packet)
        if len(context) > self.max_input_chars:
            raise PromptBudgetExceeded(
                "family adjudication context exceeds the configured input limit"
            )
        raw_output, telemetry = self._generate(
            context=context,
            template=json.dumps(
                family_template(packet),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            instructions=family_instructions(),
            images=[],
            output_token_budget=min(
                self.max_tokens,
                max(self.min_output_tokens, len(packet.cases) * 512),
            ),
            cancel_check=cancel_check,
            preflight={"family_case_count": len(packet.cases)},
        )
        try:
            decision = parse_family_decision(raw_output, packet)
        except Exception as exc:
            raise ModelRunFailure(
                str(exc),
                category="schema",
                raw_output=raw_output,
                telemetry=telemetry,
            ) from exc
        return FamilyModelRunResult(
            decision=decision,
            raw_output=raw_output,
            cleaned_output=json.dumps(
                decision.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ),
            provider=self.provider_name,
            model_id=self.model_id,
            prompt_tokens=telemetry.get("prompt_tokens"),
            generation_tokens=telemetry.get("generation_tokens"),
            finish_reason=telemetry.get("finish_reason"),
            timings=telemetry.get("timings") or {},
        )



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

