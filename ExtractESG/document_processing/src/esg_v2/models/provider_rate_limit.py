from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from esg_v2.config import Settings


class ProviderRateLimitOpen(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        scope: str,
        blocked_until: float,
        retry_after_seconds: float,
    ) -> None:
        super().__init__(message)
        self.scope = scope
        self.blocked_until = blocked_until
        self.retry_after_seconds = retry_after_seconds


class ProviderRateLimitCoordinator:
    """Coordinates provider-wide quota stops and bounded per-model RPM pacing."""

    _file_lock = threading.Lock()

    def __init__(
        self,
        settings: Settings,
        credential_scope_id: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.credential_scope_id = credential_scope_id
        self.state_path = settings.document_ir_output_root / ".state" / "provider-rate-limits.json"
        self._sleep = sleep
        self._now = now
        self._next_request_at = 0.0

    def account_block(self) -> dict[str, object] | None:
        now = self._now()
        with self._file_lock:
            payload = self._read_locked()
            scope = dict((payload.get("credential_scopes") or {}).get(self.credential_scope_id) or {})
        blocked_until = float(scope.get("account_blocked_until") or 0.0)
        if blocked_until <= now:
            return None
        return {
            "provider": "qiniu",
            "credential_scope_id": self.credential_scope_id,
            "limit_scope": "tpd",
            "blocked_until": blocked_until,
            "retry_after_seconds": max(0, round(blocked_until - now)),
            "last_error": scope.get("last_error"),
            "request_id": scope.get("request_id"),
            "updated_at": scope.get("updated_at"),
        }

    def assert_account_available(self) -> None:
        blocked = self.account_block()
        if blocked is None:
            return
        retry_after = float(blocked["retry_after_seconds"])
        raise ProviderRateLimitOpen(
            f"qiniu_account_rate_limited: cached TPD block; retry after {round(retry_after)} seconds",
            scope="tpd",
            blocked_until=float(blocked["blocked_until"]),
            retry_after_seconds=retry_after,
        )

    def before_request(self, model_id: str) -> None:
        self.assert_account_available()
        now = self._now()
        with self._file_lock:
            payload = self._read_locked()
            scope = dict((payload.get("credential_scopes") or {}).get(self.credential_scope_id) or {})
            rpm_until = float((scope.get("rpm_blocked_until_by_model") or {}).get(model_id) or 0.0)
        wait_until = max(self._next_request_at, rpm_until)
        if wait_until > now:
            self._sleep(wait_until - now)
        self.assert_account_available()
        self._next_request_at = self._now() + max(0.0, self.settings.qiniu_min_request_interval_seconds)

    def record_tpd(
        self,
        error: str,
        *,
        retry_after_seconds: float | None = None,
        request_id: str | None = None,
    ) -> dict[str, object]:
        now = self._now()
        blocked_until = (
            now + max(0.0, retry_after_seconds)
            if retry_after_seconds is not None
            else self._next_daily_reset(now)
        )
        with self._file_lock:
            payload = self._read_locked()
            scopes = payload.setdefault("credential_scopes", {})
            scope = scopes.setdefault(self.credential_scope_id, {})
            scope.update(
                {
                    "account_blocked_until": blocked_until,
                    "last_limit_scope": "tpd",
                    "last_error": error[:1000],
                    "request_id": request_id,
                    "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                }
            )
            self._write_locked(payload)
        return self.account_block() or {}

    def record_rpm(
        self,
        model_id: str,
        error: str,
        *,
        retry_index: int,
        retry_after_seconds: float | None = None,
        request_id: str | None = None,
    ) -> float:
        computed = self.settings.qiniu_rpm_backoff_base_seconds * (2 ** max(0, retry_index))
        requested = retry_after_seconds if retry_after_seconds is not None else computed
        delay = max(0.0, min(requested, self.settings.qiniu_rpm_max_wait_seconds))
        blocked_until = self._now() + delay
        with self._file_lock:
            payload = self._read_locked()
            scopes = payload.setdefault("credential_scopes", {})
            scope = scopes.setdefault(self.credential_scope_id, {})
            model_blocks = scope.setdefault("rpm_blocked_until_by_model", {})
            model_blocks[model_id] = blocked_until
            scope.update(
                {
                    "last_limit_scope": "rpm",
                    "last_error": error[:1000],
                    "request_id": request_id,
                    "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                }
            )
            self._write_locked(payload)
        return delay

    def snapshot(self) -> dict[str, object]:
        blocked = self.account_block()
        with self._file_lock:
            payload = self._read_locked()
            scope = dict((payload.get("credential_scopes") or {}).get(self.credential_scope_id) or {})
        return {
            "provider": "qiniu",
            "credential_scope_id": self.credential_scope_id,
            "account_block": blocked,
            "rpm_blocked_until_by_model": scope.get("rpm_blocked_until_by_model") or {},
            "last_limit_scope": scope.get("last_limit_scope"),
            "last_error": scope.get("last_error"),
            "updated_at": scope.get("updated_at"),
        }

    def reset(self) -> None:
        with self._file_lock:
            payload = self._read_locked()
            scopes = payload.setdefault("credential_scopes", {})
            scopes.pop(self.credential_scope_id, None)
            self._write_locked(payload)

    def _next_daily_reset(self, timestamp: float) -> float:
        timezone = ZoneInfo("Asia/Shanghai")
        current = datetime.fromtimestamp(timestamp, timezone)
        tomorrow = (current + timedelta(days=1)).date()
        reset = datetime.combine(tomorrow, datetime.min.time(), timezone)
        reset += timedelta(seconds=max(0.0, self.settings.qiniu_tpd_reset_grace_seconds))
        return reset.timestamp()

    def _read_locked(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {"schema_version": "provider-rate-limit-v1", "credential_scopes": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, ValueError, TypeError):
            pass
        return {"schema_version": "provider-rate-limit-v1", "credential_scopes": {}}

    def _write_locked(self, payload: dict[str, object]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload["schema_version"] = "provider-rate-limit-v1"
        temporary = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)
