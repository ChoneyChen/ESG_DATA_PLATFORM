from __future__ import annotations

from esg_v2.document.contracts import (
    ArtifactRef,
    AtomicPatch,
    BlockIR,
    BoundingBox,
    CellIR,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    PageIR,
    SourceTrace,
    TableIR,
)
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_v2.document.structure_reconstruction import StructureReconstructor


def _table_document() -> DocumentIR:
    trace = SourceTrace(parser="paddleocr-vl")
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=2,
        column_count=2,
        cells=[
            CellIR(cell_id="c00", table_id="table-1", page_index=0, row_index=0, col_index=0, text="A", source_trace=trace),
            CellIR(cell_id="c01", table_id="table-1", page_index=0, row_index=0, col_index=1, text="B", source_trace=trace),
            CellIR(cell_id="c10", table_id="table-1", page_index=0, row_index=1, col_index=0, text="C", source_trace=trace),
            CellIR(cell_id="c11", table_id="table-1", page_index=0, row_index=1, col_index=1, text="D", source_trace=trace),
        ],
        source_trace=trace,
    )
    return DocumentIR(metadata=DocumentIRMetadata(run_id="ir", ocr_run_id="ocr"), tables=[table])


def _visual_document() -> DocumentIR:
    trace = SourceTrace(parser="paddleocr-vl", artifact_path="page.png")
    page = PageIR(
        page_id="page-1",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        text="77",
        text_length=2,
        block_ids=["block-1"],
        figure_ids=["figure-1"],
        source_trace=trace,
    )
    block = BlockIR(
        block_id="block-1",
        page_index=0,
        order=0,
        block_type="footer",
        text="77",
        bbox=BoundingBox(x0=20, y0=700, x1=60, y1=730, unit="points"),
        source_trace=trace,
    )
    figure = FigureIR(
        figure_id="figure-1",
        page_index=0,
        order=0,
        bbox=BoundingBox(x0=10, y0=680, x1=200, y1=760, unit="points"),
        quality_flags=["image_binding_unresolved"],
        source_trace=trace,
    )
    return DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-visual", ocr_run_id="ocr-visual"),
        artifacts=[ArtifactRef(artifact_id="artifact-page", kind="page_image", path="page.png", page_index=0)],
        pages=[page],
        blocks=[block],
        figures=[figure],
    )


def test_insert_table_row_is_locally_compiled_without_rewriting_existing_cells() -> None:
    document = _table_document()
    patch = AtomicPatch(
        patch_id="patch-1",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="insert_table_row",
        proposed_value={
            "insert_before_row_index": 0,
            "cells": [{"col_index": 0, "text": "Header", "col_span": 2, "is_header": True}],
        },
        evidence_refs=["page.png"],
    )
    guard = PatchGuard().evaluate(
        document,
        "task-1",
        [patch],
        allowed_target_ids={"table-1"},
        required_target_ids={"table-1"},
    )
    assert guard.passed is True
    DocumentPatchApplier().apply(document, [patch])
    table = document.tables[0]
    assert table.row_count == 3
    assert [(cell.row_index, cell.col_index, cell.text) for cell in table.cells] == [
        (0, 0, "Header"),
        (1, 0, "A"),
        (1, 1, "B"),
        (2, 0, "C"),
        (2, 1, "D"),
    ]
    assert [cell.cell_id for cell in table.cells] == [
        "cell-p0001-t0001-r0001-c0001",
        "cell-p0001-t0001-r0002-c0001",
        "cell-p0001-t0001-r0002-c0002",
        "cell-p0001-t0001-r0003-c0001",
        "cell-p0001-t0001-r0003-c0002",
    ]
    assert "colspan=\"2\"" in table.markdown


def test_retiring_non_table_visual_preserves_snapshot_and_materializes_figure() -> None:
    document = _table_document()
    table = document.tables[0]
    table.bbox = BoundingBox(x0=20, y0=30, x1=180, y1=140, unit="points")
    table.quality_flags.append("local_only_table_candidate")
    document.pages = [
        PageIR(
            page_id="page-1",
            page_index=0,
            page_number=1,
            width=600,
            height=800,
            table_ids=[table.table_id],
            source_trace=table.source_trace,
        )
    ]
    patch = AtomicPatch(
        patch_id="patch-retire",
        source_task_id="task-1",
        target_type="table",
        target_id=table.table_id,
        operation="retire_table_candidate",
        proposed_value={"disposition": "non_table_visual"},
        evidence_refs=["page.png"],
        rationale="The region is an information graphic, not a table.",
    )

    guard = PatchGuard().evaluate(
        document,
        "task-1",
        [patch],
        allowed_target_ids={table.table_id},
        required_target_ids={table.table_id},
        allowed_operations={"retire_table_candidate"},
    )
    assert guard.passed is True

    DocumentPatchApplier().apply(document, [patch])

    assert document.tables == []
    assert document.pages[0].table_ids == []
    assert document.retired_entities[0].snapshot["table_id"] == "table-1"
    assert document.figures[0].bbox == table.bbox
    assert document.pages[0].figure_ids == [document.figures[0].figure_id]


