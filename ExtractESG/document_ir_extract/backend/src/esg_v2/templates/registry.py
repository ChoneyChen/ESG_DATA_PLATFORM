from __future__ import annotations

from pathlib import Path

from esg_v2.templates.contracts import TemplateAdapter
from esg_v2.templates.esrs_trial_xlsx import EsrsTrialXlsxAdapter


class TemplateAdapterRegistry:
    def __init__(self, adapters: list[TemplateAdapter] | None = None):
        self.adapters = adapters or [EsrsTrialXlsxAdapter()]

    def resolve(self, path: Path) -> TemplateAdapter:
        for adapter in self.adapters:
            if adapter.supports(path):
                return adapter
        raise ValueError("No registered task-template adapter supports this workbook")

    def catalog(self) -> list[dict[str, str]]:
        return [
            {"adapter_id": adapter.adapter_id, "format": "xlsx"}
            for adapter in self.adapters
        ]
