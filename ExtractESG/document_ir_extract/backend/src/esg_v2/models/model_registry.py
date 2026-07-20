from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from esg_v2.config import Settings
from esg_v2.models.qiniu_adapter import QiniuModelAdapter


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    family: str
    roles: tuple[str, ...]
    vision: bool
    structured_output: bool
    reviewer_rank: int = 0
    verifier_rank: int = 0


@dataclass
class ModelHealth:
    model_id: str
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    last_status: str = "unknown"
    last_error: str | None = None
    last_latency_ms: float | None = None
    updated_at: str | None = None


class ModelHealthRegistry:
    _lock = threading.Lock()
    _states: dict[str, ModelHealth] = {}
    failure_threshold = 2
    cooldown_seconds = 15 * 60

    @classmethod
    def state(cls, model_id: str) -> ModelHealth:
        with cls._lock:
            return cls._states.setdefault(model_id, ModelHealth(model_id=model_id))

    @classmethod
    def is_available(cls, model_id: str) -> bool:
        return cls.state(model_id).circuit_open_until <= time.time()

    @classmethod
    def record_success(cls, model_id: str, latency_ms: float | None = None) -> None:
        with cls._lock:
            state = cls._states.setdefault(model_id, ModelHealth(model_id=model_id))
            state.successes += 1
            state.consecutive_failures = 0
            state.circuit_open_until = 0.0
            state.last_status = "healthy"
            state.last_error = None
            state.last_latency_ms = latency_ms
            state.updated_at = datetime.now(timezone.utc).isoformat()

    @classmethod
    def record_failure(cls, model_id: str, error: str, *, retryable: bool = True) -> None:
        with cls._lock:
            state = cls._states.setdefault(model_id, ModelHealth(model_id=model_id))
            state.failures += 1
            state.consecutive_failures += 1
            state.last_status = "degraded" if retryable else "unavailable"
            state.last_error = error[:1000]
            state.updated_at = datetime.now(timezone.utc).isoformat()
            if retryable and state.consecutive_failures >= cls.failure_threshold:
                state.circuit_open_until = time.time() + cls.cooldown_seconds
                state.last_status = "circuit_open"

    @classmethod
    def snapshot(cls) -> list[dict[str, object]]:
        with cls._lock:
            now = time.time()
            result = []
            for state in cls._states.values():
                payload = asdict(state)
                payload["circuit_open"] = state.circuit_open_until > now
                payload["circuit_open_seconds_remaining"] = max(0, round(state.circuit_open_until - now))
                result.append(payload)
            return sorted(result, key=lambda item: str(item["model_id"]))

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._states = {}


class QiniuModelRegistry:
    RETIRED_MODELS = {
        "qwen2.5-vl-72b-instruct",
        "doubao-1.5-vision-pro",
        "qwen-vl-max-2025-01-25",
        "qwen3-vl-30b-a3b-thinking",
    }

    PROFILES = {
        "qwen/qwen3.5-plus": ModelProfile(
            model_id="qwen/qwen3.5-plus",
            family="qwen",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=100,
            verifier_rank=90,
        ),
        "bytedance/doubao-seed-2-1-pro": ModelProfile(
            model_id="bytedance/doubao-seed-2-1-pro",
            family="doubao",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=92,
            verifier_rank=100,
        ),
        "moonshotai/kimi-k2.6": ModelProfile(
            model_id="moonshotai/kimi-k2.6",
            family="kimi",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=72,
            verifier_rank=74,
        ),
    }

    def __init__(self, settings: Settings, adapter: QiniuModelAdapter):
        self.settings = settings
        self.adapter = adapter
        self._available_ids: set[str] | None = None

    def refresh(self) -> dict[str, object]:
        available = {model.id for model in self.adapter.list_models()}
        self._available_ids = available
        return {
            "available_model_count": len(available),
            "approved_vision_models": sorted(available & self.PROFILES.keys()),
            "retired_models_still_listed": sorted(available & self.RETIRED_MODELS),
            "health": ModelHealthRegistry.snapshot(),
        }

    def candidates(
        self,
        role: str,
        *,
        exclude_family: str | None = None,
        limit: int = 3,
    ) -> list[ModelProfile]:
        if self._available_ids is None:
            self.refresh()
        available = self._available_ids or set()
        pinned = [item.strip() for item in (self.settings.qiniu_vlm_model or "").split(",") if item.strip()]
        profiles = []
        if pinned:
            for model_id in pinned:
                profile = self.PROFILES.get(model_id)
                if profile and model_id in available and model_id not in self.RETIRED_MODELS:
                    profiles.append(profile)
        else:
            profiles = [
                profile
                for model_id, profile in self.PROFILES.items()
                if model_id in available and model_id not in self.RETIRED_MODELS and role in profile.roles
            ]
            rank_name = "reviewer_rank" if role == "reviewer" else "verifier_rank"
            profiles.sort(key=lambda item: getattr(item, rank_name), reverse=True)
        return [
            profile
            for profile in profiles
            if (not exclude_family or profile.family != exclude_family)
            and ModelHealthRegistry.is_available(profile.model_id)
        ][:limit]

    def catalog_snapshot(self) -> dict[str, object]:
        if self._available_ids is None:
            return self.refresh()
        return {
            "available_model_count": len(self._available_ids),
            "approved_vision_models": sorted(self._available_ids & self.PROFILES.keys()),
            "retired_models_still_listed": sorted(self._available_ids & self.RETIRED_MODELS),
            "health": ModelHealthRegistry.snapshot(),
        }

    @classmethod
    def family(cls, model_id: str) -> str:
        profile = cls.PROFILES.get(model_id)
        return profile.family if profile else model_id.split("/", 1)[0]
