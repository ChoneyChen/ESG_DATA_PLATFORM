from __future__ import annotations

import json
from typing import Any

from esg_v2.document.contracts import AtomicPatch, DocumentIR, FigureIR, ReviewerResult, SpreadIR, TableIR, VlmReviewTask
from esg_v2.document.operation_registry import OperationRegistry
from esg_v2.document.parser_fusion import ParserFusion
from esg_v2.document.patch_guard import PatchGuard


class ReviewContextCompiler:
    """Builds task-bounded model context; visual bytes stay in image inputs, not JSON."""

    def reviewer(self, document: DocumentIR, task: VlmReviewTask, feedback: list[str]) -> str:
        plan = task.review_plan
        payload = {
            "task": {
                "task_id": task.task_id,
                "page_index": task.page_index,
                "reason_codes": task.reason_codes,
                "blocking": task.blocking,
                "scope": [scope.model_dump(mode="json") for scope in task.scope],
                "review_plan": plan.model_dump(mode="json") if plan else None,
                "operation_contract": OperationRegistry.prompt_fragment(plan.allowed_operations) if plan else [],
            },
            "candidate": self.candidate(document, task),
            "repair_feedback": feedback,
            "rules": self._rules(plan.review_kind if plan else ""),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def verifier(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        reviewer: ReviewerResult,
        patches: list[AtomicPatch],
    ) -> str:
        payload = {
            "task": {"task_id": task.task_id, "reason_codes": task.reason_codes},
            "candidate": self.candidate(document, task),
            "reviewer_result": reviewer.model_dump(mode="json", exclude={"raw_response", "usage"}),
            "proposed_patches": [patch.model_dump(mode="json") for patch in patches],
            "rules": [
                "Independently compare each transaction with visual evidence.",
                "Return exactly one transaction decision per transaction_id.",
                "Accept only complete, supported, atomic transactions; reject unsupported or destructive changes.",
                "Abstain only when the visual evidence is genuinely unreadable or ambiguous.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def candidate(self, document: DocumentIR, task: VlmReviewTask) -> dict[str, Any]:
        plan = task.review_plan
        targets = {task.target_id, *(scope.target_id for scope in task.scope)}
        if plan:
            targets.update(plan.context_target_ids)
        spread = next((item for item in document.spreads if item.spread_id == task.target_id), None)
        figure_mode = bool(plan and plan.review_kind in {"figure_binding", "figure_semantic_structure"})
        page_coverage_mode = bool(plan and plan.review_kind == "page_text_coverage")
        scoped_targets = {task.target_id, *(scope.target_id for scope in task.scope)}
        page_indices = spread.page_indices if spread else [task.page_index]
        pages = [
            value for value in (
                self._page_context(document, index, targets, spread=spread, compact=figure_mode)
                for index in page_indices
            )
            if value
        ]
        result: dict[str, Any] = {
            "pages": pages,
            "spread": spread.model_dump(mode="json", exclude={"source_trace", "review_task_ids"}) if spread else None,
            "targets": [],
        }
        if spread:
            result["spread_composition_diagnostics"] = self.spread_diagnostics(document, spread)
        for item in [*document.spreads, *document.blocks, *document.tables, *document.figures, *document.sections]:
            item_id = self._entity_id(item)
            if item_id not in targets:
                continue
            raw = item.model_dump(
                mode="json",
                exclude={"source_trace", "graph_edges", "observations", "markdown", "review_task_ids"},
            )
            raw["target_type"] = PatchGuard._target_type(item)
            if page_coverage_mode and item_id not in scoped_targets:
                raw = {
                    key: raw.get(key)
                    for key in (
                        "block_id", "table_id", "figure_id", "target_type", "page_index",
                        "bbox", "caption", "visual_type", "row_count", "column_count",
                    )
                    if key in raw
                }
                if hasattr(item, "text"):
                    raw["text_preview"] = str(item.text)[:160]
                result["targets"].append(raw)
                continue
            if isinstance(item, FigureIR):
                raw = {
                    key: raw.get(key)
                    for key in (
                        "figure_id", "page_index", "caption", "legend_text", "element_block_ids",
                        "bbox", "crop_artifact_id", "visual_type", "visual_status", "chart_spec",
                        "quality_flags", "target_type",
                    )
                }
                raw["coordinate_contract"] = self.figure_coordinate_contract(document, item)
            if "cells" in raw:
                raw["cells"] = [
                    {
                        key: cell.get(key)
                        for key in (
                            "cell_id", "row_index", "col_index", "text", "row_span", "col_span",
                            "is_header", "unit_hint", "source_cell_ids",
                        )
                    }
                    for cell in raw["cells"][:100]
                ]
            result["targets"].append(raw)
        return result

    @staticmethod
    def _page_context(document, page_index, targets, *, spread, compact):
        page = next((item for item in document.pages if item.page_index == page_index), None)
        if page is None:
            return None
        blocks = sorted((item for item in document.blocks if item.page_index == page_index), key=lambda item: item.order)
        if spread or compact:
            blocks = [item for item in blocks if item.block_id in targets or item.block_type == "heading"]
        block_limit = 16 if compact else 30 if spread else 60
        text_limit = 1200 if compact else 2500 if spread else 5000
        return {
            "page_id": page.page_id,
            "page_index": page.page_index,
            "printed_page_label": page.printed_page_label,
            "width": page.width,
            "height": page.height,
            "text": ParserFusion._visible_text(page.text)[:text_limit],
            "quality_flags": page.quality_flags,
            "block_inventory": [
                {
                    "block_id": block.block_id,
                    "order": block.order,
                    "block_type": block.block_type,
                    "text": block.text[:220],
                    "bbox": block.bbox.model_dump(mode="json") if block.bbox else None,
                    "table_id": block.table_id,
                    "figure_id": block.figure_id,
                }
                for block in blocks[:block_limit]
            ],
        }

    @staticmethod
    def spread_diagnostics(document: DocumentIR, spread: SpreadIR) -> dict[str, Any]:
        tables = [table for table in document.tables if table.table_id in set(spread.member_entity_ids)]
        left_page, right_page = spread.page_indices[:2]
        pairs = []
        for left in (table for table in tables if table.page_index == left_page):
            for right in (table for table in tables if table.page_index == right_page):
                mode = "horizontal_continue_last_column" if right.column_count == 1 and left.column_count >= 2 else "horizontal_append_columns"
                pairs.append({
                    "left_table_id": left.table_id,
                    "left_grid": f"{left.row_count}x{left.column_count}",
                    "right_table_id": right.table_id,
                    "right_grid": f"{right.row_count}x{right.column_count}",
                    "row_alignment": "compatible" if left.row_count == right.row_count else f"mismatch:{left.row_count}!={right.row_count}",
                    "composition_mode": mode,
                    "likely_composition_mode": mode,
                    "required_atomic_operations": [
                        "set_table_grid when a segment is malformed",
                        "confirm_spread",
                        "link_horizontal_continuation",
                    ],
                })
        return {"spread_id": spread.spread_id, "member_pages": spread.page_indices, "table_pairs": pairs}

    @staticmethod
    def figure_coordinate_contract(document: DocumentIR, figure: FigureIR) -> dict[str, Any]:
        artifact = next((item for item in document.artifacts if item.artifact_id == figure.crop_artifact_id), None)
        return {
            "canonical_space": "page_pdf_points",
            "target_bbox_page_points": figure.bbox.model_dump(mode="json") if figure.bbox else None,
            "preferred": "figure_normalized",
            "figure_normalized": {
                "unit": "normalized", "origin": "top_left", "range": [0.0, 1.0],
                "coordinate_system_id": f"{figure.figure_id}-normalized",
            },
            "crop_artifact_id": figure.crop_artifact_id,
            "crop_path": artifact.path if artifact else None,
            "crop_width_pixels": getattr(artifact, "width_pixels", None),
            "crop_height_pixels": getattr(artifact, "height_pixels", None),
            "crop_page_pixel_bbox": (
                artifact.page_pixel_bbox.model_dump(mode="json")
                if artifact and artifact.page_pixel_bbox
                else None
            ),
        }

    @staticmethod
    def _entity_id(item) -> str | None:
        return next((
            value for value in (
                getattr(item, "block_id", None), getattr(item, "table_id", None),
                getattr(item, "figure_id", None), getattr(item, "spread_id", None),
                getattr(item, "section_id", None),
            ) if value
        ), None)

    @staticmethod
    def _rules(review_kind: str) -> list[str]:
        rules = [
            "Judge only supplied images and candidate IR.",
            "Use only mutable_target_ids and registered operations.",
            "Return one scope_decision for every routed scope.",
            "Use local, evidence-bound patches; never rewrite a whole page or infer ESG facts.",
            "Abstain only when visual evidence cannot support a reliable decision.",
        ]
        if review_kind == "horizontal_page_spread":
            rules.append("Submit Spread classification, any page-local table repair, and cross-seam links together.")
        if review_kind == "table_structure_reconstruction":
            rules.append("Every complete grid must map source_cell_ids and list missing visible text and evidence.")
        if review_kind == "figure_semantic_structure":
            rules.append("Use compact upsert_chart_spec for charts; use upsert_figure_structure only for non-chart diagrams.")
        if review_kind == "page_text_coverage":
            rules.extend([
                "Identify the exact missing text region on the page; confirm text already represented.",
                "Repair page and block text only. Existing table and figure structures are context for this task and have separate object reviews.",
                "Keep data-source notes, baseline years, absolute values, intensity, and percentage changes in their visible text roles.",
            ])
        return rules
