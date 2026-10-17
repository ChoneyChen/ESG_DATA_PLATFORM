from __future__ import annotations

from collections import defaultdict

from esg_v2.document.contracts import (
    DocumentIR,
    LogicalCellIR,
    LogicalCellMappingIR,
    LogicalTableIR,
    LogicalTableSegmentIR,
    SourceTrace,
)


class LogicalTableBuilder:
    """Compile lossless logical tables from explicit physical continuation links."""

    def build(self, document: DocumentIR) -> DocumentIR:
        previous_by_group = {
            item.continuation_group_id: item
            for item in document.logical_tables
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
            axes = {table.continuation_axis for table in ordered if table.continuation_axis}
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
            return sorted(tables, key=lambda item: (item.page_index, item.order, item.table_id))
        ordered = []
        seen = set()
        current = roots[0]
        while current and current.table_id not in seen:
            ordered.append(current)
            seen.add(current.table_id)
            current = by_id.get(current.continues_to_table_id)
        ordered.extend(
            table
            for table in sorted(tables, key=lambda item: (item.page_index, item.order, item.table_id))
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
        for index, table in enumerate(tables):
            segment = LogicalTableSegmentIR(
                table_id=table.table_id,
                page_index=table.page_index,
                sequence=index,
                row_offset=row_offset,
                column_offset=column_offset,
            )
            segments.append(segment)
            mappings.extend(
                LogicalCellMappingIR(
                    source_table_id=table.table_id,
                    source_cell_id=cell.cell_id,
                    logical_row_index=row_offset + cell.row_index,
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
            max((table.row_count for table in tables), default=0)
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
            and set(spread.page_indices).issuperset({table.page_index for table in tables})
        }
        flags = ["lossless_physical_table_composition"]
        status = previous.status if previous else "derived"
        if axis == "vertical":
            flags.append("repeated_headers_preserved")
        elif composition_mode == "horizontal_continue_last_column":
            flags.append("horizontal_last_column_continuation")
            if len({table.row_count for table in tables}) != 1:
                flags.append("horizontal_row_alignment_unresolved")
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
            quality_flags=list(dict.fromkeys([*flags, *(previous.quality_flags if previous else [])])),
            source_trace=SourceTrace(
                parser="LogicalTableBuilder",
                parser_version="v1",
                artifact_ids=list(dict.fromkeys(artifact_ids)),
                notes=list(dict.fromkeys([*notes, "Derived from explicit physical TableIR continuation links."])),
            ),
        )

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
        cell_by_id = {
            cell.cell_id: cell
            for table in tables
            for cell in table.cells
        }
        segment_order = {
            segment.table_id: segment.sequence
            for segment in segments
        }
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
