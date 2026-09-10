from __future__ import annotations

from dataclasses import asdict, dataclass


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
