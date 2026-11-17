from esg_contracts.evidence import TextEvidence, EvidenceFrame, EvidenceContextLink, EvidenceCell
from esg_document.document.contracts import LocalTextBlock
from esg_document.document.literals import numeric_rows, text_spans
from esg_document.document.native_figure_text import NativeFigureTextBuilder
from esg_document.document.text_geometry import context_links, native_text_geometry, NativeTextGeometry
from test_native_figure_text import document, box


def test_empty_context_links_preserve_existing_frame_serialization():
    frame = EvidenceFrame(object_id='block-1', kind='text', page_index=0, digest='test')
    assert 'context_links' not in frame.model_dump(mode='json')
    linked = frame.model_copy(update={'context_links': [EvidenceContextLink(
        cell_id='block-1@0:1', source_ids=['block-1@2:8'], relation='same_clause')]})
    assert linked.model_dump(mode='json')['context_links'][0]['source_ids'] == ['block-1@2:8']


def figure_context(*, wrapped=False):
    source = document()
    local = source.metadata.local_forensics.pages[0]
    entries = [('2024', 0, 0, 20, 6), ('GHG emissions', 25, 0, 100, 6),
        ('Unit: tCO2e', 0, 10, 60, 6),
        ('100', 20, 50, 40, 10), ('200', 80, 40, 100, 10),
        ('Non-' if wrapped else 'Direct', 20, 105, 50, 6),
        ('Indirect', 80, 105, 115, 6), ('7.82', 20, 140, 40, 10),
        ('Intensity', 50, 140, 95, 6)]
    if wrapped:
        entries.append(('renewable', 20, 115, 55, 6))
    local.native_text_blocks = [LocalTextBlock(text=text, bbox=box(x, y, right, y + size),
        baseline_points=y + .8 * size, font_size_points=size)
        for text, x, y, right, size in entries]
    local.native_filled_rectangles = [box(10, 20, 60, 100), box(70, 10, 120, 100),
        box(0, 135, 130, 155)]
    source.figures[0].bbox = box(0, 0, 150, 180)
    NativeFigureTextBuilder().build(source)
    block = source.blocks[0]
    text = TextEvidence(object_id=block.block_id, text=block.text, page_index=0,
        bbox={key: getattr(block.bbox, key) for key in ('x0', 'y0', 'x1', 'y1')})
    geometry = native_text_geometry(text, block.model_dump(mode='json'), local.model_dump(mode='json'))
    rows = [row.model_copy(update={'cells': [cell.model_copy(update={
        'bbox': geometry.literal_bbox(cell)}) for cell in row.cells]}) for row in numeric_rows(text)]
    return text, geometry, rows


def test_bar_columns_supply_only_their_actual_descriptors_and_literal_boxes():
    text, geometry, rows = figure_context()
    sources = {source.span_id: source for source in geometry.fragments}
    links = context_links(rows, text_spans(text), geometry)
    cells = {cell.raw_text: cell for row in rows for cell in row.cells}
    direct = [sources[source].text for link in links if link.cell_id == cells['100'].cell_id
        and link.relation == 'column' for source in link.source_ids]
    indirect = [sources[source].text for link in links if link.cell_id == cells['200'].cell_id
        and link.relation == 'column' for source in link.source_ids]
    assert direct == ['Direct']
    assert indirect == ['Indirect']
    assert cells['100'].bbox == {'x0': 20, 'y0': 50, 'x1': 40, 'y1': 60}
    assert any(link.cell_id == cells['100'].cell_id and link.relation == 'region'
        and sources[link.source_ids[0]].text == '2024 GHG emissions' for link in links)
    assert not any(link.cell_id == cells['7.82'].cell_id for link in links)
    for source in sources.values():
        assert text.text[source.char_start:source.char_start + len(source.text)] == source.text


def test_wrapped_qualifier_remains_in_the_required_descriptor_group():
    text, geometry, rows = figure_context(wrapped=True)
    sources = {source.span_id: source for source in geometry.fragments}
    cell = next(cell for row in rows for cell in row.cells if cell.raw_text == '100')
    links = [link for link in context_links(rows, text_spans(text), geometry)
        if link.cell_id == cell.cell_id and link.relation == 'column']
    assert len(links) == 1
    assert [sources[source].text for source in links[0].source_ids] == ['Non-', 'renewable']


