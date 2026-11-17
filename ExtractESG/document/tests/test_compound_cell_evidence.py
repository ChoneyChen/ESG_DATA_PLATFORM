from types import SimpleNamespace

from esg_contracts.evidence import ObjectReference
from esg_document.document.evidence_service import DocumentEvidenceService


def read_rows(rows):
    table_id = 'table-p0001-0001'
    raw = {'table_id': table_id, 'page_index': 0, 'row_count': len(rows),
        'column_count': len(rows[0]), 'header_row_indices': [0],
        'cells': [{'cell_id': f'cell-{r}-{c}', 'row_index': r, 'col_index': c,
            'row_span': 1, 'col_span': 1, 'page_index': 0, 'is_header': r == 0, 'text': text}
            for r, row in enumerate(rows) for c, text in enumerate(row)]}
    service = DocumentEvidenceService(lambda _: None)
    service._document = lambda _: (SimpleNamespace(snapshot_id='snapshot'), {},
        {table_id: raw}, {}, {}, {}, {})
    return service.read('ir-test', ObjectReference(object_id=table_id,
        kind='table', page_index=0, score=1))


def test_compound_body_cell_exposes_exact_source_literals_and_complete_labels():
    frame = read_rows([['Indicator', '2024'],
        ['Non-renewable energy consumption was 123.45 MWh.', '456']])
    local = next(row for row in frame.rows if any(cell.char_start is not None for cell in row.cells))
    cell = local.cells[0]
    assert cell.raw_text == '123.45'
    assert cell.source_object_type == 'cell' and cell.source_object_id == 'cell-1-0'
    original = next(source for source in frame.texts if source.object_id == cell.source_object_id)
    assert original.text[cell.char_start:cell.char_end] == '123.45'
    link = next(link for link in frame.context_links if link.cell_id == cell.cell_id)
    assert link.relation == 'same_clause'
    selected = {source.span_id: source for source in frame.texts}[link.source_ids[0]]
    assert selected.text == 'Non-renewable energy consumption was 123.45 MWh.'
    assert local.unit_raw == 'MWh'
    assert any(value.cell_id == 'cell-1-1' and value.char_start is None
        for row in frame.rows for value in row.cells)


def test_combined_quantities_and_unit_scales_keep_existing_cell_protocol():
    frame = read_rows([['Indicator', 'Value'], ['Emissions', '100 kg'],
        ['Energy', '10,000 kJ'], ['Range', '100±10kg'], ['Value', '2000']])
    values = [cell for row in frame.rows for cell in row.cells]
    assert [cell.raw_text for cell in values if cell.cell_id.startswith('cell-') and cell.char_start is None] == [
        'Value', '100 kg', '10,000 kJ', '100±10kg', '2000']
    assert not any(cell.char_start is not None for cell in values)


def test_printed_reference_column_uses_column_ids_for_candidate_roles():
    frame = read_rows([['Metric', '2024', 'GRI'], ['Water discharge', '123', '303-4']])
    reference = next(cell for row in frame.rows for cell in row.cells if cell.cell_id == 'cell-1-2')
    assert reference.candidate_kind == 'identifier'
    assert not any(cell.source_object_id == 'cell-1-2' for row in frame.rows for cell in row.cells)


def test_unparsed_denominator_in_physical_unit_column_produces_no_extra_measurement():
    frame=read_rows([['Metric','Unit','2024'],
        ['Energy intensity','MWh/RMB 10,000','123'],
        ['Water intensity','m³/RMB 10,000','456']])
    assert not any(cell.source_object_id in {'cell-1-1','cell-2-1'}
        for row in frame.rows for cell in row.cells)
    assert any(cell.raw_text=='123' for row in frame.rows for cell in row.cells)
