"""Page-targeted OCR → Document IR keeps original page identities end to end."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfReader

from esg_v2.config import Settings
from esg_v2.contracts import OcrRunRequest
from esg_v2.control_plane.contracts import OcrPipelineTaskRequest
from esg_v2.control_plane.report_processing import ProcessingConfig, ProcessingRequest
from esg_v2.document.artifact_loader import OcrArtifactLoader
from esg_v2.document.contracts import (
    BoundingBox,
    DocumentIR,
    DocumentIRMetadata,
    DocumentIrBuildRequest,
    LocalPageForensics,
    LocalPdfForensics,
    LocalTableCandidate,
    PageIR,
    SourceTrace,
)
from esg_v2.document.page_renderer import PageRenderer
from esg_v2.document.parser_fusion import ParserFusion
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.revision_service import DocumentIrRevisionService
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.ocr.output_writer import OcrOutputWriter
from esg_v2.ocr.page_selection import ClausePageSelector
from esg_v2.ocr.pdf_preflight import PdfCanvasPreflight
from esg_v2.ocr.providers import OcrProviderExecution, OcrProviderSelection
from esg_v2.ocr.resources import LocalOcrResourceReader
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
from esg_v2.workflows.ocr_workflow import OcrWorkflow

from test_report_processing import finish, fixture


OCR_RUN_ID = "ocr-20261017T010101Z-00000000a001"
IR_RUN_ID = "ir-20261017T010101Z-00000000b001"
SPARSE_PAGES = [3, 17, 45]
GRID_TOP = 200.0
GRID_ROWS = (("Metric", "2024", "2025"), ("Energy GWh", "12", "13"), ("Water m3", "45", "46"))


def _page_height(page_index: int) -> float:
    # Every page has its own height, so a page image joined to the wrong page is detectable.
    return 700.0 + 2.0 * page_index


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_text_pdf(path: Path, pages: list[tuple[float, float, list[str], bool]]) -> None:
    """Minimal PDF with a real Helvetica text layer and an optional ruled 3x3 grid."""
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids: list[int] = []
    for width, height, lines, grid in pages:
        ops = []
        for line_number, line in enumerate(lines):
            ops.append(f"BT /F1 12 Tf 40 {height - 60 - 18 * line_number:.1f} Td ({_escape(line)}) Tj ET")
        if grid:
            top = height - GRID_TOP
            ops.append("0 0 0 RG 1 w")
            for row in range(len(GRID_ROWS) + 1):
                ops.append(f"40 {top - 20 * row:.1f} m 400 {top - 20 * row:.1f} l S")
            for column in range(4):
                ops.append(f"{40 + 120 * column} {top:.1f} m {40 + 120 * column} {top - 60:.1f} l S")
            for row, cells in enumerate(GRID_ROWS):
                for column, cell in enumerate(cells):
                    ops.append(f"BT /F1 9 Tf {44 + 120 * column} {top - 20 * row - 14:.1f} Td ({cell}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.1f %.1f] /Resources << /Font << /F1 3 0 R >> >> "
            b"/Contents %d 0 R >>" % (width, height, content)
        )
        kids.append(len(objects))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % kid for kid in kids), len(kids))
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(output))


def _report_pdf(path: Path, page_count: int = 50) -> Path:
    _write_text_pdf(
        path,
        [
            (500.0, _page_height(index), [f"Sustainability report page {index + 1}", "Environmental performance data"], True)
            for index in range(page_count)
        ],
    )
    return path


def _jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


class SubsetEchoRouter:
    """Stands in for PaddleOCR-VL: it recognises exactly the pages of the provider input."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, request, *, input_path, provider_root, run_id, expected_page_count=0, progress_callback=None, log=None):
        resource_root = provider_root / "local-resources"
        document = pdfium.PdfDocument(str(input_path))
        lines = []
        try:
            for position in range(len(document)):
                page = document[position]
                width, height = page.get_size()
                text_page = page.get_textpage()
                title = (text_page.get_text_range().strip().splitlines() or [f"Scanned page {position + 1}"])[0].strip()
                text_page.close()
                page.close()
                table_html = "<table>" + "".join(
                    "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in GRID_ROWS
                ) + "</table>"
                images = {}
                markdown = f"# {title}\n\nEnvironmental performance data\n\n{table_html}\n"
                if position == 1:
                    relative = Path("markdown") / f"page-{position + 1:04d}" / "image-0001-chart.jpg"
                    (resource_root / relative).parent.mkdir(parents=True, exist_ok=True)
                    (resource_root / relative).write_bytes(_jpeg())
                    images = {"imgs/chart.jpg": f"local-resource://{relative.as_posix()}"}
                    markdown += '\n<div><img src="imgs/chart.jpg"></div>\n'
                payload = {
                    "prunedResult": {
                        "width": width,
                        "height": height,
                        "parsing_res_list": [
                            {"block_id": 1, "block_order": 1, "block_label": "doc_title", "block_content": title,
                             "block_bbox": [40, 46, 460, 64]},
                            {"block_id": 2, "block_order": 2, "block_label": "text",
                             "block_content": "Environmental performance data", "block_bbox": [40, 64, 460, 82]},
                            {"block_id": 3, "block_order": 3, "block_label": "table", "block_content": table_html,
                             "block_bbox": [40, GRID_TOP, 400, GRID_TOP + 60]},
                        ],
                    },
                    "markdown": {"text": markdown, "images": images},
                    "outputImages": {},
                }
                lines.append(json.dumps({"result": {"layoutParsingResults": [payload]}}))
        finally:
            document.close()
        self.calls.append({"input_path": Path(input_path), "expected_page_count": expected_page_count, "pages": len(lines)})
        execution = OcrProviderExecution(
            provider="local_paddleocr",
            job_id=f"local-{run_id}",
            result_json_url=None,
            jsonl_text="\n".join(lines) + "\n",
            submit_response={"provider": "local_paddleocr", "data": {"jobId": f"local-{run_id}"}},
            poll_events=[],
            resource_reader=LocalOcrResourceReader(resource_root),
            metadata={"model": "subset-echo-test"},
        )
        return OcrProviderSelection("local_paddleocr", execution, [{"provider": "local_paddleocr", "outcome": "selected"}])


