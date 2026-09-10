from __future__ import annotations

from pathlib import Path

from PIL import Image

from esg_v2.document.contracts import (
    ArtifactRef,
    AtomicPatch,
    BoundingBox,
    CellIR,
    CoordinateSystem,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    LayoutObjectIR,
    LocalPageForensics,
    LocalPdfForensics,
    PageIR,
    SourceTrace,
    SpreadIR,
    TableIR,
    VlmReviewTask,
)
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_v2.document.quality_router import OcrQualityRouter
from esg_v2.document.review_policy import ReviewPolicyEngine
from esg_v2.document.spread_builder import HorizontalSpreadBuilder
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.utils.hashing import sha256_file


def _spread_document(tmp_path: Path) -> DocumentIR:
    trace = SourceTrace(parser="test", artifact_ids=["artifact-page-image-p0001"])
    page_images = []
    artifacts = []
    systems = []
    pages = []
    for index, label in enumerate(("10", "11")):
        image_path = tmp_path / f"page-{index + 1:04d}.png"
        image = Image.new("RGB", (200, 100), "white")
        for y in range(12, 90):
            for x in (range(190, 200) if index == 0 else range(0, 10)):
                shade = 25 if y % 9 in {0, 1} else 90
                image.putpixel((x, y), (shade, shade, shade))
        image.save(image_path)
        page_images.append(image_path)
        artifact_id = f"artifact-page-image-p{index + 1:04d}"
        artifacts.append(
            ArtifactRef(
                artifact_id=artifact_id,
                kind="page_image",
                path=str(image_path),
                media_type="image/png",
                page_index=index,
                sha256=sha256_file(image_path),
            )
        )
        systems.append(
            CoordinateSystem(
                coordinate_system_id=f"page-{index + 1:04d}-render-144dpi",
                page_index=index,
                name="rendered_page_pixels",
                width=200,
                height=100,
                unit="pixels",
                dpi=144,
            )
        )
        pages.append(
            PageIR(
                page_id=f"page-{index + 1:04d}",
                page_index=index,
                page_number=index + 1,
                printed_page_label=label,
                width=100,
                height=50,
                page_image_path=str(image_path),
                text="Material table content is represented on this physical page.",
                text_length=60,
                coordinate_system_ids=[systems[-1].coordinate_system_id],
                table_ids=[f"table-p{index + 1:04d}-0001"],
                source_trace=SourceTrace(parser="test", artifact_ids=[artifact_id]),
            )
        )
    layouts = [
        LayoutObjectIR(
            layout_object_id="layout-p0001-0001",
            page_index=0,
            order=0,
            label="table",
            bbox=BoundingBox(x0=30, y0=8, x1=100, y1=45, unit="points"),
            table_id="table-p0001-0001",
            source_trace=trace,
        ),
        LayoutObjectIR(
            layout_object_id="layout-p0002-0001",
            page_index=1,
            order=0,
            label="table",
            bbox=BoundingBox(x0=0, y0=9, x1=70, y1=46, unit="points"),
            table_id="table-p0002-0001",
            source_trace=trace,
        ),
    ]
    tables = [
        TableIR(
            table_id="table-p0001-0001",
            page_index=0,
            page_indices=[0],
            order=0,
            row_count=1,
            column_count=1,
            cells=[
                CellIR(
                    cell_id="cell-p0001-t0001-r0001-c0001",
                    table_id="table-p0001-0001",
                    page_index=0,
                    row_index=0,
                    col_index=0,
                    text="Left",
                    source_trace=trace,
                )
            ],
            bbox=layouts[0].bbox,
            source_trace=trace,
        ),
        TableIR(
            table_id="table-p0002-0001",
            page_index=1,
            page_indices=[1],
            order=0,
            row_count=1,
            column_count=1,
            cells=[
                CellIR(
                    cell_id="cell-p0002-t0001-r0001-c0001",
                    table_id="table-p0002-0001",
                    page_index=1,
                    row_index=0,
                    col_index=0,
                    text="Right",
                    source_trace=trace,
                )
            ],
            bbox=layouts[1].bbox,
            source_trace=trace,
        ),
    ]
    return DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-spread", ocr_run_id="ocr-spread"),
        artifacts=artifacts,
        coordinate_systems=systems,
        pages=pages,
        layout_objects=layouts,
        tables=tables,
    )