def test_set_table_grid_rejects_declared_rows_without_cell_coverage() -> None:
    document = _table_document()
    patch = AtomicPatch(
        patch_id="patch-2",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="set_table_grid",
        proposed_value={
            "row_count": 3,
            "column_count": 2,
            "cells": [
                {"row_index": 0, "col_index": 0, "text": "A"},
                {"row_index": 0, "col_index": 1, "text": "B"},
                {"row_index": 1, "col_index": 0, "text": "C"},
                {"row_index": 1, "col_index": 1, "text": "D"},
            ],
        },
        evidence_refs=["page.png"],
    )
    guard = PatchGuard().evaluate(document, "task-1", [patch])
    assert guard.passed is False
    assert any(check.code.endswith("table_cell_positions") and not check.passed for check in guard.checks)


def test_set_table_grid_allows_evidence_backed_expansion_when_source_text_is_retained() -> None:
    document = _table_document()
    patch = AtomicPatch(
        patch_id="patch-expanded-grid",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="set_table_grid",
        proposed_value={
            "row_count": 3,
            "column_count": 2,
            "cells": [
                {"row_index": 0, "col_index": 0, "text": "A"},
                {"row_index": 0, "col_index": 1, "text": "B"},
                {"row_index": 1, "col_index": 0, "text": "C"},
                {"row_index": 1, "col_index": 1, "text": "D"},
                {"cell_id": "model-owned-id-must-be-ignored", "row_index": 2, "col_index": 0, "text": "New visual row"},
                {"row_index": 2, "col_index": 1, "text": "Additional evidence"},
            ],
        },
        evidence_refs=["page.png"],
    )

    guard = PatchGuard().evaluate(document, "task-1", [patch])

    assert guard.passed is True
    retention = next(check for check in guard.checks if check.code.endswith("table_text_retention"))
    assert retention.passed is True
    assert retention.message.endswith("1.000.")
    DocumentPatchApplier().apply(document, [patch])
    assert document.tables[0].cells[-2].cell_id == "cell-p0001-t0001-r0003-c0001"


def test_visual_ocr_correction_can_replace_wrong_numeric_token_without_relaxing_generic_guard() -> None:
    document = _visual_document()
    strict_patch = AtomicPatch(
        patch_id="patch-strict-text",
        source_task_id="task-1",
        target_type="block",
        target_id="block-1",
        operation="replace_block_text",
        before_value="77",
        proposed_value="117",
        evidence_refs=["page.png"],
        confidence=0.97,
    )
    strict_guard = PatchGuard().evaluate(document, "task-1", [strict_patch])
    assert strict_guard.passed is False
    assert any(
        check.code.endswith("numeric_tokens_preserved") and not check.passed
        for check in strict_guard.checks
    )

    correction = strict_patch.model_copy(
        update={"patch_id": "patch-ocr-correction", "operation": "correct_ocr_text"}
    )
    correction_guard = PatchGuard().evaluate(
        document,
        "task-1",
        [correction],
        allowed_target_ids={"block-1"},
        required_target_ids={"block-1"},
        allowed_operations={"correct_ocr_text"},
    )
    assert correction_guard.passed is True
    assert any(
        check.code.endswith("ocr_numeric_substitution_audited")
        and check.severity == "warning"
        for check in correction_guard.checks
    )
    DocumentPatchApplier().apply(document, [correction])
    assert document.blocks[0].text == "117"
    assert document.pages[0].text == "117"


