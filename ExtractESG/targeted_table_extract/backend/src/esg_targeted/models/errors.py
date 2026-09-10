from __future__ import annotations

from typing import Any


class ModelRunFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str,
        raw_output: str = "",
        cleaned_output: str = "",
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.raw_output = raw_output
        self.cleaned_output = cleaned_output
        self.telemetry = telemetry or {}


class ModelCancelled(ModelRunFailure):
    def __init__(self, raw_output: str = "", telemetry: dict[str, Any] | None = None) -> None:
        super().__init__(
            "model generation cancelled",
            category="cancelled",
            raw_output=raw_output,
            telemetry=telemetry,
        )


class ModelTimedOut(ModelRunFailure):
    def __init__(
        self, timeout_seconds: float, raw_output: str = "", telemetry: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            f"model generation exceeded {timeout_seconds:g} seconds",
            category="timeout",
            raw_output=raw_output,
            telemetry=telemetry,
        )