def _settings(tmp_path: Path, **overrides) -> Settings:
    # The text-layer route and the scan prescan have their own tests
    # (test_text_layer_route.py); these exercise the visual OCR path.
    return Settings(
        output_root=tmp_path / "ocr_output",
        document_ir_output_root=tmp_path / "document_ir_output",
        upload_root=tmp_path / "uploads",
        document_ir_job_state_root=tmp_path / "jobs" / "document-ir",
        storage_cleanup_root=tmp_path / "cleanup",
        **{"ocr_text_layer_enabled": False, "ocr_vision_prescan_enabled": False, **overrides},
    )


def _targeted_ocr(tmp_path: Path, request: OcrRunRequest, *, run_id: str = OCR_RUN_ID):
    router = SubsetEchoRouter()
    settings = _settings(tmp_path)
    result = OcrWorkflow(settings, provider_router=router).run(request, run_id=run_id)
    return settings, router, result


def test_page_targets_are_validated_at_every_request_boundary() -> None:
    assert OcrRunRequest(file_path="report.pdf", page_indices=[3, 17, 45]).page_indices == [3, 17, 45]
    for invalid in ([], [-1, 2], [17, 3], [3, 3]):
        with pytest.raises(ValidationError):
            OcrRunRequest(file_path="report.pdf", page_indices=invalid)
    with pytest.raises(ValidationError, match="remote file_url"):
        OcrRunRequest(file_url="https://example.test/report.pdf", page_indices=[1])
    with pytest.raises(ValidationError, match="remote file_url"):
        OcrRunRequest(file_url="https://example.test/report.pdf", page_selection="clauses", page_search_targets=_search_targets())
    with pytest.raises(ValidationError, match="not both"):
        OcrRunRequest(file_path="report.pdf", page_indices=[1], page_selection="clauses", page_search_targets=_search_targets())
    assert OcrPipelineTaskRequest(asset_id="asset-1", page_selection="clauses", page_search_targets=_search_targets()).page_selection == "clauses"
    with pytest.raises(ValidationError, match="report asset"):
        OcrPipelineTaskRequest(file_url="https://example.test/report.pdf", page_indices=[2])
    with pytest.raises(ValidationError):
        OcrPipelineTaskRequest(asset_id="asset-1", page_indices=[5, 2])