def test_horizontal_spread_builder_creates_composite_and_single_pair_review(tmp_path: Path) -> None:
    document = HorizontalSpreadBuilder().build(_spread_document(tmp_path), tmp_path)

    assert len(document.spreads) == 1
    spread = document.spreads[0]
    assert spread.spread_id == "spread-p0001-p0002"
    assert spread.page_indices == [0, 1]
    assert spread.status == "candidate"
    artifact = next(item for item in document.artifacts if item.artifact_id == spread.composite_artifact_id)
    assert Path(artifact.path).is_file()
    with Image.open(artifact.path) as image:
        assert image.size == (400, 100)

    routed = OcrQualityRouter().route(document).document
    spread_tasks = [task for task in routed.review_tasks if task.target_type == "spread"]
    assert len(spread_tasks) == 1
    assert spread_tasks[0].review_plan.review_kind == "horizontal_page_spread"
    assert spread_tasks[0].blocking is False
    assert spread.classification == "content_crossing"
    assert spread.content_dependency is True
    assert spread.requires_detailed_review is True
    assert all(task.target_type == "spread" for task in routed.review_tasks)


def test_same_color_background_does_not_create_spread_candidate(tmp_path: Path) -> None:
    document = _spread_document(tmp_path)
    for page in document.pages:
        Image.new("RGB", (200, 100), (224, 224, 224)).save(page.page_image_path)

    document = HorizontalSpreadBuilder().build(document, tmp_path)

    assert document.spreads == []


def test_candidate_overlapping_confirmed_spread_is_rejected_before_model_call(tmp_path: Path) -> None:
    document = _spread_document(tmp_path)
    trace = SourceTrace(parser="test")
    document.spreads = [
        SpreadIR(
            spread_id="spread-p0001-p0002",
            page_ids=["page-0001", "page-0002"],
            page_indices=[0, 1],
            status="confirmed",
            composite_artifact_id="artifact-confirmed",
            source_trace=trace,
        ),
        SpreadIR(
            spread_id="spread-p0002-p0003",
            page_ids=["page-0002", "page-0003"],
            page_indices=[1, 2],
            status="candidate",
            composite_artifact_id="artifact-candidate",
            source_trace=trace,
        ),
    ]
    task = VlmReviewTask(
        task_id="review-overlap",
        task_type="horizontal_spread_review",
        target_type="spread",
        target_id="spread-p0002-p0003",
        page_index=1,
        prompt_intent="review spread",
        input_refs=["artifact-candidate"],
    )

    result = ReviewPolicyEngine().deterministic_proposal(document, task)

    assert result is not None
    assert result.proposal.operation == "reject_spread"
    assert "membership is unique" in result.reason


def test_visual_only_spread_is_locally_resolved_without_model_task(tmp_path: Path) -> None:
    document = _spread_document(tmp_path)
    document.tables = []
    for page in document.pages:
        page.table_ids = []
    figures = []
    for index, layout in enumerate(document.layout_objects):
        figure_id = f"figure-p{index + 1:04d}-0001"
        layout.label = "image"
        layout.table_id = None
        layout.figure_id = figure_id
        document.pages[index].figure_ids = [figure_id]
        figures.append(
            FigureIR(
                figure_id=figure_id,
                page_index=index,
                order=0,
                bbox=layout.bbox,
                visual_type="unknown",
                source_trace=layout.source_trace,
            )
        )
    document.figures = figures

    document = HorizontalSpreadBuilder().build(document, tmp_path)
    routed = OcrQualityRouter().route(document).document
    spread = routed.spreads[0]

    assert spread.status == "visual_continuity"
    assert spread.classification == "visual_continuity"
    assert spread.content_dependency is False
    assert spread.requires_detailed_review is False
    assert not [task for task in routed.review_tasks if task.target_type == "spread"]
    assert routed.quality_report["horizontal_spread_local_resolved_count"] == 1


