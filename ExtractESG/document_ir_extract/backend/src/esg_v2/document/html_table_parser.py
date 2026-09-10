from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass(frozen=True)
class ParsedTableCell:
    row_index: int
    col_index: int
    text: str
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    image_sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedHtmlTable:
    row_count: int
    column_count: int
    cells: list[ParsedTableCell]
    image_sources: list[str]
    quality_flags: list[str] = field(default_factory=list)


@dataclass
class _RawCell:
    text_parts: list[str]
    row_span: int
    col_span: int
    is_header: bool
    image_sources: list[str]


class _FirstTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.finished = False
        self.rows: list[list[_RawCell]] = []
        self.current_row: list[_RawCell] | None = None
        self.current_cell: _RawCell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value for key, value in attrs}
        if tag == "table":
            if self.finished:
                return
            self.table_depth += 1
            return
        if self.table_depth != 1:
            return
        if tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"}:
            self.current_cell = _RawCell(
                text_parts=[],
                row_span=self._span(attributes.get("rowspan")),
                col_span=self._span(attributes.get("colspan")),
                is_header=tag == "th",
                image_sources=[],
            )
        elif tag == "br" and self.current_cell is not None:
            self.current_cell.text_parts.append("\n")
        elif tag == "img" and self.current_cell is not None:
            src = (attributes.get("src") or "").strip()
            if src:
                self.current_cell.image_sources.append(src)
            alt = (attributes.get("alt") or "").strip().strip('"')
            if alt and alt.lower() not in {"image", "img", "figure", "图片", "圖像"}:
                self.current_cell.text_parts.append(alt)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            if self.table_depth == 1:
                self._finish_row()
                self.finished = True
            self.table_depth = max(0, self.table_depth - 1)
            return
        if self.table_depth != 1:
            return
        if tag in {"td", "th"}:
            self._finish_cell()
        elif tag == "tr":
            self._finish_row()

    def handle_data(self, data: str) -> None:
        if self.table_depth == 1 and self.current_cell is not None:
            self.current_cell.text_parts.append(data)

    def _finish_cell(self) -> None:
        if self.current_cell is None:
            return
        if self.current_row is None:
            self.current_row = []
        self.current_row.append(self.current_cell)
        self.current_cell = None

    def _finish_row(self) -> None:
        self._finish_cell()
        if self.current_row:
            self.rows.append(self.current_row)
        self.current_row = None

    @staticmethod
    def _span(value: str | None) -> int:
        try:
            return max(1, int(value or 1))
        except (TypeError, ValueError):
            return 1


def parse_html_table(markup: str) -> ParsedHtmlTable | None:
    if not re.search(r"<table\b", markup, re.IGNORECASE):
        return None
    parser = _FirstTableParser()
    parser.feed(markup)
    parser.close()
    if not parser.rows:
        return None

    occupied: set[tuple[int, int]] = set()
    cells: list[ParsedTableCell] = []
    max_row = 0
    max_col = 0
    image_sources: list[str] = []
    quality_flags: list[str] = []
    has_explicit_headers = any(cell.is_header for row in parser.rows for cell in row)

    for row_index, raw_row in enumerate(parser.rows):
        col_index = 0
        for raw_cell in raw_row:
            while (row_index, col_index) in occupied:
                col_index += 1
            text = _normalize_cell_text("".join(raw_cell.text_parts))
            col_span = _available_col_span(
                occupied,
                row_index=row_index,
                col_index=col_index,
                row_span=raw_cell.row_span,
                requested_col_span=raw_cell.col_span,
            )
            if col_span != raw_cell.col_span:
                quality_flags.append("html_col_span_clamped_around_rowspan")
            cell = ParsedTableCell(
                row_index=row_index,
                col_index=col_index,
                text=text,
                row_span=raw_cell.row_span,
                col_span=col_span,
                is_header=raw_cell.is_header or (not has_explicit_headers and row_index == 0),
                image_sources=list(dict.fromkeys(raw_cell.image_sources)),
            )
            cells.append(cell)
            image_sources.extend(cell.image_sources)
            for span_row in range(row_index, row_index + raw_cell.row_span):
                for span_col in range(col_index, col_index + col_span):
                    occupied.add((span_row, span_col))
            max_row = max(max_row, row_index + raw_cell.row_span)
            max_col = max(max_col, col_index + col_span)
            col_index += col_span

    return ParsedHtmlTable(
        row_count=max_row,
        column_count=max_col,
        cells=cells,
        image_sources=list(dict.fromkeys(image_sources)),
        quality_flags=list(dict.fromkeys(quality_flags)),
    )


def _available_col_span(
    occupied: set[tuple[int, int]],
    *,
    row_index: int,
    col_index: int,
    row_span: int,
    requested_col_span: int,
) -> int:
    """Keep a malformed colspan from crossing cells reserved by an earlier rowspan."""

    width = 0
    for offset in range(requested_col_span):
        candidate_col = col_index + offset
        if any(
            (candidate_row, candidate_col) in occupied
            for candidate_row in range(row_index, row_index + row_span)
        ):
            break
        width += 1
    return max(1, width)


def _normalize_cell_text(text: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()
