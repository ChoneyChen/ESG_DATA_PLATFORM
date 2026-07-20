from __future__ import annotations

from esg_v2.document.contracts import (
    AtomicPatch,
    BlockIR,
    CellIR,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    SourceTrace,
    TableIR,
)
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard


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
    assert "colspan=\"2\"" in table.markdown


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
