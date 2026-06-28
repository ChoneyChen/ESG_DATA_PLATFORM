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
        raw_payload = item.get("raw") if isinstance(item.get("raw"), dict) and item["id"] == item["raw"].get("id") else item
        arch = raw_payload.get("architecture") or item.get("architecture") or {}
        constraints = raw_payload.get("model_constraints") or item.get("model_constraints") or {}
        model_id = item["id"]
        inferred = QiniuModelRegistry._infer_capabilities(model_id, raw_payload)
        input_modalities = list(arch.get("input_modalities") or item.get("input_modalities") or inferred["input_modalities"])
        output_modalities = list(arch.get("output_modalities") or item.get("output_modalities") or ["text"])
        features = list(item.get("features") or inferred["features"])
        return CloudModelInfo(
            id=model_id,
            name=item.get("name"),
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            features=features,
            context_length=constraints.get("context_length") or item.get("context_length") or inferred["context_length"],
            max_tokens=constraints.get("max_tokens") or item.get("max_tokens"),
            supports_schema_output=bool((arch.get("schema_output") or {}).get("supported") or item.get("supports_schema_output") or inferred["supports_schema_output"]),
            supports_function_calling=bool((arch.get("function_calling") or {}).get("supported") or item.get("supports_function_calling")),
            supports_reasoning=bool((arch.get("reasoning") or {}).get("supported") or item.get("supports_reasoning") or inferred["supports_reasoning"]),
            support_api_protocols=list(raw_payload.get("support_api_protocols") or item.get("support_api_protocols") or []),
            retirement_at=raw_payload.get("retirement_at") or item.get("retirement_at") or None,
            suggested_model=raw_payload.get("suggested_model") or item.get("suggested_model") or None,
            raw=raw_payload,
        )

    @staticmethod
    def _infer_capabilities(model_id: str, item: dict[str, Any]) -> dict[str, Any]:
        """Infer coarse capabilities when /v1/models only returns model IDs.

        Qiniu's OpenAI-compatible model list may expose only id/object metadata
        for some accounts. The inference here is intentionally conservative and
        only drives local routing; paid calls still store the exact model id.
        """
        text = " ".join(str(value).lower() for value in [model_id, item.get("name"), *item.get("features", [])])
        is_vision = any(token in text for token in ("vl", "vision", "visual", "multimodal"))
        is_reasoning = any(token in text for token in ("thinking", "reason", "r1", "deepseek-v4-pro", "glm-5", "m2.7"))
        context_length = 32768
        if any(token in text for token in ("deepseek-v4", "glm-5", "minimax-m3", "longcat", "kimi-k2.6")):
            context_length = 1_000_000
        elif any(token in text for token in ("qwen3", "kimi", "doubao-seed", "minimax", "qwen-max")):
            context_length = 262_144

        return {
            "input_modalities": ["text", "image"] if is_vision else ["text"],
            "features": ["inferred_capabilities_from_model_id"],
            "context_length": context_length,
            "supports_schema_output": True,
            "supports_reasoning": is_reasoning,
        }
