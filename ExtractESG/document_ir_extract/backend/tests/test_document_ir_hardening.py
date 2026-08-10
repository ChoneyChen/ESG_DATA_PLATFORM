from __future__ import annotations

from pathlib import Path

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    BlockIR,
    BoundingBox,
    CellIR,
    ConflictGroup,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    LayoutObjectIR,
    LocalPageForensics,
    LocalPdfForensics,
    LocalTableCandidate,
    LocalTextBlock,
    PageIR,
    SourceTrace,
    TableCellObservation,
    TableIR,
    VlmReviewTask,
)
from esg_v2.document.geometry import normalize_bbox_to_page
from esg_v2.document.deduplicator import DeterministicEntityDeduplicator
from esg_v2.document.local_forensics import LocalPdfProcessor
from esg_v2.document.parser_fusion import ParserFusion
from esg_v2.document.quality_router import OcrQualityRouter
from esg_v2.document.review_scheduler import ReviewScheduler
from esg_v2.document.structure_reconstruction import StructureReconstructor
from esg_v2.document.table_geometry_resolver import TableGeometryResolver
from esg_v2.document.text_normalization import normalize_ocr_text
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.document.visual_semantic_grouper import VisualSemanticGrouper


TRACE = SourceTrace(parser="test")


def test_ocr_superscript_number_normalization_handles_adjacent_cjk_text() -> None:
    assert normalize_ocr_text("死亡責任事故 $ ^{0} $起") == "死亡責任事故 0起"


def test_page_coverage_review_explicitly_allows_localized_page_blocks() -> None:
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        text="Short OCR text",
        text_length=14,
        block_ids=["block-0001"],
        source_trace=TRACE,
    )
    block = BlockIR(
        block_id="block-0001",
        page_index=0,
        order=0,
        block_type="paragraph",
        text="Short OCR text",
        bbox=BoundingBox(x0=40, y0=80, x1=300, y1=120, unit="points"),
        source_trace=TRACE,
    )
    native_text = "Reliable native source text " * 20
    forensics = LocalPdfForensics(
        exists=True,
        page_count=1,
        pages=[
            LocalPageForensics(
                page_index=0,
                native_text=native_text,
                native_text_length=len(native_text),
            )
        ],
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[page],
        blocks=[block],
    )

    routed = OcrQualityRouter().route(document, forensics).document
    task = next(item for item in routed.review_tasks if item.target_id == page.page_id)

    assert "block-0001" in task.review_plan.mutable_target_ids
    assert "set_bbox" in task.review_plan.allowed_operations


def test_unrouted_nonblocking_conflict_is_retained_as_accepted_observation() -> None:
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        text="A sufficiently long canonical page text that does not require a visual review.",
        text_length=75,
        source_trace=TRACE,
    )
    conflict = ConflictGroup(
        conflict_id="conflict-0001",
        target_id=page.page_id,
        conflict_type="secondary_parser_dimension_difference",
        blocking=False,
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[page],
        conflict_groups=[conflict],
    )

    routed = OcrQualityRouter().route(document).document

    assert routed.conflict_groups[0].status == "resolved"
    assert (
        routed.conflict_groups[0].routing_disposition
        == "accepted_nonmaterial_difference"
    )


class _FakeTable:
    def __init__(self, bbox):
        self.bbox = bbox
        self.rows = []

    def extract(self):
        return [["A", "B"], ["C", "D"]]


class _FakePage:
    def __init__(self, tables):
        self._tables = tables

    def find_tables(self):
        return self._tables


def test_bbox_normalization_clips_rounding_drift_but_rejects_material_overflow() -> None:
    coordinate = "page-0001-pdf-points"
    clipped, status = normalize_bbox_to_page(
        BoundingBox(x0=-0.5, y0=0, x1=595.8, y1=700, unit="points", coordinate_system_id=coordinate),
        page_width=595.0,
        page_height=800.0,
    )
    rejected, rejected_status = normalize_bbox_to_page(
        BoundingBox(x0=0, y0=0, x1=686.0, y1=700, unit="points", coordinate_system_id=coordinate),
        page_width=595.0,
        page_height=800.0,
    )

    assert status == "clipped_to_page"
    assert clipped is not None and clipped.x0 == 0 and clipped.x1 == 595.0
    assert rejected is None
    assert rejected_status == "rejected_out_of_bounds"


