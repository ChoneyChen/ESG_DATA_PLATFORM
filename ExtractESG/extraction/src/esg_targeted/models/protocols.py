from __future__ import annotations

from typing import Any, Callable, Protocol

from esg_targeted.contracts import EvidencePacket, ModelRunResult
from esg_targeted.models.family_adjudication import (
    FamilyAdjudicationPacket,
    FamilyModelRunResult,
)


class SemanticFillModel(Protocol):
    def decide(
        self,
        packet: EvidencePacket,
        *,
        feedback: str | None = None,
        use_visual: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ModelRunResult: ...

    def readjudicate_family(
        self,
        packet: FamilyAdjudicationPacket,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> FamilyModelRunResult: ...

    def close(self) -> None: ...


class StructuredTaskModel(Protocol):
    """A bounded generation interface; its name does not promise constrained decoding.

    Templates are canonical Pydantic-generated JSON Schema. Providers report
    whether the schema was merely prompted or requested from their API. Every
    response still needs type and task-local reference validation.
    """
    def generate_structured(
        self, *, context: dict | str, instructions: str, template: dict,
        image_path: Any = None, max_tokens: int = 2048,
        cancel_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, dict]: ...
