from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from esg_v2.config import Settings
from esg_v2.models.qiniu_adapter import QiniuModelAdapter


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    family: str
    roles: tuple[str, ...]
    vision: bool
    structured_output: bool
    provider: str = "qiniu"
    reviewer_rank: int = 0
    verifier_rank: int = 0
    thinking_control: str = "reasoning_effort"
    min_output_tokens: int = 2400
    max_output_tokens: int = 12000
    supports_json_mode: bool = True
    transient_retries: int = 1


@dataclass
class ModelHealth:
    model_id: str
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    transport_failures: int = 0
    protocol_failures: int = 0
    quota_failures: int = 0
    rpm_failures: int = 0
    consecutive_transport_failures: int = 0
    consecutive_protocol_failures: int = 0
    consecutive_rpm_failures: int = 0
    circuit_open_until: float = 0.0
    transport_circuit_open_until: float = 0.0
    protocol_circuit_open_until: float = 0.0
    rpm_circuit_open_until: float = 0.0
    last_status: str = "unknown"
    last_failure_category: str | None = None
    last_error: str | None = None
    last_latency_ms: float | None = None
    updated_at: str | None = None


class ModelHealthRegistry:
    _lock = threading.Lock()
    _states: dict[str, ModelHealth] = {}
    _state_path: Path | None = None
    transport_failure_threshold = 2
    protocol_failure_threshold = 3
    transport_cooldown_seconds = 15 * 60
    protocol_cooldown_seconds = 5 * 60
    rpm_cooldown_seconds = 60

    @classmethod
    def configure(cls, path: Path | None) -> None:
        normalized = path.expanduser().resolve() if path else None
        with cls._lock:
            if normalized == cls._state_path:
                return
            cls._state_path = normalized
            cls._states = {}
            if normalized is None or not normalized.exists():
                return
            try:
                payload = json.loads(normalized.read_text(encoding="utf-8"))
                rows = payload.get("models", []) if isinstance(payload, dict) else []
                allowed = set(ModelHealth.__dataclass_fields__)
                cls._states = {
                    str(row["model_id"]): ModelHealth(
                        **{key: value for key, value in row.items() if key in allowed}
                    )
                    for row in rows
                    if isinstance(row, dict) and row.get("model_id")
                }
            except (OSError, ValueError, TypeError):
                cls._states = {}

    @classmethod
    def state(cls, model_id: str) -> ModelHealth:
        with cls._lock:
            return cls._states.setdefault(model_id, ModelHealth(model_id=model_id))

    @classmethod
    def is_available(cls, model_id: str) -> bool:
        state = cls.state(model_id)
        return max(
            state.transport_circuit_open_until,
            state.protocol_circuit_open_until,
            state.rpm_circuit_open_until,
        ) <= time.time()

    @classmethod
    def record_success(cls, model_id: str, latency_ms: float | None = None) -> None:
        with cls._lock:
            state = cls._states.setdefault(model_id, ModelHealth(model_id=model_id))
            state.successes += 1
            state.consecutive_failures = 0
            state.consecutive_transport_failures = 0
            state.consecutive_protocol_failures = 0
            state.consecutive_rpm_failures = 0
            state.circuit_open_until = 0.0
            state.transport_circuit_open_until = 0.0
            state.protocol_circuit_open_until = 0.0
            state.rpm_circuit_open_until = 0.0
            state.last_status = "healthy"
            state.last_failure_category = None
            state.last_error = None
            state.last_latency_ms = latency_ms
            state.updated_at = datetime.now(timezone.utc).isoformat()
            cls._persist_locked()

    @classmethod
    def record_failure(
        cls,
        model_id: str,
        error: str,
        *,
        category: str = "transport",
        retryable: bool = True,
    ) -> None:
        with cls._lock:
            state = cls._states.setdefault(model_id, ModelHealth(model_id=model_id))
            state.failures += 1
            state.consecutive_failures += 1
            state.last_failure_category = category
            state.last_status = "degraded" if retryable else "unavailable"
            state.last_error = error[:1000]
            state.updated_at = datetime.now(timezone.utc).isoformat()
            if category == "protocol":
                state.protocol_failures += 1
                state.consecutive_protocol_failures += 1
                state.consecutive_transport_failures = 0
                if retryable and state.consecutive_protocol_failures >= cls.protocol_failure_threshold:
                    state.protocol_circuit_open_until = time.time() + cls.protocol_cooldown_seconds
            elif category == "quota":
                state.quota_failures += 1
                state.consecutive_transport_failures = 0
                state.consecutive_protocol_failures = 0
                state.consecutive_rpm_failures = 0
            elif category == "rpm":
                state.rpm_failures += 1
                state.consecutive_rpm_failures += 1
                state.consecutive_transport_failures = 0
                state.consecutive_protocol_failures = 0
                state.rpm_circuit_open_until = time.time() + cls.rpm_cooldown_seconds
            else:
                state.transport_failures += 1
                state.consecutive_transport_failures += 1
                state.consecutive_protocol_failures = 0
                if retryable and state.consecutive_transport_failures >= cls.transport_failure_threshold:
                    state.transport_circuit_open_until = time.time() + cls.transport_cooldown_seconds
            state.circuit_open_until = max(
                state.transport_circuit_open_until,
                state.protocol_circuit_open_until,
                state.rpm_circuit_open_until,
            )
            if state.circuit_open_until > time.time():
                state.last_status = "circuit_open"
            cls._persist_locked()

    @classmethod
    def snapshot(cls) -> list[dict[str, object]]:
        with cls._lock:
            now = time.time()
            result = []
            for state in cls._states.values():
                payload = asdict(state)
                payload["circuit_open"] = state.circuit_open_until > now
                payload["circuit_open_seconds_remaining"] = max(0, round(state.circuit_open_until - now))
                payload["transport_circuit_open"] = state.transport_circuit_open_until > now
                payload["protocol_circuit_open"] = state.protocol_circuit_open_until > now
                payload["rpm_circuit_open"] = state.rpm_circuit_open_until > now
                result.append(payload)
            return sorted(result, key=lambda item: str(item["model_id"]))

    @classmethod
    def reset(cls, *, clear_persisted: bool = True) -> None:
        with cls._lock:
            cls._states = {}
            if clear_persisted and cls._state_path:
                cls._state_path.unlink(missing_ok=True)

    @classmethod
    def _persist_locked(cls) -> None:
        if cls._state_path is None:
            return
        cls._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "model-health-v1",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "models": [
                asdict(state)
                for state in sorted(cls._states.values(), key=lambda item: item.model_id)
            ],
        }
        temporary = cls._state_path.with_suffix(f"{cls._state_path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(cls._state_path)


class QiniuModelRegistry:
    verification_policy = "different_model_family"
    RETIRED_MODELS = {
        "qwen2.5-vl-72b-instruct",
        "doubao-1.5-vision-pro",
        "qwen-vl-max-2025-01-25",
        "qwen3-vl-30b-a3b-thinking",
        "qwen3-vl-30b-a3b-instruct",
        "doubao-1.6-vision-pro",
        "doubao-1.6-vision-lite",
        "bytedance/doubao-seed-2-1-pro",
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
            thinking_control="disabled",
        ),
        "doubao-seed-2.0-pro": ModelProfile(
            model_id="doubao-seed-2.0-pro",
            family="doubao",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=98,
            verifier_rank=100,
            min_output_tokens=3000,
        ),
        "qwen3.5-397b-a17b": ModelProfile(
            model_id="qwen3.5-397b-a17b",
            family="qwen",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=94,
            verifier_rank=88,
            thinking_control="disabled",
        ),
        "stepfun/step-3.7-flash": ModelProfile(
            model_id="stepfun/step-3.7-flash",
            family="stepfun",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=90,
            verifier_rank=94,
            min_output_tokens=4096,
        ),
        "moonshotai/kimi-k2.6": ModelProfile(
            model_id="moonshotai/kimi-k2.6",
            family="kimi",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=86,
            verifier_rank=86,
            thinking_control="disabled",
            min_output_tokens=9000,
        ),
        "moonshotai/kimi-k2.5": ModelProfile(
            model_id="moonshotai/kimi-k2.5",
            family="kimi",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=82,
            verifier_rank=82,
            thinking_control="disabled",
            min_output_tokens=9000,
        ),
        "doubao-seed-2.0-mini": ModelProfile(
            model_id="doubao-seed-2.0-mini",
            family="doubao",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=78,
            verifier_rank=80,
            min_output_tokens=3000,
        ),
        "minimax/minimax-m3": ModelProfile(
            model_id="minimax/minimax-m3",
            family="minimax",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=76,
            verifier_rank=78,
            min_output_tokens=4096,
        ),
        "moonshotai/kimi-k3": ModelProfile(
            model_id="moonshotai/kimi-k3",
            family="kimi",
            roles=("reviewer", "verifier"),
            vision=True,
            structured_output=True,
            reviewer_rank=74,
            verifier_rank=76,
            thinking_control="disabled",
            min_output_tokens=9000,
        ),
    }

    def __init__(self, settings: Settings, adapter: QiniuModelAdapter):
        self.settings = settings
        self.adapter = adapter
        self._available_ids: set[str] | None = None
        ModelHealthRegistry.configure(
            settings.document_ir_output_root / ".state" / "model-health.json"
        )

    def refresh(self) -> dict[str, object]:
        available = {model.id for model in self.adapter.list_models()}
        self._available_ids = available
        return {
            "available_model_count": len(available),
            "approved_vision_models": sorted(available & self.PROFILES.keys()),
            "capability_profiles": {
                model_id: asdict(self.PROFILES[model_id])
                for model_id in sorted(available & self.PROFILES.keys())
            },
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
            profiles = self._interleave_families(profiles)
        return [
            profile
            for profile in profiles
            if (not exclude_family or profile.family != exclude_family)
            and ModelHealthRegistry.is_available(profile.model_id)
        ][:limit]

    @staticmethod
    def _interleave_families(profiles: list[ModelProfile]) -> list[ModelProfile]:
        """Keep failover diverse without discarding per-role quality ranking."""
        queues: dict[str, list[ModelProfile]] = {}
        family_order: list[str] = []
        for profile in profiles:
            if profile.family not in queues:
                queues[profile.family] = []
                family_order.append(profile.family)
            queues[profile.family].append(profile)
        result: list[ModelProfile] = []
        while any(queues.values()):
            for family in family_order:
                if queues[family]:
                    result.append(queues[family].pop(0))
        return result

    def catalog_snapshot(self) -> dict[str, object]:
        if self._available_ids is None:
            return self.refresh()
        return {
            "available_model_count": len(self._available_ids),
            "approved_vision_models": sorted(self._available_ids & self.PROFILES.keys()),
            "capability_profiles": {
                model_id: asdict(self.PROFILES[model_id])
                for model_id in sorted(self._available_ids & self.PROFILES.keys())
            },
            "retired_models_still_listed": sorted(self._available_ids & self.RETIRED_MODELS),
            "health": ModelHealthRegistry.snapshot(),
        }

    @classmethod
    def family(cls, model_id: str) -> str:
        profile = cls.PROFILES.get(model_id)
        return profile.family if profile else model_id.split("/", 1)[0]
