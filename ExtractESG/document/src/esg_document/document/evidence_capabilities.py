"""Reading capabilities derived from real geometry, never reconstructed cell boxes.

The logical cell grid and the physical table/cell locations are independent. A
table-level ``cell_geometry`` flag requires every cell to have a usable box;
individual cells can still be located when the table has partial coverage.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any, Mapping


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return (
        obj.get(key, default)
        if isinstance(obj, Mapping)
        else getattr(obj, key, default)
    )


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    )


def valid_page_bbox(
    bbox: Any, page: Any, coordinate_systems: Mapping[str, Any] | None = None
) -> bool:
    """Accept only finite, in-page PDF point geometry with the declared origin.

    If a coordinate-system identifier is declared, its page and its full
    definition must agree. Missing dimensions or unverifiable identifiers do
    not qualify for an image overlay. Unnamed boxes still need explicit point
    units and a top-left origin; no unit/origin conversion is guessed here.
    """
    if bbox is None or page is None:
        return False
    width, height = _value(page, "width"), _value(page, "height")
    if not all(_finite_number(v) and v > 0 for v in (width, height)):
        return False
    if _value(bbox, "unit") != "points" or _value(bbox, "origin") != "top_left":
        return False
    x0, y0, x1, y1 = (_value(bbox, key) for key in ("x0", "y0", "x1", "y1"))
    if not all(_finite_number(v) for v in (x0, y0, x1, y1)):
        return False
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        return False
    coordinate_id = _value(bbox, "coordinate_system_id")
    if coordinate_id is not None:
        page_index = _value(page, "page_index")
        page_system_ids = _value(page, "coordinate_system_ids", [])
        if (
            not isinstance(page_index, int)
            or isinstance(page_index, bool)
            or page_index < 0
        ):
            return False
        if (
            not isinstance(page_system_ids, (list, tuple))
            or not isinstance(coordinate_id, str)
            or coordinate_id not in page_system_ids
        ):
            return False
        systems = coordinate_systems
        if systems is None:
            systems = {
                _value(item, "coordinate_system_id"): item
                for item in (_value(page, "coordinate_systems", []) or [])
            }
        system = systems.get(coordinate_id)
        system_page = _value(system, "page_index")
        if (
            system is None
            or not isinstance(system_page, int)
            or isinstance(system_page, bool)
            or system_page != page_index
        ):
            return False
        if _value(system, "unit") != "points" or _value(system, "origin") != "top_left":
            return False
        sw, sh = _value(system, "width"), _value(system, "height")
        if not all(_finite_number(v) and v > 0 for v in (sw, sh)):
            return False
        if not (
            math.isclose(sw, width, rel_tol=1e-9, abs_tol=1e-6)
            and math.isclose(sh, height, rel_tol=1e-9, abs_tol=1e-6)
        ):
            return False
    return True


def table_location_capabilities(
    table: Any,
    page: Any,
    *,
    cell_grid_available: bool,
    page_image_available: bool,
    coordinate_systems: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cells = list(_value(table, "cells", []) or [])
    page_index = _value(page, "page_index")
    table_page = _value(table, "page_index")
    same_page = (
        isinstance(page_index, int)
        and not isinstance(page_index, bool)
        and page_index >= 0
        and isinstance(table_page, int)
        and not isinstance(table_page, bool)
        and table_page == page_index
    )
    table_region = bool(
        page_image_available
        and same_page
        and valid_page_bbox(_value(table, "bbox"), page, coordinate_systems)
    )
    cell_flags = [
        bool(
            cell_grid_available
            and page_image_available
            and same_page
            and isinstance(_value(cell, "page_index", table_page), int)
            and not isinstance(_value(cell, "page_index", table_page), bool)
            and _value(cell, "page_index", table_page) == table_page
            and valid_page_bbox(_value(cell, "bbox"), page, coordinate_systems)
        )
        for cell in cells
    ]
    located_count = sum(cell_flags)
    return {
        "cell_geometry": bool(cells) and located_count == len(cells),
        "table_region": table_region,
        "geometry_coverage": {
            "cell_count": len(cells),
            "located_cell_count": located_count,
        },
        "cells": [
            {"cell_geometry": flag, "table_region": table_region} for flag in cell_flags
        ],
    }
