"""Text-layer route of page-targeted OCR and the scan prescan of the page selector."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from esg_v2.contracts import OcrRunRequest
from esg_v2.document.contracts import DocumentIrBuildRequest
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.ocr import text_layer, vision_prescan
from esg_v2.ocr.page_selection import Foundation16PageSelector
from esg_v2.ocr.text_layer import Word, build_lines, table_grid
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
from esg_v2.workflows.ocr_workflow import OcrWorkflow

from test_page_targeted_ocr import GRID_TOP, OCR_RUN_ID, SubsetEchoRouter, _settings, _write_text_pdf

ZOOM = 2.0


def _foundation_pages() -> list[tuple[float, float, list[str], bool]]:
    pages: list[tuple[float, float, list[str], bool]] = []
    for index in range(30):
        lines = [f"Chapter page {index + 1}", "Our people, culture and community programmes."]
        grid = False
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
            grid = True
        pages.append((595.0, 842.0, lines, grid))
    return pages


class FakeLayout:
    """Stands in for the pipeline's layout model: a text region over the
    printed lines, the ruled grid as a table, and on page 10 a table region
    with no text under it (a picture of a table)."""

    name = "local_paddleocr"

    def __init__(self, pages):
        self.pages = pages
        self.calls: list[list[int]] = []

    def layout_ready(self):
        return True, "ready"

    def detect_layout(self, *, input_path, page_indices, provider_root, run_id, progress_callback=None, log=None):
        self.calls.append(list(page_indices))
        layouts = []
        for index in page_indices:
            width, height, lines, grid = self.pages[index]
            boxes = [{"label": "text", "score": 0.9, "coordinate": [0.0, 0.0, width * ZOOM, 190.0 * ZOOM]}]
            if grid:
                boxes.append({"label": "table", "score": 0.95,
                              "coordinate": [36 * ZOOM, (GRID_TOP - 4) * ZOOM, 404 * ZOOM, (GRID_TOP + 64) * ZOOM]})
            if index == 9:
                boxes.append({"label": "table", "score": 0.95, "coordinate": [36 * ZOOM, 600 * ZOOM, 404 * ZOOM, 700 * ZOOM]})
            layouts.append({"page_index": index, "width": round(width * ZOOM), "height": round(height * ZOOM),
                            "boxes": boxes, "images": {}, "layout_model": "fake-layout"})
        return layouts, [{"provider": self.name, "data": {"state": "done", "extractedPages": len(page_indices)}}]


class HybridRouter(SubsetEchoRouter):
    def __init__(self, pages):
        super().__init__()
        self.local = FakeLayout(pages)


def test_text_layer_pages_skip_visual_ocr_and_keep_page_order(tmp_path: Path) -> None:
    pages = _foundation_pages()
    pdf_path = tmp_path / "foundation.pdf"
    _write_text_pdf(pdf_path, pages)
    router = HybridRouter(pages)
    settings = _settings(tmp_path, ocr_text_layer_enabled=True)

    ocr = OcrWorkflow(settings, provider_router=router).run(
        OcrRunRequest(file_path=str(pdf_path), ocr_provider="local_paddleocr", allow_api_fallback=False,
                      page_selection="foundation16"),
        run_id=OCR_RUN_ID,
    )

    manifest = json.loads(ocr.manifest_path.read_text(encoding="utf-8"))
    targets = [0, 1, 2, 8, 9, 10, 19]
    assert manifest["page_selection"]["page_indices"] == targets
    assert router.local.calls == [targets]
    # Only the page whose table region has no text layer went to visual OCR.
    assert [call["pages"] for call in router.calls] == [1]
    assert manifest["text_layer"]["text_layer_pages"] == [0, 1, 2, 8, 10, 19]
    assert manifest["text_layer"]["visual_ocr_reasons"] == {"9": "table_region_without_text_layer"}
    route = manifest["provider_route"]
    assert (route["requested"], route["requested_model"], route["allow_api_fallback"]) == (
        "local_paddleocr", "PaddleOCR-VL-1.6", False)
    assert route["attempts"][0]["provider"] == "pdf_text_layer"
    assert manifest["model"] == "subset-echo-test+pdf-text-layer"

    lines = [json.loads(line) for line in ocr.raw_jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert [line["result"]["dataInfo"]["source"] for line in lines] == [
        "pdf_text_layer", "pdf_text_layer", "pdf_text_layer", "pdf_text_layer", "visual_ocr", "pdf_text_layer", "pdf_text_layer"]
    page19 = lines[targets.index(19)]["result"]["layoutParsingResults"][0]["prunedResult"]
    table = next(block for block in page19["parsing_res_list"] if block["block_label"] == "table")
    assert table["block_content"] == (
        "<table><tr><td>Metric</td><td>2024</td><td>2025</td></tr>"
        "<tr><td>Energy GWh</td><td>12</td><td>13</td></tr>"
        "<tr><td>Water m3</td><td>45</td><td>46</td></tr></table>")
    text = next(block for block in page19["parsing_res_list"] if block["block_label"] == "text")
    assert text["block_content"].startswith("Energy and water Total energy consumption GWh 2,340 2,410 2,288")

    ir = DocumentIrWorkflow(settings).run(DocumentIrBuildRequest(ocr_run_id=OCR_RUN_ID, pdf_path=str(pdf_path)))
    document = DocumentIrPackageReader(ir.output_dir).load_document()
    assert [page.page_index for page in document.pages] == targets
    cells = {cell.text for table in document.tables if table.page_index == 19 for cell in table.cells}
    assert {"Energy GWh", "12", "13", "Water m3", "45", "46"} <= cells


def test_explicit_pages_can_use_text_layer_without_clause_selection(tmp_path: Path) -> None:
    pages = [(595.0, 842.0, [f"Report page {index}", "Total energy consumption GWh 2,340"], index == 3)
             for index in range(6)]
    pdf_path = tmp_path / "short.pdf"
    _write_text_pdf(pdf_path, pages)
    router = HybridRouter(pages)
    settings = _settings(tmp_path, ocr_text_layer_enabled=True)
    ocr = OcrWorkflow(settings, provider_router=router).run(
        OcrRunRequest(file_path=str(pdf_path), ocr_provider="local_paddleocr", allow_api_fallback=False,
                      page_indices=[1, 3]), run_id=OCR_RUN_ID)
    manifest = json.loads(ocr.manifest_path.read_text(encoding="utf-8"))
    assert router.local.calls == [[1, 3]]
    assert router.calls == []
    assert manifest["text_layer"]["text_layer_pages"] == [1, 3]
    assert manifest["page_selection"]["page_indices"] == [1, 3]


def test_every_page_from_the_text_layer_needs_no_visual_ocr(tmp_path: Path) -> None:
    pages = [(595.0, 842.0, [f"Energy and water page {index}", "Total energy consumption GWh 2,340"], index == 3)
             for index in range(6)]
    pdf_path = tmp_path / "short.pdf"
    _write_text_pdf(pdf_path, pages)
    router = HybridRouter(pages)
    settings = _settings(tmp_path, ocr_text_layer_enabled=True)

    ocr = OcrWorkflow(settings, provider_router=router).run(
        OcrRunRequest(file_path=str(pdf_path), ocr_provider="local_paddleocr", page_selection="foundation16"),
        run_id=OCR_RUN_ID,
    )

    manifest = json.loads(ocr.manifest_path.read_text(encoding="utf-8"))
    assert router.calls == []
    assert manifest["page_count"] == 6 and manifest["model"] == "pdf-text-layer"
    assert manifest["provider_route"]["requested"] == "local_paddleocr"
    assert manifest["text_layer"]["visual_ocr_pages"] == []


def _words(*items: tuple[str, float, float, float, float]) -> list[Word]:
    return [Word(text, x0, x1, base, size) for text, x0, x1, base, size in items]


def test_small_glyphs_complete_formulas_and_become_footnote_marks() -> None:
    lines = build_lines(_words(
        ("SO", 10, 22, 100, 16), ("2", 22.5, 27, 102, 9), ("排放量", 28, 70, 100, 16),
        ("取水总量", 10, 74, 140, 16), ("1", 75, 79, 134, 9),
        ("立方米", 10, 58, 180, 16), ("m", 70, 82, 180, 16), ("3", 82.5, 87, 174, 9),
    ))
    assert [line.text for line in lines] == ["SO2排放量", "取水总量¹", "立方米 m3"]


def test_table_grid_rebuilds_rows_from_word_geometry() -> None:
    size = 16.0
    words = _words(
        ("指标", 10, 42, 20, size), ("单位", 200, 232, 20, size), ("2024", 300, 336, 20, size), ("2025", 400, 436, 20, size),
        ("能源使用情况", 10, 106, 50, size),
        ("综合能源消耗量", 10, 122, 80, size), ("2", 123, 128, 74, 9), ("兆瓦时", 200, 248, 80, size),
        ("10,524,882.70", 270, 336, 80, size), ("19,233,654.46", 370, 436, 80, size),
        # A label wrapped onto two lines around its figures.
        ("单位电信业务总量综", 10, 154, 104, size), ("千克", 200, 232, 112, size),
        ("64.5", 306, 336, 112, size), ("61.5", 406, 436, 112, size),
        ("合能耗", 10, 58, 120, size),
    )
    grid = table_grid(words, [], [0, 0, 440, 130])
    assert [[text for text, _ in row] for row in grid] == [
        ["指标", "单位", "2024", "2025"],
        ["能源使用情况", "", "", ""],
        ["综合能源消耗量²", "兆瓦时", "10,524,882.70", "19,233,654.46"],
        ["单位电信业务总量综合能耗", "千克", "64.5", "61.5"],
    ]


def test_selector_scores_scans_from_prescan_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    images = [Image.new("RGB", (300, 400), "white") for _ in range(30)]
    images[0].save(pdf_path, "PDF", save_all=True, append_images=images[1:], resolution=72)
    texts = []
    for index in range(30):
        text = f"Chapter page {index + 1}\nOur people, culture and community programmes."
        if index == 14:
            text = "温室气体排放\n范围一 温室气体排放量 tCO2e 1,204 1,180 1,096\n范围二 2,301 2,250 2,100\n" + "\n".join(
                f"站点 {site} {site * 101} {site * 97}" for site in range(1, 8))
        elif index == 21:
            text = "取水总量 立方米 1,200 1,150\n耗水总量 立方米 800 760\n能源消耗总量 兆瓦时 2,340 2,410 2,288"
        texts.append(text)
    calls = []

    def prescan(path: Path, page_count: int) -> list[str]:
        calls.append((path, page_count))
        return texts

    selection = Foundation16PageSelector().select(pdf_path, prescan=prescan)
    assert calls == [(pdf_path, 30)]
    assert selection.text_source == "vision_prescan" and selection.reason == "vision_prescan_scored"
    assert {14, 21} <= set(selection.page_indices or ())
    assert len(selection.page_indices or ()) < 30
    assert selection.provenance()["text_source"] == "vision_prescan"
    # Without a prescan a scan still falls back to whole-document OCR.
    assert Foundation16PageSelector().select(pdf_path).page_indices is None


def test_prescan_page_text_reads_rows_top_to_bottom() -> None:
    result = {"lines": [
        {"text": "2025", "bbox": [300, 41, 340, 55]},
        {"text": "指标", "bbox": [10, 40, 50, 54]},
        {"text": "范围一", "bbox": [10, 80, 60, 94]},
    ]}
    assert vision_prescan.page_text(result) == "指标\n2025\n范围一"


def test_text_layer_quality_rejects_unmapped_glyphs() -> None:
    clean = [Word("温室气体排放总量范围一", 0, 100, 10, 10)] * 6
    broken = [Word("(cid:12)(cid:40)(cid:77)", 0, 100, 10, 10)] * 6
    assert text_layer.text_layer_quality(clean) == (True, "ok")
    assert text_layer.text_layer_quality(broken)[0] is False


def test_on_device_ocr_fills_text_regions_the_text_layer_leaves_empty(tmp_path: Path) -> None:
    import pdfplumber

    pdf_path = tmp_path / "title.pdf"
    _write_text_pdf(pdf_path, [(595.0, 842.0, ["Energy and water", "Total energy consumption GWh 2,340"], False)])
    layout = {"page_index": 0, "width": 1190, "height": 1684, "images": {}, "boxes": [
        {"label": "text", "score": 0.9, "coordinate": [0.0, 0.0, 1190.0, 380.0]},
        {"label": "paragraph_title", "score": 0.5, "coordinate": [80.0, 900.0, 600.0, 960.0]},
    ]}
    with pdfplumber.open(pdf_path) as pdf:
        assert text_layer.page_result(pdf.pages[0], layout, 1) == (None, "text_region_without_text_layer")
        lines = [{"text": "环境绩效", "bbox": [90.0, 905.0, 300.0, 950.0]}, {"text": "elsewhere", "bbox": [90.0, 1500.0, 300.0, 1540.0]}]
        result, reason = text_layer.page_result(pdf.pages[0], layout, 1, lines)
    assert reason == "ok"
    title = next(b for b in result["prunedResult"]["parsing_res_list"] if b["block_label"] == "paragraph_title")
    assert title["block_content"] == "环境绩效" and title["text_source"] == "on_device_ocr"
    # Nothing legible in the region either (an ornament taken for a title): it is left out.
    with pdfplumber.open(pdf_path) as pdf:
        result, reason = text_layer.page_result(pdf.pages[0], layout, 1, [])
    assert reason == "ok"
    assert [b["block_label"] for b in result["prunedResult"]["parsing_res_list"]] == ["text"]


def test_separate_footnote_marks_stay_separate_numbers() -> None:
    lines = build_lines(_words(
        ("Energy", 0, 50, 100, 16), ("indirect", 52, 100, 100, 16), ("market-based", 102, 200, 100, 16),
        ("4", 201, 205, 94, 9), ("10", 207, 215, 94, 9),
        ("范围二", 10, 58, 140, 16), ("排放", 60, 90, 140, 16), ("总量", 92, 122, 140, 16),
        ("2", 123, 127, 134, 9), ("，", 127, 130, 134, 9), ("3", 130, 134, 134, 9),
    ))
    assert [line.text for line in lines] == ["Energy indirect market-based⁴,¹⁰", "范围二排放总量²，³"]


def test_glyphs_outside_the_visible_page_are_left_out(tmp_path: Path) -> None:
    import pdfplumber

    pdf_path = tmp_path / "spread.pdf"
    _write_text_pdf(pdf_path, [(595.0, 842.0, ["Energy and water", "Total energy consumption GWh 2,340"], False)])

    class VisibleLeftPart:
        """A page whose visible box ends at x=80 pt; the text runs past it."""

        def __init__(self, page):
            self.page = page
            self.bbox = (0.0, 0.0, 80.0, float(page.height))

        def dedupe_chars(self):
            return self.page

    with pdfplumber.open(pdf_path) as pdf:
        words, _ = text_layer.page_words(VisibleLeftPart(pdf.pages[0]), 1.0)
    assert words and max(word.x1 for word in words) <= 82
