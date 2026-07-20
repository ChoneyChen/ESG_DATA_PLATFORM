from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit


_SENSITIVE_QUERY_TOKENS = (
    "authorization",
    "credential",
    "signature",
    "token",
    "secret",
    "api_key",
    "apikey",
)


def is_sensitive_remote_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return True
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.lower().replace("-", "_")
        if any(token in normalized for token in _SENSITIVE_QUERY_TOKENS):
            return True
        if query_value.lower().startswith(("bce-auth-v1/", "aws4-hmac-sha256")):
            return True
    return False


def sanitize_remote_url(value: str) -> str:
    if not is_sensitive_remote_url(value):
        return value
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_payload(item) for item in value)
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return sanitize_remote_url(value)
    return value


def portable_input_reference(*, run_id: str, file_path: str | None, file_url: str | None) -> dict[str, str | None]:
    if file_url:
        return {
            "kind": "url",
            "display_name": sanitize_remote_url(file_url),
            "request_reference": sanitize_remote_url(file_url),
        }
    if file_path:
        name = Path(file_path).name
        return {
            "kind": "local-file",
            "display_name": name,
            "request_reference": f"runtime-upload://{run_id}/{name}",
        }
    return {"kind": "unknown", "display_name": None, "request_reference": None}
