from __future__ import annotations

from esg_v2.models.local_nuextract_registry import LocalNuExtractRegistry
from esg_v2.models.model_registry import ModelProfile


class LocalQwenRegistry(LocalNuExtractRegistry):
    MODEL_ID = "local/Qwen3.5-9B-4bit"
    PROFILE = ModelProfile(
        model_id=MODEL_ID, family="qwen3.5-local", roles=("reviewer", "verifier"),
        vision=True, structured_output=True, provider="local_qwen",
        reviewer_rank=100, verifier_rank=100, thinking_control="disabled",
        min_output_tokens=2048, max_output_tokens=8192, supports_json_mode=True,
        transient_retries=1,
    )
