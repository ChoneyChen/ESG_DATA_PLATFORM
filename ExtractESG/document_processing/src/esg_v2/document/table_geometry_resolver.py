from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from esg_v2.document.contracts import DocumentIR, LocalPdfForensics
from esg_v2.document.geometry import normalize_bbox_to_page, union_bboxes


class TableGeometryResolver:
    """Recovers canonical table regions from deterministic, traceable sources."""

    def resolve(
        self,
        document: DocumentIR,
        local_forensics: LocalPdfForensics | None = None,
    ) -> DocumentIR:
        pages = {page.page_index: page for page in document.pages}
        blocks = {block.block_id: block for block in document.blocks}
        layouts_by_table: dict[str, list] = defaultdict(list)
        layouts_by_block: dict[str, list] = defaultdict(list)
        layouts_by_page: dict[int, list] = defaultdict(list)
        for layout in document.layout_objects:
            layouts_by_page[layout.page_index].append(layout)
            if layout.table_id:
                layouts_by_table[layout.table_id].append(layout)
            if layout.block_id:
                layouts_by_block[layout.block_id].append(layout)
        claimed_layout_ids = {
            layout.layout_object_id
            for layout in document.layout_objects
            if layout.table_id
        }

        counters: Counter[str] = Counter()
        for table in document.tables:
            page = pages.get(table.page_index)
            width = page.width if page else None
            height = page.height if page else None
            if table.bbox is not None:
                table.bbox, status = normalize_bbox_to_page(
                    table.bbox,
                    page_width=width,
                    page_height=height,
                )
                if status == "clipped_to_page":
                    self._add_flag(table.quality_flags, "table_bbox_clipped_to_page")
                    counters["clipped"] += 1
                elif table.bbox is None:
                    self._add_flag(table.quality_flags, f"table_bbox_{status}")
                    counters["rejected"] += 1

            for cell in table.cells:
                if cell.bbox is None:
                    continue
                cell.bbox, status = normalize_bbox_to_page(
                    cell.bbox,
                    page_width=width,
                    page_height=height,
                )
                if status != "unchanged":
                    self._add_flag(cell.quality_flags, f"cell_bbox_{status}")

            if table.bbox is None:
                recovered, source, matched_layout = self._recover(
                    table,
                    blocks=blocks,
                    layouts_by_table=layouts_by_table,
                    layouts_by_block=layouts_by_block,
                    page_layouts=layouts_by_page.get(table.page_index, []),
                    claimed_layout_ids=claimed_layout_ids,
                    page_width=width,
                    page_height=height,
                )
                if recovered is not None:
                    table.bbox = recovered
                    self._add_flag(table.quality_flags, f"table_geometry_recovered_from_{source}")
                    counters[f"recovered_from_{source}"] += 1
                    if matched_layout is not None:
                        matched_layout.table_id = table.table_id
                        claimed_layout_ids.add(matched_layout.layout_object_id)
                        table.source_trace.notes = list(
                            dict.fromkeys(
                                [
                                    *table.source_trace.notes,
                                    f"geometry_semantically_matched_to:{matched_layout.layout_object_id}",
                                ]
                            )
                        )
                    wrapper = blocks.get(table.block_id) if table.block_id else None
                    if wrapper is not None and wrapper.bbox is None:
                        wrapper.bbox = recovered
                        self._add_flag(wrapper.quality_flags, f"geometry_recovered_from_{source}")

            if table.bbox is None:
                self._add_flag(table.quality_flags, "table_geometry_unresolved")
                counters["unresolved"] += 1
            else:
                table.quality_flags = [
                    flag
                    for flag in table.quality_flags
                    if flag not in {"table_geometry_missing", "table_geometry_unresolved", "visual_crop_unavailable"}
                ]

        document.metadata.source_artifacts["table_geometry_resolution"] = dict(counters)
        return document

    @staticmethod
    def _recover(
        table,
        *,
        blocks,
        layouts_by_table,
        layouts_by_block,
        page_layouts,
        claimed_layout_ids,
        page_width,
        page_height,
    ):
        candidates = []
        wrapper = blocks.get(table.block_id) if table.block_id else None
        if wrapper and wrapper.bbox:
            candidates.append(("wrapper_block", wrapper.bbox, None))
        for layout in layouts_by_table.get(table.table_id, []):
            if layout.bbox:
                candidates.append(("paddle_layout", layout.bbox, layout))
        if table.block_id:
            for layout in layouts_by_block.get(table.block_id, []):
                if layout.bbox:
                    candidates.append(("paddle_layout", layout.bbox, layout))

        semantic_layouts = []
        for layout in page_layouts:
            normalized_label = layout.label.lower().replace("-", "_").replace(" ", "_")
            if (
                layout.layout_object_id in claimed_layout_ids
                or layout.bbox is None
                or not any(token in normalized_label for token in ("table", "chart", "figure"))
            ):
                continue
            score = TableGeometryResolver._semantic_layout_score(table, layout)
            if score >= 0.55:
                semantic_layouts.append((score, layout))
        semantic_layouts.sort(key=lambda item: item[0], reverse=True)
        if semantic_layouts:
            candidates.append(("semantic_paddle_layout", semantic_layouts[0][1].bbox, semantic_layouts[0][1]))
        for observation in sorted(
            table.observations,
            key=lambda item: (item.match_score or 0.0, item.match_iou or 0.0),
            reverse=True,
        ):
            if observation.bbox:
                candidates.append((observation.source, observation.bbox, None))
        cell_union = union_bboxes([cell.bbox for cell in table.cells if cell.bbox is not None])
        if cell_union:
            candidates.append(("cell_union", cell_union, None))

        for source, candidate, matched_layout in candidates:
            normalized, _ = normalize_bbox_to_page(
                candidate,
                page_width=page_width,
                page_height=page_height,
            )
            if normalized is not None:
                return normalized, source, matched_layout
        return None, None, None

    @staticmethod
    def _semantic_layout_score(table, layout) -> float:
        table_values = [
            re.sub(r"\s+", "", str(cell.text or "")).strip().lower()
            for cell in sorted(table.cells, key=lambda item: (item.row_index, item.col_index))
            if str(cell.text or "").strip()
        ]
        layout_text = re.sub(r"[\s|]+", "", str(layout.text or "")).strip().lower()
        table_text = re.sub(r"[\s|]+", "", "".join(table_values)).strip().lower()
        if not table_text or not layout_text:
            return 0.0
        sequence_score = SequenceMatcher(None, table_text, layout_text).ratio()
        meaningful = [value for value in table_values if len(value) >= 2 or any(char.isdigit() for char in value)]
        covered = sum(1 for value in meaningful if value in layout_text)
        coverage_score = covered / len(meaningful) if meaningful else 0.0
        return 0.55 * coverage_score + 0.45 * sequence_score

    @staticmethod
    def _add_flag(flags: list[str], value: str) -> None:
        if value not in flags:
            flags.append(value)