def test_existing_visual_block_can_be_bound_to_figure_without_duplication() -> None:
    document = _visual_document()
    patch = AtomicPatch(
        patch_id="patch-bind-figure",
        source_task_id="task-1",
        target_type="block",
        target_id="block-1",
        operation="bind_block_to_figure",
        before_value={"figure_id": None, "visual_role": None},
        proposed_value={
            "figure_id": "figure-1",
            "relation": "element",
            "visual_role": "value",
        },
        evidence_refs=["page.png"],
        confidence=0.94,
    )
    guard = PatchGuard().evaluate(
        document,
        "task-1",
        [patch],
        allowed_target_ids={"block-1"},
        required_target_ids={"figure-1"},
        allowed_operations={"bind_block_to_figure"},
    )
    assert guard.passed is True
    DocumentPatchApplier().apply(document, [patch])
    assert document.blocks[0].figure_id == "figure-1"
    assert document.blocks[0].visual_role == "value"
    assert document.figures[0].element_block_ids == ["block-1"]
    assert document.figures[0].visual_status == "reviewed"
    assert "image_binding_unresolved" not in document.figures[0].quality_flags


def test_set_table_grid_inherits_existing_cell_geometry() -> None:
    document = _table_document()
    for index, cell in enumerate(document.tables[0].cells):
        cell.bbox = BoundingBox(
            x0=(index % 2) * 100,
            y0=(index // 2) * 40,
            x1=(index % 2 + 1) * 100,
            y1=(index // 2 + 1) * 40,
            unit="points",
            coordinate_system_id="page-0001-pdf-points",
        )
    patch = AtomicPatch(
        patch_id="patch-preserve-cell-geometry",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="set_table_grid",
        proposed_value={
            "row_count": 2,
            "column_count": 2,
            "cells": [
                {"row_index": 0, "col_index": 0, "text": "A"},
                {"row_index": 0, "col_index": 1, "text": "B"},
                {"row_index": 1, "col_index": 0, "text": "C"},
                {"row_index": 1, "col_index": 1, "text": "D corrected"},
            ],
        },
        evidence_refs=["page.png"],
    )

    guard = PatchGuard().evaluate(document, "task-1", [patch])
    DocumentPatchApplier().apply(document, [patch])

    assert guard.passed is True
    assert all(cell.bbox is not None for cell in document.tables[0].cells)
    assert "cell_geometry_preserved_after_agent_patch" in document.tables[0].quality_flags
    assert all(
        "cell_geometry_inherited_by_position" in cell.quality_flags
        for cell in document.tables[0].cells
    )


def test_table_guard_ignores_repeated_digit_ocr_noise_but_preserves_real_codes() -> None:
    document = _table_document()
    document.tables[0].row_count = 1
    document.tables[0].column_count = 2
    document.tables[0].cells = [
        CellIR(
            cell_id="c00",
            table_id="table-1",
            page_index=0,
            row_index=0,
            col_index=0,
            text="22002244--22002255 SEHK:0123",
            source_trace=SourceTrace(parser="pdfplumber"),
        ),
        CellIR(
            cell_id="c01",
            table_id="table-1",
            page_index=0,
            row_index=0,
            col_index=1,
            text="ESG分數：49",
            source_trace=SourceTrace(parser="pdfplumber"),
        ),
    ]
    base = {
        "row_count": 1,
        "column_count": 2,
        "cells": [
            {"row_index": 0, "col_index": 0, "text": "SEHK:0123"},
            {"row_index": 0, "col_index": 1, "text": "ESG分數：49"},
        ],
    }
    accepted = AtomicPatch(
        patch_id="patch-remove-noise",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="set_table_grid",
        proposed_value=base,
        evidence_refs=["page.png"],
    )
    rejected = accepted.model_copy(deep=True)
    rejected.patch_id = "patch-drop-real-code"
    rejected.proposed_value["cells"][0]["text"] = ""

    accepted_guard = PatchGuard().evaluate(document, "task-1", [accepted])
    rejected_guard = PatchGuard().evaluate(document, "task-1", [rejected])

    assert accepted_guard.passed is True
    assert any(check.code.endswith("suspected_ocr_noise") for check in accepted_guard.checks)
    assert rejected_guard.passed is False
    assert any(
        check.code.endswith("table_numeric_tokens_preserved")
        and not check.passed
        and "0123" in check.message
        for check in rejected_guard.checks
    )


def test_set_table_grid_rejects_expansion_that_drops_source_text() -> None:
    document = _table_document()
    patch = AtomicPatch(
        patch_id="patch-destructive-grid",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="set_table_grid",
        proposed_value={
            "row_count": 2,
            "column_count": 2,
            "cells": [
                {"row_index": 0, "col_index": 0, "text": "A"},
                {"row_index": 0, "col_index": 1, "text": "B"},
                {"row_index": 1, "col_index": 0, "text": ""},
                {"row_index": 1, "col_index": 1, "text": ""},
            ],
        },
        evidence_refs=["page.png"],
    )

    guard = PatchGuard().evaluate(document, "task-1", [patch])

    assert guard.passed is False
    assert any(check.code.endswith("table_text_retention") and not check.passed for check in guard.checks)


def test_source_text_retention_compares_visible_html_content() -> None:
    source = "<table><tr><td>ESG score</td><td>49</td></tr></table>"
    proposed = "ESG score 49"

    assert PatchGuard._source_text_retention(source, proposed) == 1.0


def test_figure_structure_patch_is_localized_and_survives_structure_rebuild() -> None:
    trace = SourceTrace(parser="paddleocr-vl", artifact_ids=["artifact-page-image-p0001"])
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir", ocr_run_id="ocr"),
        artifacts=[
            ArtifactRef(
                artifact_id="artifact-page-image-p0001",
                kind="page_image",
                path="page.png",
                page_index=0,
            )
        ],
        pages=[
            PageIR(
                page_id="page-0001",
                page_index=0,
                page_number=1,
                width=600,
                height=800,
                figure_ids=["figure-p0001-0001"],
                source_trace=trace,
            )
        ],
        figures=[
            FigureIR(
                figure_id="figure-p0001-0001",
                page_index=0,
                order=0,
                visual_type="diagram",
                bbox=BoundingBox(x0=100, y0=100, x1=500, y1=500, unit="points"),
                source_trace=trace,
            )
        ],
    )
    patch = AtomicPatch(
        patch_id="patch-figure-structure",
        source_task_id="task-1",
        target_type="figure",
        target_id="figure-p0001-0001",
        operation="upsert_figure_structure",
        proposed_value={
            "elements": [
                {
                    "element_id": "a",
                    "text": "Board oversight",
                    "visual_role": "node_title",
                    "bbox": {"x0": 120, "y0": 130, "x1": 260, "y1": 180, "unit": "points"},
                },
                {
                    "element_id": "b",
                    "text": "Climate committee",
                    "visual_role": "node_title",
                    "bbox": {"x0": 300, "y0": 250, "x1": 460, "y1": 300, "unit": "points"},
                },
            ],
            "relations": [
                {
                    "source_element_id": "a",
                    "target_element_id": "b",
                    "relation": "visual_flow_to",
                    "confidence": 0.94,
                }
            ],
        },
        evidence_refs=["page.png"],
        confidence=0.94,
    )

    guard = PatchGuard().evaluate(
        document,
        "task-1",
        [patch],
        allowed_target_ids={"figure-p0001-0001"},
        allowed_operations={"upsert_figure_structure"},
    )

    assert guard.passed is True
    DocumentPatchApplier().apply(document, [patch])
    assert len(document.figures[0].element_block_ids) == 2
    assert any(edge.relation == "visual_flow_to" for edge in document.structure_edges)
    StructureReconstructor().reconstruct(document)
    assert any(edge.relation == "visual_flow_to" for edge in document.structure_edges)


def test_figure_structure_patch_rejects_element_outside_figure() -> None:
    trace = SourceTrace(parser="paddleocr-vl")
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir", ocr_run_id="ocr"),
        artifacts=[ArtifactRef(artifact_id="artifact-page-image-p0001", kind="page_image", path="page.png")],
        pages=[PageIR(page_id="page-0001", page_index=0, page_number=1, source_trace=trace)],
        figures=[
            FigureIR(
                figure_id="figure-p0001-0001",
                page_index=0,
                order=0,
                bbox=BoundingBox(x0=100, y0=100, x1=200, y1=200, unit="points"),
                source_trace=trace,
            )
        ],
    )
    patch = AtomicPatch(
        patch_id="patch-outside",
        source_task_id="task-1",
        target_type="figure",
        target_id="figure-p0001-0001",
        operation="upsert_figure_structure",
        proposed_value={
            "elements": [
                {
                    "element_id": "outside",
                    "text": "Unsupported",
                    "visual_role": "label",
                    "bbox": {"x0": 300, "y0": 300, "x1": 400, "y1": 340, "unit": "points"},
                }
            ],
            "relations": [],
        },
        evidence_refs=["page.png"],
    )

    guard = PatchGuard().evaluate(document, "task-1", [patch])

    assert guard.passed is False
    assert any(
        check.code.endswith("figure_structure_elements") and not check.passed
        for check in guard.checks
    )

