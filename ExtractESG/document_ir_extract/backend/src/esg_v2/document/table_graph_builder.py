from __future__ import annotations

import re
from difflib import SequenceMatcher

from esg_v2.document.contracts import DocumentIR, StructureEdge, TableGraphEdge


class TableGraphBuilder:
    UNIT_PATTERN = re.compile(
        r"(?:^|[\s(（])(%|percent|percentage|kg|t|tonnes?|mwh|kwh|gj|m3|m³|hours?|persons?|employees?|rmb|cny|usd|hk\$|million|billion)(?:$|[\s)）])",
        re.IGNORECASE,
    )

    def build(self, document: DocumentIR) -> DocumentIR:
        for table in document.tables:
            table.page_indices = sorted(set(table.page_indices or [table.page_index]))
            table.graph_edges = self._cell_edges(table)
            self._propagate_headers_and_units(table)
        self._link_cross_page_tables(document.tables)
        self._write_continuation_edges(document)
        self._normalize_structure_edge_ids(document)
        return document

    @staticmethod
    def _normalize_structure_edge_ids(document: DocumentIR) -> None:
        ordered = sorted(
            document.structure_edges,
            key=lambda item: (item.source_id, item.relation, item.target_id, item.edge_id),
        )
        for sequence, edge in enumerate(ordered, start=1):
            edge.edge_id = f"edge-{sequence:06d}"
        document.structure_edges = ordered

    def _cell_edges(self, table):
        by_position = self._covered_positions(table)
        edges: list[TableGraphEdge] = []
        counter = 0

        def add(source, target, relation, confidence=1.0):
            nonlocal counter
            counter += 1
            edges.append(
                TableGraphEdge(
                    edge_id=f"{table.table_id}-edge-{counter:05d}",
                    table_id=table.table_id,
                    source_cell_id=source.cell_id,
                    target_cell_id=target.cell_id,
                    relation=relation,
                    confidence=confidence,
                )
            )

        for cell in table.cells:
            right = by_position.get((cell.row_index, cell.col_index + cell.col_span))
            below = by_position.get((cell.row_index + cell.row_span, cell.col_index))
            if right:
                add(cell, right, "right_of")
            if below:
                add(cell, below, "below")
            if cell.row_index > 0:
                header = by_position.get((0, cell.col_index))
                if header and header.text:
                    add(header, cell, "column_header_for")
            if cell.col_index > 0:
                row_header = by_position.get((cell.row_index, 0))
                if row_header and row_header.text:
                    add(row_header, cell, "row_header_for")
        return edges

    def _propagate_headers_and_units(self, table):
        by_position = self._covered_positions(table)
        if table.cells and not table.header_row_indices:
            table.header_row_indices.append(0)
        for cell in table.cells:
            cell.is_header = cell.row_index in table.header_row_indices
            if cell.row_index > 0:
                headers = [
                    by_position[(header_row, cell.col_index)].text.strip()
                    for header_row in table.header_row_indices
                    if (header_row, cell.col_index) in by_position and by_position[(header_row, cell.col_index)].text.strip()
                ]
                cell.column_header_path = headers
            if cell.col_index > 0:
                row_header = by_position.get((cell.row_index, 0))
                cell.row_header_path = [row_header.text.strip()] if row_header and row_header.text.strip() else []
            unit_sources = [*cell.column_header_path, *cell.row_header_path, table.caption or ""]
            for source in unit_sources:
                match = self.UNIT_PATTERN.search(source)
                if match:
                    cell.unit_hint = match.group(1)
                    break

    def _link_cross_page_tables(self, tables):
        ordered = sorted(tables, key=lambda item: (item.page_index, item.order))
        group_counter = 0
        for first, second in zip(ordered, ordered[1:]):
            if second.page_index != first.page_index + 1:
                continue
            if first.continues_to_table_id or second.continues_from_table_id:
                continue
            similarity = self._header_similarity(first, second)
            repeated_title = self._leading_row_similarity(first, second)
            if (
                first.column_count <= 1
                or first.column_count != second.column_count
                or max(similarity, repeated_title) < 0.62
            ):
                continue
            group_counter += 1
            group_id = first.continuation_group_id or f"table-group-{group_counter:04d}-{first.table_id}"
            first.continuation_group_id = group_id
            second.continuation_group_id = group_id
            first.continues_to_table_id = second.table_id
            second.continues_from_table_id = first.table_id
            self._add_flag(first.quality_flags, "cross_page_table_linked")
            self._add_flag(second.quality_flags, "cross_page_table_linked")

    @staticmethod
    def _header_similarity(first, second):
        def text(table):
            headers = [cell.text.lower().strip() for cell in table.cells if cell.row_index == 0]
            return "|".join(headers)

        a, b = text(first), text(second)
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def _leading_row_similarity(first, second):
        def leading(table):
            rows = []
            for row_index in range(min(2, table.row_count)):
                values = [
                    cell.text.lower().strip()
                    for cell in sorted(table.cells, key=lambda item: item.col_index)
                    if cell.row_index == row_index and cell.text.strip()
                ]
                if values:
                    rows.append("|".join(values))
            return "|".join(rows)

        first_text, second_text = leading(first), leading(second)
        if not first_text or not second_text:
            return 0.0
        shorter, longer = sorted((first_text, second_text), key=len)
        if shorter in longer:
            return len(shorter) / len(longer)
        return SequenceMatcher(None, first_text, second_text).ratio()

    @staticmethod
    def _covered_positions(table):
        positions = {}
        for cell in table.cells:
            for row_index in range(cell.row_index, cell.row_index + cell.row_span):
                for col_index in range(cell.col_index, cell.col_index + cell.col_span):
                    positions[(row_index, col_index)] = cell
        return positions

    @staticmethod
    def _write_continuation_edges(document: DocumentIR) -> None:
        existing = {edge.edge_id for edge in document.structure_edges}
        for table in document.tables:
            if not table.continues_to_table_id:
                continue
            edge_id = f"edge-{table.table_id}-continues-{table.continues_to_table_id}"
            if edge_id in existing:
                continue
            document.structure_edges.append(
                StructureEdge(
                    edge_id=edge_id,
                    source_id=table.table_id,
                    target_id=table.continues_to_table_id,
                    relation="continues",
                    confidence=0.82,
                    source="deterministic_table_graph",
                )
            )
            existing.add(edge_id)

    @staticmethod
    def _add_flag(flags, value):
        if value not in flags:
            flags.append(value)