def test_local_table_forensics_drops_out_of_page_false_candidate() -> None:
    errors: list[str] = []
    candidates = LocalPdfProcessor._table_candidates(
        _FakePage([_FakeTable((0, 0, 686, 700))]),
        "page-0001-pdf-points",
        errors,
        0,
        page_width=595.0,
        page_height=800.0,
    )

    assert candidates == []
    assert errors == ["page_1_table_candidate_1_rejected_out_of_bounds"]


def test_parser_fusion_matches_missing_geometry_by_shape_and_text() -> None:
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=2,
        column_count=2,
        cells=[
            CellIR(cell_id=f"cell-{row}-{col}", table_id="table-1", page_index=0, row_index=row, col_index=col, text=text, source_trace=TRACE)
            for row, col, text in ((0, 0, "Metric"), (0, 1, "2024"), (1, 0, "Women"), (1, 1, "48%"))
        ],
        quality_flags=["table_geometry_missing"],
        source_trace=TRACE,
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[PageIR(page_id="page-1", page_index=0, page_number=1, width=600, height=800, table_ids=["table-1"], source_trace=TRACE)],
        tables=[table],
    )
    local_bbox = BoundingBox(x0=40, y0=100, x1=560, y1=300, unit="points", coordinate_system_id="page-0001-pdf-points")
    local = LocalPdfForensics(
        exists=True,
        page_count=1,
        pages=[
            LocalPageForensics(
                page_index=0,
                width_points=600,
                height_points=800,
                table_candidates=[
                    LocalTableCandidate(
                        candidate_id="local-table-1",
                        bbox=local_bbox,
                        row_count=2,
                        column_count=2,
                        cells=[
                            TableCellObservation(row_index=row, col_index=col, text=text)
                            for row, col, text in ((0, 0, "Metric"), (0, 1, "2024"), (1, 0, "Women"), (1, 1, "48%"))
                        ],
                    )
                ],
            )
        ],
    )

    fused = ParserFusion().fuse(document, local_forensics=local)

    assert len(fused.tables) == 1
    assert fused.tables[0].bbox == local_bbox
    assert "table_geometry_recovered_from_pdfplumber" in fused.tables[0].quality_flags
    assert fused.tables[0].observations[0].match_score == 1.0


