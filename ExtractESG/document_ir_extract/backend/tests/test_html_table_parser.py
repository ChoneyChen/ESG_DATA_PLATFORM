from __future__ import annotations

from esg_v2.document.html_table_parser import parse_html_table


def test_malformed_colspan_is_clamped_before_existing_rowspan_cells() -> None:
    markup = """
    <table>
      <tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td><td>F</td></tr>
      <tr>
        <td rowspan="2">RI 3</td><td>3-1</td><td>治理</td>
        <td rowspan="2" colspan="3">不适用从略</td>
      </tr>
      <tr><td>3-2</td><td colspan="4">可持续发展治理</td></tr>
    </table>
    """

    table = parse_html_table(markup)

    assert table is not None
    assert table.row_count == 3
    assert table.column_count == 6
    repaired = next(cell for cell in table.cells if cell.text == "可持续发展治理")
    assert (repaired.row_index, repaired.col_index, repaired.col_span) == (2, 2, 1)
    assert table.quality_flags == ["html_col_span_clamped_around_rowspan"]

    occupied: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        for row in range(cell.row_index, cell.row_index + cell.row_span):
            for column in range(cell.col_index, cell.col_index + cell.col_span):
                assert (row, column) not in occupied
                occupied[(row, column)] = cell.text