def test_preflight_submits_an_unchanged_subset_of_original_pages(tmp_path: Path) -> None:
    source = tmp_path / "uniform.pdf"
    _write_text_pdf(source, [(595.0, 842.0, [f"Uniform page {index + 1}"], False) for index in range(6)])
    provider = tmp_path / "provider-input.pdf"

    prepared = PdfCanvasPreflight().prepare(source, provider, page_indices=[1, 4])

    assert prepared.page_indices == (1, 4)
    assert prepared.provider_path == provider.resolve()
    assert prepared.transformed is False
    assert prepared.report["strategy"] == "original_pdf_page_subset"
    assert prepared.report["source"]["page_count"] == 6
    assert prepared.report["provider_input"]["page_indices"] == [1, 4]
    assert [row["page_number"] for row in prepared.report["pages"]] == [2, 5]
    assert [page.extract_text().strip() for page in PdfReader(provider).pages] == ["Uniform page 2", "Uniform page 5"]
    with pytest.raises(ValueError, match="the PDF has 6 pages"):
        PdfCanvasPreflight().prepare(source, tmp_path / "other.pdf", page_indices=[2, 6])
    # Selecting every page is an ordinary whole-document run with no subset file.
    full = PdfCanvasPreflight().prepare(source, tmp_path / "full.pdf", page_indices=list(range(6)))
    assert full.page_indices is None and full.provider_path == source.resolve()


