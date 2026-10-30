"""Local general-purpose Qwen VLM; no NuExtract-specific template tokens."""

from __future__ import annotations

import json
import time
from pathlib import Path

from esg_extraction.models.capabilities import local_model_readiness
from esg_extraction.models.errors import ModelRunFailure
from esg_extraction.models.mlx_runtime import MlxVlmRuntime
from esg_extraction.models.schema_decoding import (
    SchemaDecodingError,
    build_decoding_constraint,
)
from esg_extraction.models.structured_template import (
    STRUCTURED_OUTPUT_RULES,
    json_schema,
)
from esg_runtime.model_calls import ModelCapabilities, ModelCallRequest, prepare_call, finish_call, content_digest


class LocalQwenModel(MlxVlmRuntime):
    provider_name = "local_qwen"

    def __init__(self, model_path: Path, *, max_tokens=8192, timeout_seconds=600):
        self.model_path = Path(model_path).resolve()
        self.model_id = self.model_path.name
        self.max_tokens = max_tokens
        self.max_input_chars = 160000
        self.max_input_tokens = 65536
        self.timeout_seconds = timeout_seconds
        self._model = None
        self._processor = None

    def complete(self, *, instructions, context, schema, max_tokens, cancel_check):
        execution_context = context.get("execution", {})
        context = {key: value for key, value in context.items() if key != "execution"}
        return self.generate_structured(
            context=context,
            instructions=instructions,
            template=schema,
            max_tokens=max_tokens,
            cancel_check=cancel_check,
            progress_callback=getattr(self, "progress_callback", None),
            execution_context=execution_context,
        )

    def _ensure_loaded(self) -> float:
        if self._model is None:
            readiness = local_model_readiness(self.model_path)
            if not readiness["available"]:
                raise ModelRunFailure(
                    f"Local Qwen runtime unavailable: {readiness.get('error')}",
                    category="request_config",
                    telemetry={"readiness": readiness},
                )
        return super()._ensure_loaded()

    def _render_prompt(self, *, context, template, instructions, images):
        from mlx_vlm.prompt_utils import apply_chat_template

        schema = json_schema(json.loads(template))
        prompt = "\n\n".join(
            (
                instructions,
                STRUCTURED_OUTPUT_RULES,
                "OUTPUT_SCHEMA="
                + json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                "EVIDENCE=" + context,
            )
        )
        return apply_chat_template(
            self._processor,
            self._model.config,
            prompt,
            num_images=len(images),
            enable_thinking=False,
        )

    def _generation_constraints(self, *, template: str) -> tuple[dict, dict]:
        started = time.perf_counter()
        tokenizer = getattr(self._processor, "tokenizer", self._processor)
        try:
            processor, details = build_decoding_constraint(
                tokenizer, json_schema(json.loads(template))
            )
        except (SchemaDecodingError, ValueError, TypeError) as exc:
            raise ModelRunFailure(
                str(exc),
                category="request_config",
                telemetry={
                    "schema_enforcement": "constraint_not_installed",
                    "schema_decoding": {"status": "failed", "lenient": False},
                },
            ) from exc
        details.update(
            status="installed", compile_seconds=round(time.perf_counter() - started, 3)
        )
        enforcement = (
            "grammar_structure_with_deferred_constraints"
            if details["deferred_constraints"]
            else "grammar_schema"
        )
        return {"logits_processors": [processor], "enable_thinking": False}, {
            "schema_enforcement": enforcement,
            "schema_decoding": details,
        }

    def generate_structured(
        self,
        *,
        context,
        instructions,
        template,
        image_path=None,
        max_tokens=2048,
        cancel_check=None,
        progress_callback=None,
        execution_context=None,
    ):
        if not isinstance(context, str):
            context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        started = time.perf_counter()
        execution_context = execution_context or {}
        configured_timeout = self.timeout_seconds
        receipt = prepare_call(ModelCallRequest(
            run_id=execution_context.get("run_id"), evidence_ids=execution_context.get("evidence_ids", []),
            attempt=execution_context.get("attempt", 0), parent_task_id=execution_context.get("parent_task_id"),
            task_type=execution_context.get("task_type", "structured_reading"), input_modalities=["text", "image"] if image_path else ["text"],
            prompt_digest=content_digest([instructions, context]), schema_digest=content_digest(json_schema(template)),
            requested_output_tokens=max_tokens, requested_timeout_seconds=execution_context.get("remaining_seconds", configured_timeout),
        ), ModelCapabilities(provider=self.provider_name, model=self.model_id, input_modalities=["text", "image"],
            max_output_tokens=self.max_tokens, timeout_seconds=self.timeout_seconds, output_constraint="grammar_schema"))
        try:
            self.timeout_seconds = receipt.effective_timeout_seconds
            raw, telemetry = self._generate(
                context=context,
                template=json.dumps(json_schema(template), ensure_ascii=False),
                instructions=instructions,
                images=[str(image_path)] if image_path else [],
                output_token_budget=receipt.effective_output_tokens,
                cancel_check=cancel_check,
                preflight={"route": "bounded_agent_task", "effective_budget": receipt.model_dump(mode="json")},
                progress_callback=progress_callback,
            )
        except Exception as exc:
            telemetry = getattr(exc, "telemetry", {})
            exc.telemetry = {**telemetry, "execution": finish_call(receipt, started, telemetry, error=exc)}
            raise
        finally:
            self.timeout_seconds = configured_timeout
        return raw, {
            **telemetry,
            "provider": self.provider_name,
            "model_id": self.model_id,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "execution": finish_call(receipt, started, telemetry),
        }
