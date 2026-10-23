"""Compact, model-readable views of fixed Document IR tables."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from esg_targeted.foundation.catalog import METHOD_TERMS
from esg_targeted.foundation.textnorm import fold

PERIOD_RE = re.compile(r"(?<!\d)(?:FY\s?)?((?:19|20)\d{2})(?:\s*[/／-]\s*(\d{4}|\d{2}))?(?!\d)", re.I)
YEAR_HEADER_RE = re.compile(
    r"^(?:FY\s?)?(?:19|20)\d{2}(?:\s*[/／-]\s*\d{2,4})?\s*(?:年|年度|财年|財年|E|A)?\s*(?:合计|合計|总计|總計|total)?$",
    re.I,
)
NUMBER_RE = re.compile(
    r"^[<>≤≥~约約]?\s*[\(（]?\s*[-−–+]?\s*(?:\d{1,3}(?:[,，\s]\d{3})+|\d+)(?:\.\d+)?\s*[\)）]?\s*[%％]?\s*[*#¹²³⁴⁵⁶⁷⁸⁹⁰]*$"
)
NULL_TEXTS = {
    "", "/", "-", "—", "–", "--", "－", "／", "n/a", "na", "n.a.", "nil", "none", "不适用", "无", "暂无",
    "未披露", "*", "—*", "-*", "/*", "n.d.", "nd",
}
LABEL_HEADER_RE = re.compile(r"^(?:指标|指標|项目|項目|类别|類別|分类|内容|名称|indicator|indicators|item|items|kpi|kpis|metric|category|description)s?$", re.I)
UNIT_HEADER_RE = re.compile(r"^(?:单位|單位|计量单位|unit|units|unit of measurement)$", re.I)
UNIT_TOKEN_RE = re.compile(
    r"(吨|噸|立方米|升|千瓦时|兆瓦时|吉瓦时|瓦时|焦|标准煤|标煤|二氧化碳|co2|co_|%|％|元|人|次|小时|"
    r"\bm3\b|m³|\bkwh\b|\bmwh\b|\bgwh\b|\btwh\b|\bgj\b|\btj\b|\bmj\b|\btce\b|\btoe\b|\bkg\b|\bt\b|\bml\b|\bmegalit)",
    re.I,
)
NOTE_START_RE = re.compile(r"^\s*(?:注|註|备注|說明|说明|note|notes|source|\*|※|[¹²³⁴⁵⁶⁷⁸⁹]|\(?\d{1,2}[.、)）])", re.I)
FOOTNOTE_MARK_RE = re.compile(r"^[\(（]\s*\d{1,2}\s*[\)）]$")
SCOPE_TERMS = ("范围", "scope", "location", "market", "地点", "位置", "市场", "排放因子", "emission factor", "电网")


UNIT_PARENTHETICAL_RE = re.compile(r"^([^()（）]{1,24}?)\s*[（(].*$")


def clean_unit(text: str | None) -> str:
    """The unit cell sometimes carries a parenthetical after the unit itself
    (`m3 (m3 per ...)`); the printed unit is the head, not the explanation."""
    value = clean_text(text)
    match = UNIT_PARENTHETICAL_RE.match(value)
    if match and match.group(1).strip():
        head = match.group(1).strip()
        if re.search(r"[A-Za-z\u4e00-\u9fff]", head):
            return head
    return value


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\\n", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_null_text(text: str) -> bool:
    return text.strip().casefold() in NULL_TEXTS


SUPERSCRIPT_MARK_RE = re.compile(r"\$?\s*\^\s*\{+\s*\(?\s*[\dA-Za-z]{1,3}\s*\)?\s*\}+\s*\$?"
                                 r"|[⁽]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[,，、][⁰¹²³⁴⁵⁶⁷⁸⁹]+)*[⁾]?")
TRAILING_SUPERSCRIPT_RE = re.compile(r"(?<=[\d%％)）])\s*[¹²³⁴⁵⁶⁷⁸⁹⁰]+(?:[,，、][¹²³⁴⁵⁶⁷⁸⁹⁰]+)*$")
TRAILING_FOOTNOTE_RE = re.compile(r"^(.*?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+))\s+\(?\d{1,2}\)?$")


def number_text(text: str | None) -> str:
    """Cell text without footnote marks printed after the figure ("8,432,549 4", "2,557 $^{(7)}$")."""
    raw = TRAILING_SUPERSCRIPT_RE.sub("", (text or "").strip()).strip()
    if NUMBER_RE.match(raw):
        return raw
    stripped = SUPERSCRIPT_MARK_RE.sub("", raw).strip()
    match = TRAILING_FOOTNOTE_RE.match(stripped)
    if match:
        stripped = match.group(1).strip()
    return stripped if NUMBER_RE.match(stripped) else raw


def is_number_text(text: str) -> bool:
    text = number_text(text)
    return bool(text) and bool(NUMBER_RE.match(text)) and not YEAR_HEADER_RE.match(text.replace(",", ""))


def parse_number(text: str | None) -> Decimal | None:
    if not text:
        return None
    raw = number_text(text)
    if not NUMBER_RE.match(raw):
        return None
    negative = bool(re.search(r"[\(（].*[\)）]", raw)) or bool(re.match(r"^[<>≤≥~约約]?\s*[\(（]?\s*[-−–]", raw))
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits or digits.count(".") > 1:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


CN_DIGITS = {"零": "0", "〇": "0", "○": "0", "Ｏ": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
             "六": "6", "七": "7", "八": "8", "九": "9"}
CN_YEAR_RE = re.compile(r"([零〇○Ｏ一二三四五六七八九]{4})(?=\s*年)")


def arabic_years(text: str | None) -> str:
    """二零二五年 -> 2025年; other characters unchanged."""
    return CN_YEAR_RE.sub(lambda m: "".join(CN_DIGITS[ch] for ch in m.group(1)), text or "")


def period_years(text: str) -> list[int]:
    years = []
    for match in PERIOD_RE.finditer(arabic_years(text)):
        start = int(match.group(1))
        end = match.group(2)
        if end:
            end_year = int(end) if len(end) == 4 else (start // 100) * 100 + int(end)
            if start < end_year <= start + 1:
                years.append(end_year)
                continue
        years.append(start)
    return years


@dataclass
class CellRef:
    cell_id: str
    row: int
    col: int
    text: str
    row_span: int
    col_span: int


@dataclass
class DataLine:
    row: int
    rid: str
    label: str
    unit: str | None
    section: str | None
    values: dict[int, CellRef]
    ocr_label: str | None = None  # set when the label was corrected from the PDF text layer


@dataclass
class Note:
    nid: str
    block_id: str
    text: str


@dataclass
class TableView:
    table_id: str
    page_index: int
    page_number: int
    n_rows: int
    n_cols: int
    header_rows: list[int]
    header_lines: list[list[tuple[int, int, str]]]
    value_cols: list[int]
    unit_col: int | None
    label_cols: list[int]
    lines: list[DataLine]
    sections: dict[int, str]
    headers_aligned: bool
    column_paths: dict[int, list[str]]
    headings: list[str]
    caption: str | None
    notes: list[Note] = field(default_factory=list)
    arithmetic: list[str] = field(default_factory=list)
    sums: list["ColumnSum"] = field(default_factory=list)
    continues_from: str | None = None
    title: str | None = None
    trailing_text: str | None = None
    heading_above: str | None = None
    headers_rebuilt: bool = False
    text_layer_agreement: float | None = None
    unconfirmed_cells: set[str] = field(default_factory=set)
    corrected_cells: dict[str, str] = field(default_factory=dict)

    @property
    def numeric_line_count(self) -> int:
        return sum(1 for line in self.lines if any(is_number_text(c.text) for c in line.values.values()))

    def search_text(self) -> str:
        parts = [*self.headings, self.caption or "", self.title or "", *self.sections.values()]
        for header in self.header_lines:
            parts.extend(text for _, _, text in header)
        for line in self.lines:
            parts.append(line.label)
            if line.unit:
                parts.append(line.unit)
        return fold(" ".join(parts))

    def header_years(self) -> list[int]:
        years: list[int] = []
        for header in self.header_lines:
            for _, _, text in header:
                years.extend(period_years(text))
        return years


def _grid(table: dict) -> tuple[dict[tuple[int, int], CellRef], dict[tuple[int, int], CellRef]]:
    origin: dict[tuple[int, int], CellRef] = {}
    covered: dict[tuple[int, int], CellRef] = {}
    for cell in table.get("cells") or []:
        ref = CellRef(
            cell["cell_id"], int(cell["row_index"]), int(cell["col_index"]), clean_text(cell.get("text")),
            max(1, int(cell.get("row_span") or 1)), max(1, int(cell.get("col_span") or 1)),
        )
        origin[(ref.row, ref.col)] = ref
        for r in range(ref.row, ref.row + ref.row_span):
            for c in range(ref.col, ref.col + ref.col_span):
                covered[(r, c)] = ref
    return origin, covered


TOTAL_LABEL_RE = re.compile(r"(?:合计|合計|总计|總計|汇总|匯總|小计|小計|总额|集团|集團|total|group|consolidated)", re.I)
REMARK_HEADER_RE = re.compile(r"(?:备注|備註|说明|說明|remark|note|comment)", re.I)


def _sibling_run_straddles_parents(header_rows, origin, covered, value_cols) -> bool:
    """Detect an OCR colspan error that splits one sibling run across periods.

    A run like entity | entity | 合计 under 2025 (colspan 2) | 2024 places the group
    total under the next year. A parent whose only child is a total-like label,
    directly after a parent with non-total children, is not a reliable layout.
    """
    for upper, lower in zip(header_rows, header_rows[1:]):
        children: dict[str, list[str]] = {}
        order: list[str] = []
        for c in value_cols:
            child = origin.get((lower, c))
            parent = covered.get((upper, c))
            if child is None or not child.text or parent is None or parent.row > upper:
                continue
            if parent.row + parent.row_span - 1 >= lower:
                continue
            if parent.cell_id not in children:
                children[parent.cell_id] = []
                order.append(parent.cell_id)
            children[parent.cell_id].append(child.text)
        for previous, current in zip(order, order[1:]):
            before, after = children[previous], children[current]
            if len(after) == 1 and TOTAL_LABEL_RE.search(after[0]) and not any(TOTAL_LABEL_RE.search(t) for t in before):
                return True
    return False


def _is_serial_column(texts: list[str]) -> bool:
    numbers = [parse_number(t) for t in texts if is_number_text(t)]
    return (len(numbers) >= 3 and len(numbers) >= 0.6 * len(texts) and all(n == n.to_integral_value() for n in numbers)
            and all(b - a == 1 for a, b in zip(numbers, numbers[1:])))


def rebuild_column_paths(header_lines, value_cols, unit_col, label_cols) -> dict[int, list[str]] | None:
    """Column paths from header reading order when OCR shifted spanning headers.

    Wide tables repeat one period cycle per entity group ("Total | Site A |
    Site B" over "2025 2024 2023" each). When the period labels in reading
    order number exactly the value columns and repeat one cycle, and the group
    labels times the cycle length do too, each value column's group and period
    follow from order alone, whatever columns OCR attached them to.
    """
    first_value = min(value_cols) if value_cols else 0
    years, groups = [], []
    for header in header_lines:
        for col, span, text in header:
            if YEAR_HEADER_RE.match(text.replace(",", "")) and len(period_years(text)) == 1:
                years.append(text)
            elif (col >= first_value - 1 and col not in label_cols and col != unit_col and not UNIT_HEADER_RE.match(text)
                  and not LABEL_HEADER_RE.match(text)):
                groups.append(text)
    cycle = len(dict.fromkeys(years))
    if len(value_cols) < 4 or len(years) != len(value_cols) or cycle < 2 or len(years) % cycle:
        return None
    if any(years[i] != years[i % cycle] for i in range(len(years))):
        return None
    if groups and len(groups) * cycle != len(value_cols):
        return None
    return {c: ([groups[i // cycle]] if groups else []) + [years[i]] for i, c in enumerate(value_cols)}


def header_text_at(header_rows, origin, covered, col: int) -> str:
    return " ".join(covered[(r, col)].text for r in header_rows if (r, col) in covered and covered[(r, col)].text)


LABEL_WORD_RE = re.compile(r"(?:排放|消耗|使用|用量|总量|范围|範圍|能源|用水|取水|耗水|排水|废|廢|emission|consum|energy|water|waste|scope|total)", re.I)


INLINE_UNIT_RE = re.compile(r"[（(]\s*([^（）()]{1,16})\s*[)）]\s*$")


def _is_unit_like(text: str | None) -> bool:
    if not text:
        return False
    compact = re.sub(r"[\s$\\{}_^]+", "", text)
    if not compact or len(compact) > 16 or is_number_text(text) or not UNIT_TOKEN_RE.search(text):
        return False
    if LABEL_WORD_RE.search(text):
        return False
    # "氮氧化物（NOx）排放量（万吨）" is a row label whose unit is in
    # parentheses, not a unit cell. A unit column holds units alone; a cell
    # that keeps a substantive name after its trailing unit belongs to a label
    # column, otherwise every row loses its name to the unit column.
    match = INLINE_UNIT_RE.search(text.strip())
    if match and UNIT_TOKEN_RE.search(match.group(1)):
        stem = text[:match.start()]
        if len(re.sub(r"[^一-鿿]", "", stem)) >= 4:
            return False
    return True


def composed_tables(tables: list[dict]) -> list[dict]:
    """Fold each horizontal continuation group into one table dict.

    A table wider than a page is laid out across a spread: the left page
    holds the row labels and the leading entity columns, the right page holds
    further entity columns and repeats the period headers. The IR links the
    two, but a view built from either half alone reads a sub-entity's column
    as if it were the total. Merging keeps every cell and its id, so evidence
    still points at the page the value was printed on.
    """
    groups: dict[str, list[dict]] = {}
    for table in tables:
        group = table.get("continuation_group_id")
        if group and table.get("continuation_axis") == "horizontal":
            groups.setdefault(group, []).append(table)
    merged: dict[str, dict] = {}
    dropped: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        by_id = {t["table_id"]: t for t in members}
        root = next((t for t in members if (t.get("continues_from_table_id") or "") not in by_id), None)
        if root is None:
            continue
        chain, seen = [], set()
        current = root
        while current and current["table_id"] not in seen:
            chain.append(current)
            seen.add(current["table_id"])
            current = by_id.get(current.get("continues_to_table_id") or "")
        if len(chain) < 2:
            continue
        merged[root["table_id"]] = _compose_horizontal(chain)
        dropped.update(t["table_id"] for t in chain[1:])
    return [merged.get(t["table_id"], t) for t in tables if t["table_id"] not in dropped]


def _compose_horizontal(chain: list[dict]) -> dict:
    root = chain[0]
    cells: list[dict] = [
        {**cell, "row_index": int(cell["row_index"]), "col_index": int(cell["col_index"])}
        for cell in root.get("cells") or []
    ]
    column_offset = int(root.get("column_count") or 0)
    rows = int(root.get("row_count") or 0)
    headers = set(root.get("header_row_indices") or [])
    aligned = True
    for table in chain[1:]:
        shift, aligned_here = _row_shift(root, table)
        aligned = aligned and aligned_here
        for cell in table.get("cells") or []:
            cells.append({
                **cell,
                "row_index": int(cell["row_index"]) + shift,
                "col_index": int(cell["col_index"]) + column_offset,
            })
            if int(cell["row_index"]) in set(table.get("header_row_indices") or []):
                headers.add(int(cell["row_index"]) + shift)
        column_offset += int(table.get("column_count") or 0)
        rows = max(rows, int(table.get("row_count") or 0) + shift)
    flags = list(dict.fromkeys([
        *(root.get("quality_flags") or []),
        "horizontal_columns_appended",
        *([] if aligned else ["horizontal_row_alignment_unresolved"]),
    ]))
    return {
        **root,
        "row_count": rows,
        "column_count": column_offset,
        "cells": cells,
        "header_row_indices": sorted(headers),
        "page_indices": sorted({int(t.get("page_index") or 0) for t in chain}),
        "quality_flags": flags,
    }


def _derived_header_rows(table: dict) -> list[int]:
    """Leading rows before the first row that carries a value.

    The IR's header_row_indices is often only row 0 even though a table's
    period row sits just below it, so derive the band the same way the view
    does rather than trusting the stored list.
    """
    n_rows = int(table.get("row_count") or 0)
    n_cols = int(table.get("column_count") or 0)
    by_position = {
        (int(cell["row_index"]), int(cell["col_index"])): cell.get("text") or ""
        for cell in table.get("cells") or []
    }
    rows: list[int] = []
    for r in range(min(n_rows, 4)):
        texts = [by_position.get((r, c), "") for c in range(1, n_cols)]
        if any(is_number_text(t) and not FOOTNOTE_MARK_RE.match(t) for t in texts):
            break
        rows.append(r)
    return rows


def _row_shift(root: dict, table: dict) -> tuple[int, bool]:
    """Shift so the continuation's data band starts level with the root's.

    Both halves usually share a header count, but a section title that only
    the left page prints moves the right page's data up by a row. Align on
    the first data row; report an unresolved alignment when the data bands
    differ in size, rather than silently pairing the wrong rows.
    """
    def band(item: dict) -> tuple[list[int], list[int]]:
        rows = range(int(item.get("row_count") or 0))
        header = set(_derived_header_rows(item))
        return ([r for r in rows if r in header], [r for r in rows if r not in header])

    root_headers, root_data = band(root)
    table_headers, table_data = band(table)
    if root_data and table_data and len(root_data) == len(table_data):
        shift = root_data[0] - table_data[0]
        if abs(shift) <= 3:
            return shift, True
    # Header counts differ; fall back to the header band, which is what the
    # period columns are keyed on.
    if root_headers and table_headers:
        return root_headers[0] - table_headers[0], False
    return 0, False


def build_table_view(table: dict, page_payload: dict | None) -> TableView | None:
    n_rows = int(table.get("row_count") or 0)
    n_cols = int(table.get("column_count") or 0)
    if n_rows < 2 or n_cols < 2:
        return None
    origin, covered = _grid(table)

    def text_at(r: int, c: int) -> str:
        ref = origin.get((r, c))
        return ref.text if ref else ""

    header_rows: list[int] = []
    for r in range(n_rows):
        texts = [text_at(r, c) for c in range(n_cols)]
        # A bare "(3)" over a heading is a footnote mark, not a parenthesised
        # negative; counting it as a value would end the header band before
        # the period row and leave every column header empty.
        has_value = any(is_number_text(t) and not FOOTNOTE_MARK_RE.match(t) for t in texts[1:])
        if has_value:
            break
        header_rows.append(r)
        if len(header_rows) >= 4:
            break
    data_rows = [r for r in range(n_rows) if r not in header_rows]
    if not data_rows:
        return None

    numeric = [0] * n_cols
    textual = [0] * n_cols
    unitish = [0] * n_cols
    filled = [0] * n_cols
    for r in data_rows:
        for c in range(n_cols):
            t = text_at(r, c)
            filled[c] += bool(t)
            if not t or is_null_text(t):
                continue
            if is_number_text(t):
                numeric[c] += 1
            else:
                textual[c] += 1
                if _is_unit_like(t):
                    unitish[c] += 1
    header_text = {c: " ".join(text_at(r, c) for r in header_rows).strip() for c in range(n_cols)}
    unit_col = None
    by_data = [c for c in range(n_cols) if textual[c] >= 2 and unitish[c] >= 0.6 * textual[c] and numeric[c] <= textual[c] * 0.2]
    by_header = [c for c in range(n_cols) if UNIT_HEADER_RE.match(header_text[c]) and textual[c]]
    # A "Unit" header over an empty column is an OCR shift; the unit-like data column wins.
    if by_data or by_header:
        unit_col = (by_data or by_header)[0]
    value_cols = [
        c for c in range(n_cols)
        if c != unit_col and numeric[c] >= 1 and numeric[c] >= textual[c]
    ]
    if value_cols and _is_serial_column([text_at(r, value_cols[0]) for r in data_rows]) and any(
            textual[c] for c in range(value_cols[0] + 1, n_cols)):
        value_cols = value_cols[1:]  # a leading 序号 / No. column numbers the rows; it holds no data
    if not value_cols:
        return None
    first_value = min(value_cols)
    # Columns right of the first value column that hold only null markers are
    # still period/entity columns (e.g. a subsidiary acquired this year).
    value_cols = [
        c for c in range(first_value, n_cols)
        if c != unit_col and (c in value_cols or (textual[c] == 0 and header_text_at(header_rows, origin, covered, c)))
    ]
    # A last header cell above a column with no data at all means OCR split one
    # spanning header (e.g. a year over its entity columns) into an extra column.
    phantom = len(value_cols) > 1 and filled[value_cols[-1]] == 0 and not REMARK_HEADER_RE.search(
        header_text_at(header_rows, origin, covered, value_cols[-1]))
    if phantom:
        value_cols = value_cols[:-1]
    label_cols = [c for c in range(first_value) if c != unit_col
                  and not _is_serial_column([text_at(r, c) for r in data_rows])]
    if not label_cols:
        label_cols = [c for c in range(n_cols) if c not in value_cols and c != unit_col][:1]

    header_lines: list[list[tuple[int, int, str]]] = []
    for r in header_rows:
        items = [
            (ref.col, ref.col_span, ref.text)
            for (rr, _), ref in sorted(origin.items())
            if rr == r and ref.text
        ]
        if items:
            header_lines.append(items)

    aligned = True
    for index, r in enumerate(header_rows):
        for c in label_cols + ([unit_col] if unit_col is not None else []):
            ref = origin.get((r, c))
            if not ref or not ref.text:
                continue
            if LABEL_HEADER_RE.match(ref.text) or UNIT_HEADER_RE.match(ref.text):
                continue
            if index == 0 and c == min(label_cols or [0]):
                continue
            # A period or number over a label column means the header row is shifted.
            if period_years(ref.text) or is_number_text(ref.text):
                aligned = False
    column_paths: dict[int, list[str]] = {}
    for c in value_cols:
        path = []
        for r in header_rows:
            ref = covered.get((r, c))
            if ref and ref.text and (not path or path[-1] != ref.text):
                path.append(ref.text)
        column_paths[c] = path
    rebuilt = rebuild_column_paths(header_lines, value_cols, unit_col, label_cols)
    if rebuilt and rebuilt != column_paths:
        column_paths, aligned = rebuilt, True
    if aligned and header_rows and not any(column_paths.values()):
        aligned = False
    if aligned and (phantom or _sibling_run_straddles_parents(header_rows, origin, covered, value_cols)):
        aligned = False

    sections: dict[int, str] = {}
    lines: list[DataLine] = []
    current_section: str | None = None
    for r in data_rows:
        label_parts = []
        for c in label_cols:
            ref = covered.get((r, c))
            if ref is None or is_null_text(ref.text):
                continue
            if ref.text and (not label_parts or label_parts[-1] != ref.text):
                label_parts.append(ref.text)
        values = {}
        for c in value_cols:
            ref = origin.get((r, c))
            if ref is not None and ref.text:
                values[c] = ref
        has_numeric = any(is_number_text(v.text) for v in values.values())
        unit = clean_unit(text_at(r, unit_col)) if unit_col is not None else None
        if not has_numeric:
            text = " ".join(part for part in label_parts if part)
            if text and not values:
                sections[r] = text
                current_section = text
            continue
        label = " › ".join(part for part in label_parts if part)
        # A two-panel table can land the unit cell in the label column; when the
        # roles are visibly reversed, trust the cell contents.
        if unit and _is_unit_like(label) and not _is_unit_like(unit):
            label, unit = unit, label
        lines.append(DataLine(r, f"r{r}", label, unit or None, current_section, values))

    page = (page_payload or {}).get("page") or {}
    blocks = (page_payload or {}).get("blocks") or []
    headings = [clean_text(b.get("text")) for b in blocks if b.get("block_type") in {"heading", "title"} and clean_text(b.get("text"))][:4]
    view = TableView(
        table_id=table["table_id"], page_index=int(table.get("page_index") or 0),
        page_number=int(page.get("page_number") or int(table.get("page_index") or 0) + 1),
        n_rows=n_rows, n_cols=n_cols, header_rows=header_rows, header_lines=header_lines,
        value_cols=value_cols, unit_col=unit_col, label_cols=label_cols, lines=lines, sections=sections,
        headers_aligned=aligned, column_paths=column_paths, headings=headings,
        caption=clean_text(table.get("caption")) or None, continues_from=table.get("continues_from_table_id"),
    )
    view.headers_rebuilt = bool(rebuilt and rebuilt == column_paths)
    view.title, view.trailing_text, view.heading_above = table_context(blocks, table.get("block_id"))
    table_block = next((b for b in blocks if b.get("block_id") == table.get("block_id")), None)
    view.notes = page_notes(blocks, table_box=_box(table_block) if table_block else None, cited=cited_marks(view))
    view.sums = column_sums(view)
    view.arithmetic = [relation.text for relation in view.sums]
    return view


SKIP_CONTEXT_TYPES = {"header", "footer", "table_markdown", "table", "figure", "image", "page_number"}
TABLE_LABEL_RE = re.compile(r"(?:table|表)\s*\d+[.:：]?", re.I)


def _box(block: dict) -> tuple[float, float, float, float] | None:
    box = block.get("bbox")
    try:
        return float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"])
    except (KeyError, TypeError, ValueError):
        return None


def _same_column(box, table_box) -> bool:
    overlap = min(box[2], table_box[2]) - max(box[0], table_box[0])
    return overlap >= 0.3 * min(box[2] - box[0], table_box[2] - table_box[0])


def _title_like(block: dict) -> str | None:
    text = clean_text(block.get("text"))
    if block.get("block_type") in SKIP_CONTEXT_TYPES or not text or len(text) > 140 or TABLE_LABEL_RE.fullmatch(text):
        return None
    if NOTE_START_RE.match(fold(text)) or re.match(r"^\s*\d", text) or "=" in text or "÷" in text:
        return None  # footnotes and formula definitions are not table titles
    return text


def table_context(blocks: list[dict], block_id: str | None) -> tuple[str | None, str | None, str | None]:
    """Title, short unit line after the table, and the heading above it.

    On multi-column pages reading order interleaves columns, so blocks are
    related to the table by position: above it and overlapping its column.
    """
    order = [b.get("block_id") for b in blocks]
    if not block_id or block_id not in order:
        return None, None, None
    index = order.index(block_id)
    table_box = _box(blocks[index])
    if table_box is None:
        above = list(reversed(blocks[max(0, index - 3):index]))
        below = blocks[index + 1:index + 2]
        headings = [b for b in reversed(blocks[:index]) if b.get("block_type") in {"heading", "title"}]
    else:
        placed = [(b, _box(b)) for i, b in enumerate(blocks) if i != index]
        placed = [(b, box) for b, box in placed if box is not None and _same_column(box, table_box)]
        above = [b for b, box in sorted(((b, box) for b, box in placed if box[3] <= table_box[1] + 8),
                                        key=lambda item: table_box[1] - item[1][3])]
        below = [b for b, box in sorted(((b, box) for b, box in placed if box[1] >= table_box[3] - 8),
                                        key=lambda item: item[1][1] - table_box[3])][:1]
        headings = [b for b in above if b.get("block_type") in {"heading", "title"}]
    title = next((text for text in map(_title_like, above[:3]) if text), None)
    heading = next((text for text in map(_title_like, headings) if text), None)
    trailing = None
    if below:
        text = clean_text(below[0].get("text"))
        if text and len(re.sub(r"[\s$\\{}_^]+", "", text)) <= 24 and UNIT_TOKEN_RE.search(text):
            trailing = text
    return title, trailing, heading


SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
CITED_MARK_RE = re.compile(r"\$\s*\^\s*\{+\s*\(?\s*(\d{1,3})\s*\)?\s*\}+\s*\$|([⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[,，、][⁰¹²³⁴⁵⁶⁷⁸⁹]+)*)")
NOTE_NUMBER_RE = re.compile(r"^\s*(?:注|註|note)?\s*[\(（]?\s*(\d{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})(?![\d,.，．]*\d)", re.I)


def cited_marks(view: "TableView") -> set[str]:
    """Footnote numbers the table's labels and headings cite."""
    texts = [line.label for line in view.lines] + list(view.sections.values()) + [
        text for header in view.header_lines for _, _, text in header]
    marks: set[str] = set()
    for text in texts:
        for latex, superscript in CITED_MARK_RE.findall(text or ""):
            if latex:
                marks.add(str(int(latex)))
            for part in re.split(r"[,，、]", superscript or ""):
                if part:
                    marks.add(str(int(part.translate(SUPERSCRIPT_DIGITS))))
    return marks


def note_number(text: str) -> str | None:
    match = NOTE_NUMBER_RE.match(text or "")
    return str(int(match.group(1).translate(SUPERSCRIPT_DIGITS))) if match else None


def page_notes(blocks: list[dict], *, table_box=None, cited: set[str] | None = None,
               limit: int = 6, max_chars: int = 360) -> list[Note]:
    """Notes that may explain the table.

    On a multi-column page a note counts when it sits in the table's own
    column, or when it carries a number the table cites and no note in the
    table's column does: two columns often number their notes independently
    ("1", "2" in each), while a table continued across columns has its notes
    at the end of the last column."""
    cited = cited or set()
    candidates = []
    for order, block in enumerate(blocks):
        if block.get("block_type") in {"table_markdown", "table", "figure", "image", "header", "footer"}:
            continue
        text = clean_text(block.get("text"))
        if len(text) < 8:
            continue
        box = _box(block) if table_box is not None else None
        own = box is None or _same_column(box, table_box)
        candidates.append((order, block, text, own, note_number(text)))
    own_numbers = {number for _, _, _, own, number in candidates if own and number}
    scored = []
    for order, block, text, own, number in candidates:
        if not own and not (number and number in cited and number not in own_numbers):
            continue
        lowered = fold(text)
        score = 0
        if NOTE_START_RE.match(lowered):
            score += 3
        if number and number in cited:
            score += 3
        score += sum(1 for term in METHOD_TERMS if term in lowered)
        score += sum(2 for term in SCOPE_TERMS if term in lowered)
        if score <= 0:
            continue
        scored.append((score, order, block["block_id"], text[:max_chars]))
    chosen = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
    chosen.sort(key=lambda item: item[1])
    return [Note(f"n{i}", block_id, text) for i, (_, _, block_id, text) in enumerate(chosen, 1)]


@dataclass(frozen=True)
class ColumnSum:
    target: int
    parts: tuple[int, ...]
    matched: int
    checked: int
    informative: int

    @property
    def text(self) -> str:
        text = f"c{self.target} = " + " + ".join(f"c{p}" for p in self.parts) + f" in {self.matched}/{self.checked} rows"
        return text if self.informative >= min(2, self.matched) else text + " (other parts mostly 0)"

    def holds(self, line: DataLine) -> bool:
        total = parse_number(line.values[self.target].text) if self.target in line.values else None
        nums = [parse_number(line.values[p].text) if p in line.values else None for p in self.parts]
        return total is not None and None not in nums and _sums_to(nums, total)


def _sums_to(nums: list[Decimal], total: Decimal) -> bool:
    return abs(sum(nums) - total) <= max(Decimal("0.015"), abs(total) * Decimal("0.006"))


def _precise(number: Decimal) -> bool:
    return abs(number) >= 100 and len(number.normalize().as_tuple().digits) >= 5


def column_sums(view: TableView, *, min_rows: int = 2) -> list[ColumnSum]:
    """Column sum identities; evidence for which column totals which period.

    One row is enough when the total is a large, precise number whose parts are
    all non-zero: such a match is not a coincidence.
    """
    cols = view.value_cols
    found: dict[frozenset, tuple] = {}
    for target in cols:
        for width in (2, 3, 4):
            for start in (cols.index(target) - width, cols.index(target) + 1):
                if start < 0 or start + width > len(cols):
                    continue
                parts = tuple(cols[start:start + width])
                checked = matched = informative = nonzero_total = precise = 0
                for line in view.lines:
                    total = parse_number(line.values[target].text) if target in line.values else None
                    nums = [parse_number(line.values[p].text) if p in line.values else None for p in parts]
                    if total is None or any(n is None for n in nums):
                        continue
                    checked += 1
                    if _sums_to(nums, total):
                        matched += 1
                        nonzero_total += total != 0
                        informative += sum(1 for n in nums if n != 0) >= 2
                        precise += _precise(total) and all(n != 0 for n in nums)
                if matched < int(0.75 * checked + 0.999) or not checked:
                    continue
                if (checked < min_rows or nonzero_total < min_rows) and not (checked == matched == precise == 1):
                    continue
                key = frozenset([target, *parts])
                rank = (informative, target == max(key), matched)
                if key not in found or rank > found[key][0]:
                    found[key] = (rank, ColumnSum(target, parts, matched, checked, informative))
    return [relation for _, relation in sorted(found.values(), key=lambda item: item[1].target)]


def arithmetic_relations(view: TableView, *, min_rows: int = 2) -> list[str]:
    return [relation.text for relation in column_sums(view, min_rows=min_rows)]


def entity_sum_role(view: TableView, col: int | None, line: DataLine) -> tuple[str | None, int | None]:
    """("sum", col) or ("part", total_col) when col takes part in a same-period
    column sum that holds in this row. Sums across different years are ignored."""
    if col is None:
        return None, None
    for relation in view.sums:
        if col != relation.target and col not in relation.parts:
            continue
        years = {y for c in (relation.target, *relation.parts)
                 for y in period_years(" ".join(view.column_paths.get(c, [])))}
        if len(years) > 1 or not relation.holds(line):
            continue
        if col == relation.target:
            return "sum", col
        return "part", relation.target
    return None, None


def explicit_total_column(view: TableView, col: int | None, line: DataLine, period_year: int | None) -> int | None:
    """The column under an explicit Total header for the same period, if any.

    Entity columns need not add up to the group (their boundaries can overlap
    or nest), so a row-wise sum check cannot find the total. When exactly one
    same-period column is headed Total and the selected column is not, the
    total column is the reporting-group figure and the selected one is a
    part of it.
    """
    if col is None or period_year is None or not view.headers_aligned:
        return None
    if TOTAL_LABEL_RE.search(fold(" ".join(view.column_paths.get(col, [])))):
        return None
    candidates = [
        c for c in view.value_cols
        if c != col and TOTAL_LABEL_RE.search(fold(" ".join(view.column_paths.get(c, []))))
    ]
    resolved = []
    for candidate in candidates:
        period_col = total_column_group(view, candidate, period_year)
        if period_col is not None and period_col != col and period_col in line.values:
            resolved.append(period_col)
    return resolved[0] if len(set(resolved)) == 1 else None


def total_column_group(view: TableView, label_col: int, period_year: int) -> int | None:
    """The column reporting a period inside an entity group headed Total.

    An entity header ("Infrastructure", "Total") labels one cell, while the
    period row beneath it repeats one year per value column, so the group
    spans the label column and the period columns that follow it up to the
    next entity label. The label column itself often holds the first year.
    """
    label_years = period_years(" ".join(view.column_paths.get(label_col, [])))
    if period_year in label_years:
        return label_col
    following = [
        col for col in view.value_cols
        if col > label_col and col in view.column_paths and view.column_paths[col]
        and not _is_unit_like(" ".join(view.column_paths[col]))
    ]
    block: list[int] = []
    for col in following:
        path_years = period_years(" ".join(view.column_paths[col]))
        if not path_years:
            break  # the next entity label starts a new group
        block.append(col)
        if period_year in path_years:
            return col
    return None


LATEX_MARK_RE = re.compile(r"\s*\$\s*\^\s*\{+\s*\(?\s*([\dA-Za-z][\dA-Za-z,，、 ]{0,8}?)\s*\)?\s*\}+\s*\$")
SUPERSCRIPT_RUN_RE = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[,，、][⁰¹²³⁴⁵⁶⁷⁸⁹]+)*")
# A raised figure after a length unit or a number is an exponent (m³, km², 10⁴), not a note.
EXPONENT_BASE_RE = re.compile(r"(?:(?<![A-Za-z])(?:m|km|cm|dm|mm|Nm|ft)|\d)\s*$")


