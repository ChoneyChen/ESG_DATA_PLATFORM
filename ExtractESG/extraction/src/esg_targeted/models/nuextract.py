"""NuExtract provider-specific protocol over shared execution and binding."""
from __future__ import annotations

import json
import time

from esg_targeted.models.direct_fill import DirectFillModelAdapter
from esg_targeted.models.mlx_runtime import MlxVlmRuntime
from esg_targeted.models.structured_template import nuextract_template


class NuExtractMlxModel(DirectFillModelAdapter, MlxVlmRuntime):
    provider_name = "local_nuextract"

    def _render_prompt(self, *, context, template, instructions, images):
        from mlx_vlm.prompt_utils import apply_chat_template

        return apply_chat_template(
            self._processor, self._model.config, context, num_images=len(images),
            template=template, instructions=instructions,
            enable_thinking=self.capabilities.enable_thinking,
        )

    def generate_structured(self, *, context, instructions, template, image_path=None,
                            max_tokens=2048, cancel_check=None, progress_callback=None):
        """One bounded Agent question using NuExtract's native typed template."""
        if not isinstance(context, str):
            context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        started = time.perf_counter()
        raw, telemetry = self._generate(
            context=context, template=json.dumps(nuextract_template(template), ensure_ascii=False),
            instructions=instructions, images=[str(image_path)] if image_path else [],
            output_token_budget=min(self.max_tokens, max(128, max_tokens)),
            cancel_check=cancel_check, preflight={"route": "bounded_agent_task"},
            progress_callback=progress_callback,
        )
        return raw, {**telemetry, "provider": self.provider_name, "model_id": self.model_id,
                     "elapsed_seconds": round(time.perf_counter() - started, 3)}
