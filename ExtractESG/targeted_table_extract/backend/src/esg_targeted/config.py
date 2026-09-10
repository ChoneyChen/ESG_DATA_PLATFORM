from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    project_root: Path
    ir_roots: tuple[Path, ...]
    standard_dist_root: Path
    output_root: Path
    index_cache_root: Path
    state_db: Path
    nuextract_model_path: Path
    embedding_model_path: Path
    backend_host: str
    backend_port: int
    frontend_port: int
    require_ready_ir: bool
    retrieval_top_k: int
    packet_max_chars: int
    model_max_tokens: int
    model_retries: int
    retrieval_object_top_n: int = 3
    packet_max_spans: int = 40
    packet_max_groups: int = 12
    packet_max_candidates: int = 80
    packet_max_images: int = 1
    model_max_input_tokens: int = 65536
    model_timeout_seconds: float = 600.0
    model_min_output_tokens: int = 2048
    qiniu_api_key: str | None = None
    qiniu_base_url: str = "https://api.qnaigc.com/v1"
    qiniu_model: str = "qwen3.5-397b-a17b"
    qiniu_timeout_seconds: float = 240.0
    qiniu_max_output_tokens: int = 32768
    qiniu_max_image_bytes: int = 7_500_000
    cloud_packet_max_chars: int = 180_000
    cloud_packet_max_spans: int = 240
    cloud_packet_max_groups: int = 24
    cloud_packet_max_candidates: int = 480
    cloud_packet_max_images: int = 1

    @classmethod
    def load(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[3]
        default_ir_roots = (
            project_root.parent / "document_ir_extract" / "document_ir_output",
        )
        raw_ir_roots = os.getenv("ESG_TARGETED_IR_ROOTS")
        if raw_ir_roots:
            ir_roots = tuple(
                Path(item).expanduser().resolve()
                for item in raw_ir_roots.split(os.pathsep)
                if item.strip()
            )
        else:
            ir_roots = tuple(path.resolve() for path in default_ir_roots)

        output_root = Path(
            os.getenv("ESG_TARGETED_OUTPUT_ROOT", project_root / "targeted_extract_output")
        ).expanduser().resolve()
        local_root = project_root / ".local"
        model_root = Path.home() / "Desktop" / "model"
        return cls(
            project_root=project_root,
            ir_roots=ir_roots,
            standard_dist_root=Path(
                os.getenv(
                    "ESG_TARGETED_STANDARD_ROOT",
                    project_root.parent / "standard_packages" / "dist",
                )
            ).expanduser().resolve(),
            output_root=output_root,
            index_cache_root=Path(
                os.getenv("ESG_TARGETED_INDEX_CACHE", local_root / "indexes")
            ).expanduser().resolve(),
            state_db=Path(
                os.getenv("ESG_TARGETED_STATE_DB", local_root / "targeted-jobs.sqlite3")
            ).expanduser().resolve(),
            nuextract_model_path=Path(
                os.getenv("NUEXTRACT_MODEL_PATH", model_root / "NuExtract3-mlx-4bits")
            ).expanduser().resolve(),
            embedding_model_path=Path(
                os.getenv(
                    "QWEN3_EMBEDDING_MODEL_PATH",
                    model_root / "Qwen3-Embedding-0.6B-8bit",
                )
            ).expanduser().resolve(),
            backend_host=os.getenv("ESG_TARGETED_BACKEND_HOST", "127.0.0.1"),
            backend_port=int(os.getenv("ESG_TARGETED_BACKEND_PORT", "18180")),
            frontend_port=int(
                os.getenv(
                    "ESG_PLATFORM_FRONTEND_PORT",
                    os.getenv("ESG_TARGETED_FRONTEND_PORT", "18081"),
                )
            ),
            require_ready_ir=_bool_env("ESG_TARGETED_REQUIRE_READY_IR", True),
            retrieval_top_k=max(4, int(os.getenv("ESG_TARGETED_RETRIEVAL_TOP_K", "12"))),
            packet_max_chars=max(
                4000, int(os.getenv("ESG_TARGETED_PACKET_MAX_CHARS", "160000"))
            ),
            model_max_tokens=max(
                512, int(os.getenv("ESG_TARGETED_MODEL_MAX_TOKENS", "32768"))
            ),
            model_retries=max(0, int(os.getenv("ESG_TARGETED_MODEL_RETRIES", "2"))),
            retrieval_object_top_n=min(
                5,
                max(1, int(os.getenv("ESG_TARGETED_RETRIEVAL_OBJECT_TOP_N", "3"))),
            ),
            packet_max_spans=max(2, int(os.getenv("ESG_TARGETED_PACKET_MAX_SPANS", "40"))),
            packet_max_groups=max(1, int(os.getenv("ESG_TARGETED_PACKET_MAX_GROUPS", "12"))),
            packet_max_candidates=max(
                10, int(os.getenv("ESG_TARGETED_PACKET_MAX_CANDIDATES", "80"))
            ),
            packet_max_images=min(
                1, max(0, int(os.getenv("ESG_TARGETED_PACKET_MAX_IMAGES", "1")))
            ),
            model_max_input_tokens=max(
                1024, int(os.getenv("ESG_TARGETED_MODEL_MAX_INPUT_TOKENS", "65536"))
            ),
            model_timeout_seconds=max(
                10.0, float(os.getenv("ESG_TARGETED_MODEL_TIMEOUT_SECONDS", "600"))
            ),
            model_min_output_tokens=max(
                128, int(os.getenv("ESG_TARGETED_MODEL_MIN_OUTPUT_TOKENS", "2048"))
            ),
            qiniu_api_key=os.getenv("QINIU_API_KEY") or os.getenv("QINIU_AI_API_KEY"),
            qiniu_base_url=os.getenv(
                "ESG_TARGETED_QINIU_BASE_URL", "https://api.qnaigc.com/v1"
            ).rstrip("/"),
            qiniu_model=os.getenv(
                "ESG_TARGETED_QINIU_MODEL", "qwen3.5-397b-a17b"
            ),
            qiniu_timeout_seconds=max(
                20.0, float(os.getenv("ESG_TARGETED_QINIU_TIMEOUT_SECONDS", "240"))
            ),
            qiniu_max_output_tokens=max(
                1024, int(os.getenv("ESG_TARGETED_QINIU_MAX_OUTPUT_TOKENS", "32768"))
            ),
            qiniu_max_image_bytes=max(
                1_000_000,
                int(os.getenv("ESG_TARGETED_QINIU_MAX_IMAGE_BYTES", "7500000")),
            ),
            cloud_packet_max_chars=max(
                16_000,
                int(os.getenv("ESG_TARGETED_CLOUD_PACKET_MAX_CHARS", "180000")),
            ),
            cloud_packet_max_spans=max(
                40, int(os.getenv("ESG_TARGETED_CLOUD_PACKET_MAX_SPANS", "240"))
            ),
            cloud_packet_max_groups=max(
                8, int(os.getenv("ESG_TARGETED_CLOUD_PACKET_MAX_GROUPS", "24"))
            ),
            cloud_packet_max_candidates=max(
                80,
                int(os.getenv("ESG_TARGETED_CLOUD_PACKET_MAX_CANDIDATES", "480")),
            ),
            cloud_packet_max_images=min(
                1, max(0, int(os.getenv("ESG_TARGETED_CLOUD_PACKET_MAX_IMAGES", "1")))
            ),
        )

    def ensure_runtime_dirs(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.index_cache_root.mkdir(parents=True, exist_ok=True)
        self.state_db.parent.mkdir(parents=True, exist_ok=True)


settings = Settings.load()