def prompt_label(text: str | None) -> str:
    """A row label for the model with footnote marks spelt out ("中水使用量 [note 38]").

    Marks reach the IR as LaTeX (" $ ^{38} $") from visual OCR or as
    superscript digits ("³⁸") from the PDF text layer; written as a note
    reference they read neither as part of the label nor as a figure.
    """
    if not text:
        return text or ""

    def note(match: re.Match, digits: str) -> str:
        if EXPONENT_BASE_RE.search(match.string[:match.start()]):
            return match.group(0)
        return " [note " + re.sub(r"[,，、 ]+", ",", digits).strip(",") + "]"

    text = LATEX_MARK_RE.sub(lambda m: note(m, m.group(1)), text)
    return SUPERSCRIPT_RUN_RE.sub(lambda m: note(m, m.group(0).translate(SUPERSCRIPT_DIGITS)), text)


def render_table(view: TableView, lines: list[DataLine] | None = None) -> str:
    lines = view.lines if lines is None else lines
    out = [f"TABLE {view.table_id} | page {view.page_number}"]
    if view.title:
        out.append("TABLE TITLE: " + view.title)
    if view.trailing_text:
        out.append("UNIT LINE AFTER TABLE: " + view.trailing_text)
    if view.heading_above and view.heading_above != view.title:
        out.append("HEADING ABOVE TABLE: " + view.heading_above)
    context = [*view.headings]
    if view.caption:
        context.insert(0, view.caption)
    if context:
        out.append("PAGE HEADINGS: " + " / ".join(context))
    label_header = " | ".join(
        text for header in view.header_lines for col, _, text in header
        if col in view.label_cols and text and not UNIT_HEADER_RE.match(text) and not LABEL_HEADER_RE.match(text)
    )
    if label_header:
        out.append("ROW HEADER: " + prompt_label(label_header))
    if view.headers_aligned and any(view.column_paths.values()):
        if view.headers_rebuilt:
            out.append("(value column headers rebuilt from header order: each group repeats the same periods)")
        out.append("VALUE COLUMNS: " + " | ".join(
            f"c{c} = " + (" › ".join(view.column_paths[c]) or "(no header)") for c in view.value_cols))
    else:
        out.append("HEADER ROWS (OCR positions unreliable; infer each column from header order and data):")
        for header in view.header_lines:
            out.append("  " + " | ".join(text for _, _, text in header))
        out.append("VALUE COLUMNS: " + " ".join(f"c{c}" for c in view.value_cols))
    if view.arithmetic:
        out.append("ARITHMETIC: " + "; ".join(view.arithmetic))
    last_section = None
    for line in lines:
        if line.section and line.section != last_section:
            out.append(f"§ {prompt_label(line.section)}")
            last_section = line.section
        unit = f" [{line.unit}]" if line.unit else ""
        cells = " | ".join(f"c{c} {line.values[c].text}" for c in view.value_cols if c in line.values)
        out.append(f"{line.rid} {prompt_label(line.label)}{unit}: {cells}")
    if view.notes:
        out.append("NOTES ON THIS PAGE:")
        out.extend(f"  {note.nid}: {note.text}" for note in view.notes)
    return "\n".join(out)


