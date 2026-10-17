from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlparse


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def require_loopback_url(server_url: str) -> str:
    parsed = urlparse(server_url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or host not in LOOPBACK_HOSTS:
        raise ValueError("Local MLX-VLM server URL must use HTTP(S) on a loopback host.")
    return host


def loopback_proxy_bypass_environment(
    environment: Mapping[str, str] | None,
    server_url: str,
) -> dict[str, str]:
    """Preserve proxy settings while forcing local model traffic to stay local."""

    require_loopback_url(server_url)
    result = dict(os.environ if environment is None else environment)
    existing: list[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        existing.extend(item.strip() for item in result.get(key, "").split(",") if item.strip())
    bypass = list(dict.fromkeys([*existing, *LOOPBACK_HOSTS]))
    value = ",".join(bypass)
    result["NO_PROXY"] = value
    result["no_proxy"] = value
    return result
