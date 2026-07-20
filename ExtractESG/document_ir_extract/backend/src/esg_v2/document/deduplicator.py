from __future__ import annotations

import html
import re
from difflib import SequenceMatcher

from esg_v2.document.contracts import DocumentIR
from esg_v2.document.geometry import bbox_iou


class DeterministicEntityDeduplicator:
    """Deduplicates canonical entities while preserving raw layout observations."""

    def deduplicate(self, document: DocumentIR) -> DocumentIR:
        document.blocks, block_map = self._dedupe_blocks(document.blocks)
        document.tables, table_map = self._dedupe_tables(document.tables)
        document.figures, figure_map = self._dedupe_figures(document.figures)
        self._rewrite_references(document, block_map, table_map, figure_map)
        return document

    def _dedupe_blocks(self, blocks):
        kept = []
        mapping: dict[str, str] = {}
        for block in sorted(blocks, key=lambda item: (item.page_index, item.order, item.block_id)):
            duplicate = next((item for item in kept if self._same_block(item, block)), None)
            if duplicate is None:
                kept.append(block)
                mapping[block.block_id] = block.block_id
                continue
            preferred, discarded = self._prefer_block(duplicate, block)
            if preferred is block:
                kept[kept.index(duplicate)] = block
                mapping[duplicate.block_id] = block.block_id
                mapping[block.block_id] = block.block_id
                self._merge_block(block, duplicate)
            else:
                mapping[block.block_id] = duplicate.block_id
                self._merge_block(duplicate, discarded)
        self._renumber_orders(kept)
        return kept, mapping

    def _dedupe_tables(self, tables):
        kept = []
        mapping: dict[str, str] = {}
        for table in sorted(tables, key=lambda item: (item.page_index, item.order, item.table_id)):
            duplicate = next((item for item in kept if self._same_table(item, table)), None)
            if duplicate is None:
                kept.append(table)
                mapping[table.table_id] = table.table_id
                continue
            preferred = max((duplicate, table), key=self._table_quality)
            discarded = table if preferred is duplicate else duplicate
            if preferred is table:
                kept[kept.index(duplicate)] = table
            self._merge_table(preferred, discarded)
            mapping[discarded.table_id] = preferred.table_id
            mapping[preferred.table_id] = preferred.table_id
        self._renumber_orders(kept)
        return kept, mapping

    def _dedupe_figures(self, figures):
        kept = []
        mapping: dict[str, str] = {}
        for figure in sorted(figures, key=lambda item: (item.page_index, item.order, item.figure_id)):
            duplicate = next((item for item in kept if self._same_figure(item, figure)), None)
            if duplicate is None:
                kept.append(figure)
                mapping[figure.figure_id] = figure.figure_id
                continue
            preferred = max((duplicate, figure), key=self._figure_quality)
            discarded = figure if preferred is duplicate else duplicate
            if preferred is figure:
                kept[kept.index(duplicate)] = figure
            self._merge_figure(preferred, discarded)
            mapping[discarded.figure_id] = preferred.figure_id
            mapping[preferred.figure_id] = preferred.figure_id
        self._renumber_orders(kept)
        return kept, mapping

    def _same_block(self, first, second) -> bool:
        if first.page_index != second.page_index or first.block_type != second.block_type:
            return False
        first_text = self._normalize(first.text)
        second_text = self._normalize(second.text)
        if not first_text or not second_text:
            return False
        if first.bbox and second.bbox:
            return bbox_iou(first.bbox, second.bbox) >= 0.88 and self._text_similarity(first_text, second_text) >= 0.9
        return self._text_similarity(first_text, second_text) >= 0.96

    def _same_table(self, first, second) -> bool:
        if first.page_index != second.page_index:
            return False
        if first.bbox and second.bbox and bbox_iou(first.bbox, second.bbox) >= 0.88:
            return True
        first_text = self._normalize(first.markdown)
        second_text = self._normalize(second.markdown)
        if first_text and second_text and self._text_similarity(first_text, second_text) >= 0.95:
            return True
        if first.row_count != second.row_count or first.column_count != second.column_count:
            return False
        first_cells = self._cell_signature(first)
        second_cells = self._cell_signature(second)
        return bool(first_cells and second_cells and self._text_similarity(first_cells, second_cells) >= 0.9)

    @staticmethod
    def _same_figure(first, second) -> bool:
        if first.page_index != second.page_index:
            return False
        if first.image_path and first.image_path == second.image_path:
            return True
        return bbox_iou(first.bbox, second.bbox) >= 0.92

    @staticmethod
    def _prefer_block(first, second):
        def score(item):
            return (bool(item.bbox), bool(item.layout_object_id), len(item.text), -len(item.quality_flags))

        return (first, second) if score(first) >= score(second) else (second, first)

    @staticmethod
    def _table_quality(table):
        return (bool(table.bbox), len(table.cells), table.row_count * table.column_count, bool(table.block_id))

    @staticmethod
    def _figure_quality(figure):
        return (bool(figure.bbox), bool(figure.image_path), bool(figure.crop_artifact_id))

    @staticmethod
    def _merge_block(target, source) -> None:
        target.quality_flags = list(dict.fromkeys([*target.quality_flags, *source.quality_flags, "deduplicated_entity"]))
        target.review_task_ids = list(dict.fromkeys([*target.review_task_ids, *source.review_task_ids]))
        target.bbox = target.bbox or source.bbox
        target.polygon = target.polygon or source.polygon
        target.markdown = target.markdown or source.markdown
        target.layout_object_id = target.layout_object_id or source.layout_object_id
        DeterministicEntityDeduplicator._merge_source_trace(target.source_trace, source.source_trace)

    @staticmethod
    def _merge_table(target, source) -> None:
        target.quality_flags = list(dict.fromkeys([*target.quality_flags, *source.quality_flags, "deduplicated_entity"]))
        target.review_task_ids = list(dict.fromkeys([*target.review_task_ids, *source.review_task_ids]))
        target.observations = list({item.observation_id: item for item in [*target.observations, *source.observations]}.values())
        target.bbox = target.bbox or source.bbox
        target.block_id = target.block_id or source.block_id
        if not target.cells and source.cells:
            target.cells = source.cells
            target.row_count = source.row_count
            target.column_count = source.column_count
        if target.bbox:
            target.quality_flags = [
                flag for flag in target.quality_flags if flag not in {"table_geometry_missing", "visual_crop_unavailable"}
            ]
        if target.cells:
            target.quality_flags = [flag for flag in target.quality_flags if flag != "empty_markdown_table"]
        DeterministicEntityDeduplicator._merge_source_trace(target.source_trace, source.source_trace)

    @staticmethod
    def _merge_figure(target, source) -> None:
        target.quality_flags = list(dict.fromkeys([*target.quality_flags, *source.quality_flags, "deduplicated_entity"]))
        target.review_task_ids = list(dict.fromkeys([*target.review_task_ids, *source.review_task_ids]))
        target.bbox = target.bbox or source.bbox
        target.image_path = target.image_path or source.image_path
        target.crop_artifact_id = target.crop_artifact_id or source.crop_artifact_id
        if target.visual_type == "unknown":
            target.visual_type = source.visual_type
        DeterministicEntityDeduplicator._merge_source_trace(target.source_trace, source.source_trace)

    @staticmethod
    def _merge_source_trace(target, source) -> None:
        target.artifact_ids = list(dict.fromkeys([*target.artifact_ids, *source.artifact_ids]))
        target.notes = list(dict.fromkeys([*target.notes, *source.notes]))

    @staticmethod
    def _rewrite_references(document, block_map, table_map, figure_map) -> None:
        for page in document.pages:
            page.block_ids = list(dict.fromkeys(block_map.get(item, item) for item in page.block_ids))
            page.table_ids = list(dict.fromkeys(table_map.get(item, item) for item in page.table_ids))
            page.figure_ids = list(dict.fromkeys(figure_map.get(item, item) for item in page.figure_ids))
        for layout in document.layout_objects:
            if layout.block_id:
                layout.block_id = block_map.get(layout.block_id, layout.block_id)
            if layout.table_id:
                layout.table_id = table_map.get(layout.table_id, layout.table_id)
            if layout.figure_id:
                layout.figure_id = figure_map.get(layout.figure_id, layout.figure_id)
        for block in document.blocks:
            if block.table_id:
                block.table_id = table_map.get(block.table_id, block.table_id)
            if block.figure_id:
                block.figure_id = figure_map.get(block.figure_id, block.figure_id)
        for table in document.tables:
            if table.block_id:
                table.block_id = block_map.get(table.block_id, table.block_id)

    @staticmethod
    def _renumber_orders(items) -> None:
        pages: dict[int, list] = {}
        for item in items:
            pages.setdefault(item.page_index, []).append(item)
        for page_items in pages.values():
            for order, item in enumerate(sorted(page_items, key=lambda entry: (entry.order, getattr(entry, "bbox", None).y0 if getattr(entry, "bbox", None) else float("inf")))):
                item.order = order

    @staticmethod
    def _normalize(text: str) -> str:
        value = re.sub(r"<img\b[^>]*>", " ", text or "", flags=re.IGNORECASE)
        value = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", "", html.unescape(value)).strip().lower()

    @classmethod
    def _cell_signature(cls, table) -> str:
        return "|".join(
            cls._normalize(cell.text)
            for cell in sorted(table.cells, key=lambda item: (item.row_index, item.col_index))
        )

    @staticmethod
    def _text_similarity(first: str, second: str) -> float:
        shorter, longer = sorted((first, second), key=len)
        if shorter and shorter in longer and len(shorter) / len(longer) >= 0.8:
            return 1.0
        return SequenceMatcher(None, first, second).ratio()
