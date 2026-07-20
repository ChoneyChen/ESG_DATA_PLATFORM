from __future__ import annotations

from esg_v2.document.contracts import BoundingBox, DocumentIR
from esg_v2.document.geometry import bbox_area_ratio, bbox_containment


class VisualSemanticGrouper:
    """Builds canonical visual regions from Paddle's often-fragmented image boxes."""

    def group(self, document: DocumentIR) -> DocumentIR:
        pages = {page.page_index: page for page in document.pages}
        tables_by_page = self._by_page(document.tables)
        blocks_by_page = self._by_page(document.blocks)
        figures_by_page = self._by_page(document.figures)
        canonical_figures = []
        figure_mapping: dict[str, str | None] = {}

        for page_index, figures in figures_by_page.items():
            page = pages.get(page_index)
            tables = tables_by_page.get(page_index, [])
            blocks = blocks_by_page.get(page_index, [])
            material = []
            for figure in figures:
                if figure.visual_type == "decoration":
                    figure_mapping[figure.figure_id] = None
                    continue
                if self._belongs_to_table(figure.bbox, tables, page.width if page else None):
                    figure_mapping[figure.figure_id] = None
                    continue
                material.append(figure)

            clusters = self._clusters(material, page.height if page else None)
            for cluster in clusters:
                if len(cluster) == 1:
                    figure = cluster[0]
                    area_ratio = bbox_area_ratio(
                        figure.bbox,
                        page_width=page.width if page else None,
                        page_height=page.height if page else None,
                    )
                    if figure.visual_type == "icon" and area_ratio < 0.02:
                        figure_mapping[figure.figure_id] = None
                        continue
                    canonical_figures.append(figure)
                    figure_mapping[figure.figure_id] = figure.figure_id
                    continue

                primary = cluster[0]
                component_bbox = self._union([item.bbox for item in cluster if item.bbox])
                related_blocks = self._related_blocks(component_bbox, blocks, page.height if page else None)
                combined_bbox = self._union([component_bbox, *[item.bbox for item in related_blocks if item.bbox]])
                primary.bbox = combined_bbox or component_bbox
                primary.image_path = None
                primary.visual_type = "diagram"
                primary.caption = self._caption(related_blocks)
                primary.quality_flags = list(
                    dict.fromkeys([*primary.quality_flags, "grouped_visual_components", "embedded_text_modeled"])
                )
                for block in related_blocks:
                    block.figure_id = primary.figure_id
                for component in cluster:
                    figure_mapping[component.figure_id] = primary.figure_id
                canonical_figures.append(primary)

        document.figures = sorted(canonical_figures, key=lambda item: (item.page_index, item.order, item.figure_id))
        for page in document.pages:
            page_figures = [item for item in document.figures if item.page_index == page.page_index]
            for order, figure in enumerate(page_figures):
                figure.order = order
            page.figure_ids = [item.figure_id for item in page_figures]
        for layout in document.layout_objects:
            if not layout.figure_id:
                continue
            mapped = figure_mapping.get(layout.figure_id, layout.figure_id)
            if mapped is None:
                layout.quality_flags = list(dict.fromkeys([*layout.quality_flags, "canonical_visual_fragment_filtered"]))
            layout.figure_id = mapped
        return document

    @staticmethod
    def _belongs_to_table(bbox: BoundingBox | None, tables, page_width: float | None) -> bool:
        if bbox is None:
            return False
        for table in tables:
            if bbox_containment(bbox, table.bbox) >= 0.7:
                return True
            if not table.bbox:
                continue
            vertical = max(0.0, min(bbox.y1, table.bbox.y1) - max(bbox.y0, table.bbox.y0))
            vertical_ratio = vertical / max(1.0, bbox.y1 - bbox.y0)
            horizontal_gap = min(abs(bbox.x1 - table.bbox.x0), abs(table.bbox.x1 - bbox.x0))
            if vertical_ratio >= 0.7 and horizontal_gap <= max(10.0, (page_width or 0) * 0.025):
                return True
        return False

    def _clusters(self, figures, page_height: float | None):
        remaining = list(figures)
        clusters = []
        max_gap = max(45.0, (page_height or 0) * 0.065)
        while remaining:
            cluster = [remaining.pop(0)]
            changed = True
            while changed:
                changed = False
                for candidate in list(remaining):
                    if any(self._vertical_gap(candidate.bbox, item.bbox) <= max_gap for item in cluster):
                        cluster.append(candidate)
                        remaining.remove(candidate)
                        changed = True
            clusters.append(sorted(cluster, key=lambda item: item.order))
        return clusters

    @staticmethod
    def _vertical_gap(first: BoundingBox | None, second: BoundingBox | None) -> float:
        if first is None or second is None:
            return float("inf")
        if first.y1 < second.y0:
            return second.y0 - first.y1
        if second.y1 < first.y0:
            return first.y0 - second.y1
        return 0.0

    @staticmethod
    def _related_blocks(bbox: BoundingBox | None, blocks, page_height: float | None):
        if bbox is None:
            return []
        expansion = max(55.0, min(145.0, (page_height or 0) * 0.18))
        top = bbox.y0 - 28.0
        bottom = bbox.y1 + expansion
        return [
            block
            for block in blocks
            if block.bbox
            and block.block_type not in {"header", "footer", "table_markdown"}
            and block.bbox.y0 >= top
            and block.bbox.y1 <= bottom
        ]

    @staticmethod
    def _caption(blocks) -> str | None:
        headings = [block.text.strip() for block in blocks if block.block_type == "heading" and block.text.strip()]
        return "：".join(headings[:2]) if headings else None

    @staticmethod
    def _union(boxes) -> BoundingBox | None:
        values = [bbox for bbox in boxes if bbox is not None]
        if not values:
            return None
        first = values[0]
        return BoundingBox(
            x0=min(item.x0 for item in values),
            y0=min(item.y0 for item in values),
            x1=max(item.x1 for item in values),
            y1=max(item.y1 for item in values),
            unit=first.unit,
            origin=first.origin,
            coordinate_system_id=first.coordinate_system_id,
        )

    @staticmethod
    def _by_page(items):
        result = {}
        for item in items:
            result.setdefault(item.page_index, []).append(item)
        return result
