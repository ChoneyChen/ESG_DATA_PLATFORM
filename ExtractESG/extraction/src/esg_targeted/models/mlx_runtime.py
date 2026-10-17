"""Shared MLX execution; no NuExtract or Qwen prompt assumptions."""
from __future__ import annotations

import gc
import json
import time
from typing import Callable

from esg_targeted.models.errors import ModelCancelled, ModelTimedOut, ModelRunFailure
from esg_targeted.models.preflight import PromptBudgetExceeded


class MlxVlmRuntime:
    def _prepare_runtime_inputs(self, prompt: str, images: list[str]) -> dict | None:
        """Prepare the same engine tensors used by stream_generate, no forward.

        This only uses an already loaded runtime. Synthetic/text adapters with
        no engine config retain their explicitly text-only fallback.
        """
        config = getattr(self._model, "config", None)
        model_type = getattr(config, "model_type", None)
        if model_type is None and isinstance(config, dict):
            model_type = config.get("model_type")
        if not model_type:
            return None
        from mlx_vlm.utils import prepare_inputs, should_add_special_tokens
        return prepare_inputs(self._processor, images=images or None, audio=None,
            videos=None, prompts=prompt, image_token_index=getattr(config, "image_token_index", None),
            resize_shape=None, add_special_tokens=should_add_special_tokens(model_type, self._processor))

    @staticmethod
    def _prepared_token_count(inputs: dict | None) -> int | None:
        ids = inputs.get("input_ids") if inputs else None
        if ids is None:
            return None
        size = getattr(ids, "size", None)
        if isinstance(size, int):
            return size
        shape = getattr(ids, "shape", None)
        if shape:
            result = 1
            for axis in shape:
                result *= int(axis)
            return result
        if isinstance(ids, (list, tuple)):
            return sum(len(row) if isinstance(row, (list, tuple)) else 1 for row in ids)
        return None

    def input_admission(self, *, context, instructions, template, image_path=None):
        """Ready model count; never load weights for a preview or cold estimate."""
        if getattr(self, "_model", None) is None or getattr(self, "_processor", None) is None:
            return None
        from esg_targeted.models.token_admission import InputAdmission
        from esg_targeted.models.structured_template import json_schema, nuextract_template
        context = context if isinstance(context, str) else json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if isinstance(template, str):
            template = json.loads(template)
        if getattr(self, "provider_name", "") == "local_qwen":
            template = json_schema(template)
        elif getattr(self, "provider_name", "") == "local_nuextract":
            template = nuextract_template(template)
        images = [str(image_path)] if image_path else []
        prompt = self._render_prompt(context=context, instructions=instructions,
                                    template=json.dumps(template, ensure_ascii=False), images=images)
        count = self._prepared_token_count(self._prepare_runtime_inputs(prompt, images))
        if count is not None:
            return InputAdmission(count, "prepared_engine_input_tokens", True,
                                  count if not images else None, 0 if not images else None)
        # A text tokenizer does not prove expanded image-token usage.
        if images:
            return None
        count = self._token_count(prompt)
        return InputAdmission(count, "rendered_text_tokens", True, count, 0) if count is not None else None

    def _ensure_loaded(self) -> float:
        if self._model is not None:
            return 0.0
        from mlx_vlm import load

        started = time.perf_counter()
        self._model, self._processor = load(str(self.model_path))
        return time.perf_counter() - started

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
        progress_callback=None,
    ) -> tuple[str, dict]:
        request_started = time.perf_counter()
        try:
            if cancel_check and cancel_check():
                raise ModelCancelled("", {})
            if progress_callback:
                progress_callback({"phase": "loading", "generation_tokens": 0})
            load_seconds = self._ensure_loaded()
            from mlx_vlm.generate import stream_generate

            template_started = time.perf_counter()
            prompt = self._render_prompt(context=context, template=template, instructions=instructions, images=images)
            template_seconds = time.perf_counter() - template_started
            input_chars = len(prompt)
            if input_chars > self.max_input_chars:
                raise PromptBudgetExceeded(
                    f"rendered prompt exceeds hard limit: {input_chars} > "
                    f"{self.max_input_chars} chars"
                )
            prepared_inputs = self._prepare_runtime_inputs(prompt, images)
            input_tokens = self._prepared_token_count(prepared_inputs)
            exact_input = input_tokens is not None
            if input_tokens is None:
                input_tokens = self._token_count(prompt)
            if input_tokens is not None and input_tokens > self.max_input_tokens:
                raise PromptBudgetExceeded(
                    f"rendered prompt exceeds token limit: {input_tokens} > "
                    f"{self.max_input_tokens}"
                )

            admission_guard = getattr(self, "_input_admission_callback", None)
            if callable(admission_guard):
                from esg_targeted.models.token_admission import InputAdmission, estimate_input_tokens
                if exact_input:
                    admission = InputAdmission(input_tokens, "prepared_engine_input_tokens", True,
                                               input_tokens if not images else None, 0 if not images else None)
                elif input_tokens is not None and not images:
                    admission = InputAdmission(input_tokens, "rendered_text_tokens", True, input_tokens, 0)
                else:
                    admission = estimate_input_tokens(prompt, image_count=len(images))
                admission_guard(admission)

            # These checks run before inference. A failure here consumes a
            # request attempt, but no model prompt or generation tokens.
            generation_kwargs, decoding_telemetry = self._generation_constraints(template=template)
            if cancel_check and cancel_check():
                raise ModelCancelled("", {})
        except Exception as exc:
            exc.telemetry = {**getattr(exc, "telemetry", {}), "generation_started": False}
            raise

        started = time.perf_counter()
        if progress_callback:
            progress_callback({"phase": "prefill", "input_chars": input_chars, "input_tokens": input_tokens,
                               "generation_tokens": 0, "output_token_budget": output_token_budget})
        raw_parts: list[str] = []
        last = None
        last_progress = 0.0
        prepared_kwargs = {}
        if prepared_inputs is not None:
            # The count and inference use identical image-expanded input IDs.
            # Avoid preprocessing the full image a second time in the engine.
            prepared_kwargs = {key: value for key, value in prepared_inputs.items()
                               if key not in {"attention_mask", "pixel_values", "input_ids"}}
            prepared_kwargs.update(input_ids=prepared_inputs.get("input_ids"),
                                   pixel_values=prepared_inputs.get("pixel_values"),
                                   mask=prepared_inputs.get("attention_mask"))
        generator = stream_generate(
            self._model,
            self._processor,
            prompt=prompt,
            image=images or None,
            max_tokens=output_token_budget,
            temperature=0.0,
            verbose=False,
            **prepared_kwargs,
            **generation_kwargs,
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
                    decoding_telemetry=decoding_telemetry,
                )
                if progress_callback and elapsed - last_progress >= 1:
                    progress_callback({"phase": "generation", "generation_tokens": getattr(response, "generation_tokens", None),
                                       "elapsed_seconds": round(time.perf_counter() - request_started, 3)})
                    last_progress = elapsed
                if cancel_check and cancel_check():
                    raise ModelCancelled("".join(raw_parts), telemetry)
                if time.perf_counter() - request_started > self.timeout_seconds:
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
            decoding_telemetry=decoding_telemetry,
        )
        if telemetry.get("finish_reason") == "length":
            raise ModelRunFailure(
                "Generation reached its output token limit; split the source task before retrying",
                category="generation_incomplete", raw_output=raw_output, telemetry=telemetry)
        return raw_output, telemetry

    def _generation_constraints(self, *, template: str) -> tuple[dict, dict]:
        """Provider hook; no shared mutable grammar state or default fallback."""
        return {}, {}

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
        decoding_telemetry=None,
    ) -> dict:
        return {
            "generation_started": True,
            "input_chars": input_chars,
            "input_tokens": input_tokens,
            "prompt_tokens": getattr(response, "prompt_tokens", input_tokens),
            "generation_tokens": getattr(response, "generation_tokens", None),
            "finish_reason": getattr(response, "finish_reason", None),
            "output_token_budget": output_token_budget,
            "peak_memory_gb": getattr(response, "peak_memory", None),
            "model_lifecycle": "reused" if load_seconds == 0 else "loaded",
            "prompt_tps": getattr(response, "prompt_tps", None),
            "generation_tps": getattr(response, "generation_tps", None),
            "preflight": preflight,
            "timings": {
                "model_load_seconds": round(load_seconds, 3),
                "template_seconds": round(template_seconds, 3),
                "generation_seconds": round(generation_seconds, 3),
            },
            **(decoding_telemetry or {}),
        }

    def close(self) -> None:
        if self._model is None:
            return
        import mlx.core as mx

        self._model = None
        self._processor = None
        gc.collect()
        mx.clear_cache()