def test_parser_fusion_subsumes_second_local_fragment_into_canonical_table() -> None:
    canonical_bbox = BoundingBox(
        x0=20,
        y0=80,
        x1=580,
        y1=700,
        unit="points",
        coordinate_system_id="page-0001-pdf-points",
    )
    canonical = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=4,
        column_count=3,
        cells=[
            CellIR(
                cell_id=f"cell-{row}-{col}",
                table_id="table-1",
                page_index=0,
                row_index=row,
                col_index=col,
                text=text,
                source_trace=TRACE,
            )
            for row, values in enumerate(
                (
                    ("SDGs", "我們的理解", "行動計劃"),
                    ("氣候行動", "碳盤查", "範圍三盤查"),
                    ("水下生物", "海洋保護", "TNFD管理"),
                    ("陸地生物", "生態保護", "自然風險"),
                )
            )
            for col, text in enumerate(values)
        ],
        bbox=canonical_bbox,
        quality_flags=["html_table_parsed"],
        source_trace=TRACE,
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[
            PageIR(
                page_id="page-1",
                page_index=0,
                page_number=1,
                width=600,
                height=800,
                table_ids=["table-1"],
                source_trace=TRACE,
            )
        ],
        tables=[canonical],
    )
    local = LocalPdfForensics(
        exists=True,
        page_count=1,
        pages=[
            LocalPageForensics(
                page_index=0,
                width_points=600,
                height_points=800,
                table_candidates=[
                    LocalTableCandidate(
                        candidate_id="local-full",
                        bbox=canonical_bbox,
                        row_count=4,
                        column_count=3,
                        cells=[
                            TableCellObservation(
                                row_index=cell.row_index,
                                col_index=cell.col_index,
                                text=cell.text,
                                bbox=BoundingBox(
                                    x0=20 + cell.col_index * 180,
                                    y0=80 + cell.row_index * 100,
                                    x1=200 + cell.col_index * 180,
                                    y1=180 + cell.row_index * 100,
                                    unit="points",
                                    coordinate_system_id="page-0001-pdf-points",
                                ),
                            )
                            for cell in canonical.cells
                        ],
                    ),
                    LocalTableCandidate(
                        candidate_id="local-fragment",
                        bbox=BoundingBox(
                            x0=200,
                            y0=180,
                            x1=580,
                            y1=280,
                            unit="points",
                            coordinate_system_id="page-0001-pdf-points",
                        ),
                        row_count=2,
                        column_count=4,
                        cells=[
                            TableCellObservation(row_index=0, col_index=0, text="碳盤查"),
                            TableCellObservation(row_index=0, col_index=1, text="範圍三盤查"),
                            TableCellObservation(row_index=1, col_index=0, text="海洋保護"),
                            TableCellObservation(row_index=1, col_index=1, text="TNFD管理"),
                        ],
                    ),
                ],
            )
        ],
    )

    fused = ParserFusion().fuse(document, local_forensics=local)

    assert len(fused.tables) == 1
    assert len(fused.tables[0].observations) == 2
    assert "local_candidate_subsumed" in fused.tables[0].quality_flags
    assert "local_only_table_candidate" not in fused.tables[0].quality_flags
    routed = OcrQualityRouter().route(fused, local)
    assert routed.review_tasks == []


def test_parser_fusion_filters_tiny_sparse_label_grid_but_keeps_forensic_reason() -> None:
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[
            PageIR(
                page_id="page-1",
                page_index=0,
                page_number=1,
                width=600,
                height=800,
                source_trace=TRACE,
            )
        ],
    )
    local_candidate = LocalTableCandidate(
        candidate_id="local-decoration",
        bbox=BoundingBox(x0=20, y0=20, x1=80, y1=70, unit="points"),
        row_count=4,
        column_count=4,
        cells=[
            TableCellObservation(
                row_index=row,
                col_index=column,
                text="案例" if row == 0 else "",
            )
            for row in range(4)
            for column in range(4)
        ],
    )
    local_page = LocalPageForensics(
        page_index=0,
        width_points=600,
        height_points=800,
        table_candidates=[local_candidate],
    )
    local = LocalPdfForensics(exists=True, page_count=1, pages=[local_page])

    fused = ParserFusion().fuse(document, local_forensics=local)

    assert fused.tables == []
    assert "rejected_noncredible_local_table_candidate" in local_candidate.quality_flags
    assert "noncredible_local_table_candidate_filtered" in local_page.quality_flags


def test_table_geometry_resolver_uses_linked_paddle_layout_before_vlm() -> None:
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=1,
        column_count=1,
        cells=[CellIR(cell_id="cell-1", table_id="table-1", page_index=0, row_index=0, col_index=0, text="A", source_trace=TRACE)],
        quality_flags=["table_geometry_missing"],
        source_trace=TRACE,
    )
    bbox = BoundingBox(x0=20, y0=30, x1=400, y1=200, unit="points", coordinate_system_id="page-0001-pdf-points")
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[PageIR(page_id="page-1", page_index=0, page_number=1, width=600, height=800, table_ids=["table-1"], source_trace=TRACE)],
        tables=[table],
        layout_objects=[LayoutObjectIR(layout_object_id="layout-1", page_index=0, order=0, label="table", table_id="table-1", bbox=bbox, source_trace=TRACE)],
    )

    resolved = TableGeometryResolver().resolve(document)

    assert resolved.tables[0].bbox == bbox
    assert "table_geometry_recovered_from_paddle_layout" in resolved.tables[0].quality_flags


