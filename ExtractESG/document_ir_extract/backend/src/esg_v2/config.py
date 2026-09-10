from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DOCUMENT_IR_APP_ROOT = Path(__file__).resolve().parents[3]
OCR_OUTPUT_ROOT = DOCUMENT_IR_APP_ROOT / "ocr_output"
DOCUMENT_IR_OUTPUT_ROOT = DOCUMENT_IR_APP_ROOT / "document_ir_output"
UPLOAD_ROOT = DOCUMENT_IR_APP_ROOT / ".local" / "uploads"
OCR_JOB_STATE_ROOT = DOCUMENT_IR_APP_ROOT / ".local" / "jobs" / "ocr"
DOCUMENT_IR_JOB_STATE_ROOT = DOCUMENT_IR_APP_ROOT / ".local" / "jobs" / "document-ir"
WORKSPACE_ROOT = DOCUMENT_IR_APP_ROOT.parents[2]
PLATFORM_RUNTIME_ROOT = WORKSPACE_ROOT / ".local" / "platform-runtime"
REPORT_ASSET_ROOT = WORKSPACE_ROOT / "pdf"
TARGETED_APP_ROOT = DOCUMENT_IR_APP_ROOT.parent / "targeted_table_extract"


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


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
    load_env_file(DOCUMENT_IR_APP_ROOT / ".env")
    load_env_file(DOCUMENT_IR_APP_ROOT / ".env.local")


def default_storage_cleanup_root() -> Path:
    explicit = os.getenv("ESG_V2_STORAGE_CLEANUP_DIR")
    if explicit:
        return Path(explicit)
    state_root = Path(
        os.getenv("ESG_V2_DOCUMENT_IR_JOB_STATE_DIR", str(DOCUMENT_IR_JOB_STATE_ROOT))
    )
    return state_root.parent.parent / "storage-cleanup"


