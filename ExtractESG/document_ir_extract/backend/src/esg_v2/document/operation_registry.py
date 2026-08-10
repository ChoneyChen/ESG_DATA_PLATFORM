from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationSpec:
    name: str
    target_types: frozenset[str]
    risk_level: str
    payload_contract: str
    prompt_instruction: str
    guard_handler: str
    applier_handler: str


def _spec(
    name: str,
    targets: str,
    risk: str,
    payload: str,
    prompt: str,
) -> OperationSpec:
    return OperationSpec(
        name=name,
        target_types=frozenset(targets.split()),
        risk_level=risk,
        payload_contract=payload,
        prompt_instruction=prompt,
        guard_handler=f"guard:{name}",
        applier_handler=f"apply:{name}",
    )


OPERATION_SPECS = (
    _spec("confirm", "page spread block table figure cell section", "low", "null or concise evidence object", "Confirm only when the scoped candidate already matches the visible evidence."),
    _spec("confirm_spread", "spread", "low", "{reading_direction}", "Confirm only an information-bearing horizontal composition. Decorative continuity alone is not a semantic spread; use reject_spread unless a text-bearing entity or reading unit crosses the seam."),
    _spec("reject_spread", "spread", "low", "{reason?}", "Reject when pages are independent or their only cross-seam continuity is decorative and no information-bearing entity continues."),
    _spec("link_horizontal_continuation", "spread table block figure", "low", "{target_id, composition_mode?}", "Link only objects that visibly continue across the confirmed seam."),
    _spec("replace_block_text", "block", "medium", "string or {text}", "Replace block text only from readable visual evidence."),
    _spec("replace_cell_text", "cell", "medium", "string or {text}", "Replace one cell without changing unrelated cells."),
    _spec("correct_ocr_text", "block cell", "medium", "{text}", "Correct a localized OCR error and retain source evidence."),
    _spec("set_table_grid", "table", "high", "TableGridRepairProposal", "Return a complete final grid with source-cell mappings, missing-text accounting, and visual evidence."),
    _spec("insert_table_row", "table", "medium", "{row_index,cells}", "Insert only a visually present missing row."),
    _spec("set_bbox", "page block table figure cell", "medium", "BoundingBox", "Set a bounding box in the target coordinate system."),
    _spec("set_printed_page_label", "page", "low", "string", "Set only the page label visibly printed on the page."),
    _spec("set_visual_type", "figure", "low", "visual type string", "Classify the visible object without inventing semantics."),
    _spec("set_caption", "table figure", "low", "string", "Use only a caption visibly associated with the target."),
    _spec("link_continuation", "table block", "medium", "{target_id,axis}", "Link only a visible continuation."),
    _spec("merge_blocks", "block", "high", "{block_ids,text?}", "Merge blocks only when they are one visible reading unit."),
    _spec("split_block", "block", "high", "{segments}", "Split a block only at visible semantic boundaries."),
    _spec("add_quality_flags", "page spread block table figure cell", "low", "list[string]", "Add diagnostic flags; do not use them as a substitute for a repair."),
    _spec("retire_table_candidate", "table", "medium", "{disposition}", "Retire only a false-positive table candidate."),
    _spec("add_visual_text_block", "page figure", "medium", "{text,bbox,visual_role?}", "Add only visible text absent from existing blocks and cells."),
    _spec("set_figure_legend_text", "figure", "medium", "list[string]", "Transcribe visible legend labels exactly."),
    _spec("upsert_figure_structure", "figure", "high", "figure structure object", "Model a non-chart visual using bounded visible nodes and relations."),
    _spec("upsert_chart_spec", "figure", "high", "ChartSpec", "Return a compact chart specification with visible categories, series, values, units, and evidence refs."),
    _spec("bind_block_to_figure", "block", "medium", "{figure_id,visual_role?}", "Bind an existing block instead of duplicating its text."),
    # Compatibility operations are readable for old revisions but are never planned for new work.
    _spec("replace_text", "page block cell", "high", "legacy", "Legacy compatibility operation."),
    _spec("replace_table", "table", "high", "legacy", "Legacy compatibility operation."),
)

PATCH_OPERATION_NAMES = tuple(spec.name for spec in OPERATION_SPECS)


class OperationRegistry:
    """Runtime source of truth for review operations and their contracts."""

    _by_name = {spec.name: spec for spec in OPERATION_SPECS}

    REVIEW_KIND_OPERATIONS = {
        "horizontal_page_spread": (
            "confirm_spread", "reject_spread", "link_horizontal_continuation", "confirm",
            "replace_block_text", "replace_cell_text", "correct_ocr_text", "set_table_grid",
            "insert_table_row", "add_visual_text_block", "bind_block_to_figure",
            "set_figure_legend_text", "set_visual_type", "set_bbox", "set_caption",
            "upsert_chart_spec", "upsert_figure_structure", "merge_blocks", "split_block",
            "add_quality_flags",
        ),
        "table_candidate_classification": (
            "confirm", "retire_table_candidate", "replace_cell_text", "set_table_grid",
            "insert_table_row",
        ),
        "table_structure_reconstruction": (
            "confirm", "replace_cell_text", "set_table_grid", "insert_table_row",
            "retire_table_candidate",
        ),
        "figure_binding": (
            "confirm", "set_bbox", "set_visual_type", "set_caption",
            "set_figure_legend_text", "bind_block_to_figure",
        ),
        "figure_semantic_structure": (
            "confirm", "set_visual_type", "set_caption", "set_figure_legend_text",
            "upsert_chart_spec", "upsert_figure_structure", "bind_block_to_figure", "set_bbox",
        ),
        "page_text_coverage": (
            "confirm", "replace_block_text", "replace_cell_text", "correct_ocr_text",
            "set_bbox", "add_visual_text_block", "bind_block_to_figure", "set_table_grid",
            "insert_table_row", "set_caption", "set_figure_legend_text",
            "upsert_chart_spec", "upsert_figure_structure",
        ),
        "generic_document_ir_review": (
            "confirm", "replace_block_text", "set_bbox", "add_quality_flags",
        ),
    }

    @classmethod
    def get(cls, name: str) -> OperationSpec:
        try:
            return cls._by_name[name]
        except KeyError as exc:
            raise ValueError(f"Unknown review operation: {name}") from exc

    @classmethod
    def for_review_kind(cls, review_kind: str) -> list[str]:
        operations = cls.REVIEW_KIND_OPERATIONS.get(review_kind)
        if operations is None:
            raise ValueError(f"Unknown review kind: {review_kind}")
        return list(operations)

    @classmethod
    def risk_level(cls, name: str) -> str:
        return cls.get(name).risk_level

    @classmethod
    def supports_target(cls, name: str, target_type: str) -> bool:
        return target_type in cls.get(name).target_types

    @classmethod
    def prompt_fragment(cls, names: list[str]) -> list[dict[str, object]]:
        return [
            {
                "operation": spec.name,
                "target_types": sorted(spec.target_types),
                "payload_contract": spec.payload_contract,
                "risk_level": spec.risk_level,
                "guard_handler": spec.guard_handler,
                "applier_handler": spec.applier_handler,
                "instruction": spec.prompt_instruction,
            }
            for spec in (cls.get(name) for name in names)
        ]
