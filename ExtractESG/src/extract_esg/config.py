from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _get_env(name: str, env_file: dict[str, str], default: str | None = None) -> str | None:
    return os.getenv(name) or env_file.get(name) or default


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
    sqlite_db_path: Path
    default_text_model: str | None
    default_vision_model: str | None
    default_reasoning_model: str | None
    default_fast_model: str | None

    @classmethod
    def from_env(cls, env_file_path: str | Path | None = None) -> "Settings":
        env_path = Path(env_file_path or os.getenv("EXTRACT_ESG_ENV_FILE", ".env"))
        env_file = _load_env_file(env_path)
        return cls(
            env=_get_env("EXTRACT_ESG_ENV", env_file, "local") or "local",
            qiniu_api_key=_get_env("QINIU_API_KEY", env_file) or None,
            qiniu_base_url=(_get_env("QINIU_BASE_URL", env_file, "https://api.qnaigc.com/v1") or "").rstrip("/"),
            qiniu_default_timeout_seconds=float(_get_env("QINIU_DEFAULT_TIMEOUT_SECONDS", env_file, "120") or "120"),
            object_store=Path(_get_env("EXTRACT_ESG_OBJECT_STORE", env_file, "./.local/artifacts") or "./.local/artifacts"),
            model_cache=Path(_get_env("EXTRACT_ESG_MODEL_CACHE", env_file, "./.local/qiniu_models.json") or "./.local/qiniu_models.json"),
            sqlite_db_path=Path(_get_env("EXTRACT_ESG_SQLITE_DB", env_file, "./.local/extract_esg.db") or "./.local/extract_esg.db"),
            default_text_model=_get_env("EXTRACT_ESG_TEXT_MODEL", env_file),
            default_vision_model=_get_env("EXTRACT_ESG_VISION_MODEL", env_file),
            default_reasoning_model=_get_env("EXTRACT_ESG_REASONING_MODEL", env_file),
            default_fast_model=_get_env("EXTRACT_ESG_FAST_MODEL", env_file),
        )