@dataclass(frozen=True)
class Settings:
    report_asset_root: Path = field(
        default_factory=lambda: Path(
            os.getenv("ESG_REPORT_ASSET_DIR", str(REPORT_ASSET_ROOT))
        ).expanduser()
    )
    pipeline_queue_db: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "ESG_PIPELINE_QUEUE_DB",
                str(PLATFORM_RUNTIME_ROOT / "pipeline-queue.sqlite3"),
            )
        ).expanduser()
    )
    pipeline_task_root: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "ESG_PIPELINE_TASK_DIR",
                str(PLATFORM_RUNTIME_ROOT / "tasks"),
            )
        ).expanduser()
    )
    targeted_app_root: Path = field(
        default_factory=lambda: Path(
            os.getenv("ESG_TARGETED_APP_ROOT", str(TARGETED_APP_ROOT))
        ).expanduser()
    )
    targeted_runtime_python: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "ESG_TARGETED_RUNTIME_PYTHON",
                str(Path.home() / "Desktop" / "model" / ".runtime" / "venv" / "bin" / "python"),
            )
        ).expanduser()
    )
    targeted_backend_url: str = field(
        default_factory=lambda: os.getenv(
            "ESG_TARGETED_BACKEND_URL", "http://127.0.0.1:18180"
        ).rstrip("/")
    )
    pipeline_poll_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("ESG_PIPELINE_POLL_INTERVAL", "0.5"))
    )
    default_ocr_provider: str = field(
        default_factory=lambda: os.getenv("ESG_V2_OCR_PROVIDER", "local_first")
    )
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
    storage_cleanup_root: Path = field(default_factory=default_storage_cleanup_root)
    document_ir_best_only_retention: bool = field(
        default_factory=lambda: env_bool("ESG_V2_DOCUMENT_IR_BEST_ONLY", True)
    )
    poll_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("PADDLEOCR_VL_POLL_INTERVAL", "5"))
    )
    request_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("PADDLEOCR_VL_REQUEST_TIMEOUT", "120"))
    )
    paddle_trust_environment_proxy: bool = field(
        default_factory=lambda: env_bool("PADDLEOCR_VL_TRUST_ENV_PROXY", False)
    )
    local_paddleocr_runtime_python: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "LOCAL_PADDLEOCR_RUNTIME_PYTHON",
                str(Path.home() / "Desktop" / "model" / ".runtime" / "paddleocr-vl" / "bin" / "python"),
            )
        ).expanduser()
    )
    local_paddleocr_model_path: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "LOCAL_PADDLEOCR_MODEL_PATH",
                str(Path.home() / "Desktop" / "model" / "PaddleOCR-VL-1.6"),
            )
        ).expanduser()
    )
    local_paddleocr_pipeline_cache: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "LOCAL_PADDLEOCR_PIPELINE_CACHE",
                str(Path.home() / "Desktop" / "model" / ".cache" / "paddlex"),
            )
        ).expanduser()
    )
    local_paddleocr_pipeline_version: str = field(
        default_factory=lambda: os.getenv("LOCAL_PADDLEOCR_PIPELINE_VERSION", "v1.6")
    )
    local_paddleocr_vlm_backend: str = field(
        default_factory=lambda: os.getenv("LOCAL_PADDLEOCR_VLM_BACKEND", "mlx-vlm-server")
    )
    local_paddleocr_vlm_server_url: str = field(
        default_factory=lambda: os.getenv("LOCAL_PADDLEOCR_VLM_SERVER_URL", "http://127.0.0.1:8111/")
    )
    local_paddleocr_autostart_vlm_server: bool = field(
        default_factory=lambda: env_bool("LOCAL_PADDLEOCR_AUTOSTART_VLM_SERVER", True)
    )
    local_paddleocr_server_start_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LOCAL_PADDLEOCR_SERVER_START_TIMEOUT_SECONDS", "60"))
    )
    local_paddleocr_job_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LOCAL_PADDLEOCR_JOB_TIMEOUT_SECONDS", "7200"))
    )
    local_paddleocr_stall_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LOCAL_PADDLEOCR_STALL_TIMEOUT_SECONDS", "600"))
    )
    local_paddleocr_heartbeat_seconds: float = field(
        default_factory=lambda: float(os.getenv("LOCAL_PADDLEOCR_HEARTBEAT_SECONDS", "15"))
    )
    ocr_pdf_canvas_normalization_enabled: bool = field(
        default_factory=lambda: env_bool("ESG_V2_OCR_PDF_CANVAS_NORMALIZATION", True)
    )
    ocr_provider_max_canvas_points: float = field(
        default_factory=lambda: float(os.getenv("ESG_V2_OCR_PROVIDER_MAX_CANVAS_POINTS", "1200"))
    )
    ocr_provider_max_local_file_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("ESG_V2_OCR_PROVIDER_MAX_LOCAL_FILE_BYTES", str(100 * 1024 * 1024))
        )
    )
    ocr_provider_max_pdf_pages: int = field(
        default_factory=lambda: int(os.getenv("ESG_V2_OCR_PROVIDER_MAX_PDF_PAGES", "1000"))
    )
    qiniu_api_key: str | None = field(default_factory=lambda: os.getenv("QINIU_API_KEY"))
    qiniu_base_url: str = field(
        default_factory=lambda: os.getenv("QINIU_BASE_URL", "https://api.qnaigc.com/v1").rstrip("/")
    )
    qiniu_default_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("QINIU_DEFAULT_TIMEOUT_SECONDS", "120"))
    )
    qiniu_min_request_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("QINIU_MIN_REQUEST_INTERVAL_SECONDS", "0.75"))
    )
    qiniu_rpm_backoff_base_seconds: float = field(
        default_factory=lambda: float(os.getenv("QINIU_RPM_BACKOFF_BASE_SECONDS", "5"))
    )
    qiniu_rpm_max_wait_seconds: float = field(
        default_factory=lambda: float(os.getenv("QINIU_RPM_MAX_WAIT_SECONDS", "30"))
    )
    qiniu_tpd_reset_grace_seconds: float = field(
        default_factory=lambda: float(os.getenv("QINIU_TPD_RESET_GRACE_SECONDS", "300"))
    )
    qiniu_vlm_model: str | None = field(default_factory=lambda: os.getenv("QINIU_VLM_MODEL"))
    default_review_provider: str = field(
        default_factory=lambda: os.getenv("ESG_V2_REVIEW_PROVIDER", "qiniu")
    )
    nuextract_model_path: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "NUEXTRACT_MODEL_PATH",
                str(Path.home() / "Desktop" / "model" / "NuExtract3-mlx-4bits"),
            )
        ).expanduser()
    )
    nuextract_runtime_python: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "NUEXTRACT_RUNTIME_PYTHON",
                str(Path.home() / "Desktop" / "model" / ".runtime" / "venv" / "bin" / "python"),
            )
        ).expanduser()
    )
    nuextract_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("NUEXTRACT_TIMEOUT_SECONDS", "900"))
    )
    max_vlm_reviews_per_ir_run: int = field(
        default_factory=lambda: int(os.getenv("ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN", "0"))
    )
    max_blocking_vlm_reviews_per_ir_run: int = field(
        default_factory=lambda: int(os.getenv("ESG_V2_MAX_BLOCKING_VLM_REVIEWS_PER_IR_RUN", "64"))
    )
    max_optional_vlm_reviews_per_ir_run: int = field(
        default_factory=lambda: int(os.getenv("ESG_V2_MAX_OPTIONAL_VLM_REVIEWS_PER_IR_RUN", "64"))
    )
    review_completeness_mode: bool = field(
        default_factory=lambda: env_bool("ESG_V2_REVIEW_COMPLETENESS_MODE", True)
    )
    pdf_render_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("ESG_V2_PDF_RENDER_TIMEOUT_SECONDS", "1800"))
    )


def get_settings() -> Settings:
    load_default_env_files()
    return Settings()
