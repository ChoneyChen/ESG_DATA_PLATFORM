from __future__ import annotations

from esg_v2.document.contracts import BoundingBox, CoordinateSystem, Polygon


class CoordinateMapper:
    """Maps parser/image coordinates into canonical top-left PDF points."""

    @staticmethod
    def canonical_system(page_index: int, width_points: float, height_points: float) -> CoordinateSystem:
        return CoordinateSystem(
            coordinate_system_id=f"page-{page_index + 1:04d}-pdf-points",
            page_index=page_index,
            name="canonical_pdf_points",
            width=width_points,
            height=height_points,
            unit="points",
            origin="top_left",
        )

    @staticmethod
    def image_system(
        page_index: int,
        width_pixels: int,
        height_pixels: int,
        dpi: float,
        canonical_id: str,
    ) -> CoordinateSystem:
        return CoordinateSystem(
            coordinate_system_id=f"page-{page_index + 1:04d}-render-{int(dpi)}dpi",
            page_index=page_index,
            name="rendered_page_pixels",
            width=width_pixels,
            height=height_pixels,
            unit="pixels",
            origin="top_left",
            dpi=dpi,
            maps_to=canonical_id,
            scale_x=72.0 / dpi,
            scale_y=72.0 / dpi,
        )

    @staticmethod
    def parser_system(
        page_index: int,
        width: float,
        height: float,
        canonical: CoordinateSystem,
    ) -> CoordinateSystem:
        return CoordinateSystem(
            coordinate_system_id=f"page-{page_index + 1:04d}-paddle-input",
            page_index=page_index,
            name="paddle_input_pixels",
            width=width,
            height=height,
            unit="pixels",
            origin="top_left",
            maps_to=canonical.coordinate_system_id,
            scale_x=canonical.width / width if width else None,
            scale_y=canonical.height / height if height else None,
        )

    @staticmethod
    def to_canonical_bbox(
        bbox: BoundingBox,
        source: CoordinateSystem,
        canonical: CoordinateSystem,
    ) -> BoundingBox:
        if source.width <= 0 or source.height <= 0:
            return bbox
        x_scale = canonical.width / source.width
        y_scale = canonical.height / source.height
        y0, y1 = bbox.y0, bbox.y1
        if source.origin == "bottom_left":
            y0, y1 = source.height - bbox.y1, source.height - bbox.y0
        return BoundingBox(
            x0=max(0.0, min(canonical.width, bbox.x0 * x_scale)),
            y0=max(0.0, min(canonical.height, y0 * y_scale)),
            x1=max(0.0, min(canonical.width, bbox.x1 * x_scale)),
            y1=max(0.0, min(canonical.height, y1 * y_scale)),
            unit="points",
            origin="top_left",
            coordinate_system_id=canonical.coordinate_system_id,
        )

    @staticmethod
    def to_canonical_polygon(
        polygon: Polygon,
        source: CoordinateSystem,
        canonical: CoordinateSystem,
    ) -> Polygon:
        if source.width <= 0 or source.height <= 0:
            return polygon
        x_scale = canonical.width / source.width
        y_scale = canonical.height / source.height
        points = []
        for x, y in polygon.points:
            mapped_y = source.height - y if source.origin == "bottom_left" else y
            points.append((x * x_scale, mapped_y * y_scale))
        return Polygon(
            points=points,
            unit="points",
            origin="top_left",
            coordinate_system_id=canonical.coordinate_system_id,
        )

    @staticmethod
    def canonical_to_pixels(
        bbox: BoundingBox,
        canonical: CoordinateSystem,
        image: CoordinateSystem,
    ) -> tuple[int, int, int, int]:
        x_scale = image.width / canonical.width
        y_scale = image.height / canonical.height
        left = int(max(0, min(image.width, bbox.x0 * x_scale)))
        top = int(max(0, min(image.height, bbox.y0 * y_scale)))
        right = int(max(left + 1, min(image.width, bbox.x1 * x_scale)))
        bottom = int(max(top + 1, min(image.height, bbox.y1 * y_scale)))
        return left, top, right, bottom