def test_page_targeted_ocr_and_ir_keep_original_page_identity(tmp_path: Path) -> None:
    pdf_path = _report_pdf(tmp_path / "report.pdf")
    settings, router, ocr = _targeted_ocr(
        tmp_path,
        OcrRunRequest(file_path=str(pdf_path), ocr_provider="local_paddleocr", page_indices=SPARSE_PAGES),
    )

    # Mixed page heights take the normalized branch; the provider sees only the 3 pages.
    provider_input = PdfReader(router.calls[0]["input_path"])
    assert router.calls[0]["expected_page_count"] == 3
    assert [round(float(page.mediabox.height)) for page in provider_input.pages] == [
        round(_page_height(index)) for index in SPARSE_PAGES
    ]
    manifest = json.loads(ocr.manifest_path.read_text(encoding="utf-8"))
    assert manifest["page_count"] == 3
    assert ocr.page_indices == SPARSE_PAGES
    assert manifest["page_selection"] == {
        "mode": "targeted",
        "source_page_count": 50,
        "page_indices": SPARSE_PAGES,
        "selector": {"name": "explicit", "reason": "requested_page_indices"},
    }
    root = ocr.output_dir
    assert sorted(path.name for path in (root / "content" / "pages").glob("*.md")) == [
        "page-0004.md", "page-0018.md", "page-0046.md",
    ]
    observation = json.loads((root / "observations" / "pages" / "page-0018.json").read_text(encoding="utf-8"))
    assert (observation["page_index"], observation["page_number"]) == (17, 18)
    assert "page 18" in (root / "content" / "pages" / "page-0018.md").read_text(encoding="utf-8")
    assert (root / "artifacts" / "images" / "page-0018" / "image-0001.jpg").is_file()
    artifacts = json.loads((root / "artifacts" / "index.json").read_text(encoding="utf-8"))["artifacts"]
    assert [(row["page_index"], row["artifact_id"]) for row in artifacts] == [(17, "ocr-image-p0018-0001")]
    loaded = OcrArtifactLoader(settings.output_root).load(OCR_RUN_ID)
    assert [page.page_index for page in loaded.pages] == SPARSE_PAGES
    assert loaded.covered_page_indices == SPARSE_PAGES

    result = DocumentIrWorkflow(settings).run(
        DocumentIrBuildRequest(ocr_run_id=OCR_RUN_ID, pdf_path=str(pdf_path)),
        run_id=IR_RUN_ID,
    )
    document = DocumentIrPackageReader(result.output_dir, ocr_output_root=settings.output_root).load_document(
        hydrate_local_paths=True
    )

    assert [page.page_id for page in document.pages] == ["page-0004", "page-0018", "page-0046"]
    assert [page.page_number for page in document.pages] == [4, 18, 46]
    for page in document.pages:
        # The rendered image and the OCR text both belong to the same original page.
        assert page.height == pytest.approx(_page_height(page.page_index))
        assert Path(page.page_image_path).name == f"{page.page_id}.png"
        with Image.open(page.page_image_path) as image:
            assert image.height == round(_page_height(page.page_index) * 2)
        assert f"page {page.page_number}" in page.text
        assert "pdf_ocr_page_count_mismatch" not in page.quality_flags
    assert sorted(path.name for path in (result.output_dir / "artifacts" / "page-images").glob("*.png")) == [
        "page-0004.png", "page-0018.png", "page-0046.png",
    ]
    local_pages = document.metadata.local_forensics.pages
    assert [page.page_index for page in local_pages] == SPARSE_PAGES
    assert document.metadata.local_forensics.page_count == 50
    assert all(f"page {page.page_index + 1}" in (page.native_text or "") for page in local_pages)
    assert document.tables and {table.page_index for table in document.tables} <= set(SPARSE_PAGES)
    assert all(
        cell.page_index in SPARSE_PAGES for table in document.tables for cell in table.cells
    )

    coverage = document.metadata.source_artifacts["page_coverage"]
    assert coverage["mode"] == "targeted" and coverage["page_indices"] == SPARSE_PAGES
    report = document.validation_report
    assert document.readiness in {"ready", "ready_with_warnings"}, [issue.model_dump() for issue in report.issues]
    assert report.checks["page_indices_valid"] is True
    assert report.checks["page_coverage_complete"] is True
    assert report.checks["document_page_coverage_complete"] is False
    assert (report.metrics["source_page_count"], report.metrics["covered_page_count"]) == (50, 3)
    assert [issue.severity for issue in report.issues if issue.code == "targeted_page_coverage"] == ["info"]
    ir_manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert ir_manifest["source_page_count"] == 50
    assert ir_manifest["page_coverage"]["page_indices"] == SPARSE_PAGES
    assert ir_manifest["evidence_policy"]["mode"] == "full"
    assert ir_manifest["can_build_evidence"] is True

    # A revision reloads the persisted IR and re-validates the sparse pages from metadata.
    service = DocumentIrRevisionService(settings)
    child, child_dir = service._child(IR_RUN_ID, "local")
    child_manifest = service._finalize(child, child_dir, rebuild=True)
    revised = DocumentIrPackageReader(child_dir, ocr_output_root=settings.output_root).load_document()
    assert child_manifest["readiness"] in {"ready", "ready_with_warnings"}
    assert child_manifest["page_coverage"]["page_indices"] == SPARSE_PAGES
    assert [page.page_id for page in revised.pages] == ["page-0004", "page-0018", "page-0046"]
    assert revised.validation_report.checks["page_indices_valid"] is True
    assert revised.validation_report.metrics["source_page_count"] == 50