def test_illustration_is_a_supported_visual_type() -> None:
    trace = SourceTrace(parser="paddleocr-vl")
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir", ocr_run_id="ocr"),
        figures=[FigureIR(figure_id="figure-1", page_index=0, order=0, source_trace=trace)],
    )
    patch = AtomicPatch(
        patch_id="patch-3",
        source_task_id="task-1",
        target_type="figure",
        target_id="figure-1",
        operation="set_visual_type",
        before_value="unknown",
        proposed_value="illustration",
        evidence_refs=["page.png"],
    )
    guard = PatchGuard().evaluate(document, "task-1", [patch], allowed_target_ids={"figure-1"})
    assert guard.passed is True


def test_empty_caption_is_rejected() -> None:
    trace = SourceTrace(parser="paddleocr-vl")
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir", ocr_run_id="ocr"),
        figures=[FigureIR(figure_id="figure-1", page_index=0, order=0, source_trace=trace)],
    )
    patch = AtomicPatch(
        patch_id="patch-4",
        source_task_id="task-1",
        target_type="figure",
        target_id="figure-1",
        operation="set_caption",
        before_value=None,
        proposed_value="",
        evidence_refs=["page.png"],
    )
    guard = PatchGuard().evaluate(document, "task-1", [patch])
    assert guard.passed is False
    assert any(check.code.endswith("caption_nonempty") and not check.passed for check in guard.checks)


