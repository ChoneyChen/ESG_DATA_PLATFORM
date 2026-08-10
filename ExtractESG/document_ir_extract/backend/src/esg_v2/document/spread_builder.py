from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from esg_v2.document.contracts import (
    ArtifactRef,
    DocumentIR,
    SourceTrace,
    SpreadIR,
    SpreadPagePlacement,
    StructureEdge,
)
from esg_v2.storage.package_layout import DocumentIrPackageLayout
from esg_v2.utils.hashing import sha256_file


@dataclass(frozen=True)
class _SeamObject:
    object_id: str
    label: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)


class HorizontalSpreadBuilder:
    """Builds conservative adjacent-page spread candidates and composite evidence."""

    EDGE_RATIO = 0.025
    MIN_WIDTH_RATIO = 0.045
    MIN_HEIGHT_RATIO = 0.012
    MIN_ALIGNMENT = 0.22
    MIN_CANDIDATE_SCORE = 0.60
    MIN_ACTIVE_SEAM_ROWS = 0.03
    MIN_ACTIVE_SEAM_OVERLAP = 0.45
    MAX_SEAM_DARKNESS_DIFFERENCE = 0.12
    VISUAL_LABELS = {"table", "image", "figure", "chart", "diagram", "seal"}

    def build(self, document: DocumentIR, output_dir: Path) -> DocumentIR:
        previous = {
            tuple(item.page_indices): item
            for item in document.spreads
            if len(item.page_indices) == 2
        }
        document.artifacts = [
            artifact for artifact in document.artifacts if artifact.kind != "spread_image"
        ]
        document.structure_edges = [
            edge for edge in document.structure_edges if edge.source != "horizontal_spread_builder"
        ]
        for page in document.pages:
            page.spread_ids = []
            page.quality_flags = [
                flag
                for flag in page.quality_flags
                if flag not in {
                    "horizontal_spread_candidate",
                    "horizontal_spread_confirmed",
                    "horizontal_spread_rejected",
                }
            ]

        pages = sorted(document.pages, key=lambda item: item.page_index)
        layouts_by_page = {
            page.page_index: [
                item for item in document.layout_objects if item.page_index == page.page_index
            ]
            for page in pages
        }
        spreads: list[SpreadIR] = []
        for left, right in zip(pages, pages[1:]):
            if right.page_index != left.page_index + 1:
                continue
            candidate = self._candidate(
                document,
                left,
                right,
                layouts_by_page.get(left.page_index, []),
                layouts_by_page.get(right.page_index, []),
                previous.get((left.page_index, right.page_index)),
                output_dir,
            )
            if candidate is not None:
                spreads.append(candidate)

        document.spreads = spreads
        self._attach(document)
        return document

    def _candidate(
        self,
        document,
        left_page,
        right_page,
        left_layouts,
        right_layouts,
        previous,
        output_dir: Path,
    ) -> SpreadIR | None:
        if not left_page.width or not left_page.height or not right_page.width or not right_page.height:
            return None
        size_ratio = min(left_page.width, right_page.width) / max(left_page.width, right_page.width)
        height_ratio = min(left_page.height, right_page.height) / max(left_page.height, right_page.height)
        if size_ratio < 0.97 or height_ratio < 0.97:
            return None

        left_objects = self._seam_objects(left_layouts, left_page, side="right")
        right_objects = self._seam_objects(right_layouts, right_page, side="left")
        if not left_objects or not right_objects:
            return None
        alignment, aligned_pair = self._best_alignment(left_objects, right_objects)
        if alignment < self.MIN_ALIGNMENT:
            return None

        visual_signal = (
            aligned_pair[0].label.casefold() in self.VISUAL_LABELS
            or aligned_pair[1].label.casefold() in self.VISUAL_LABELS
        )
        repeated_edge_signal = len(left_objects) >= 2 or len(right_objects) >= 2
        if not visual_signal and not repeated_edge_signal:
            return None
        pixel_metrics = self._visual_seam_metrics(
            left_page.page_image_path,
            right_page.page_image_path,
        )
        if (
            pixel_metrics is None
            or pixel_metrics["active_row_ratio"] < self.MIN_ACTIVE_SEAM_ROWS
            or pixel_metrics["active_row_overlap"] < self.MIN_ACTIVE_SEAM_OVERLAP
            or pixel_metrics["darkness_difference"] > self.MAX_SEAM_DARKNESS_DIFFERENCE
        ):
            return None

        parity_signal = self._printed_pair_consistent(
            left_page.printed_page_label,
            right_page.printed_page_label,
        )
        large_signal = max(
            aligned_pair[0].height / left_page.height,
            aligned_pair[1].height / right_page.height,
        ) >= 0.12
        score = (
            0.30
            + min(0.25, alignment * 0.25)
            + (0.20 if parity_signal else 0.0)
            + (0.15 if visual_signal else 0.0)
            + (0.05 if repeated_edge_signal else 0.0)
            + (0.05 if large_signal else 0.0)
            + 0.05
        )
        score = min(1.0, score)
        if score < self.MIN_CANDIDATE_SCORE:
            return None

        spread_id = f"spread-p{left_page.page_index + 1:04d}-p{right_page.page_index + 1:04d}"
        artifact = self._composite(
            spread_id,
            left_page,
            right_page,
            output_dir,
        )
        if artifact is None:
            return None
        document.artifacts.append(artifact)
        reasons = ["adjacent_pages_touch_opposite_binding_edges"]
        if alignment >= 0.50:
            reasons.append("cross_seam_vertical_alignment")
        if parity_signal:
            reasons.append("printed_page_pair_consistent")
        if visual_signal:
            reasons.append("cross_seam_visual_structure")
        reasons.append("pixel_seam_continuity")

        member_ids = self._member_entity_ids(
            document,
            left_page,
            right_page,
        )
        artifact_ids = [
            artifact.artifact_id,
            *(
                source.artifact_id
                for source in document.artifacts
                if source.kind == "page_image"
                and source.page_index in {left_page.page_index, right_page.page_index}
            ),
        ]
        return SpreadIR(
            spread_id=spread_id,
            page_ids=[left_page.page_id, right_page.page_id],
            page_indices=[left_page.page_index, right_page.page_index],
            status=previous.status if previous else "candidate",
            confidence=previous.confidence if previous and previous.status != "candidate" else round(score, 4),
            reason_codes=reasons,
            composite_artifact_id=artifact.artifact_id,
            placements=self._placements(artifact, left_page, right_page),
            seam_metrics={
                "candidate_score": round(score, 4),
                "best_vertical_alignment": round(alignment, 4),
                "left_edge_object_count": float(len(left_objects)),
                "right_edge_object_count": float(len(right_objects)),
                **pixel_metrics,
            },
            member_entity_ids=member_ids,
            linked_entities=list(previous.linked_entities) if previous else [],
            quality_flags=list(previous.quality_flags) if previous else ["horizontal_spread_candidate"],
            review_task_ids=list(previous.review_task_ids) if previous else [],
            source_trace=SourceTrace(
                parser="horizontal-spread-builder",
                parser_version="v0.6",
                artifact_path=artifact.path,
                artifact_ids=list(dict.fromkeys(artifact_ids)),
                confidence=round(score, 4),
                notes=reasons,
            ),
        )

    def _seam_objects(self, layouts, page, *, side: str) -> list[_SeamObject]:
        result = []
        edge = page.width * self.EDGE_RATIO
        for item in layouts:
            bbox = item.bbox
            if bbox is None:
                continue
            width = bbox.x1 - bbox.x0
            height = bbox.y1 - bbox.y0
            if width < page.width * self.MIN_WIDTH_RATIO or height < page.height * self.MIN_HEIGHT_RATIO:
                continue
            label = str(item.label or "").casefold()
            if label in {"header", "footer", "header_image", "footer_image", "number", "page_number"}:
                continue
            touches = bbox.x1 >= page.width - edge if side == "right" else bbox.x0 <= edge
            if not touches:
                continue
            result.append(
                _SeamObject(
                    object_id=item.layout_object_id,
                    label=label,
                    x0=bbox.x0,
                    y0=bbox.y0,
                    x1=bbox.x1,
                    y1=bbox.y1,
                )
            )
        return result

    @staticmethod
    def _best_alignment(left, right):
        best = 0.0
        best_pair = (left[0], right[0])
        for first in left:
            for second in right:
                overlap = max(0.0, min(first.y1, second.y1) - max(first.y0, second.y0))
                denominator = max(1.0, min(first.height, second.height))
                ratio = overlap / denominator
                if ratio > best:
                    best = ratio
                    best_pair = (first, second)
        return best, best_pair

    @staticmethod
    def _printed_pair_consistent(left_label: str | None, right_label: str | None) -> bool:
        if not left_label or not right_label:
            return False
        left_match = re.fullmatch(r"\s*(\d+)\s*", str(left_label))
        right_match = re.fullmatch(r"\s*(\d+)\s*", str(right_label))
        if not left_match or not right_match:
            return False
        left_value = int(left_match.group(1))
        right_value = int(right_match.group(1))
        return left_value % 2 == 0 and right_value == left_value + 1

    @staticmethod
    def _visual_seam_metrics(
        left_path: str | None,
        right_path: str | None,
    ) -> dict[str, float] | None:
        if not left_path or not right_path:
            return None
        try:
            from PIL import Image  # type: ignore

            with Image.open(left_path) as source:
                left = source.convert("L")
                strip_width = max(3, int(left.width * 0.02))
                left = left.crop((left.width - strip_width, 0, left.width, left.height))
                left = left.resize((8, 256))
            with Image.open(right_path) as source:
                right = source.convert("L")
                strip_width = max(3, int(right.width * 0.02))
                right = right.crop((0, 0, strip_width, right.height))
                right = right.resize((8, 256))
        except Exception:
            return None
        left_pixels = left.load()
        right_pixels = right.load()
        left_darkness = [
            sum(255 - left_pixels[x, y] for x in range(8)) / 8
            for y in range(256)
        ]
        right_darkness = [
            sum(255 - right_pixels[x, y] for x in range(8)) / 8
            for y in range(256)
        ]
        left_active = [value > 6 for value in left_darkness]
        right_active = [value > 6 for value in right_darkness]
        both_active = sum(
            first and second
            for first, second in zip(left_active, right_active)
        )
        either_active = sum(
            first or second
            for first, second in zip(left_active, right_active)
        )
        darkness_difference = sum(
            abs(first - second)
            for first, second in zip(left_darkness, right_darkness)
        ) / (256 * 255)
        return {
            "active_row_ratio": round(both_active / 256, 4),
            "active_row_overlap": round(both_active / max(1, either_active), 4),
            "darkness_difference": round(darkness_difference, 4),
        }

    @staticmethod
    def _member_entity_ids(document, left_page, right_page) -> list[str]:
        page_by_index = {
            left_page.page_index: (left_page, "right"),
            right_page.page_index: (right_page, "left"),
        }

        def touches(entity) -> bool:
            if entity.bbox is None or entity.page_index not in page_by_index:
                return False
            page, side = page_by_index[entity.page_index]
            edge = page.width * HorizontalSpreadBuilder.EDGE_RATIO
            return (
                entity.bbox.x1 >= page.width - edge
                if side == "right"
                else entity.bbox.x0 <= edge
            )

        result = [
            left_page.page_id,
            right_page.page_id,
            *(
                item.block_id
                for item in document.blocks
                if touches(item)
            ),
            *(
                item.table_id
                for item in document.tables
                if touches(item)
            ),
            *(
                item.figure_id
                for item in document.figures
                if touches(item)
            ),
        ]
        return list(dict.fromkeys(result))

    @staticmethod
    def _composite(spread_id, left_page, right_page, output_dir: Path) -> ArtifactRef | None:
        if not left_page.page_image_path or not right_page.page_image_path:
            return None
        try:
            from PIL import Image  # type: ignore

            with Image.open(left_page.page_image_path) as left_source:
                left = left_source.convert("RGB")
            with Image.open(right_page.page_image_path) as right_source:
                right = right_source.convert("RGB")
            height = max(left.height, right.height)
            canvas = Image.new("RGB", (left.width + right.width, height), "white")
            canvas.paste(left, (0, 0))
            canvas.paste(right, (left.width, 0))
            target_dir = DocumentIrPackageLayout(output_dir).root / "artifacts" / "spreads"
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"{spread_id}.png"
            canvas.save(path, format="PNG", optimize=True)
            return ArtifactRef(
                artifact_id=f"artifact-{spread_id}",
                kind="spread_image",
                path=str(path.resolve()),
                media_type="image/png",
                page_index=left_page.page_index,
                page_indices=[left_page.page_index, right_page.page_index],
                width_pixels=canvas.width,
                height_pixels=canvas.height,
                sha256=sha256_file(path),
                source="HorizontalSpreadBuilder/Pillow",
            )
        except Exception:
            return None

    @staticmethod
    def _placements(artifact, left_page, right_page):
        try:
            from PIL import Image  # type: ignore

            page_sizes = {}
            for page in (left_page, right_page):
                with Image.open(page.page_image_path) as image:
                    page_sizes[page.page_index] = (image.width, image.height)
        except Exception:
            return []
        left_width, left_height = page_sizes[left_page.page_index]
        right_width, right_height = page_sizes[right_page.page_index]
        return [
            SpreadPagePlacement(
                page_id=left_page.page_id,
                page_index=left_page.page_index,
                side="left",
                x_offset_pixels=0,
                width_pixels=left_width,
                height_pixels=left_height,
                source_coordinate_system_id=HorizontalSpreadBuilder._render_system(left_page),
            ),
            SpreadPagePlacement(
                page_id=right_page.page_id,
                page_index=right_page.page_index,
                side="right",
                x_offset_pixels=left_width,
                width_pixels=right_width,
                height_pixels=right_height,
                source_coordinate_system_id=HorizontalSpreadBuilder._render_system(right_page),
            ),
        ]

    @staticmethod
    def _render_system(page):
        return next(
            (value for value in page.coordinate_system_ids if "-render-" in value),
            None,
        )

    @staticmethod
    def _attach(document: DocumentIR) -> None:
        next_edge = len(document.structure_edges) + 1
        pages = {page.page_id: page for page in document.pages}
        for spread in document.spreads:
            for page_id in spread.page_ids:
                page = pages.get(page_id)
                if page is None:
                    continue
                page.spread_ids.append(spread.spread_id)
                flag = {
                    "candidate": "horizontal_spread_candidate",
                    "confirmed": "horizontal_spread_confirmed",
                    "rejected": "horizontal_spread_rejected",
                    "ambiguous": "horizontal_spread_candidate",
                    "visual_continuity": "horizontal_spread_visual_continuity",
                }[spread.status]
                if flag not in page.quality_flags:
                    page.quality_flags.append(flag)
                document.structure_edges.append(
                    StructureEdge(
                        edge_id=f"edge-{next_edge:06d}",
                        source_id=page.page_id,
                        target_id=spread.spread_id,
                        relation="part_of_spread",
                        confidence=spread.confidence,
                        source="horizontal_spread_builder",
                    )
                )
                next_edge += 1