def test_complete_literal_inventory_controls_sentence_links_and_figures_do_not_gain_them():
    source = TextEvidence(object_id='block-1', page_index=0,
        text='Energy consumption was 100 or 200 MWh.')
    rows = numeric_rows(source)
    assert not context_links(rows, text_spans(source), NativeTextGeometry())
    source = source.model_copy(update={'text': 'Energy consumption was 100 MWh.'})
    rows = numeric_rows(source)
    assert len(context_links(rows, text_spans(source), NativeTextGeometry())) == 1
    assert not context_links(rows, text_spans(source), NativeTextGeometry(is_native_figure=True))


def test_raised_footnote_cannot_supply_the_numeric_prefix_box_of_a_measurement():
    source = TextEvidence(object_id='block-1', page_index=0,
        text='Total\n5,011,000.40\nOther⁵')
    block = {'source_trace': {'parser': 'pdf-native-figure-text',
        'notes': ['native_word_indices:0,1,2,3']}}
    raw = [('Total', 10, 10, 40, 8), ('5,011,000.40', 10, 25, 60, 8),
        ('Other', 10, 40, 40, 8), ('5', 40, 37, 43, 4)]
    page = {'native_text_blocks': [{'text': text,
        'bbox': {'x0': x, 'y0': y, 'x1': right, 'y1': y + size},
        'baseline_points': y + .8 * size, 'font_size_points': size}
        for text, x, y, right, size in raw]}
    geometry = native_text_geometry(source, block, page)
    cell = next(cell for row in numeric_rows(source) for cell in row.cells
        if cell.raw_text == '5,011,000.40')
    assert geometry.literal_bbox(cell) == {'x0': 10, 'y0': 25, 'x1': 60, 'y1': 33}


def test_fragment_box_uses_its_source_words_with_shifted_font_metrics():
    source = TextEvidence(object_id='block-1', page_index=0, text='Upper\nLower')
    block = {'source_trace': {'parser': 'pdf-native-figure-text',
        'notes': ['native_word_indices:0,1']}}
    page = {'native_text_blocks': [
        {'text': text, 'bbox': {'x0': 0, 'y0': top, 'x1': 30, 'y1': top + 8},
            'baseline_points': top - 2, 'font_size_points': 8}
        for text, top in [('Upper', 10), ('Lower', 20)]]}
    geometry = native_text_geometry(source, block, page)
    lower = next(fragment for fragment in geometry.fragments if fragment.text == 'Lower')
    assert lower.bbox == {'x0': 0, 'y0': 20, 'x1': 30, 'y1': 28}


def test_closed_convex_enclosure_keeps_its_value_and_descriptor_together():
    fragments = [TextEvidence(object_id='figure-text', page_index=0,
        span_id=identity, text=text, bbox=bbox) for identity, text, bbox in [
            ('total', 'Total', {'x0': 80, 'y0': 70, 'x1': 120, 'y1': 80}),
            ('other', 'Other', {'x0': 160, 'y0': 70, 'x1': 190, 'y1': 80}),
            ('unit', 'Unit: MWh', {'x0': 10, 'y0': 10, 'x1': 60, 'y1': 20})]]
    inner = {'bbox': {'x0': 40, 'y0': 40, 'x1': 160, 'y1': 160},
        'commands': [['m', [100, 40]], ['l', [160, 100]], ['l', [100, 160]],
            ['l', [40, 100]], ['h']]}
    outer = {'bbox': {'x0': 0, 'y0': 0, 'x1': 220, 'y1': 200},
        'commands': [['m', [0, 0]], ['l', [220, 0]], ['l', [220, 200]], ['l', [0, 200]], ['h']]}
    geometry = NativeTextGeometry(is_native_figure=True, fragments=fragments, paths=[inner, outer])
    cells = [EvidenceCell(cell_id=identity, column_id='value', raw_text=value, bbox=bbox)
        for identity, value, bbox in [
            ('value-1', '100', {'x0': 80, 'y0': 90, 'x1': 120, 'y1': 100}),
            ('value-2', '200', {'x0': 170, 'y0': 90, 'x1': 190, 'y1': 100})]]
    links = geometry.enclosure_links(cells)
    assert [(link.cell_id, link.source_ids) for link in links if link.relation == 'column'] == [
        ('value-1', ['total'])]
    assert any(link.cell_id == 'value-1' and link.source_ids == ['unit'] for link in links)
    # A concave path's bounding box cannot establish filled-area containment.
    inner['commands'] = [['m', [40, 40]], ['l', [160, 40]], ['l', [160, 80]],
        ['l', [60, 80]], ['l', [60, 160]], ['l', [40, 160]], ['h']]
    assert not geometry.enclosure_links(cells)
