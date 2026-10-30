from __future__ import annotations

import re
from collections import defaultdict

from esg_document.document.contracts import (
    DocumentIR,
    LogicalCellIR,
    LogicalCellMappingIR,
    LogicalTableIR,
    LogicalTableSegmentIR,
    SourceTrace,
)

VALUE_RE = re.compile(
    r"^[<>≤≥~约約]?\s*[\(（]?\s*[-−–+]?\s*(?:\d{1,3}(?:[,，\s]\d{3})+|\d+)(?:\.\d+)?\s*[\)）]?\s*[%％]?\s*[*#¹²³⁴⁵⁶⁷⁸⁹⁰]*$"
)
FOOTNOTE_MARK_RE = re.compile(r"^[\(（]\s*\d{1,2}\s*[\)）]$")
YEAR_RE = re.compile(
    r"^(?:FY\s?)?(?:19|20)\d{2}(?:\s*[/／-]\s*\d{2,4})?\s*(?:年|年度|财年|財年|E|A)?$",
    re.I,
)


def _is_value(text: str | None) -> bool:
    """A figure, not a period header or a footnote mark."""
    raw = (text or "").strip()
    if not raw or YEAR_RE.match(raw) or FOOTNOTE_MARK_RE.match(raw):
        return False
    return bool(VALUE_RE.match(raw))


