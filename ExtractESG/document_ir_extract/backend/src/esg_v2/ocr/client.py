from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from esg_v2.contracts import OptionalPayload


class PaddleOcrVlClient:
    def __init__(
        self,
        job_url: str,
        token: str,
        timeout_seconds: float = 120,
        trust_environment_proxy: bool = False,
    ):
        if not token:
            raise ValueError("PaddleOCR-VL token is required")
        self.job_url = job_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.headers = {"Authorization": f"bearer {token}"}
        self.session = requests.Session()
        self.session.trust_env = trust_environment_proxy

    def submit(
        self,
        *,
        model: str,
        optional_payload: OptionalPayload,
        file_path: str | Path | None = None,
        file_url: str | None = None,
    ) -> dict[str, Any]:
        if bool(file_path) == bool(file_url):
            raise ValueError("Exactly one of file_path or file_url is required")

        if file_url:
            headers = {**self.headers, "Content-Type": "application/json"}
            payload = {
                "fileUrl": file_url,
                "model": model,
                "optionalPayload": optional_payload.model_dump(),
            }
            response = self.session.post(
                self.job_url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        else:
            path = Path(file_path or "")
            if not path.exists():
                raise FileNotFoundError(f"PDF file not found: {path}")
            data = {
                "model": model,
                "optionalPayload": json.dumps(optional_payload.model_dump()),
            }
            with path.open("rb") as fh:
                files = {"file": fh}
                response = self.session.post(
                    self.job_url,
                    headers=self.headers,
                    data=data,
                    files=files,
                    timeout=self.timeout_seconds,
                )

        self._raise_for_response(response)
        return response.json()

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.job_url}/{job_id}",
            headers=self.headers,
            timeout=self.timeout_seconds,
        )
        self._raise_for_response(response)
        return response.json()

    def download_text(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.text

    def download_bytes(self, url: str) -> bytes:
        response = self.session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _raise_for_response(response: requests.Response) -> None:
        if response.status_code == 200:
            return
        body = response.text[:2000]
        raise RuntimeError(f"PaddleOCR-VL API returned {response.status_code}: {body}")
