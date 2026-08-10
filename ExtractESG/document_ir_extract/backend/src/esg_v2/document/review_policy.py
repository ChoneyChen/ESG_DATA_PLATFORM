from __future__ import annotations

import re
from dataclasses import dataclass

from esg_v2.document.contracts import AtomicPatchProposal, DocumentIR, VlmReviewTask
from esg_v2.document.geometry import bbox_area_ratio
from esg_v2.document.review_plan_compiler import ReviewPlanCompiler


@dataclass(frozen=True)
class DeterministicReviewProposal:
    proposal: AtomicPatchProposal
    reason: str


class ReviewPolicyEngine:
    """Conservative deterministic decisions; plan compilation lives elsewhere."""

    def __init__(self) -> None:
        self.compiler = ReviewPlanCompiler()

    def attach(self, document: DocumentIR, task: VlmReviewTask) -> VlmReviewTask:
        """Compatibility entrypoint for callers creating one new task."""
        return self.compiler.compile(document, task)

    def deterministic_proposal(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
    ) -> DeterministicReviewProposal | None:
        if not task.review_plan or task.review_plan.review_kind != "table_candidate_classification":
            return None
        table_ids = [
            scope.target_id
            for scope in task.scope
            if scope.target_type == "table" and scope.blocking
        ]
        if not table_ids and task.target_type == "table":
            table_ids = [task.target_id]
        if len(table_ids) != 1:
            return None
        table = next((item for item in document.tables if item.table_id == table_ids[0]), None)
        page = next((item for item in document.pages if item.page_index == task.page_index), None)
        if not table or not page or not self._is_decoration_like_local_table(table, page):
            return None
        reason = (
            "The local-only candidate is a small, highly sparse region with no material numeric token "
            "and only a short fragmented label; it is deterministically classified as decoration."
        )
        return DeterministicReviewProposal(
            proposal=AtomicPatchProposal(
                target_type="table",
                target_id=table.table_id,
                operation="retire_table_candidate",
                proposed_value={"disposition": "decoration"},
                evidence_refs=list(task.input_refs),
                rationale=reason,
                confidence=1.0,
            ),
            reason=reason,
        )

    @staticmethod
    def _is_decoration_like_local_table(table, page) -> bool:
        if "local_only_table_candidate" not in table.quality_flags or not table.cells:
            return False
        nonempty = [str(cell.text or "").strip() for cell in table.cells if str(cell.text or "").strip()]
        blank_ratio = 1.0 - len(nonempty) / max(1, len(table.cells))
        alphanumeric = "".join(character for value in nonempty for character in value if character.isalnum())
        has_material_number = bool(re.search(r"\d", alphanumeric))
        area_ratio = bbox_area_ratio(
            table.bbox,
            page_width=page.width,
            page_height=page.height,
        )
        return blank_ratio >= 0.70 and not has_material_number and len(alphanumeric) <= 12 and area_ratio <= 0.025
