from __future__ import annotations

from typing import Callable, Protocol

from esg_targeted.contracts import EvidencePacket, ModelRunResult


class SemanticFillModel(Protocol):
    def decide(
        self,
        packet: EvidencePacket,
        *,
        feedback: str | None = None,
        use_visual: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ModelRunResult: ...

    def close(self) -> None: ...
