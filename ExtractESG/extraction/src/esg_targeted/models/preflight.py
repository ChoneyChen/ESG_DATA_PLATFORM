from __future__ import annotations

import json
from dataclasses import dataclass

from esg_targeted.contracts import EvidencePacket
from esg_targeted.models.template import DirectFillTemplateCompiler


class PromptBudgetExceeded(ValueError):
    pass


@dataclass(frozen=True)
class PromptPreflightResult:
    context_chars: int
    template_chars: int
    instruction_chars: int
    estimated_total_chars: int
    max_total_chars: int

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "accepted": self.estimated_total_chars <= self.max_total_chars,
            "context_chars": self.context_chars,
            "template_chars": self.template_chars,
            "instruction_chars": self.instruction_chars,
            "estimated_total_chars": self.estimated_total_chars,
            "max_total_chars": self.max_total_chars,
        }


class PacketPreflightGuard:
    def __init__(self, max_total_chars: int) -> None:
        self.max_total_chars = max_total_chars
        self.template_compiler = DirectFillTemplateCompiler()

    def validate(
        self, packet: EvidencePacket, *, feedback: str | None = None
    ) -> PromptPreflightResult:
        if len(packet.page_image_paths) > 1:
            raise PromptBudgetExceeded(
                "one evidence region may contain at most one visual object/image; "
                f"received {len(packet.page_image_paths)}"
            )
        template = json.dumps(
            self.template_compiler.compile(packet), ensure_ascii=False, separators=(",", ":")
        )
        instructions = self.template_compiler.instructions(feedback)
        result = PromptPreflightResult(
            context_chars=len(packet.model_context),
            template_chars=len(template),
            instruction_chars=len(instructions),
            estimated_total_chars=len(packet.model_context) + len(template) + len(instructions),
            max_total_chars=self.max_total_chars,
        )
        if result.estimated_total_chars > result.max_total_chars:
            raise PromptBudgetExceeded(
                "prompt preflight rejected packet: "
                f"{result.estimated_total_chars} > {result.max_total_chars} chars"
            )
        return result

    def fit_feedback(
        self, packet: EvidencePacket, feedback: str | None
    ) -> str | None:
        if not feedback:
            return None
        template = json.dumps(
            self.template_compiler.compile(packet), ensure_ascii=False, separators=(",", ":")
        )
        base_instructions = self.template_compiler.instructions(None)
        feedback_prefix = len(self.template_compiler.instructions("")) - len(base_instructions)
        available = (
            self.max_total_chars
            - len(packet.model_context)
            - len(template)
            - len(base_instructions)
            - feedback_prefix
            - 128
        )
        if available <= 0:
            return None
        if len(feedback) <= available:
            return feedback
        clipped = feedback[:available]
        if "\n" in clipped:
            clipped = clipped.rsplit("\n", 1)[0]
        return clipped.rstrip() + "\n[feedback truncated to prompt budget]"