def test_spread_guard_confirms_pair_and_links_opposite_page_tables(tmp_path: Path) -> None:
    document = HorizontalSpreadBuilder().build(_spread_document(tmp_path), tmp_path)
    spread = document.spreads[0]
    patches = [
        AtomicPatch(
            patch_id="patch-000001",
            source_task_id="review-000001",
            target_type="spread",
            target_id=spread.spread_id,
            operation="confirm_spread",
            proposed_value={"reading_direction": "left_to_right"},
            evidence_refs=[next(item.path for item in document.artifacts if item.kind == "spread_image")],
            confidence=0.98,
        ),
        AtomicPatch(
            patch_id="patch-000002",
            source_task_id="review-000001",
            target_type="spread",
            target_id=spread.spread_id,
            operation="link_horizontal_continuation",
            proposed_value={
                "links": [
                    {
                        "source_id": "table-p0001-0001",
                        "target_id": "table-p0002-0001",
                        "confidence": 0.96,
                    }
                ]
            },
            evidence_refs=[next(item.path for item in document.artifacts if item.kind == "spread_image")],
            confidence=0.96,
        ),
    ]

    guard = PatchGuard().evaluate(
        document,
        "review-000001",
        patches,
        allowed_target_ids={spread.spread_id},
        required_target_ids={spread.spread_id},
        allowed_operations={"confirm_spread", "link_horizontal_continuation"},
        required_any_operations={"confirm_spread", "reject_spread"},
    )

    assert guard.passed is True
    DocumentPatchApplier().apply(document, patches)
    assert spread.status == "confirmed"
    assert document.tables[0].continuation_axis == "horizontal"
    assert document.tables[0].continues_to_table_id == document.tables[1].table_id
    assert any(edge.relation == "horizontal_continuation" for edge in document.structure_edges)
    assert len(document.logical_tables) == 1
    logical = document.logical_tables[0]
    assert logical.composition_axis == "horizontal"
    assert logical.source_table_ids == ["table-p0001-0001", "table-p0002-0001"]
    assert (logical.logical_row_count, logical.logical_column_count) == (1, 2)
    assert [
        (mapping.logical_row_index, mapping.logical_col_index)
        for mapping in logical.cell_mappings
    ] == [(0, 0), (0, 1)]


def test_horizontal_last_column_continuation_requires_row_alignment_and_builds_logical_cells(
    tmp_path: Path,
) -> None:
    document = HorizontalSpreadBuilder().build(_spread_document(tmp_path), tmp_path)
    left, right = document.tables
    trace = left.source_trace
    left.row_count = 2
    left.column_count = 2
    left.cells = [
        CellIR(
            cell_id=f"cell-p0001-t0001-r{row + 1:04d}-c{col + 1:04d}",
            table_id=left.table_id,
            page_index=0,
            row_index=row,
            col_index=col,
            text=text,
            is_header=row == 0,
            source_trace=trace,
        )
        for row, values in enumerate((("Risk", "Response"), ("Policy", "Left half")))
        for col, text in enumerate(values)
    ]
    right.cells[0].text = "Right header Right half"
    spread = document.spreads[0]
    base_patches = [
        AtomicPatch(
            patch_id="patch-000001",
            source_task_id="review-000001",
            target_type="spread",
            target_id=spread.spread_id,
            operation="confirm_spread",
            proposed_value={"reading_direction": "left_to_right"},
            evidence_refs=[next(item.path for item in document.artifacts if item.kind == "spread_image")],
            confidence=0.98,
        ),
        AtomicPatch(
            patch_id="patch-000002",
            source_task_id="review-000001",
            target_type="spread",
            target_id=spread.spread_id,
            operation="link_horizontal_continuation",
            proposed_value={
                "links": [
                    {
                        "source_id": left.table_id,
                        "target_id": right.table_id,
                        "confidence": 0.96,
                    }
                ]
            },
            evidence_refs=[next(item.path for item in document.artifacts if item.kind == "spread_image")],
            confidence=0.96,
        ),
    ]

    unresolved = PatchGuard().evaluate(
        document,
        "review-000001",
        base_patches,
        allowed_target_ids={spread.spread_id, right.table_id},
        required_target_ids={spread.spread_id},
        allowed_operations={"confirm_spread", "link_horizontal_continuation", "set_table_grid"},
        required_any_operations={"confirm_spread", "reject_spread"},
    )
    assert unresolved.passed is False
    assert any(
        check.code == "horizontal_table_composition_resolved" and not check.passed
        for check in unresolved.checks
    )

    grid_patch = AtomicPatch(
        patch_id="patch-000003",
        source_task_id="review-000001",
        target_type="table",
        target_id=right.table_id,
        operation="set_table_grid",
        proposed_value={
            "row_count": 2,
            "column_count": 1,
            "cells": [
                {"row_index": 0, "col_index": 0, "text": "Right header", "is_header": True},
                {"row_index": 1, "col_index": 0, "text": "Right half"},
            ],
        },
        evidence_refs=[next(item.path for item in document.artifacts if item.kind == "spread_image")],
        confidence=0.96,
    )
    repaired_patches = [*base_patches, grid_patch]
    repaired = PatchGuard().evaluate(
        document,
        "review-000001",
        repaired_patches,
        allowed_target_ids={spread.spread_id, right.table_id},
        required_target_ids={spread.spread_id},
        allowed_operations={"confirm_spread", "link_horizontal_continuation", "set_table_grid"},
        required_any_operations={"confirm_spread", "reject_spread"},
    )
    assert repaired.passed is True

    DocumentPatchApplier().apply(document, repaired_patches)
    logical = document.logical_tables[0]
    assert logical.composition_mode == "horizontal_continue_last_column"
    assert logical.status == "derived"
    assert (logical.logical_row_count, logical.logical_column_count) == (2, 2)
    logical_by_position = {
        (cell.row_index, cell.col_index): cell
        for cell in logical.cells
    }
    assert logical_by_position[(0, 1)].text == "Response Right header"
    assert logical_by_position[(1, 1)].text == "Left half Right half"
    assert len(logical_by_position[(1, 1)].source_cell_ids) == 2


