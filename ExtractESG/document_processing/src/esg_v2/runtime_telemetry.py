from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable


PublishFn = Callable[[dict[str, Any]], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunTelemetryTracker:
    """Persists stage timing and model-attempt counters while a run is active."""

    def __init__(self, publish: PublishFn, *, started_at: str | None = None) -> None:
        self.publish = publish
        self._lock = Lock()
        started = started_at or utc_now()
        self._data: dict[str, Any] = {
            "schema_version": "document-ir-runtime-telemetry-v1",
            "started_at": started,
            "updated_at": started,
            "finished_at": None,
            "status": "running",
            "stages": [],
            "current_stage": None,
            "model_calls": {
                "logical_started": 0,
                "actual_attempts": 0,
                "completed_attempts": 0,
                "succeeded": 0,
                "failed": 0,
                "invalid_response": 0,
                "retries": 0,
                "by_role": {},
                "by_model": {},
                "failure_categories": {},
                "total_latency_ms": 0.0,
                "usage": {},
                "current_call": None,
            },
        }
        self._publish()

    def event(self, payload: dict[str, Any]) -> None:
        event = str(payload.get("event") or "")
        with self._lock:
            if event == "stage_started":
                self._start_stage(payload)
            elif event == "logical_model_call_started":
                self._increment_logical(payload)
            elif event == "model_attempt_started":
                self._start_model_attempt(payload)
            elif event == "model_attempt_completed":
                self._complete_model_attempt(payload)
            self._data["updated_at"] = utc_now()
            self._publish_locked()

    def finish(self, status: str) -> dict[str, Any]:
        with self._lock:
            now = utc_now()
            current = self._data.get("current_stage")
            if isinstance(current, dict) and current.get("status") == "running":
                current["status"] = "done" if status == "done" else "failed"
                current["finished_at"] = now
            self._data["status"] = status
            self._data["finished_at"] = now
            self._data["updated_at"] = now
            model_calls = self._data["model_calls"]
            model_calls["current_call"] = None
            self._publish_locked()
            return deepcopy(self._data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def _start_stage(self, payload: dict[str, Any]) -> None:
        now = utc_now()
        current = self._data.get("current_stage")
        if isinstance(current, dict) and current.get("status") == "running":
            current["status"] = "done"
            current["finished_at"] = now
        stage = {
            "index": int(payload.get("index") or 0),
            "total": int(payload.get("total") or 0),
            "name": str(payload.get("name") or ""),
            "status": "running",
            "started_at": now,
            "finished_at": None,
        }
        self._data["stages"].append(stage)
        self._data["current_stage"] = stage

    def _increment_logical(self, payload: dict[str, Any]) -> None:
        calls = self._data["model_calls"]
        calls["logical_started"] += 1
        self._increment(calls["by_role"], str(payload.get("role") or "unknown"))

    def _start_model_attempt(self, payload: dict[str, Any]) -> None:
        calls = self._data["model_calls"]
        calls["actual_attempts"] += 1
        if int(payload.get("retry_index") or 0) > 0:
            calls["retries"] += 1
        model_id = str(payload.get("model_id") or "unknown")
        self._increment(calls["by_model"], model_id)
        calls["current_call"] = {
            key: payload.get(key)
            for key in ("task_id", "role", "round_index", "retry_index", "model_id", "provider")
        }
        calls["current_call"]["started_at"] = utc_now()

    def _complete_model_attempt(self, payload: dict[str, Any]) -> None:
        calls = self._data["model_calls"]
        calls["completed_attempts"] += 1
        status = str(payload.get("status") or "failed")
        if status in {"succeeded", "failed", "invalid_response"}:
            calls[status] += 1
        category = str(payload.get("failure_category") or "none")
        self._increment(calls["failure_categories"], category)
        latency = payload.get("latency_ms")
        if isinstance(latency, (int, float)):
            calls["total_latency_ms"] += float(latency)
        usage = payload.get("usage") or {}
        if isinstance(usage, dict):
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    calls["usage"][key] = calls["usage"].get(key, 0) + value
        calls["current_call"] = None

    @staticmethod
    def _increment(target: dict[str, int], key: str) -> None:
        target[key] = target.get(key, 0) + 1

    def _publish(self) -> None:
        with self._lock:
            self._publish_locked()

    def _publish_locked(self) -> None:
        self.publish(deepcopy(self._data))
