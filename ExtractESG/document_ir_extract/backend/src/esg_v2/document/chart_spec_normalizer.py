from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from esg_v2.document.contracts import ChartSpec


class ChartSpecNormalizer:
    """Losslessly maps common model chart aliases into canonical ChartSpec."""

    _TYPE_ALIASES = {
        "bar_chart": "bar",
        "stacked_bar": "bar",
        "stacked_bar_chart": "bar",
        "column_chart": "column",
        "stacked_column": "column",
        "stacked_column_chart": "column",
        "line_chart": "line",
        "area_chart": "area",
        "pie_chart": "pie",
        "doughnut": "donut",
        "doughnut_chart": "donut",
        "donut_chart": "donut",
        "scatter_plot": "scatter",
        "bubble_chart": "bubble",
        "waterfall_chart": "waterfall",
        "radar_chart": "radar",
        "heat_map": "heatmap",
        "heatmap_chart": "heatmap",
        "mixed_chart": "mixed",
    }
    _CANONICAL_TYPES = {
        "bar", "column", "line", "area", "pie", "donut", "scatter", "bubble",
        "waterfall", "radar", "heatmap", "mixed", "other",
    }

    @classmethod
    def recognized_aliases(cls, value: Any) -> list[str]:
        if not isinstance(value, dict):
            return []
        aliases = [
            key for key in ("type", "x_categories", "y_unit", "evidence_refs")
            if key in value
        ]
        raw_type = str(value.get("chart_type") or "").strip().casefold().replace("-", "_")
        if raw_type and raw_type not in cls._CANONICAL_TYPES:
            aliases.append(f"chart_type:{raw_type}")
        categories = value.get("categories")
        if isinstance(categories, list) and any(isinstance(item, dict) for item in categories):
            aliases.append("categories:objects")
            if any(
                isinstance(item, dict)
                and any(isinstance(item.get(key), list) for key in ("points", "values", "data"))
                for item in categories
            ):
                aliases.append("categories:series_objects")
        for item in value.get("series") if isinstance(value.get("series"), list) else []:
            if not isinstance(item, dict):
                continue
            aliases.extend(
                f"series:{key}" for key in ("label", "values", "data") if key in item
            )
        return list(dict.fromkeys(aliases))

    @classmethod
    def normalize(
        cls,
        value: dict[str, Any],
        *,
        evidence_refs: Iterable[str] = (),
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            return value

        visual_refs = cls._visual_evidence_refs(
            [
                *cls._string_list(value.get("visual_evidence_refs")),
                *cls._string_list(value.get("evidence_refs")),
                *[str(item) for item in evidence_refs if item],
            ]
        )
        canonical = cls._validated(value, visual_refs)
        if canonical is not None:
            return canonical

        raw_type = str(value.get("chart_type") or value.get("type") or "other")
        chart_type = cls._chart_type(raw_type)
        category_items = (
            value.get("categories")
            if isinstance(value.get("categories"), list)
            else value.get("x_categories")
            if isinstance(value.get("x_categories"), list)
            else []
        )
        category_labels = cls._category_labels(category_items)
        series: list[dict[str, Any]] = []

        nested_category_series = cls._nested_category_series(
            category_items,
            default_unit=value.get("unit") or value.get("y_unit"),
        )
        if nested_category_series:
            category_labels = []
            series.extend(nested_category_series)
        else:
            series.extend(
                cls._category_object_series(
                    category_items,
                    name=(
                        value.get("category_series_name")
                        or value.get("categories_name")
                        or ("value" if chart_type in {"pie", "donut"} else "categories")
                    ),
                    unit=value.get("unit"),
                )
            )

        raw_series = value.get("series") if isinstance(value.get("series"), list) else []
        for index, item in enumerate(raw_series):
            if not isinstance(item, dict):
                continue
            points = cls._points(
                item.get("points")
                if isinstance(item.get("points"), list)
                else item.get("values")
                if isinstance(item.get("values"), list)
                else item.get("data"),
                cls._category_labels(item.get("categories")) or category_labels,
            )
            if not points:
                continue
            series.append(
                {
                    "name": str(
                        item.get("name")
                        or item.get("label")
                        or item.get("title")
                        or f"series-{index + 1}"
                    ),
                    "unit": cls._optional_string(
                        item.get("unit") or value.get("y_unit") or value.get("unit")
                    ),
                    "points": points,
                }
            )

        if not any(item.get("points") for item in series):
            return value

        all_categories = list(category_labels)
        for item in series:
            for point in item["points"]:
                category = str(point["category"])
                if category not in all_categories:
                    all_categories.append(category)

        normalized = {
            "chart_type": chart_type,
            "title": cls._optional_string(value.get("title") or value.get("caption")),
            "categories": all_categories,
            "series": series,
            "legend": cls._legend(value.get("legend")),
            "x_axis_label": cls._optional_string(value.get("x_axis_label") or value.get("x_label")),
            "y_axis_label": cls._optional_string(value.get("y_axis_label") or value.get("y_label")),
            "notes": cls._notes(value, raw_type, chart_type),
            "visual_evidence_refs": visual_refs,
        }
        return cls._validated(normalized, visual_refs) or value

    @classmethod
    def _validated(
        cls,
        value: dict[str, Any],
        visual_refs: list[str],
    ) -> dict[str, Any] | None:
        try:
            chart = ChartSpec.model_validate(value)
        except (TypeError, ValueError):
            return None
        if not chart.series or not any(series.points for series in chart.series):
            return None
        chart.visual_evidence_refs = list(dict.fromkeys([
            *chart.visual_evidence_refs,
            *visual_refs,
        ]))
        return chart.model_dump(mode="json")

    @classmethod
    def _chart_type(cls, value: str) -> str:
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in cls._CANONICAL_TYPES:
            return normalized
        return cls._TYPE_ALIASES.get(normalized, "other")

    @classmethod
    def _category_object_series(
        cls,
        items: list[Any],
        *,
        name: Any,
        unit: Any,
    ) -> list[dict[str, Any]]:
        value_points: list[dict[str, Any]] = []
        percentage_points: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            category = cls._category_label(item)
            if not category:
                continue
            if "value" in item or "amount" in item:
                raw_value = item.get("value") if "value" in item else item.get("amount")
                value_points.append(cls._point(category, raw_value, item.get("display_value")))
            if item.get("percentage") is not None:
                percentage_points.append(cls._point(category, item.get("percentage"), None))
        output: list[dict[str, Any]] = []
        if value_points:
            output.append({
                "name": str(name or "value"),
                "unit": cls._optional_string(unit),
                "points": value_points,
            })
        if percentage_points:
            output.append({"name": "percentage", "unit": "%", "points": percentage_points})
        return output

    @classmethod
    def _nested_category_series(
        cls,
        items: list[Any],
        *,
        default_unit: Any,
    ) -> list[dict[str, Any]]:
        """Maps model aliases that put complete series objects under categories."""

        output: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            raw_points = next(
                (
                    item[key]
                    for key in ("points", "values", "data")
                    if isinstance(item.get(key), list)
                ),
                None,
            )
            if raw_points is None:
                continue
            points = cls._points(raw_points, cls._category_labels(item.get("categories")))
            if not points:
                continue
            output.append(
                {
                    "name": str(
                        item.get("name")
                        or item.get("label")
                        or item.get("title")
                        or f"series-{index + 1}"
                    ),
                    "unit": cls._optional_string(item.get("unit") or default_unit),
                    "points": points,
                }
            )
        return output

    @classmethod
    def _points(cls, values: Any, categories: list[str]) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []
        points: list[dict[str, Any]] = []
        for index, item in enumerate(values):
            fallback_category = categories[index] if index < len(categories) else str(index + 1)
            if isinstance(item, dict):
                category = cls._category_label(item) or fallback_category
                if "value" in item:
                    raw_value = item.get("value")
                elif "amount" in item:
                    raw_value = item.get("amount")
                elif "percentage" in item:
                    raw_value = item.get("percentage")
                else:
                    continue
                point = cls._point(category, raw_value, item.get("display_value") or item.get("display"))
                point["evidence_refs"] = cls._string_list(item.get("evidence_refs"))
            else:
                point = cls._point(fallback_category, item, None)
            points.append(point)
        return points

    @staticmethod
    def _point(category: str, value: Any, display_value: Any) -> dict[str, Any]:
        return {
            "category": str(category),
            "value": value,
            "display_value": str(display_value) if display_value is not None else None,
            "evidence_refs": [],
        }

    @classmethod
    def _category_labels(cls, items: Any) -> list[str]:
        if not isinstance(items, list):
            return []
        labels: list[str] = []
        for item in items:
            label = cls._category_label(item)
            if label and label not in labels:
                labels.append(label)
        return labels

    @staticmethod
    def _category_label(item: Any) -> str:
        if isinstance(item, str):
            return item
        if not isinstance(item, dict):
            return ""
        return str(item.get("category") or item.get("label") or item.get("name") or "")

    @classmethod
    def _legend(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return cls._string_list(value)
        output: list[str] = []
        for item in value:
            if isinstance(item, dict):
                label = cls._category_label(item)
                if label:
                    output.append(label)
            elif item is not None:
                output.append(str(item))
        return list(dict.fromkeys(output))

    @classmethod
    def _notes(cls, value: dict[str, Any], raw_type: str, chart_type: str) -> list[str]:
        notes = cls._string_list(value.get("notes"))
        normalized_type = raw_type.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized_type != chart_type:
            notes.append(f"source_chart_type={raw_type}")
        if "stacked" in normalized_type:
            notes.append("layout=stacked")
        for key in ("total_value", "y_min", "y_max"):
            if value.get(key) is not None:
                notes.append(f"{key}={json.dumps(value[key], ensure_ascii=False)}")
        return list(dict.fromkeys(notes))

    @staticmethod
    def _visual_evidence_refs(values: Iterable[str]) -> list[str]:
        output: list[str] = []
        for raw in values:
            value = str(raw or "").strip()
            lowered = value.casefold().split("?", 1)[0]
            if not value:
                continue
            if (
                lowered.startswith("data:image/")
                or lowered.endswith((".png", ".jpg", ".jpeg", ".webp"))
                or "artifacts/crops/" in lowered
                or "artifacts/page-images/" in lowered
            ) and value not in output:
                output.append(value)
        return output

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if item is not None and str(item)]
        return [str(value)]

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return str(value) if value is not None and str(value) else None
