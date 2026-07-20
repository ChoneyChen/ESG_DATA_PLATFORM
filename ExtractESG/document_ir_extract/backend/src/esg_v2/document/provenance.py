from __future__ import annotations

from typing import Any

from esg_v2.utils.sanitization import (
    is_sensitive_remote_url,
    sanitize_payload,
    sanitize_remote_url,
)


def durable_remote_reference(value: Any) -> str | None:
    if not isinstance(value, str) or not value.startswith(("http://", "https://", "kodo://")):
        return None
    if is_sensitive_remote_url(value):
        return None
    return value


def sanitize_ir_payload(value: Any) -> Any:
    return sanitize_payload(value)
