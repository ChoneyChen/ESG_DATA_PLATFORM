from __future__ import annotations

from dataclasses import asdict

from esg_v2.config import Settings
from esg_v2.models.local_nuextract_adapter import LocalNuExtractAdapter
from esg_v2.models.model_registry import ModelHealthRegistry, ModelProfile


class LocalNuExtractRegistry:
    MODEL_ID = "local/numind-nuextract3-mlx-4bits"
    PROFILE = ModelProfile(
        model_id=MODEL_ID,
        family="nuextract3",
        roles=("reviewer", "verifier"),
        vision=True,
        structured_output=True,
        provider="local_nuextract",
        reviewer_rank=100,
        verifier_rank=100,
        thinking_control="disabled",
        min_output_tokens=2400,
        max_output_tokens=12000,
        supports_json_mode=True,
        transient_retries=1,
    )
    verification_policy = "same_model_secondary_verification"

    def __init__(self, settings: Settings, adapter: LocalNuExtractAdapter) -> None:
        self.settings = settings
        self.adapter = adapter
        self._probe: dict[str, object] | None = None
        ModelHealthRegistry.configure(
            settings.document_ir_output_root / ".state" / "model-health.json"
        )

    def refresh(self) -> dict[str, object]:
        self._probe = self.adapter.probe()
        available = bool(self._probe.get("available"))
        return {
            "provider": "local_nuextract",
            "available_model_count": 1 if available else 0,
            "approved_vision_models": [self.MODEL_ID] if available else [],
            "capability_profiles": {self.MODEL_ID: asdict(self.PROFILE)} if available else {},
            "verification_policy": self.verification_policy,
            "independent_model_family": False,
            "probe": self._probe,
            "health": ModelHealthRegistry.snapshot(),
        }

    def candidates(
        self,
        role: str,
        *,
        exclude_family: str | None = None,
        limit: int = 3,
    ) -> list[ModelProfile]:
        if self._probe is None:
            self.refresh()
        if not self._probe or not self._probe.get("available"):
            return []
        if role not in self.PROFILE.roles or not ModelHealthRegistry.is_available(self.MODEL_ID):
            return []
        if exclude_family == self.PROFILE.family:
            return []
        return [self.PROFILE][:limit]

    def catalog_snapshot(self) -> dict[str, object]:
        return self.refresh() if self._probe is None else {
            "provider": "local_nuextract",
            "available_model_count": 1 if self._probe.get("available") else 0,
            "approved_vision_models": [self.MODEL_ID] if self._probe.get("available") else [],
            "capability_profiles": {
                self.MODEL_ID: asdict(self.PROFILE)
            } if self._probe.get("available") else {},
            "verification_policy": self.verification_policy,
            "independent_model_family": False,
            "probe": self._probe,
            "health": ModelHealthRegistry.snapshot(),
        }
