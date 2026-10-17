from __future__ import annotations

from dataclasses import dataclass

from esg_targeted.contracts import EvidencePacket, EvidenceSufficiency


@dataclass(frozen=True)
class VisualRoutePlan:
    route: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"route": self.route, "reasons": list(self.reasons)}


class VisualEscalationPlanner:
    """Use bounded crops as first-class evidence, not an error-only fallback."""

    VISUAL_FLAGS = {
        "needs_visual_review",
        "ocr_low_confidence",
        "visual_only",
        "table_structure_unreliable",
        "review_required",
    }

    def plan(
        self,
        packet: EvidencePacket,
        sufficiency: EvidenceSufficiency,
        *,
        visual_enabled: bool,
        force_table_visual: bool = False,
    ) -> VisualRoutePlan:
        if not visual_enabled or not packet.page_image_paths:
            return VisualRoutePlan("text_only", ("visual_input_unavailable",))

        table = any(item.has_table_structure for item in packet.spans)
        figure = any(item.span_type == "figure_text" for item in packet.spans)
        flagged = any(
            self.VISUAL_FLAGS.intersection(item.quality_flags)
            for item in packet.spans
        )
        reasons = []
        if table:
            reasons.append("retrieved_table_crop")
        if figure:
            reasons.append("retrieved_figure_crop")
        if flagged:
            reasons.append("document_ir_visual_signal")
        if sufficiency.status == "uncertain":
            reasons.append("text_evidence_incomplete")

        if table or figure or flagged or force_table_visual:
            return VisualRoutePlan("visual_first", tuple(reasons or ["visual_enabled"]))
        return VisualRoutePlan("text_then_visual", ("prose_evidence_with_page_image",))
