from __future__ import annotations

import re
from pathlib import Path

from esg_v2.document.contracts import BoundingBox, CoordinateSystem
from esg_v2.document.coordinate_mapper import CoordinateMapper


_IMAGE_BOX_PATTERN = re.compile(
    r"_box_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)(?:\.[^.]+)?$",
    re.IGNORECASE,
)


def bbox_iou(first: BoundingBox | None, second: BoundingBox | None) -> float:
    if first is None or second is None:
        return 0.0
    left = max(first.x0, second.x0)
    top = max(first.y0, second.y0)
    right = min(first.x1, second.x1)
    bottom = min(first.y1, second.y1)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    if intersection <= 0:
        return 0.0
    first_area = max(0.0, first.x1 - first.x0) * max(0.0, first.y1 - first.y0)
    second_area = max(0.0, second.x1 - second.x0) * max(0.0, second.y1 - second.y0)
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def bbox_containment(inner: BoundingBox | None, outer: BoundingBox | None) -> float:
    if inner is None or outer is None:
        return 0.0
    left = max(inner.x0, outer.x0)
    top = max(inner.y0, outer.y0)
    right = min(inner.x1, outer.x1)
    bottom = min(inner.y1, outer.y1)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    inner_area = max(0.0, inner.x1 - inner.x0) * max(0.0, inner.y1 - inner.y0)
    return intersection / inner_area if inner_area > 0 else 0.0


def bbox_area_ratio(bbox: BoundingBox | None, *, page_width: float | None, page_height: float | None) -> float:
    if bbox is None or not page_width or not page_height:
        return 0.0
    area = max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)
    return area / (page_width * page_height)


def normalize_bbox_to_page(
    bbox: BoundingBox,
    *,
    page_width: float | None,
    page_height: float | None,
    reject_overflow_ratio: float = 0.02,
) -> tuple[BoundingBox | None, str]:
    """Validate a box against its page, clipping only small parser drift.

    A materially out-of-page box is evidence that the parser found the wrong
    region, not a coordinate rounding problem. Returning it as rejected keeps a
    false table candidate from becoming canonical merely because it can be
    clipped into a legal rectangle.
    """
    if not page_width or not page_height or page_width <= 0 or page_height <= 0:
        return bbox, "unchanged"
    if bbox.x0 >= bbox.x1 or bbox.y0 >= bbox.y1:
        return None, "rejected_degenerate"

    overflow_ratio = max(
        max(0.0, -bbox.x0) / page_width,
        max(0.0, bbox.x1 - page_width) / page_width,
        max(0.0, -bbox.y0) / page_height,
        max(0.0, bbox.y1 - page_height) / page_height,
    )
    if overflow_ratio > reject_overflow_ratio:
        return None, "rejected_out_of_bounds"

    clipped = BoundingBox(
        x0=max(0.0, min(page_width, bbox.x0)),
        y0=max(0.0, min(page_height, bbox.y0)),
        x1=max(0.0, min(page_width, bbox.x1)),
        y1=max(0.0, min(page_height, bbox.y1)),
        unit=bbox.unit,
        origin=bbox.origin,
        coordinate_system_id=bbox.coordinate_system_id,
    )
    if clipped.x0 >= clipped.x1 or clipped.y0 >= clipped.y1:
        return None, "rejected_degenerate"
    if clipped == bbox:
        return clipped, "unchanged"
    return clipped, "clipped_to_page"


def union_bboxes(boxes: list[BoundingBox]) -> BoundingBox | None:
    values = [box for box in boxes if box is not None]
    if not values:
        return None
    first = values[0]
    if any(
        box.unit != first.unit
        or box.origin != first.origin
        or box.coordinate_system_id != first.coordinate_system_id
        for box in values[1:]
    ):
        return None
    return BoundingBox(
        x0=min(box.x0 for box in values),
        y0=min(box.y0 for box in values),
        x1=max(box.x1 for box in values),
        y1=max(box.y1 for box in values),
        unit=first.unit,
        origin=first.origin,
        coordinate_system_id=first.coordinate_system_id,
    )


def image_bbox_from_path(path: str, *, coordinate_system_id: str | None = None) -> BoundingBox | None:
    match = _IMAGE_BOX_PATTERN.search(Path(path).name)
    if not match:
        return None
    x0, y0, x1, y1 = (float(value) for value in match.groups())
    return BoundingBox(
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        unit="pixels",
        origin="top_left",
        coordinate_system_id=coordinate_system_id,
    )


def image_bbox_to_canonical(
    path: str,
    parser_system: CoordinateSystem | None,
    canonical_system: CoordinateSystem | None,
) -> BoundingBox | None:
    bbox = image_bbox_from_path(
        path,
        coordinate_system_id=parser_system.coordinate_system_id if parser_system else None,
    )
    if bbox and parser_system and canonical_system:
        return CoordinateMapper.to_canonical_bbox(bbox, parser_system, canonical_system)
    return bbox
