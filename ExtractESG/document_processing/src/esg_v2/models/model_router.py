from __future__ import annotations

from esg_v2.config import Settings
from esg_v2.models.model_registry import QiniuModelRegistry
from esg_v2.models.qiniu_adapter import QiniuModelAdapter


class QiniuVisionModelRouter:
    """Selects an ordered, auditable failover chain for visual review."""

    def __init__(self, settings: Settings, adapter: QiniuModelAdapter):
        self.settings = settings
        self.adapter = adapter
        self.registry = QiniuModelRegistry(settings, adapter)

    def candidates(
        self,
        *,
        role: str = "reviewer",
        exclude_family: str | None = None,
        limit: int = 3,
    ) -> list[str]:
        profiles = self.registry.candidates(role, exclude_family=exclude_family, limit=limit)
        if not profiles:
            raise RuntimeError(
                f"No approved healthy Qiniu vision model found for role={role}. "
                "Check /models, model retirement status, and circuit health."
            )
        return [item.model_id for item in profiles]
