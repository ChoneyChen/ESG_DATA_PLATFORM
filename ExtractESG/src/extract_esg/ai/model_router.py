from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from extract_esg.ai.qiniu_registry import QiniuModelRegistry
from extract_esg.config import Settings
from extract_esg.contracts.cloud import CloudModelInfo


TaskKind = Literal[
    "page_classification",
    "vision_ocr",
    "table_vision_extract",
    "structured_extract",
    "qualitative_summarization",
    "long_context_understanding",
    "mapping_review",
    "verification",
    "visual_verification",
]


@dataclass(frozen=True)
class ModelChoice:
    task_kind: TaskKind
    model: CloudModelInfo | None
    explicit_model_id: str | None
    reason: str


class CloudModelRouter:
    """Capability-based Qiniu model selection."""

    def __init__(self, registry: QiniuModelRegistry, settings: Settings | None = None) -> None:
        self.registry = registry
        self.settings = settings or Settings.from_env()

    def choose(self, task_kind: TaskKind) -> ModelChoice:
        explicit = self._explicit_model(task_kind)
        if explicit:
            model = self._find(explicit)
            return ModelChoice(
                task_kind=task_kind,
                model=model,
                explicit_model_id=explicit,
                reason="explicit_env_model" if model else "explicit_env_model_not_in_registry",
            )

        preferred = self._preferred_model(task_kind)
        if preferred:
            return ModelChoice(task_kind, preferred, None, "preferred_available_model")

        if task_kind in {"vision_ocr", "table_vision_extract", "visual_verification"}:
            model = self.registry.select(requires_image=True, min_context=32000)
            return ModelChoice(task_kind, model, None, "requires_image")
        if task_kind in {"structured_extract", "verification"}:
            model = self.registry.select(requires_schema=True, min_context=64000)
            return ModelChoice(task_kind, model, None, "requires_schema")
        if task_kind in {"qualitative_summarization", "long_context_understanding", "mapping_review"}:
            model = self.registry.select(requires_reasoning=task_kind == "mapping_review", min_context=128000)
            return ModelChoice(task_kind, model, None, "requires_long_context_or_reasoning")
        model = self.registry.select(min_context=32000)
        return ModelChoice(task_kind, model, None, "default_low_requirement")

    def _explicit_model(self, task_kind: TaskKind) -> str | None:
        if task_kind in {"vision_ocr", "table_vision_extract", "visual_verification"}:
            return self.settings.default_vision_model
        if task_kind == "mapping_review":
            return self.settings.default_reasoning_model or self.settings.default_text_model
        if task_kind == "page_classification":
            return self.settings.default_fast_model or self.settings.default_text_model
        return self.settings.default_text_model

    def _find(self, model_id: str) -> CloudModelInfo | None:
        for model in self.registry.models:
            if model.id == model_id:
                return model
        return None

    def _preferred_model(self, task_kind: TaskKind) -> CloudModelInfo | None:
        preferred_ids = {
            "page_classification": (
                "deepseek/deepseek-v4-flash",
                "qwen-turbo",
                "qwen3-next-80b-a3b-instruct",
            ),
            "vision_ocr": (
                "qwen/qwen3.5-plus",
                "qwen-vl-max-2025-01-25",
                "qwen2.5-vl-72b-instruct",
                "moonshotai/kimi-k2.5",
            ),
            "table_vision_extract": (
                "qwen/qwen3.5-plus",
                "qwen-vl-max-2025-01-25",
                "qwen2.5-vl-72b-instruct",
                "moonshotai/kimi-k2.5",
            ),
            "structured_extract": (
                "doubao-seed-2.0-lite",
                "deepseek/deepseek-v4-flash",
                "qwen3-next-80b-a3b-instruct",
                "qwen/qwen3.5-plus",
                "qwen-turbo",
            ),
            "qualitative_summarization": (
                "deepseek/deepseek-v4-flash",
                "doubao-seed-1.6-flash",
                "qwen3-next-80b-a3b-instruct",
            ),
            "long_context_understanding": (
                "deepseek/deepseek-v4-flash",
                "deepseek/deepseek-v4-pro",
                "z-ai/glm-5.2",
            ),
            "mapping_review": (
                "deepseek/deepseek-v4-pro",
                "z-ai/glm-5",
                "minimax/minimax-m2.7",
                "qwen3-max",
            ),
            "verification": (
                "z-ai/glm-5",
                "deepseek/deepseek-v4-pro",
                "minimax/minimax-m2.7",
                "qwen3-next-80b-a3b-thinking",
            ),
            "visual_verification": (
                "moonshotai/kimi-k2.5",
                "qwen/qwen3.5-plus",
                "qwen-vl-max-2025-01-25",
            ),
        }
        for model_id in preferred_ids.get(task_kind, ()):
            model = self._find(model_id)
            if model:
                return model
        return None
