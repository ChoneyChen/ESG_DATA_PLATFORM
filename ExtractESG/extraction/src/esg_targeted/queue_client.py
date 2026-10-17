"""Thin submission adapter; execution belongs to the document control plane."""
from __future__ import annotations

import os

import httpx


def enqueue_existing(kind: str, identity: str, *, backend_url=None, qiniu_api_key=None, ocr_token=None):
    if kind not in {"targeted-extraction", "report-session"}:
        raise ValueError("Unsupported queue operation")
    base = (backend_url or os.getenv("ESG_DOCUMENT_BACKEND_URL", "http://127.0.0.1:18080")).rstrip("/")
    # Secrets travel directly to the in-memory vault, never the immutable request.
    credentials = {key: value for key, value in {
        "qiniu_api_key": qiniu_api_key or os.getenv("QINIU_API_KEY"),
        "ocr_token": ocr_token,
    }.items() if value}
    try:
        response = httpx.post(f"{base}/api/pipeline/tasks/{kind}/{identity}/run-existing", json=credentials, timeout=30)
    except httpx.TransportError as exc:
        raise RuntimeError("Unified queue is unavailable; the immutable request remains queued. Start the document backend and submit it again.") from exc
    if not response.is_success:
        # Avoid echoing an upstream response that could contain submitted credentials.
        raise ValueError(f"Unified queue rejected submission (HTTP {response.status_code}); inspect the queue service for the action required")
    return response.json()


def cancel_existing(identity: str):
    base = os.getenv("ESG_DOCUMENT_BACKEND_URL", "http://127.0.0.1:18080").rstrip("/")
    try:
        response = httpx.post(f"{base}/api/pipeline/tasks/native/{identity}/cancel", json={}, timeout=10)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        # Native cancellation is already recorded; its watchdog still enforces
        # that flag if the scheduling service is restarting.
        return {"cancel_requested": True, "queue_notification": "unavailable"}
