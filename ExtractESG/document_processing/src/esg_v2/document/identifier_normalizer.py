from __future__ import annotations

from collections import defaultdict

from esg_v2.document.contracts import DocumentIR
from esg_v2.storage.package_layout import cell_id, page_object_id, page_stem


class CanonicalIdentifierNormalizer:
    """Assign stable, readable IDs before review tasks or patches reference entities."""

    def normalize(self, document: DocumentIR) -> DocumentIR:
        page_map = self._page_ids(document)
        layout_map = self._page_object_ids(document.layout_objects, "layout", "layout_object_id")
        block_map = self._page_object_ids(document.blocks, "block", "block_id")
        table_map, table_sequences = self._table_ids(document)
        figure_map = self._page_object_ids(document.figures, "figure", "figure_id")
        entity_map = {**page_map, **layout_map, **block_map, **table_map, **figure_map}

        for page in document.pages:
            page.page_id = page_map.get(page.page_id, page.page_id)
            page.layout_object_ids = self._map_list(page.layout_object_ids, layout_map)
            page.block_ids = self._map_list(page.block_ids, block_map)
            page.table_ids = self._map_list(page.table_ids, table_map)
            page.figure_ids = self._map_list(page.figure_ids, figure_map)

        for layout in document.layout_objects:
            layout.layout_object_id = layout_map.get(layout.layout_object_id, layout.layout_object_id)
            layout.block_id = self._map_optional(layout.block_id, block_map)
            layout.table_id = self._map_optional(layout.table_id, table_map)
            layout.figure_id = self._map_optional(layout.figure_id, figure_map)

        for block in document.blocks:
            block.block_id = block_map.get(block.block_id, block.block_id)
            block.layout_object_id = self._map_optional(block.layout_object_id, layout_map)
            block.table_id = self._map_optional(block.table_id, table_map)
            block.figure_id = self._map_optional(block.figure_id, figure_map)
            block.section_id = None

        for table in document.tables:
            old_table_id = table.table_id
            table.table_id = table_map.get(old_table_id, old_table_id)
            table.block_id = self._map_optional(table.block_id, block_map)
            table.footnote_block_ids = self._map_list(table.footnote_block_ids, block_map)
            table.continues_from_table_id = self._map_optional(table.continues_from_table_id, table_map)
            table.continues_to_table_id = self._map_optional(table.continues_to_table_id, table_map)
            sequence = table_sequences.get(old_table_id, table.order + 1)
            cell_map = {}
            for item in sorted(table.cells, key=lambda value: (value.row_index, value.col_index, value.cell_id)):
                new_cell_id = cell_id(table.page_index, sequence, item.row_index, item.col_index)
                cell_map[item.cell_id] = new_cell_id
                item.cell_id = new_cell_id
                item.table_id = table.table_id
            for edge in table.graph_edges:
                edge.table_id = table.table_id
                edge.source_cell_id = cell_map.get(edge.source_cell_id, edge.source_cell_id)
                edge.target_cell_id = cell_map.get(edge.target_cell_id, edge.target_cell_id)

        for figure in document.figures:
            figure.figure_id = figure_map.get(figure.figure_id, figure.figure_id)

        for logical_table in document.logical_tables:
            logical_table.source_table_ids = self._map_list(logical_table.source_table_ids, table_map)
            for segment in logical_table.segments:
                segment.table_id = table_map.get(segment.table_id, segment.table_id)
            for mapping in logical_table.cell_mappings:
                mapping.source_table_id = table_map.get(mapping.source_table_id, mapping.source_table_id)

        for conflict in document.conflict_groups:
            conflict.target_id = entity_map.get(conflict.target_id, conflict.target_id)
        for sequence, conflict in enumerate(
            sorted(document.conflict_groups, key=lambda item: (item.target_id, item.conflict_type, item.conflict_id)),
            start=1,
        ):
            conflict.conflict_id = f"conflict-{sequence:06d}"

        document.sections = []
        document.structure_edges = []
        document.metadata.source_artifacts["identifier_contract"] = "document-ir-identifiers-v1"
        return document

    @staticmethod
    def _page_ids(document: DocumentIR) -> dict[str, str]:
        return {
            page.page_id: page_stem(page.page_index)
            for page in sorted(document.pages, key=lambda item: item.page_index)
        }

    @staticmethod
    def _page_object_ids(items, kind: str, id_field: str) -> dict[str, str]:
        mapping = {}
        by_page = defaultdict(list)
        for item in items:
            by_page[item.page_index].append(item)
        for page_index, page_items in sorted(by_page.items()):
            ordered = sorted(page_items, key=lambda item: (item.order, getattr(item, id_field)))
            for sequence, item in enumerate(ordered, start=1):
                mapping[getattr(item, id_field)] = page_object_id(kind, page_index, sequence)
        return mapping

    @staticmethod
    def _table_ids(document: DocumentIR) -> tuple[dict[str, str], dict[str, int]]:
        mapping = {}
        sequences = {}
        by_page = defaultdict(list)
        for table in document.tables:
            by_page[table.page_index].append(table)
        for page_index, tables in sorted(by_page.items()):
            for sequence, table in enumerate(sorted(tables, key=lambda item: (item.order, item.table_id)), start=1):
                mapping[table.table_id] = page_object_id("table", page_index, sequence)
                sequences[table.table_id] = sequence
        return mapping, sequences

    @staticmethod
    def _map_optional(value: str | None, mapping: dict[str, str]) -> str | None:
        return mapping.get(value, value) if value else None

    @staticmethod
    def _map_list(values: list[str], mapping: dict[str, str]) -> list[str]:
        return [mapping.get(value, value) for value in values]
