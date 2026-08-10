from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

from esg_v2.document.contracts import DocumentIR, RetiredEntityIR
from esg_v2.document.geometry import bbox_containment, bbox_iou
from esg_v2.document.text_normalization import comparison_key


class DeterministicEntityDeduplicator:
    """Deduplicates canonical entities while preserving raw layout observations."""

    def deduplicate(self, document: DocumentIR) -> DocumentIR:
        document.blocks, block_map = self._dedupe_blocks(document.blocks, document)
        document.tables, table_map = self._dedupe_tables(document.tables)
        document.figures, figure_map = self._dedupe_figures(document.figures)
        self._rewrite_references(document, block_map, table_map, figure_map)
        return document

    def _dedupe_blocks(self, blocks, document: DocumentIR):
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
        kept, fallback_mapping = self._retire_duplicate_fallbacks(kept, document)
        mapping.update(fallback_mapping)
        self._renumber_orders(kept)
        return kept, mapping

    def _retire_duplicate_fallbacks(self, blocks, document: DocumentIR):
        fallback_mapping: dict[str, str] = {}
        kept = list(blocks)
        by_page: dict[int, list] = {}
        for block in kept:
            by_page.setdefault(block.page_index, []).append(block)

        for page_blocks in by_page.values():
            grounded = [
                block
                for block in page_blocks
                if block.bbox is not None
                and "markdown_fallback_without_layout_geometry" not in block.quality_flags
            ]
            fallbacks = [
                block
                for block in page_blocks
                if block.bbox is None
                and block.table_id is None
                and "markdown_fallback_without_layout_geometry" in block.quality_flags
            ]
            for fallback in fallbacks:
                matches = self._fallback_matches(fallback, grounded)
                if not matches:
                    continue
                fallback_mapping[fallback.block_id] = matches[0].block_id
                if fallback in kept:
                    kept.remove(fallback)
                evidence_refs = list(
                    dict.fromkeys(
                        [
                            *fallback.source_trace.artifact_ids,
                            *(
                                [fallback.source_trace.page_markdown_path]
                                if fallback.source_trace.page_markdown_path
                                else []
                            ),
                        ]
                    )
                )
                document.retired_entities.append(
                    RetiredEntityIR(
                        entity_id=fallback.block_id,
                        entity_type="block",
                        page_index=fallback.page_index,
                        disposition="duplicate_fallback",
                        snapshot=fallback.model_dump(mode="json"),
                        canonical_target_ids=[match.block_id for match in matches],
                        evidence_refs=evidence_refs,
                        reason=(
                            "Markdown fallback text is already represented by a grounded canonical "
                            "block sequence on the same page: "
                            f"{', '.join(match.block_id for match in matches)}."
                        ),
                    )
                )
                for match in matches:
                    self._merge_source_trace(match.source_trace, fallback.source_trace)
                    match.quality_flags = list(
                        dict.fromkeys([*match.quality_flags, "markdown_fallback_subsumed"])
                    )
        return kept, fallback_mapping

    def _fallback_matches(self, fallback, grounded):
        candidate = self._normalize(fallback.text)
        if not candidate:
            return []
        eligible = [
            block
            for block in sorted(grounded, key=lambda item: (item.order, item.block_id))
            if self._same_source_observation(fallback, block)
        ]
        for block in eligible:
            target = self._normalize(block.text)
            if candidate == target or candidate in target:
                return [block]
        for start in range(len(eligible)):
            for length in range(2, min(6, len(eligible) - start) + 1):
                sequence = eligible[start : start + length]
                if any(
                    second.order - first.order > 2
                    for first, second in zip(sequence, sequence[1:])
                ):
                    break
                combined = "".join(self._normalize(block.text) for block in sequence)
                if candidate == combined:
                    return sequence
        return []

    @staticmethod
    def _same_source_observation(first, second) -> bool:
        first_artifacts = set(first.source_trace.artifact_ids)
        second_artifacts = set(second.source_trace.artifact_ids)
        return bool(first_artifacts & second_artifacts) or (
            first.source_trace.page_markdown_path
            and first.source_trace.page_markdown_path == second.source_trace.page_markdown_path
        )

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
        local, canonical = self._local_and_canonical(first, second)
        if local is not None and canonical is not None:
            if bbox_containment(local.bbox, canonical.bbox) >= 0.92:
                local_text = self._cell_signature(local)
                canonical_text = self._cell_signature(canonical) or self._normalize(canonical.markdown)
                if self._text_coverage(local_text, canonical_text) >= 0.72:
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
        return (
            "local_only_table_candidate" not in table.quality_flags,
            bool(table.bbox),
            len(table.cells),
            table.row_count * table.column_count,
            bool(table.block_id),
        )

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
        source_flags = list(source.quality_flags)
        if (
            "local_only_table_candidate" in source_flags
            and "local_only_table_candidate" not in target.quality_flags
        ):
            source_flags = [
                flag
                for flag in source_flags
                if flag
                not in {
                    "local_only_table_candidate",
                    "blank_visual_encoding_cells",
                    "table_structure_recovered_from_pdfplumber",
                }
            ]
            source_flags.append("local_candidate_subsumed")
        target.quality_flags = list(
            dict.fromkeys([*target.quality_flags, *source_flags, "deduplicated_entity"])
        )
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
        for section in document.sections:
            if section.heading_block_id:
                section.heading_block_id = block_map.get(section.heading_block_id, section.heading_block_id)
            section.block_ids = list(dict.fromkeys(block_map.get(item, item) for item in section.block_ids))

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
        return comparison_key(text)

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

    @staticmethod
    def _local_and_canonical(first, second):
        first_local = "local_only_table_candidate" in first.quality_flags
        second_local = "local_only_table_candidate" in second.quality_flags
        if first_local == second_local:
            return None, None
        return (first, second) if first_local else (second, first)

    @staticmethod
    def _text_coverage(source: str, candidate: str) -> float:
        source_chars = Counter(character for character in source if character.isalnum())
        candidate_chars = Counter(character for character in candidate if character.isalnum())
        if not source_chars:
            return 0.0
        retained = sum((source_chars & candidate_chars).values())
        return retained / sum(source_chars.values())
