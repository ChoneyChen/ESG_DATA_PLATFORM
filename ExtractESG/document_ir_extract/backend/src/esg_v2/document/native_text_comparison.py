from __future__ import annotations

from collections import defaultdict

from esg_v2.document.contracts import DocumentIR, LocalPdfForensics, NativeTextExclusionIR
from esg_v2.document.geometry import bbox_area_ratio


class NativeTextComparisonPreprocessor:
    """Builds a page-level native-text view without embedded document microtext."""

    MAX_PREVIEW_AREA_RATIO = 0.10
    MAX_MICROTEXT_HEIGHT_POINTS = 3.5
    MIN_MICROTEXT_BLOCKS = 12
    MIN_MICROTEXT_CHARACTERS = 120
    FIGURE_BBOX_EXPANSION_RATIO = 0.20
    IMAGE_LABEL_MARKERS = {
        "image",
        "figure",
        "illustration",
        "picture",
        "photo",
        "document",
    }

    def prepare(self, document: DocumentIR, forensics: LocalPdfForensics) -> None:
        pages_by_index = {page.page_index: page for page in document.pages}
        figures_by_page: dict[int, list] = defaultdict(list)
        layouts_by_page: dict[int, list] = defaultdict(list)
        for figure in document.figures:
            figures_by_page[figure.page_index].append(figure)
        for layout in document.layout_objects:
            layouts_by_page[layout.page_index].append(layout)

        for local_page in forensics.pages:
            local_page.native_text_for_comparison = local_page.native_text
            local_page.native_text_for_comparison_length = local_page.native_text_length
            local_page.native_text_excluded_figure_ids = []
            local_page.native_text_excluded_block_count = 0
            local_page.native_text_exclusions = []

            page = pages_by_index.get(local_page.page_index)
            if page is None or not local_page.native_text_blocks:
                continue

            excluded_block_indices: set[int] = set()
            excluded_figure_ids: list[str] = []
            exclusion_counter = 0
            for region_id, region_bbox, matched_figure_id, evidence_refs in self._preview_regions(
                page,
                figures_by_page.get(local_page.page_index, []),
                layouts_by_page.get(local_page.page_index, []),
            ):
                if region_bbox is None:
                    continue
                expanded_bbox = self._expanded_bbox(region_bbox, page.width, page.height)
                microtext_indices = [
                    index
                    for index, block in enumerate(local_page.native_text_blocks)
                    if self._is_microtext(block)
                    and self._center_inside(block.bbox, expanded_bbox)
                    and index not in excluded_block_indices
                ]
                character_count = sum(
                    len(local_page.native_text_blocks[index].text.strip())
                    for index in microtext_indices
                )
                if (
                    len(microtext_indices) < self.MIN_MICROTEXT_BLOCKS
                    or character_count < self.MIN_MICROTEXT_CHARACTERS
                ):
                    continue
                excluded_block_indices.update(microtext_indices)
                if matched_figure_id:
                    excluded_figure_ids.append(matched_figure_id)
                    figure = next(
                        (
                            item
                            for item in figures_by_page.get(local_page.page_index, [])
                            if item.figure_id == matched_figure_id
                        ),
                        None,
                    )
                    if figure:
                        self._add_flag(figure.quality_flags, "embedded_document_preview_candidate")
                exclusion_counter += 1
                local_page.native_text_exclusions.append(
                    NativeTextExclusionIR(
                        exclusion_id=f"native-exclusion-p{local_page.page_index + 1:04d}-{exclusion_counter:04d}",
                        page_index=local_page.page_index,
                        bbox=region_bbox.model_copy(deep=True),
                        reason="embedded_document_preview_microtext",
                        native_block_indices=microtext_indices,
                        excluded_character_count=character_count,
                        matched_layout_object_ids=[region_id] if region_id.startswith("layout-") else [],
                        matched_figure_id=matched_figure_id,
                        evidence_refs=list(dict.fromkeys(evidence_refs)),
                    )
                )

            if not excluded_block_indices:
                continue

            effective_text = " ".join(
                block.text.strip()
                for index, block in enumerate(local_page.native_text_blocks)
                if index not in excluded_block_indices and block.text.strip()
            )
            local_page.native_text_for_comparison = effective_text
            local_page.native_text_for_comparison_length = len(effective_text)
            local_page.native_text_excluded_figure_ids = excluded_figure_ids
            local_page.native_text_excluded_block_count = len(excluded_block_indices)
            self._add_flag(
                local_page.quality_flags,
                "embedded_document_preview_native_text_excluded_from_coverage",
            )
            self._add_flag(
                page.quality_flags,
                "embedded_document_preview_native_text_excluded_from_coverage",
            )

    def _preview_regions(self, page, figures, layouts):
        seen: set[tuple[float, float, float, float]] = set()
        figure_by_id = {figure.figure_id: figure for figure in figures}
        for layout in layouts:
            label = str(layout.label or "").casefold()
            if (
                layout.bbox is None
                or not any(marker in label for marker in self.IMAGE_LABEL_MARKERS)
                or not self._small_region(layout.bbox, page)
            ):
                continue
            figure = figure_by_id.get(layout.figure_id) if layout.figure_id else None
            if figure and figure.visual_type == "chart":
                continue
            key = self._bbox_key(layout.bbox)
            if key in seen:
                continue
            seen.add(key)
            refs = [
                reference
                for reference in [
                    page.page_image_path,
                    figure.image_path if figure else None,
                ]
                if reference
            ]
            yield layout.layout_object_id, layout.bbox, figure.figure_id if figure else None, refs

        for figure in figures:
            if not self._can_be_document_preview(figure, page):
                continue
            key = self._bbox_key(figure.bbox)
            if key in seen:
                continue
            seen.add(key)
            refs = [
                reference
                for reference in [page.page_image_path, figure.image_path]
                if reference
            ]
            yield figure.figure_id, figure.bbox, figure.figure_id, refs

    def _can_be_document_preview(self, figure, page) -> bool:
        if figure.bbox is None or figure.visual_type not in {"unknown", "illustration"}:
            return False
        return self._small_region(figure.bbox, page)

    def _small_region(self, bbox, page) -> bool:
        area_ratio = bbox_area_ratio(
            bbox,
            page_width=page.width,
            page_height=page.height,
        )
        return 0 < area_ratio <= self.MAX_PREVIEW_AREA_RATIO

    @classmethod
    def _expanded_bbox(cls, bbox, page_width: float | None, page_height: float | None):
        width = max(0.0, bbox.x1 - bbox.x0)
        height = max(0.0, bbox.y1 - bbox.y0)
        x_margin = width * cls.FIGURE_BBOX_EXPANSION_RATIO
        y_margin = height * cls.FIGURE_BBOX_EXPANSION_RATIO
        return (
            max(0.0, bbox.x0 - x_margin),
            max(0.0, bbox.y0 - y_margin),
            min(page_width, bbox.x1 + x_margin) if page_width is not None else bbox.x1 + x_margin,
            min(page_height, bbox.y1 + y_margin) if page_height is not None else bbox.y1 + y_margin,
        )

    @classmethod
    def _is_microtext(cls, block) -> bool:
        return 0 < block.bbox.y1 - block.bbox.y0 <= cls.MAX_MICROTEXT_HEIGHT_POINTS

    @staticmethod
    def _bbox_key(bbox) -> tuple[float, float, float, float]:
        return tuple(round(value, 2) for value in (bbox.x0, bbox.y0, bbox.x1, bbox.y1))

    @staticmethod
    def _center_inside(bbox, region: tuple[float, float, float, float]) -> bool:
        center_x = (bbox.x0 + bbox.x1) / 2
        center_y = (bbox.y0 + bbox.y1) / 2
        return region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]

    @staticmethod
    def _add_flag(flags: list[str], flag: str) -> None:
        if flag not in flags:
            flags.append(flag)
