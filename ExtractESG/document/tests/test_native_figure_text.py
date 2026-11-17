from esg_document.document.contracts import (
    BlockIR, BoundingBox, DocumentIR, DocumentIRMetadata, FigureIR,
    LocalPageForensics, LocalPdfForensics, LocalTextBlock, PageIR, SourceTrace,
)
from esg_document.document.native_figure_text import NativeFigureTextBuilder
from esg_document.document.local_forensics import LocalPdfProcessor


def box(x0, y0, x1, y1):
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1, unit="points")


def document():
    trace = SourceTrace(parser="test")
    rows = [
        ("Water consumption in 2024", 10, 20, 160),
        ("Total water consumption", 10, 50, 150),
        ("4.477 million cubic metres", 10, 70, 180),
    ]
    local = LocalPageForensics(page_index=0, width_points=300, height_points=300,
        native_text_blocks=[LocalTextBlock(text=text, bbox=box(x0, y, x1, y + 10))
            for text, x0, y, x1 in rows])
    return DocumentIR(metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test",
        local_forensics=LocalPdfForensics(pdf_path="source.pdf", exists=True, pages=[local])),
        pages=[PageIR(page_id="page-0001", page_index=0, page_number=1,
            width=300, height=300, source_trace=trace)],
        figures=[FigureIR(figure_id="figure-p0001-0001", page_index=0, order=0,
            bbox=box(0, 0, 200, 100), visual_type="chart", source_trace=trace)])


def test_native_figure_words_preserve_printed_values_lines_and_original_figure():
    source = document()
    original_figure = source.figures[0].model_copy(deep=True)
    NativeFigureTextBuilder().build(source)
    assert len(source.figures) == 1
    assert source.figures[0].bbox == original_figure.bbox
    block = source.blocks[0]
    assert block.text == "Water consumption in 2024\nTotal water consumption\n4.477 million cubic metres"
    assert block.figure_id == original_figure.figure_id
    assert block.source_trace.artifact_ids == ["artifact-source-pdf"]
    assert block.source_trace.notes == ["native_word_indices:0,1,2"]
    assert block.bbox == original_figure.bbox
    assert block.block_id in source.figures[0].element_block_ids
    NativeFigureTextBuilder().build(source)
    assert len(source.blocks) == 1


def test_subsumed_fragment_is_retired_with_snapshot_and_references_rewritten():
    source = document()
    fragment = BlockIR(block_id="block-p0001-0001", page_index=0, order=0,
        block_type="paragraph", text="Total water consumption", bbox=box(10, 50, 150, 60),
        figure_id=source.figures[0].figure_id, source_trace=SourceTrace(parser="ocr"))
    source.blocks = [fragment]
    source.pages[0].block_ids = [fragment.block_id]
    source.figures[0].element_block_ids = [fragment.block_id]
    NativeFigureTextBuilder().build(source)
    assert len(source.blocks) == 1
    replacement = source.blocks[0]
    assert replacement.block_id != fragment.block_id
    assert source.retired_entities[0].snapshot["text"] == fragment.text
    assert source.retired_entities[0].canonical_target_ids == [replacement.block_id]
    assert source.pages[0].block_ids == [replacement.block_id]
    assert source.figures[0].element_block_ids == [replacement.block_id]


def test_already_searchable_region_keeps_existing_evidence():
    source = document()
    complete = BlockIR(block_id="block-p0001-0001", page_index=0, order=0,
        block_type="paragraph", text="Water consumption in 2024 Total water consumption 4.477 million cubic metres",
        bbox=box(0, 0, 200, 100), source_trace=SourceTrace(parser="ocr"))
    source.blocks = [complete]
    NativeFigureTextBuilder().build(source)
    assert source.blocks == [complete]
    assert not source.retired_entities


def test_partial_overlap_or_unrepresented_ocr_text_is_preserved():
    source = document()
    independent = BlockIR(block_id="block-p0001-0001", page_index=0, order=0,
        block_type="paragraph", text="An additional OCR-only note.", bbox=box(10, 50, 150, 60),
        source_trace=SourceTrace(parser="ocr"))
    source.blocks = [independent]
    NativeFigureTextBuilder().build(source)
    assert independent in source.blocks
    assert not source.retired_entities


def test_nested_figure_regions_do_not_duplicate_the_same_native_words():
    source = document()
    outer = source.figures[0].model_copy(update={
        "figure_id": "figure-p0001-0002", "bbox": box(0, 0, 250, 150)
    })
    source.figures.append(outer)
    NativeFigureTextBuilder().build(source)
    assert len(source.figures) == 2 and len(source.blocks) == 1
    assert source.blocks[0].figure_id == "figure-p0001-0001"


def test_scanned_figure_without_native_words_does_not_create_text():
    source = document()
    source.metadata.local_forensics.pages[0].native_text_blocks = []
    NativeFigureTextBuilder().build(source)
    assert not source.blocks
    assert len(source.figures) == 1


def test_numerical_transcription_difference_is_not_retired_as_a_duplicate():
    source = document()
    contradictory = BlockIR(block_id="block-p0001-0001", page_index=0, order=0,
        block_type="paragraph", text="44.77 million cubic metres", bbox=box(10, 70, 180, 80),
        source_trace=SourceTrace(parser="ocr"))
    source.blocks = [contradictory]
    NativeFigureTextBuilder().build(source)
    assert contradictory in source.blocks
    assert not source.retired_entities
    assert any("4.477 million cubic metres" in block.text for block in source.blocks)


def test_native_glyph_metrics_keep_a_raised_year_footnote_separate(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

    pdf = tmp_path / "year-footnote.pdf"
    output = PdfWriter()
    page = output.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): output._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 7 Tf 10 50 Td (Reporting year) Tj ET\n"
        b"BT /F1 7 Tf 100 50 Td (2022) Tj ET\n"
        b"BT /F1 4.081 Tf 115.568 53.4694 Td (3) Tj ET")
    page[NameObject("/Contents")] = output._add_object(stream)
    output.write(pdf)
    source = document()
    source.metadata.local_forensics = LocalPdfProcessor().analyze(str(pdf))
    source.figures[0].bbox = box(0, 0, 200, 300)
    NativeFigureTextBuilder().build(source)
    assert "2022³" in source.blocks[0].text
    assert "20223" not in source.blocks[0].text
    words = source.metadata.local_forensics.pages[0].native_text_blocks
    assert next(word for word in words if word.text == "3").font_size_points < 5
    assert next(word for word in words if word.text == "2022").font_size_points == 7
