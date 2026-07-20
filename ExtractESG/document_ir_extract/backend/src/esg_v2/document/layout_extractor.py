from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from esg_v2.document.artifact_loader import OcrPageArtifact
from esg_v2.document.contracts import (
    BoundingBox,
    CoordinateSystem,
    LayoutObjectIR,
    Polygon,
    SourceTrace,
)
from esg_v2.document.coordinate_mapper import CoordinateMapper
from esg_v2.document.provenance import sanitize_ir_payload


@dataclass(frozen=True)
class LayoutExtractionResult:
    objects: list[LayoutObjectIR]
    parser_coordinate_system: CoordinateSystem | None
    raw_container_path: str | None
    quality_flags: list[str]


class PaddleLayoutExtractor:
    """Version-tolerant reader for PaddleOCR-VL's structured page result."""

    _LIST_KEYS = ("parsing_res_list", "parsingResList", "layout_res", "layout", "blocks")

    def extract(
        self,
        page: OcrPageArtifact,
        *,
        canonical_system: CoordinateSystem | None,
        source_trace: SourceTrace,
    ) -> LayoutExtractionResult:
        container, container_path = self._find_container(page.raw_result)
        if not container:
            return LayoutExtractionResult([], None, container_path, ["paddle_layout_objects_missing"])

        width = self._number(page.raw_result, ("width", "image_width", "img_width"))
        height = self._number(page.raw_result, ("height", "image_height", "img_height"))
        if (not width or not height) and canonical_system:
            width, height = canonical_system.width, canonical_system.height

        parser_system = None
        if width and height and canonical_system:
            parser_system = CoordinateMapper.parser_system(page.page_index, width, height, canonical_system)
        elif width and height:
            parser_system = CoordinateSystem(
                coordinate_system_id=f"page-{page.page_index + 1:04d}-paddle-input",
                page_index=page.page_index,
                name="paddle_input_pixels",
                width=width,
                height=height,
                unit="pixels",
                origin="top_left",
            )

        objects: list[LayoutObjectIR] = []
        flags: list[str] = []
        for fallback_order, raw in enumerate(container):
            if not isinstance(raw, dict):
                flags.append("non_object_layout_item_preserved_in_raw_result")
                continue
            order = self._int(raw, ("block_order", "blockOrder", "order"), fallback_order)
            raw_id = raw.get("block_id", raw.get("blockId", fallback_order))
            label = str(raw.get("block_label") or raw.get("blockLabel") or raw.get("label") or raw.get("type") or "unknown")
            text = self._text(raw)
            bbox, polygon = self._geometry(raw, parser_system, canonical_system)
            confidence = self._number(raw, ("confidence", "score", "block_score"))
            trace = source_trace.model_copy(deep=True)
            trace.raw_result_index = page.raw_result_index
            trace.raw_object_path = f"{container_path or 'raw'}.{fallback_order}"
            trace.confidence = confidence
            object_flags: list[str] = []
            if bbox is None and polygon is None:
                object_flags.append("layout_geometry_missing")
            sanitized_raw = sanitize_ir_payload(raw)
            if sanitized_raw != raw:
                object_flags.append("sensitive_remote_url_redacted")
                trace.notes.append("Exact unsanitized payload remains in artifact-ocr-raw.")
            objects.append(
                LayoutObjectIR(
                    layout_object_id=f"layout-{page.page_index + 1:04d}-{fallback_order + 1:04d}-{raw_id}",
                    page_index=page.page_index,
                    order=order,
                    label=label,
                    text=text,
                    bbox=bbox,
                    polygon=polygon,
                    raw_payload=sanitized_raw,
                    quality_flags=object_flags,
                    source_trace=trace,
                )
            )
        objects.sort(key=lambda item: (item.order, item.layout_object_id))
        if any(item.bbox is None and item.polygon is None for item in objects):
            flags.append("partial_layout_geometry")
        return LayoutExtractionResult(objects, parser_system, container_path, flags)

    def _find_container(self, payload: dict[str, Any]) -> tuple[list[Any], str | None]:
        queue: list[tuple[Any, str]] = [(payload, "raw_result")]
        seen: set[int] = set()
        while queue:
            current, path = queue.pop(0)
            if id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, dict):
                for key in self._LIST_KEYS:
                    value = current.get(key)
                    if isinstance(value, list):
                        return value, f"{path}.{key}"
                for key in ("prunedResult", "pruned_result", "result", "layoutParsingResult"):
                    value = current.get(key)
                    if isinstance(value, (dict, list)):
                        queue.append((value, f"{path}.{key}"))
            elif isinstance(current, list):
                for index, value in enumerate(current):
                    if isinstance(value, (dict, list)):
                        queue.append((value, f"{path}.{index}"))
        return [], None

    @classmethod
    def _number(cls, payload: Any, keys: tuple[str, ...]) -> float | None:
        queue = [payload]
        seen: set[int] = set()
        while queue:
            current = queue.pop(0)
            if id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, dict):
                for key in keys:
                    value = current.get(key)
                    if isinstance(value, (int, float)):
                        return float(value)
                for key in ("prunedResult", "pruned_result", "dataInfo", "inputImage", "doc_preprocessor_res"):
                    value = current.get(key)
                    if isinstance(value, (dict, list)):
                        queue.append(value)
            elif isinstance(current, list):
                queue.extend(value for value in current if isinstance(value, (dict, list)))
        return None

    @staticmethod
    def _int(payload: dict[str, Any], keys: tuple[str, ...], default: int) -> int:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return default

    @staticmethod
    def _text(raw: dict[str, Any]) -> str:
        for key in ("block_content", "blockContent", "content", "text", "markdown"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
        return ""

    @staticmethod
    def _geometry(
        raw: dict[str, Any],
        parser_system: CoordinateSystem | None,
        canonical_system: CoordinateSystem | None,
    ) -> tuple[BoundingBox | None, Polygon | None]:
        value = None
        for key in ("block_bbox", "blockBbox", "bbox", "box", "coordinate"):
            if key in raw:
                value = raw[key]
                break
        if isinstance(value, dict):
            value = [value.get("x0", value.get("left")), value.get("y0", value.get("top")), value.get("x1", value.get("right")), value.get("y1", value.get("bottom"))]
        if isinstance(value, list) and len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
            bbox = BoundingBox(
                x0=float(value[0]), y0=float(value[1]), x1=float(value[2]), y1=float(value[3]),
                unit="pixels" if parser_system else "unknown",
                origin="top_left",
                coordinate_system_id=parser_system.coordinate_system_id if parser_system else None,
            )
            if parser_system and canonical_system:
                bbox = CoordinateMapper.to_canonical_bbox(bbox, parser_system, canonical_system)
            return bbox, None
        points_value = raw.get("polygon") or raw.get("poly") or raw.get("points")
        if not points_value and isinstance(value, list) and len(value) >= 8:
            points_value = value
        points: list[tuple[float, float]] = []
        if isinstance(points_value, list):
            if points_value and all(isinstance(item, (int, float)) for item in points_value):
                points = [(float(points_value[i]), float(points_value[i + 1])) for i in range(0, len(points_value) - 1, 2)]
            elif points_value and all(isinstance(item, list) and len(item) >= 2 for item in points_value):
                points = [(float(item[0]), float(item[1])) for item in points_value]
        if points:
            polygon = Polygon(
                points=points,
                unit="pixels" if parser_system else "unknown",
                origin="top_left",
                coordinate_system_id=parser_system.coordinate_system_id if parser_system else None,
            )
            if parser_system and canonical_system:
                polygon = CoordinateMapper.to_canonical_polygon(polygon, parser_system, canonical_system)
            xs = [point[0] for point in polygon.points]
            ys = [point[1] for point in polygon.points]
            bbox = BoundingBox(
                x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys),
                unit=polygon.unit, origin=polygon.origin, coordinate_system_id=polygon.coordinate_system_id,
            )
            return bbox, polygon
        return None, None