def test_table_geometry_resolver_semantically_matches_chart_layout() -> None:
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=3,
        column_count=2,
        cells=[
            CellIR(cell_id=f"cell-{index}", table_id="table-1", page_index=0, row_index=row, col_index=col, text=text, source_trace=TRACE)
            for index, (row, col, text) in enumerate(
                ((0, 0, "性别"), (0, 1, "比例"), (1, 0, "男性"), (1, 1, "61%"), (2, 0, "女性"), (2, 1, "39%"))
            )
        ],
        quality_flags=["table_geometry_missing"],
        source_trace=TRACE,
    )
    bbox = BoundingBox(x0=65, y0=318, x1=233, y1=494, unit="points", coordinate_system_id="page-0001-pdf-points")
    layout = LayoutObjectIR(
        layout_object_id="layout-chart",
        page_index=0,
        order=0,
        label="chart",
        text="性别 | 比例 男性 | 61% 女性 | 39%",
        bbox=bbox,
        source_trace=TRACE,
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[PageIR(page_id="page-1", page_index=0, page_number=1, width=600, height=800, table_ids=["table-1"], source_trace=TRACE)],
        tables=[table],
        layout_objects=[layout],
    )

    resolved = TableGeometryResolver().resolve(document)

    assert resolved.tables[0].bbox == bbox
    assert layout.table_id == "table-1"
    assert "table_geometry_recovered_from_semantic_paddle_layout" in resolved.tables[0].quality_flags


def test_quality_router_does_not_create_one_task_per_ordinary_diagram_page() -> None:
    pages = []
    figures = []
    for index in range(300):
        pages.append(
            PageIR(
                page_id=f"page-{index + 1:04d}",
                page_index=index,
                page_number=index + 1,
                width=600,
                height=800,
                text="A normal page with enough OCR text to establish document coverage.",
                text_length=68,
                figure_ids=[f"figure-{index + 1:04d}"],
                source_trace=TRACE,
            )
        )
        figures.append(
            FigureIR(
                figure_id=f"figure-{index + 1:04d}",
                page_index=index,
                order=0,
                visual_type="diagram",
                bbox=BoundingBox(x0=50, y0=100, x1=550, y1=700, unit="points"),
                source_trace=TRACE,
            )
        )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=pages,
        figures=figures,
    )

    result = OcrQualityRouter().route(document)

    assert result.review_tasks == []
    assert result.quality_report["suppressed_optional_visual_review_count"] == 300


def test_unresolved_table_geometry_is_routed_to_full_page_vlm_review() -> None:
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        text="A page with enough OCR text to avoid page coverage routing.",
        text_length=58,
        table_ids=["table-1"],
        page_image_path="page.png",
        source_trace=TRACE,
    )
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=1,
        column_count=1,
        cells=[CellIR(cell_id="cell-1", table_id="table-1", page_index=0, row_index=0, col_index=0, text="Metric", source_trace=TRACE)],
        source_trace=TRACE,
    )
    result = OcrQualityRouter().route(
        DocumentIR(
            metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
            pages=[page],
            tables=[table],
        )
    )

    assert len(result.review_tasks) == 1
    assert result.review_tasks[0].blocking is True
    assert result.review_tasks[0].reason_codes == ["table_geometry_missing"]
    assert "page.png" in result.review_tasks[0].input_refs


def test_sparse_local_only_table_is_routed_without_reopening_all_visuals() -> None:
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        text="A page with enough OCR text to avoid page coverage routing.",
        text_length=58,
        table_ids=["table-1"],
        page_image_path="page.png",
        source_trace=TRACE,
    )
    cells = [
        CellIR(
            cell_id=f"cell-{row}-{col}",
            table_id="table-1",
            page_index=0,
            row_index=row,
            col_index=col,
            text="Header" if row == 0 else "",
            source_trace=TRACE,
        )
        for row in range(3)
        for col in range(3)
    ]
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=3,
        column_count=3,
        cells=cells,
        bbox=BoundingBox(x0=20, y0=100, x1=580, y1=600, unit="points"),
        quality_flags=["local_only_table_candidate"],
        source_trace=TRACE,
    )
    result = OcrQualityRouter().route(
        DocumentIR(
            metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
            pages=[page],
            tables=[table],
        )
    )

    assert len(result.review_tasks) == 1
    assert result.review_tasks[0].reason_codes == ["blank_visual_encoding_cells"]


