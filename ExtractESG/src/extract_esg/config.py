from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings.

    The project intentionally keeps configuration simple at this stage so the
    Qiniu adapter can run without introducing a settings framework dependency.
    """

    env: str
    qiniu_api_key: str | None
    qiniu_base_url: str
    qiniu_default_timeout_seconds: float
    object_store: Path
    model_cache: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            env=os.getenv("EXTRACT_ESG_ENV", "local"),
            qiniu_api_key=os.getenv("QINIU_API_KEY") or None,
            qiniu_base_url=os.getenv("QINIU_BASE_URL", "https://api.qnaigc.com/v1").rstrip("/"),
            qiniu_default_timeout_seconds=float(os.getenv("QINIU_DEFAULT_TIMEOUT_SECONDS", "120")),
            object_store=Path(os.getenv("EXTRACT_ESG_OBJECT_STORE", "./.local/artifacts")),
            model_cache=Path(os.getenv("EXTRACT_ESG_MODEL_CACHE", "./.local/qiniu_models.json")),
        )