GROUP_SCOPE_RE = re.compile(r"(?:集团|全集团|本集团|公司整体|全公司|合并|总计|合计|汇总|group|consolidated|company[-\s]wide|whole\s+company|total)", re.I)
SUBSET_SCOPE_RE = re.compile(
    r"(?:基地|工厂|厂区|园区|数据中心|办公(?:楼|室|场所)?|分公司|子公司|事业部|板块|运营中心|门店|矿山|矿区|电站|项目公司|"
    r"境内|境外|海外|国内|中国大陆|香港|澳門|澳门|台灣|台湾|\bsites?\b|\bplants?\b|\bfacilit|\bbase\b|\bsegments?\b|\bsubsidiar|\bdata\s+cent|"
    r"\boffices?\b|\bdomestic\b|\boverseas\b|\bmainland\b|hong\s+kong|macau|macao|taiwan|outside\s+hong\s+kong)",
    re.I,
)


# A report may disclose the same indicator under several reporting scopes:
# the group total, a production base, a subsidiary, an overseas region. They
# are different measurements, not conflicting values, so each keeps its own
# label and becomes its own published observation.
BOUNDARY_VOCAB: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("battery_production_bases", re.compile(r"电池生产基地|生产基地|制造基地|生产基地群")),
    ("subsidiary", re.compile(r"子公司|下属公司|附属公司|subsidiar")),
    ("overseas", re.compile(r"海外|境外|国外|overseas|outside\s+china")),
    ("hong_kong", re.compile(r"香港|hong\s*kong")),
    ("mainland", re.compile(r"中国大陆|中国内地|内地|mainland")),
    ("group", re.compile(r"集团|合并口径|合并报表|本公司|公司整体|全公司|全球范围|全球|\bgroup\b")),
)


