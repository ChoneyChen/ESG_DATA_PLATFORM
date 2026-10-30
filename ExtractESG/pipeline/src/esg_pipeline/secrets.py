from __future__ import annotations

import threading
from contextlib import contextmanager


class RuntimeSecretVault:
    """Process-local secret handoff; queue persistence never contains credentials."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()

    @contextmanager
    def handoff(self):
        """Keep newly visible queue records and their credential handoff ordered."""
        with self._lock:
            yield

    def put(self, task_id: str, values: dict[str, str | None]) -> None:
        clean = {key: value for key, value in values.items() if value}
        if not clean:
            return
        with self._lock:
            self._values.setdefault(task_id, {}).update(clean)

    def pop(self, task_id: str) -> dict[str, str]:
        with self._lock:
            return self._values.pop(task_id, {})

    def discard(self, task_id: str) -> None:
        with self._lock:
            self._values.pop(task_id, None)
