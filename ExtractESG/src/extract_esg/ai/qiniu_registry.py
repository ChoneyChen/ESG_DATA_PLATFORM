from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from extract_esg.ai.qiniu_adapter import QiniuModelAdapter
from extract_esg.config import Settings
from extract_esg.contracts.cloud import CloudModelInfo


class QiniuModelRegistry:
    """Local cache and selector for Qiniu model capabilities."""

    def __init__(self, cache_path: Path | None = None, adapter: QiniuModelAdapter | None = None) -> None:
        self.settings = Settings.from_env()
        self.cache_path = cache_path or self.settings.model_cache
        self.adapter = adapter or QiniuModelAdapter(self.settings)
        self._models: list[CloudModelInfo] = []

    @property
    def models(self) -> list[CloudModelInfo]:
        return list(self._models)

    def load_cache(self) -> list[CloudModelInfo]:
        if not self.cache_path.exists():
            self._models = []
            return []
        raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        items = raw.get("models", raw if isinstance(raw, list) else [])
        self._models = [self._parse_model(item) for item in items]
        return self.models

    def refresh(self) -> list[CloudModelInfo]:
        raw = self.adapter.list_models()
        items = raw.get("data", raw.get("models", []))
        self._models = [self._parse_model(item) for item in items]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps({"models": [m.model_dump(mode="json") for m in self._models]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.models

    def seed(self, models: Iterable[CloudModelInfo]) -> None:
        self._models = list(models)

    def select(
        self,
        *,
        requires_image: bool = False,
        requires_schema: bool = False,
        requires_reasoning: bool = False,
        min_context: int = 0,
    ) -> CloudModelInfo | None:
        candidates = []
        for model in self._models:
            if model.retirement_at:
                continue
            if requires_image and "image" not in model.input_modalities:
                continue
            if requires_schema and not model.supports_schema_output:
                continue
            if requires_reasoning and not model.supports_reasoning:
                continue
            if (model.context_length or 0) < min_context:
                continue
            candidates.append(model)
        return max(candidates, key=lambda m: (m.context_length or 0, m.supports_function_calling), default=None)

    @staticmethod
    def _parse_model(item: dict[str, Any]) -> CloudModelInfo:
        arch = item.get("architecture") or {}
        constraints = item.get("model_constraints") or {}
        return CloudModelInfo(
            id=item["id"],
            name=item.get("name"),
            input_modalities=list(arch.get("input_modalities") or []),
            output_modalities=list(arch.get("output_modalities") or []),
            features=list(item.get("features") or []),
            context_length=constraints.get("context_length"),
            max_tokens=constraints.get("max_tokens"),
            supports_schema_output=bool((arch.get("schema_output") or {}).get("supported")),
            supports_function_calling=bool((arch.get("function_calling") or {}).get("supported")),
            supports_reasoning=bool((arch.get("reasoning") or {}).get("supported")),
            support_api_protocols=list(item.get("support_api_protocols") or []),
            retirement_at=item.get("retirement_at") or None,
            suggested_model=item.get("suggested_model") or None,
            raw=item,
        )