def boundary_label(view: TableView, *texts: str | None) -> str | None:
    """The reporting scope a printed row belongs to, when the report names one.

    Only an explicit scope word counts; a bare number never implies a scope.
    """
    near = " ".join(str(text) for text in [*texts, view.title, view.caption, *view.headings] if text)
    near = fold(near)
    if not near:
        return None
    for key, pattern in BOUNDARY_VOCAB:
        if pattern.search(near):
            return key
    return None


def structural_boundary(view: TableView, col: int | None, period_year: int | None) -> str | None:
    """Several columns of the same period under different parents are segments."""
    if col is None or period_year is None or not view.headers_aligned:
        return None
    same_period = [c for c in view.value_cols if period_year in period_years(" ".join(view.column_paths.get(c, [])))]
    if len(same_period) <= 1:
        return None
    return "group" if TOTAL_LABEL_RE.search(fold(" ".join(view.column_paths.get(col, [])))) else "subset"


def title_subset_evidence(view: TableView) -> bool:
    """The table as a whole (title or caption) is limited to part of the group."""
    return bool(SUBSET_SCOPE_RE.search(fold(" ".join([view.title or "", view.caption or ""]))))


def textual_subset_evidence(view: TableView) -> bool:
    texts = [view.title or "", view.caption or "", *view.headings, *(t for h in view.header_lines for _, _, t in h)]
    return bool(SUBSET_SCOPE_RE.search(fold(" ".join(texts))))


def boundary_hint(view: TableView, col: int | None) -> str | None:
    """Deterministic entity-boundary evidence near the selected value column."""
    near = [view.caption or "", *(view.column_paths.get(col, []) if col is not None else [])]
    near += [text for header in view.header_lines for _, _, text in header]
    near_text = fold(" ".join(near))
    column_text = fold(" ".join(view.column_paths.get(col, []) if col is not None else []))
    if col is not None and GROUP_SCOPE_RE.search(column_text):
        return "group"
    if SUBSET_SCOPE_RE.search(near_text) and not GROUP_SCOPE_RE.search(near_text):
        return "subset"
    if GROUP_SCOPE_RE.search(near_text):
        return "group"
    return None