def test_last_column_continuation_does_not_allow_overlap_in_other_columns(
    tmp_path: Path,
) -> None:
    document = HorizontalSpreadBuilder().build(_spread_document(tmp_path), tmp_path)
    spread = document.spreads[0]
    patches = [
        AtomicPatch(
            patch_id="patch-000001",
            source_task_id="review-000001",
            target_type="spread",
            target_id=spread.spread_id,
            operation="confirm_spread",
            proposed_value={"reading_direction": "left_to_right"},
            evidence_refs=[next(item.path for item in document.artifacts if item.kind == "spread_image")],
            confidence=0.98,
        ),
        AtomicPatch(
            patch_id="patch-000002",
            source_task_id="review-000001",
            target_type="spread",
            target_id=spread.spread_id,
            operation="link_horizontal_continuation",
            proposed_value={
                "links": [
                    {
                        "source_id": document.tables[0].table_id,
                        "target_id": document.tables[1].table_id,
                        "confidence": 0.96,
                    }
                ]
            },
            evidence_refs=[next(item.path for item in document.artifacts if item.kind == "spread_image")],
            confidence=0.96,
        ),
    ]
    DocumentPatchApplier().apply(document, patches)
    logical = document.logical_tables[0]
    logical.composition_mode = "horizontal_continue_last_column"
    logical.cell_mappings[1].logical_col_index = 0

    validated = DocumentIrValidator().validate(document, expected_page_count=2)

    assert any(
        issue.code == "invalid_logical_table"
        for issue in validated.validation_report.issues
    )


def test_spread_becomes_blocking_when_a_member_has_material_text_coverage_risk(tmp_path: Path) -> None:
    document = HorizontalSpreadBuilder().build(_spread_document(tmp_path), tmp_path)
    document.pages[0].text = ""
    document.pages[0].text_length = 0
    forensics = LocalPdfForensics(
        exists=True,
        page_count=2,
        pages=[
            LocalPageForensics(
                page_index=0,
                native_text="Material source text " * 20,
                native_text_length=420,
            ),
            LocalPageForensics(
                page_index=1,
                native_text="",
                native_text_length=0,
            ),
        ],
    )

    routed = OcrQualityRouter().route(document, forensics).document
    spread_task = next(task for task in routed.review_tasks if task.target_type == "spread")

    assert spread_task.blocking is True
    assert "page-0001" in spread_task.review_plan.required_decision_target_ids
    assert {"table-p0001-0001", "table-p0002-0001"}.issubset(
        set(spread_task.review_plan.mutable_target_ids)
    )
    assert "native_text_present_ocr_weak" in spread_task.reason_codes
