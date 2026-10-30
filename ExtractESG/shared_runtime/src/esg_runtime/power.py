"""Worker-owned idle sleep prevention, without blocking explicit system sleep."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Callable

_owners: dict[int, dict] = {}
_lock = threading.Lock()


class IdleSleepAssertion:
    """A nested assertion ends at context exit or exact worker process death.

    Only macOS idle sleep is inhibited (-i). Closing the laptop or explicitly
    requesting sleep can still suspend the worker and its watchdog.
    """

    def __init__(self, event: Callable[[dict], None] | None = None):
        self.event = event
        self.pid = os.getpid()
        self.entered = False

    def _emit(self, **payload):
        if self.event:
            self.event(payload)

    def __enter__(self):
        if self.entered:
            return self
        self.entered = True
        if (
            sys.platform != "darwin"
            or os.getenv("ESG_DISABLE_IDLE_SLEEP_ASSERTION") == "1"
        ):
            return self
        with _lock:
            owner = _owners.get(self.pid)
            if owner:
                owner["references"] += 1
                return self
            try:
                process = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-i", "-w", str(self.pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            except OSError as exc:
                self._emit(
                    kind="idle_sleep_assertion_unavailable",
                    owner_pid=self.pid,
                    error=str(exc),
                )
                return self
            _owners[self.pid] = {"process": process, "references": 1}
            self._emit(
                kind="idle_sleep_assertion_acquired",
                owner_pid=self.pid,
                assertion_pid=process.pid,
                prevents="idle_sleep",
            )
        return self

    def close(self):
        if not self.entered:
            return
        self.entered = False
        with _lock:
            owner = _owners.get(self.pid)
            if not owner:
                return
            owner["references"] -= 1
            if owner["references"]:
                return
            _owners.pop(self.pid, None)
        process = owner["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self._emit(
                        kind="idle_sleep_assertion_cleanup_pending",
                        owner_pid=self.pid,
                        assertion_pid=process.pid,
                    )
        self._emit(
            kind="idle_sleep_assertion_released",
            owner_pid=self.pid,
            assertion_pid=process.pid,
        )

    def __exit__(self, *_):
        self.close()
        return False


def idle_sleep_assertion(event=None) -> IdleSleepAssertion:
    return IdleSleepAssertion(event)


class WatchdogClock:
    """Report observable scheduler gaps; a gap alone does not prove OS sleep."""

    def __init__(
        self,
        wall: float,
        monotonic: float,
        *,
        poll_seconds: float = 0.5,
        gap_threshold_seconds: float = 5,
    ):
        self.wall_started = self.wall_last = wall
        self.monotonic_started = self.monotonic_last = monotonic
        self.poll_seconds = poll_seconds
        self.gap_threshold_seconds = gap_threshold_seconds

    def observe(
        self, wall: float, monotonic: float, *, deadline: float | None = None
    ) -> dict | None:
        wall_gap, active_gap = wall - self.wall_last, monotonic - self.monotonic_last
        self.wall_last, self.monotonic_last = wall, monotonic
        if max(wall_gap, active_gap) < self.gap_threshold_seconds:
            return None
        excluded = max(0.0, wall_gap - active_gap)
        return {
            "kind": "watchdog_clock_gap",
            "wall_gap_seconds": round(wall_gap, 3),
            "monotonic_gap_seconds": round(active_gap, 3),
            "sleep_or_clock_adjustment_seconds": round(excluded, 3),
            "classification": "suspend_or_wall_clock_change"
            if excluded >= self.gap_threshold_seconds
            else "scheduler_observation_gap",
            "expected_poll_seconds": self.poll_seconds,
            "wall_elapsed_seconds": round(wall - self.wall_started, 3),
            "monotonic_elapsed_seconds": round(monotonic - self.monotonic_started, 3),
            "wall_deadline": deadline,
            "deadline_overdue_seconds": round(max(0.0, wall - deadline), 3)
            if deadline
            else 0.0,
            "deadline_policy": "wall_clock; suspended host is checked on the next watchdog observation",
        }