def test_declared_table_patch_cannot_target_a_block() -> None:
    trace = SourceTrace(parser="paddleocr-vl")
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir", ocr_run_id="ocr"),
        blocks=[
            BlockIR(
                block_id="block-1",
                page_index=0,
                order=0,
                block_type="paragraph",
                text="visible text",
                source_trace=trace,
            )
        ],
    )
    patch = AtomicPatch(
        patch_id="patch-wrong-type",
        source_task_id="task-1",
        target_type="table",
        target_id="block-1",
        operation="set_table_grid",
        proposed_value={
            "row_count": 1,
            "column_count": 1,
            "cells": [{"row_index": 0, "col_index": 0, "text": "visible text"}],
        },
        evidence_refs=["page.png"],
    )

    guard = PatchGuard().evaluate(document, "task-1", [patch], allowed_target_ids={"block-1"})

    assert guard.passed is False
    assert any(check.code.endswith("target_type_matches_entity") and not check.passed for check in guard.checks)


def test_malformed_table_payload_is_a_guard_failure_not_an_exception() -> None:
    document = _table_document()
    patch = AtomicPatch(
        patch_id="patch-malformed-grid",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="set_table_grid",
        proposed_value={"row_count": "not-a-number", "column_count": 2, "cells": []},
        evidence_refs=["page.png"],
    )

    guard = PatchGuard().evaluate(document, "task-1", [patch])

    assert guard.passed is False
    assert any(check.code.endswith("table_grid_schema") and not check.passed for check in guard.checks)


def test_table_reference_on_wrapper_block_does_not_shadow_table_identity() -> None:
    document = _table_document()
    document.blocks.append(
        BlockIR(
            block_id="block-table-wrapper",
            page_index=0,
            order=0,
            block_type="table_markdown",
            text="A B C D",
            table_id="table-1",
            source_trace=SourceTrace(parser="paddleocr-vl"),
        )
    )
    patch = AtomicPatch(
        patch_id="patch-table-identity",
        source_task_id="task-1",
        target_type="table",
        target_id="table-1",
        operation="set_table_grid",
        proposed_value={
            "row_count": 2,
            "column_count": 2,
            "cells": [
                {"row_index": 0, "col_index": 0, "text": "A"},
                {"row_index": 0, "col_index": 1, "text": "B"},
                {"row_index": 1, "col_index": 0, "text": "C"},
                {"row_index": 1, "col_index": 1, "text": "D"},
            ],
        },
        evidence_refs=["page.png"],
    )

    guard = PatchGuard().evaluate(document, "task-1", [patch], allowed_target_ids={"table-1"})

    assert PatchGuard._target(document, "table-1") is document.tables[0]
    assert guard.passed is True
    assert any(check.code == "atomic_batch_application" and check.passed for check in guard.checks)