def test_ocr_longer_than_native_text_does_not_trigger_symmetric_divergence() -> None:
    native_text = "Scope and methodology 2025 " * 20
    ocr_text = native_text + ("additional visible chart labels and bilingual content " * 30)
    document = DocumentIR(
        metadata=DocumentIRMetadata(
            run_id="ir-test",
            ocr_run_id="ocr-test",
            local_forensics=LocalPdfForensics(
                exists=True,
                page_count=1,
                pages=[
                    LocalPageForensics(
                        page_index=0,
                        native_text=native_text,
                        native_text_length=len(native_text),
                    )
                ],
            ),
        ),
        pages=[
            PageIR(
                page_id="page-0001",
                page_index=0,
                page_number=1,
                width=600,
                height=800,
                text=ocr_text,
                text_length=len(ocr_text),
                source_trace=TRACE,
            )
        ],
    )

    result = OcrQualityRouter().route(document)

    assert result.review_tasks == []
    assert "native_ocr_text_length_divergence" not in document.pages[0].quality_flags


def test_embedded_document_preview_microtext_is_excluded_from_page_coverage() -> None:
    body_text = "Sustainability management information remains complete and accurate. " * 6
    microtext = "Independent assurance statement scope methodology evidence " * 12
    body_blocks = [
        LocalTextBlock(
            text=word,
            bbox=BoundingBox(
                x0=50 + index * 12,
                y0=140,
                x1=60 + index * 12,
                y1=149,
                unit="points",
            ),
        )
        for index, word in enumerate(body_text.split()[:20])
    ]
    micro_blocks = [
        LocalTextBlock(
            text=word,
            bbox=BoundingBox(
                x0=430 + (index % 6) * 10,
                y0=150 + (index // 6) * 3,
                x1=438 + (index % 6) * 10,
                y1=151.5 + (index // 6) * 3,
                unit="points",
            ),
        )
        for index, word in enumerate(microtext.split())
    ]
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        text=body_text,
        text_length=len(body_text),
        figure_ids=["figure-1"],
        source_trace=TRACE,
    )
    figure = FigureIR(
        figure_id="figure-1",
        page_index=0,
        order=0,
        visual_type="unknown",
        bbox=BoundingBox(x0=425, y0=135, x1=510, y1=230, unit="points"),
        source_trace=TRACE,
    )
    forensics = LocalPdfForensics(
        exists=True,
        page_count=1,
        pages=[
            LocalPageForensics(
                page_index=0,
                width_points=600,
                height_points=800,
                native_text=f"{body_text} {microtext}",
                native_text_length=len(body_text) + len(microtext) + 1,
                native_text_blocks=[*body_blocks, *micro_blocks],
            )
        ],
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[page],
        figures=[figure],
    )

    document = ParserFusion().fuse(document, local_forensics=forensics)
    result = OcrQualityRouter().route(document, forensics)

    assert result.review_tasks == []
    assert "native_ocr_text_length_divergence" not in page.quality_flags
    assert "embedded_document_preview_native_text_excluded_from_coverage" in page.quality_flags
    assert "embedded_document_preview_candidate" in figure.quality_flags
    assert forensics.pages[0].native_text_excluded_figure_ids == ["figure-1"]
    assert forensics.pages[0].native_text_excluded_block_count == len(micro_blocks)


def test_raw_image_layout_can_exclude_preview_microtext_without_canonical_figure() -> None:
    body_text = "Material governance text " * 15
    microtext = "Tiny embedded report preview words " * 20
    micro_blocks = [
        LocalTextBlock(
            text=word,
            bbox=BoundingBox(
                x0=420 + (index % 8) * 8,
                y0=120 + (index // 8) * 2,
                x1=426 + (index % 8) * 8,
                y1=121 + (index // 8) * 2,
                unit="points",
            ),
        )
        for index, word in enumerate(microtext.split())
    ]
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        text=body_text,
        text_length=len(body_text),
        source_trace=TRACE,
    )
    layout = LayoutObjectIR(
        layout_object_id="layout-p0001-0001",
        page_index=0,
        order=0,
        label="image",
        bbox=BoundingBox(x0=410, y0=100, x1=510, y1=230, unit="points"),
        source_trace=TRACE,
    )
    forensics = LocalPdfForensics(
        exists=True,
        page_count=1,
        pages=[
            LocalPageForensics(
                page_index=0,
                width_points=600,
                height_points=800,
                native_text=f"{body_text} {microtext}",
                native_text_length=len(body_text) + len(microtext) + 1,
                native_text_blocks=micro_blocks,
            )
        ],
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[page],
        layout_objects=[layout],
    )

    ParserFusion().fuse(document, local_forensics=forensics)

    assert forensics.pages[0].native_text_exclusions
    exclusion = forensics.pages[0].native_text_exclusions[0]
    assert exclusion.matched_layout_object_ids == ["layout-p0001-0001"]
    assert exclusion.matched_figure_id is None
    assert "embedded_document_preview_native_text_excluded_from_coverage" in page.quality_flags


def test_markdown_fallback_matching_adjacent_grounded_blocks_is_retired_losslessly() -> None:
    trace = SourceTrace(
        parser="test",
        artifact_ids=["artifact-page-markdown-p0001"],
        page_markdown_path="content/pages/page-0001.md",
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[
            PageIR(
                page_id="page-0001",
                page_index=0,
                page_number=1,
                block_ids=["grounded-a", "grounded-b", "fallback"],
                source_trace=trace,
            )
        ],
        blocks=[
            BlockIR(
                block_id="grounded-a",
                page_index=0,
                order=0,
                block_type="paragraph",
                text="Employee turnover",
                bbox=BoundingBox(x0=10, y0=10, x1=100, y1=20, unit="points"),
                source_trace=trace.model_copy(deep=True),
            ),
            BlockIR(
                block_id="grounded-b",
                page_index=0,
                order=1,
                block_type="paragraph",
                text="was 12 percent.",
                bbox=BoundingBox(x0=10, y0=22, x1=100, y1=32, unit="points"),
                source_trace=trace.model_copy(deep=True),
            ),
            BlockIR(
                block_id="fallback",
                page_index=0,
                order=2,
                block_type="paragraph",
                text="Employee turnover was 12 percent.",
                quality_flags=["markdown_fallback_without_layout_geometry"],
                source_trace=trace.model_copy(deep=True),
            ),
        ],
    )

    DeterministicEntityDeduplicator().deduplicate(document)

    assert [block.block_id for block in document.blocks] == ["grounded-a", "grounded-b"]
    retired = document.retired_entities[0]
    assert retired.disposition == "duplicate_fallback"
    assert retired.canonical_target_ids == ["grounded-a", "grounded-b"]
    assert document.pages[0].block_ids == ["grounded-a", "grounded-b"]


def test_microtext_in_a_chart_is_not_silently_excluded() -> None:
    ocr_text = "Visible chart summary " * 6
    native_text = ocr_text + ("material chart labels and values " * 30)
    micro_blocks = [
        LocalTextBlock(
            text=f"metric-{index}",
            bbox=BoundingBox(
                x0=430 + (index % 6) * 10,
                y0=150 + (index // 6) * 3,
                x1=438 + (index % 6) * 10,
                y1=151.5 + (index // 6) * 3,
                unit="points",
            ),
        )
        for index in range(36)
    ]
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        text=ocr_text,
        text_length=len(ocr_text),
        figure_ids=["figure-1"],
        source_trace=TRACE,
    )
    figure = FigureIR(
        figure_id="figure-1",
        page_index=0,
        order=0,
        visual_type="chart",
        bbox=BoundingBox(x0=425, y0=135, x1=510, y1=230, unit="points"),
        source_trace=TRACE,
    )
    forensics = LocalPdfForensics(
        exists=True,
        page_count=1,
        pages=[
            LocalPageForensics(
                page_index=0,
                width_points=600,
                height_points=800,
                native_text=native_text,
                native_text_length=len(native_text),
                native_text_blocks=micro_blocks,
            )
        ],
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[page],
        figures=[figure],
    )

    document = ParserFusion().fuse(document, local_forensics=forensics)
    result = OcrQualityRouter().route(document, forensics)

    assert any("native_ocr_text_length_divergence" in task.reason_codes for task in result.review_tasks)
    assert "embedded_document_preview_candidate" not in figure.quality_flags


def test_visual_grouping_keeps_side_paragraph_out_but_binds_text_inside_figure() -> None:
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        width=600,
        height=800,
        source_trace=TRACE,
    )
    figures = [
        FigureIR(
            figure_id="figure-1",
            page_index=0,
            order=0,
            visual_type="illustration",
            bbox=BoundingBox(x0=100, y0=100, x1=200, y1=220, unit="points"),
            source_trace=TRACE,
        ),
        FigureIR(
            figure_id="figure-2",
            page_index=0,
            order=1,
            visual_type="illustration",
            bbox=BoundingBox(x0=212, y0=100, x1=312, y1=220, unit="points"),
            source_trace=TRACE,
        ),
    ]
    inside = BlockIR(
        block_id="block-inside",
        page_index=0,
        order=0,
        block_type="paragraph",
        text="61%",
        bbox=BoundingBox(x0=130, y0=130, x1=170, y1=150, unit="points"),
        source_trace=TRACE,
    )
    side = BlockIR(
        block_id="block-side",
        page_index=0,
        order=1,
        block_type="paragraph",
        text="This ordinary paragraph sits beside the visual.",
        bbox=BoundingBox(x0=330, y0=110, x1=560, y1=210, unit="points"),
        source_trace=TRACE,
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
        pages=[page],
        blocks=[inside, side],
        figures=figures,
    )

    result = VisualSemanticGrouper().group(document)

    assert len(result.figures) == 1
    assert inside.figure_id == result.figures[0].figure_id
    assert side.figure_id is None
    assert result.figures[0].bbox.x1 == 312


def test_section_reconstruction_rejects_punctuation_and_repeated_card_labels() -> None:
    pages = [PageIR(page_id=f"page-{index + 1}", page_index=index, page_number=index + 1, source_trace=TRACE) for index in range(6)]
    blocks = [
        BlockIR(block_id="valid", page_index=0, order=0, block_type="heading", text="1.1 氣候策略", source_trace=TRACE),
        BlockIR(block_id="noise", page_index=0, order=1, block_type="heading", text="“", source_trace=TRACE),
        *[
            BlockIR(block_id=f"case-{index}", page_index=index, order=2, block_type="heading", text="案例", source_trace=TRACE)
            for index in range(6)
        ],
    ]
    document = StructureReconstructor().reconstruct(
        DocumentIR(
            metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test"),
            pages=pages,
            blocks=blocks,
        )
    )
    document = DocumentIrValidator().validate(document, expected_page_count=6)

    assert [section.title for section in document.sections] == ["1.1 氣候策略"]
    assert "section_heading_rejected_semantic_noise" in blocks[1].quality_flags
    assert all("section_heading_rejected_repeated_label" in block.quality_flags for block in blocks[2:])
    assert document.validation_report.checks["section_semantic_quality_acceptable"] is True


def test_dynamic_scheduler_selects_blocking_queue_before_optional(tmp_path: Path) -> None:
    settings = Settings(
        output_root=tmp_path,
        document_ir_output_root=tmp_path,
        upload_root=tmp_path,
        max_vlm_reviews_per_ir_run=0,
        max_blocking_vlm_reviews_per_ir_run=4,
            max_optional_vlm_reviews_per_ir_run=1,
            review_completeness_mode=False,
        )
    tasks = [
        VlmReviewTask(
            task_id=f"review-{index:06d}",
            task_type="page_compound_review",
            target_type="page",
            target_id=f"page-{index:04d}",
            page_index=index,
            prompt_intent="review",
            blocking=index < 6,
        )
        for index in range(8)
    ]

    scheduled = set(
        ReviewScheduler(settings).schedule(
            tasks,
            explicitly_targeted=False,
        ).scheduled_task_ids
    )

    assert scheduled == {
        "review-000000",
        "review-000001",
        "review-000002",
        "review-000003",
        "review-000006",
    }