def test_clause_selection_targets_data_pages_and_records_provenance(tmp_path: Path) -> None:
    pages: list[tuple[float, float, list[str], bool]] = []
    for index in range(30):
        lines = [f"Chapter page {index + 1}", "Our people, culture and community programmes."]
        if index == 8:
            lines = [f"Region {site} {site * 11} {site * 12} {site * 14}" for site in range(1, 6)]
        elif index == 9:
            lines = ["Climate performance", "Scope 1 GHG emissions tCO2e 2023 2024 2025",
                     "Scope 2 GHG emissions tCO2e 1,204 1,180 1,096", "Scope 3 greenhouse gas emissions 9,801 9,950 9,640",
                     *[f"Site {site} emissions {site * 101} {site * 97} {site * 93}" for site in range(1, 6)]]
        elif index == 10:
            lines = [f"Site {site} {site * 13} {site * 17} {site * 19} {site * 23}" for site in range(6, 12)]
        elif index == 19:
            lines = ["Energy and water", "Total energy consumption GWh 2,340 2,410 2,288",
                     "Water withdrawal m3 1,200 1,150 1,090", "Water consumption m3 800 760 744",
                     "Water discharge m3 400 390 346", "Renewable energy share 12% 14% 17%"]
        pages.append((595.0, 842.0, lines, False))
    pdf_path = tmp_path / "foundation.pdf"
    _write_text_pdf(pdf_path, pages)

    selection = ClausePageSelector(search_targets=_search_targets()).select(pdf_path)
    assert selection.reason == "text_layer_scored"
    # Keyword pages, front matter, and the numeric-dense neighbours of the best page.
    assert selection.page_indices == (0, 1, 2, 8, 9, 10, 19)
    assert "lead_in_to_page_10" in selection.inclusion_reasons[8]
    assert "continuation_of_page_10" in selection.inclusion_reasons[10]

    settings, router, ocr = _targeted_ocr(
        tmp_path,
        OcrRunRequest(file_path=str(pdf_path), ocr_provider="local_paddleocr", page_selection="clauses", page_search_targets=_search_targets()),
    )
    manifest = json.loads(ocr.manifest_path.read_text(encoding="utf-8"))
    assert manifest["page_selection"]["mode"] == "targeted"
    assert manifest["page_selection"]["page_indices"] == [0, 1, 2, 8, 9, 10, 19]
    assert manifest["page_selection"]["selector"]["name"] == "clauses"
    assert [row["page_index"] for row in manifest["page_selection"]["selector"]["selected_pages"]] == [0, 1, 2, 8, 9, 10, 19]
    assert manifest["entrypoints"]["source_page_selection"] == "source/page-selection.json"
    scores = json.loads((ocr.output_dir / "source" / "page-selection.json").read_text(encoding="utf-8"))
    assert len(scores["pages"]) == 30 and any(
        key.startswith("E1-6") and value > 0
        for key, value in scores["pages"][9]["group_scores"].items())
    assert router.calls[0]["pages"] == 7