class LogicalTableBuilder:
    """Compile lossless logical tables from explicit physical continuation links."""

    def build(self, document: DocumentIR) -> DocumentIR:
        previous_by_group = {
            item.continuation_group_id: item for item in document.logical_tables
        }
        groups = self._groups(document)
        logical_tables: list[LogicalTableIR] = []
        reserved_ids = {
            item.logical_table_id
            for group_id, item in previous_by_group.items()
            if group_id in groups
        }
        next_sequence = max(
            (
                int(value.rsplit("-", 1)[-1])
                for value in reserved_ids
                if value.rsplit("-", 1)[-1].isdigit()
            ),
            default=0,
        )
        for group_id, tables in sorted(groups.items()):
            ordered = self._ordered_chain(tables)
            axes = {
                table.continuation_axis for table in ordered if table.continuation_axis
            }
            if len(ordered) < 2 or len(axes) != 1:
                continue
            axis = next(iter(axes))
            previous = previous_by_group.get(group_id)
            if previous:
                logical_table_id = previous.logical_table_id
            else:
                next_sequence += 1
                logical_table_id = f"logical-table-{next_sequence:06d}"
                while logical_table_id in reserved_ids:
                    next_sequence += 1
                    logical_table_id = f"logical-table-{next_sequence:06d}"
                reserved_ids.add(logical_table_id)
            logical_tables.append(
                self._compile(
                    document,
                    logical_table_id=logical_table_id,
                    group_id=group_id,
                    axis=axis,
                    tables=ordered,
                    previous=previous,
                )
            )
        document.logical_tables = logical_tables
        return document

    @staticmethod
    def _groups(document: DocumentIR) -> dict[str, list]:
        groups: dict[str, list] = defaultdict(list)
        for table in document.tables:
            if table.continuation_group_id and table.continuation_axis:
                groups[table.continuation_group_id].append(table)
        return groups

    @staticmethod
    def _ordered_chain(tables: list) -> list:
        by_id = {table.table_id: table for table in tables}
        roots = [
            table
            for table in tables
            if not table.continues_from_table_id
            or table.continues_from_table_id not in by_id
        ]
        if len(roots) != 1:
            return sorted(
                tables, key=lambda item: (item.page_index, item.order, item.table_id)
            )
        ordered = []
        seen = set()
        current = roots[0]
        while current and current.table_id not in seen:
            ordered.append(current)
            seen.add(current.table_id)
            current = by_id.get(current.continues_to_table_id)
        ordered.extend(
            table
            for table in sorted(
                tables, key=lambda item: (item.page_index, item.order, item.table_id)
            )
            if table.table_id not in seen
        )
        return ordered

    @classmethod
    def _compile(
        cls,
        document: DocumentIR,
        *,
        logical_table_id: str,
        group_id: str,
        axis: str,
        tables: list,
        previous: LogicalTableIR | None,
    ) -> LogicalTableIR:
        composition_mode = cls._composition_mode(axis, tables)
        row_offset = 0
        column_offset = 0
        segments = []
        mappings = []
        artifact_ids = []
        notes = []
        horizontal_shift = (
            cls._horizontal_row_shift(tables) if axis == "horizontal" else 0
        )
        if horizontal_shift is None:
            horizontal_shift = 0  # unresolved; the group carries the review flag
        for index, table in enumerate(tables):
            segment = LogicalTableSegmentIR(
                table_id=table.table_id,
                page_index=table.page_index,
                sequence=index,
                row_offset=horizontal_shift
                if axis == "horizontal" and index
                else row_offset,
                column_offset=column_offset,
            )
            segments.append(segment)
            mappings.extend(
                LogicalCellMappingIR(
                    source_table_id=table.table_id,
                    source_cell_id=cell.cell_id,
                    logical_row_index=segment.row_offset + cell.row_index,
                    logical_col_index=column_offset + cell.col_index,
                    row_span=cell.row_span,
                    col_span=cell.col_span,
                )
                for cell in table.cells
            )
            artifact_ids.extend(table.source_trace.artifact_ids)
            notes.extend(table.source_trace.notes)
            if axis == "horizontal":
                if composition_mode == "horizontal_continue_last_column":
                    column_offset = max(0, column_offset + table.column_count - 1)
                else:
                    column_offset += table.column_count
            else:
                row_offset += table.row_count

        logical_rows = (
            max(
                (
                    segment.row_offset + table.row_count
                    for segment, table in zip(segments, tables)
                ),
                default=0,
            )
            if axis == "horizontal"
            else sum(table.row_count for table in tables)
        )
        logical_columns = (
            (
                max((table.column_count for table in tables), default=0)
                if composition_mode == "horizontal_continue_last_column"
                else sum(table.column_count for table in tables)
            )
            if axis == "horizontal"
            else max((table.column_count for table in tables), default=0)
        )
        spread_ids = {
            spread.spread_id
            for spread in document.spreads
            if spread.status == "confirmed"
            and set(spread.page_indices).issuperset(
                {table.page_index for table in tables}
            )
        }
        flags = ["lossless_physical_table_composition"]
        status = previous.status if previous else "derived"
        if axis == "vertical":
            flags.append("repeated_headers_preserved")
        elif composition_mode == "horizontal_continue_last_column":
            flags.append("horizontal_last_column_continuation")
        if axis == "horizontal" and cls._horizontal_row_shift(tables) is None:
            # Appended columns keep every cell whether or not the bands line
            # up, so record the doubt without blocking evidence; a last-column
            # continuation overlaps cells and does need a resolvable alignment.
            flags.append("horizontal_row_alignment_unresolved")
            if composition_mode == "horizontal_continue_last_column":
                status = "review_required"
        cells = cls._logical_cells(
            tables=tables,
            segments=segments,
            mappings=mappings,
            logical_rows=logical_rows,
            logical_columns=logical_columns,
        )
        return LogicalTableIR(
            logical_table_id=logical_table_id,
            continuation_group_id=group_id,
            composition_axis=axis,
            composition_mode=composition_mode,
            source_table_ids=[table.table_id for table in tables],
            page_indices=sorted({table.page_index for table in tables}),
            spread_id=next(iter(sorted(spread_ids)), None),
            logical_row_count=logical_rows,
            logical_column_count=logical_columns,
            segments=segments,
            cell_mappings=mappings,
            cells=cells,
            status=status,
            quality_flags=list(
                dict.fromkeys([*flags, *(previous.quality_flags if previous else [])])
            ),
            source_trace=SourceTrace(
                parser="LogicalTableBuilder",
                parser_version="v1",
                artifact_ids=list(dict.fromkeys(artifact_ids)),
                notes=list(
                    dict.fromkeys(
                        [
                            *notes,
                            "Derived from explicit physical TableIR continuation links.",
                        ]
                    )
                ),
            ),
        )

    @classmethod
    def _horizontal_row_shift(cls, tables: list) -> int | None:
        """Rows to shift a continuation table down so its data starts level.

        Facing pages of one wide table do not always carry the same number
        of header or section rows: a left page may keep a section title the
        right page omits. Align on the first data row instead of assuming
        row 0, and refuse the alignment when the data bands disagree in size.
        """
        if len(tables) < 2:
            return 0
        bands = [cls._row_band(table) for table in tables]
        length = {len(data) for _, data in bands}
        if len(length) != 1:
            return None
        if not next(iter(length)):
            # Header-only halves: they line up row for row by definition.
            return 0 if len({len(headers) for headers, _ in bands}) == 1 else None
        shifts = [bands[0][1][0] - data[0] for _, data in bands[1:]]
        if len(set(shifts)) != 1 or abs(shifts[0]) > 3:
            return None
        return shifts[0]

    @staticmethod
    def _row_band(table) -> tuple[list[int], list[int]]:
        """Header and data rows, deriving the header band when the IR under-reports it.

        A period row often sits below the stored header_row_indices, so read
        the leading rows until one carries a value, exactly as table view
        construction does; otherwise two halves of one table look unequal
        only because their header lists were recorded differently.
        """
        rows = range(table.row_count)
        header = set(LogicalTableBuilder._leading_header_rows(table))
        return ([r for r in rows if r in header], [r for r in rows if r not in header])

    @staticmethod
    def _leading_header_rows(table) -> list[int]:
        by_position = {
            (cell.row_index, cell.col_index): cell.text or "" for cell in table.cells
        }
        rows: list[int] = []
        for row in range(min(table.row_count, 4)):
            values = [
                by_position.get((row, col), "") for col in range(1, table.column_count)
            ]
            if any(_is_value(text) for text in values):
                break
            rows.append(row)
        return rows or list(table.header_row_indices or [0])

    @staticmethod
    def _composition_mode(axis: str, tables: list) -> str:
        if axis == "vertical":
            return "vertical_stack"
        if (
            len(tables) >= 2
            and tables[0].column_count >= 2
            and all(table.column_count == 1 for table in tables[1:])
        ):
            return "horizontal_continue_last_column"
        return "horizontal_append_columns"

    @staticmethod
    def _logical_cells(
        *,
        tables: list,
        segments: list[LogicalTableSegmentIR],
        mappings: list[LogicalCellMappingIR],
        logical_rows: int,
        logical_columns: int,
    ) -> list[LogicalCellIR]:
        cell_by_id = {cell.cell_id: cell for table in tables for cell in table.cells}
        segment_order = {segment.table_id: segment.sequence for segment in segments}
        by_position: dict[tuple[int, int], list[tuple[int, object]]] = defaultdict(list)
        for mapping in mappings:
            cell = cell_by_id.get(mapping.source_cell_id)
            if cell is None:
                continue
            by_position[(mapping.logical_row_index, mapping.logical_col_index)].append(
                (segment_order.get(mapping.source_table_id, 0), cell)
            )

        result = []
        for row_index in range(logical_rows):
            for col_index in range(logical_columns):
                source_cells = [
                    cell
                    for _, cell in sorted(
                        by_position.get((row_index, col_index), []),
                        key=lambda item: (item[0], item[1].cell_id),
                    )
                ]
                result.append(
                    LogicalCellIR(
                        row_index=row_index,
                        col_index=col_index,
                        text=" ".join(
                            dict.fromkeys(
                                cell.text.strip()
                                for cell in source_cells
                                if cell.text.strip()
                            )
                        ),
                        source_cell_ids=[cell.cell_id for cell in source_cells],
                        is_header=any(cell.is_header for cell in source_cells),
                        unit_hint=next(
                            (cell.unit_hint for cell in source_cells if cell.unit_hint),
                            None,
                        ),
                    )
                )
        return result
