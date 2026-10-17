from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.metadata
import json
from pathlib import Path


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    supports_vision: bool
    supports_structured_output: bool
    supports_thinking: bool
    enable_thinking: bool
    min_output_tokens: int

    def as_dict(self) -> dict:
        return asdict(self)


NUEXTRACT3_CAPABILITIES = ModelCapabilities(
    model_id="numind/NuExtract3-mlx-4bits",
    supports_vision=True,
    supports_structured_output=True,
    supports_thinking=False,
    enable_thinking=False,
    min_output_tokens=512,
)

QWEN35_CAPABILITIES = ModelCapabilities(
    model_id="local/Qwen3.5-9B-4bit",
    supports_vision=True,
    supports_structured_output=True,
    supports_thinking=True,
    enable_thinking=False,
    min_output_tokens=512,
)


def local_model_readiness(model_path: Path) -> dict:
    """Check weights/config/runtime architecture without loading a model.

    Availability here is static compatibility, not a promise of business output
    quality. In particular, a weights directory alone is not enough to advertise
    a newly installed architecture as ready.
    """
    result = {"available": False, "path": str(model_path)}
    try:
        config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
        model_type = str(config.get("model_type") or "").lower()
        result["model_type"] = model_type
        if not model_type:
            raise ValueError("Model config has no model_type")
        if not any(model_path.glob("*.safetensors")):
            raise ValueError("Model weights are missing")
        distribution = importlib.metadata.distribution("mlx-vlm")
        result["mlx_vlm_version"] = distribution.version
        module = distribution.locate_file(f"mlx_vlm/models/{model_type}")
        if not (module / "__init__.py").is_file() and not module.with_suffix(".py").is_file():
            raise ValueError(f"Installed mlx-vlm does not support model_type={model_type}")
        result["available"] = True
    except (OSError, ValueError, TypeError, importlib.metadata.PackageNotFoundError) as exc:
        result["error"] = str(exc)
    return result