def test_clause_falls_back_to_full_ocr_without_a_text_layer(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    images = [Image.new("RGB", (300, 400), "white") for _ in range(8)]
    images[0].save(pdf_path, "PDF", save_all=True, append_images=images[1:], resolution=72)

    settings, router, ocr = _targeted_ocr(
        tmp_path,
        OcrRunRequest(file_path=str(pdf_path), ocr_provider="local_paddleocr", page_selection="clauses", page_search_targets=_search_targets()),
    )
    manifest = json.loads(ocr.manifest_path.read_text(encoding="utf-8"))

    assert router.calls[0]["pages"] == 8
    assert manifest["page_count"] == 8 and ocr.page_indices is None
    assert manifest["page_selection"]["mode"] == "all"
    assert manifest["page_selection"]["selector"]["reason"] == "insufficient_text_layer"
    assert OcrArtifactLoader(settings.output_root).load(OCR_RUN_ID).page_coverage is None


def test_position_mapping_mismatch_fails_instead_of_shifting_pages(tmp_path: Path) -> None:
    writer = OcrOutputWriter(tmp_path / OCR_RUN_ID)
    page = {"markdown": {"text": "page", "images": {}}, "outputImages": {}}
    jsonl = json.dumps({"result": {"layoutParsingResults": [page, page]}}) + "\n"
    with pytest.raises(RuntimeError, match="2 pages for 3 selected"):
        writer.extract_artifacts_from_jsonl(jsonl_text=jsonl, resource_reader=LocalOcrResourceReader(tmp_path),
                                            page_indices=[3, 17, 45])
    with pytest.raises(RuntimeError, match="more pages than the 1 selected"):
        writer.extract_artifacts_from_jsonl(jsonl_text=jsonl, resource_reader=LocalOcrResourceReader(tmp_path),
                                            page_indices=[3])
    raw = tmp_path / "result.jsonl"
    raw.write_text(jsonl, encoding="utf-8")
    with pytest.raises(ValueError, match="2 pages for 3 selected"):
        OcrArtifactLoader(tmp_path)._load_pages(tmp_path, raw, {}, [], page_indices=[3, 17, 45])


def test_renderer_validator_and_fusion_work_on_covered_pages_only(tmp_path: Path) -> None:
    pdf_path = _report_pdf(tmp_path / "report.pdf", page_count=6)
    progress: list[tuple[int, int]] = []
    rendered = PageRenderer(timeout_seconds=60).render(
        str(pdf_path), tmp_path / "page-images", page_indices=[1, 4],
        progress=lambda current, total: progress.append((current, total)),
    )
    assert [page.page_index for page in rendered.pages] == [1, 4]
    assert [page.image_path.name for page in rendered.pages] == ["page-0002.png", "page-0005.png"]
    assert progress[-1] == (2, 2)

    trace = SourceTrace(parser="test")
    document = DocumentIR(
        metadata=DocumentIRMetadata(
            run_id="ir-coverage", ocr_run_id="ocr-coverage",
            source_artifacts={"page_coverage": {"mode": "targeted", "source_page_count": 6, "page_indices": [1, 4]}},
        ),
        pages=[PageIR(page_id=f"page-{index + 1:04d}", page_index=index, page_number=index + 1, source_trace=trace)
               for index in (1, 4)],
    )
    # pdfplumber saw a credible table on page 3, which the targeted OCR never covered.
    stray = LocalTableCandidate(
        candidate_id="local-table-p0003-0001",
        bbox=BoundingBox(x0=40, y0=200, x1=400, y1=260, unit="points", origin="top_left"),
        row_count=3, column_count=3,
    )
    ParserFusion().fuse(document, local_forensics=LocalPdfForensics(
        pdf_path=str(pdf_path), exists=True, page_count=6,
        pages=[LocalPageForensics(page_index=2, table_candidates=[stray])],
    ))
    assert document.tables == []
    validated = DocumentIrValidator().validate(document, expected_page_count=2)
    assert validated.validation_report.checks["page_indices_valid"] is True
    assert validated.quality_report["page_coverage"]["source_page_count"] == 6

    document.metadata.source_artifacts.pop("page_coverage")
    assert DocumentIrValidator().validate(document).validation_report.checks["page_indices_valid"] is False
    document.metadata.source_artifacts["page_coverage"] = {"mode": "targeted", "source_page_count": 6, "page_indices": [1, 5]}
    mismatch = DocumentIrValidator().validate(document)
    assert mismatch.readiness == "failed"
    assert mismatch.validation_report.checks["page_indices_valid"] is False


def test_targeted_ocr_is_never_reused_for_a_full_document_request(tmp_path: Path) -> None:
    engine, store, asset, calls, _ = fixture(tmp_path)
    targeted_config = ProcessingConfig(page_selection="clauses", page_search_targets=_search_targets(), execute_required_reviews=False)
    targeted = engine.create(ProcessingRequest(asset_id=asset["asset_id"], config=targeted_config))
    engine.tick()
    task = finish(engine, store, "ocr")
    manifest_path = engine.roots["ocr"] / task.native_job_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["page_selection"] = {"mode": "targeted", "source_page_count": 40, "page_indices": [0, 1, 2, 12],
                                  "selector": {"name": "clauses"}}
    manifest_path.write_text(json.dumps(manifest))
    engine.tick()
    finish(engine, store, "ir")
    engine.tick()
    targeted = engine.get(targeted["processing_id"])
    assert targeted["status"] == "ready"

    again = engine.create(ProcessingRequest(asset_id=asset["asset_id"], config=targeted_config))
    assert engine.get(again["processing_id"])["ir_run_id"] == targeted["ir_run_id"]
    full = engine.create(ProcessingRequest(asset_id=asset["asset_id"], config=ProcessingConfig(execute_required_reviews=False)))
    engine.tick()
    full = engine.get(full["processing_id"])
    assert full.get("ocr_run_id") is None and calls == ["ocr", "ir", "ocr"]
    full_state = {"source_sha256": asset["sha256"], "request": {"config": ProcessingConfig().model_dump(mode="json")}}
    with pytest.raises(ValueError, match="页面范围"):
        engine._verify_manifest(full_state, "ocr", manifest)




def _search_targets():
    from esg_clauses.repository import ClauseRepository
    from esg_v2.document.search import PageSearchIntent
    repository=ClauseRepository(Path(__file__).resolve().parents[4]/'ExtractESG/standard')
    return [PageSearchIntent(target_id=key,contract_digest=c.clause_digest,data_kind=c.data_kind,positive_terms=c.retrieval.positive_terms,negative_terms=c.retrieval.negative_terms) for key in ['E1-6_07','E1-5_01','E3-4_03'] for c in [repository.get(key)]]
