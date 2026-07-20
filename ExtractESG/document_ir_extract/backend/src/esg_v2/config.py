from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[3]
OCR_OUTPUT_ROOT = V2_ROOT / "ocr_output"
DOCUMENT_IR_OUTPUT_ROOT = V2_ROOT / "document_ir_output"
UPLOAD_ROOT = V2_ROOT / ".local" / "uploads"
OCR_JOB_STATE_ROOT = V2_ROOT / ".local" / "jobs" / "ocr"
DOCUMENT_IR_JOB_STATE_ROOT = V2_ROOT / ".local" / "jobs" / "document-ir"


def load_env_file(path: str | Path | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_default_env_files() -> None:
    load_env_file(V2_ROOT / ".env")
    load_env_file(V2_ROOT / ".env.local")


@dataclass(frozen=True)
class Settings:
    paddle_job_url: str = field(
        default_factory=lambda: os.getenv(
            "PADDLEOCR_VL_JOB_URL",
            "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
        )
    )
    paddle_model: str = field(default_factory=lambda: os.getenv("PADDLEOCR_VL_MODEL", "PaddleOCR-VL-1.6"))
    paddle_token: str | None = field(default_factory=lambda: os.getenv("PADDLEOCR_VL_API_TOKEN"))
    output_root: Path = field(default_factory=lambda: Path(os.getenv("ESG_V2_OCR_OUTPUT_DIR", str(OCR_OUTPUT_ROOT))))
    document_ir_output_root: Path = field(
        default_factory=lambda: Path(os.getenv("ESG_V2_DOCUMENT_IR_OUTPUT_DIR", str(DOCUMENT_IR_OUTPUT_ROOT)))
    )
    upload_root: Path = field(default_factory=lambda: Path(os.getenv("ESG_V2_UPLOAD_DIR", str(UPLOAD_ROOT))))
    ocr_job_state_root: Path = field(
        default_factory=lambda: Path(os.getenv("ESG_V2_OCR_JOB_STATE_DIR", str(OCR_JOB_STATE_ROOT)))
    )
    document_ir_job_state_root: Path = field(
        default_factory=lambda: Path(
            os.getenv("ESG_V2_DOCUMENT_IR_JOB_STATE_DIR", str(DOCUMENT_IR_JOB_STATE_ROOT))
        )
    )
    poll_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("PADDLEOCR_VL_POLL_INTERVAL", "5"))
    )
    request_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("PADDLEOCR_VL_REQUEST_TIMEOUT", "120"))
    )
    qiniu_api_key: str | None = field(default_factory=lambda: os.getenv("QINIU_API_KEY"))
    qiniu_base_url: str = field(
        default_factory=lambda: os.getenv("QINIU_BASE_URL", "https://api.qnaigc.com/v1").rstrip("/")
    )
    qiniu_default_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("QINIU_DEFAULT_TIMEOUT_SECONDS", "120"))
    )
    qiniu_vlm_model: str | None = field(default_factory=lambda: os.getenv("QINIU_VLM_MODEL"))
    max_vlm_reviews_per_ir_run: int = field(
        default_factory=lambda: int(os.getenv("ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN", "3"))
    )


def get_settings() -> Settings:
    load_default_env_files()
    return Settings()
