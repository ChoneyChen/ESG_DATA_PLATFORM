from __future__ import annotations

import json
from pathlib import Path


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_ir_fixture(
    root: Path,
    *,
    ready: bool = True,
    pollutant_rows: list[tuple[str, str, str, str]] | None = None,
    value_columns: list[tuple[str, str]] | None = None,
) -> Path:
    rows = pollutant_rows or [("氮氧化物", "125.6吨", "2024年", "中国境内运营")]
    page_text = "；".join(
        f"{year}，公司空气污染物{pollutant}排放量为{amount}；统计范围为{boundary}"
        for pollutant, amount, year, boundary in rows
    ) + "。"
    run_root = root / "ir-fixture-e2-4"
    write_json(
        run_root / "manifest.json",
        {
            "schema_version": "document-ir-v0.12",
            "run_id": "ir-fixture-e2-4",
            "document_id": "doc-fixture-esg-2024",
            "document_label": "Fixture ESG Report 2024",
            "ir_revision": 1,
            "readiness": "ready" if ready else "repair_required",
            "can_build_evidence": ready,
            "page_count": 1,
            "entrypoints": {
                "canonical_document": "canonical/document.json",
                "pages": "indexes/pages.json",
                "tables": "indexes/tables.json",
            },
        },
    )
    write_json(
        run_root / "canonical/document.json",
        {
            "metadata": {"document_id": "doc-fixture-esg-2024"},
            "sections": [{"section_id": "section-pollution", "title": "污染物排放"}],
        },
    )
    write_json(
        run_root / "indexes/pages.json",
        {"pages": [{"path": "canonical/pages/page-0001.json"}]},
    )
    write_json(
        run_root / "canonical/pages/page-0001.json",
        {
            "page": {
                "page_id": "page-0001",
                "page_index": 0,
                "printed_page_label": "16",
                "text": page_text,
            },
            "blocks": [
                {
                    "block_id": "block-0001",
                    "section_id": "section-pollution",
                    "text": page_text,
                    "bbox": {"x0": 10, "y0": 20, "x1": 500, "y1": 80},
                }
            ],
        },
    )
    write_json(
        run_root / "indexes/tables.json",
        {"tables": [{"path": "canonical/tables/table-0001.json"}]},
    )
    cells = []
    for row_index, (pollutant, amount, year, boundary) in enumerate(rows):
        suffix = "" if row_index == 0 else f"-{row_index + 1}"
        row_cells = (
            [
                (f"cell-pollutant{suffix}", "污染物", pollutant),
                (f"cell-unit{suffix}", "单位", "吨"),
                *[
                    (f"cell-value-{index}{suffix}", header, value)
                    for index, (header, value) in enumerate(value_columns, 1)
                ],
            ]
            if value_columns
            else [
                (f"cell-pollutant{suffix}", "污染物", pollutant),
                (f"cell-value{suffix}", "排放量", amount),
                (f"cell-year{suffix}", "报告期", year),
                (f"cell-boundary{suffix}", "统计范围", boundary),
            ]
        )
        for column_index, (cell_id, header, text) in enumerate(row_cells):
            y0 = 100 + row_index * 35
            cells.append(
                {
                    "cell_id": cell_id,
                    "row_index": row_index,
                    "col_index": column_index,
                    "page_index": 0,
                    "text": text,
                    "column_header_path": [header],
                    "row_header_path": [] if column_index == 0 else [pollutant],
                    "bbox": {
                        "x0": column_index * 100,
                        "y0": y0,
                        "x1": (column_index + 1) * 100,
                        "y1": y0 + 30,
                    },
                }
            )
    write_json(
        run_root / "canonical/tables/table-0001.json",
        {
            "table_id": "table-0001",
            "caption": "空气污染物排放",
            "page_index": 0,
            "cells": cells,
        },
    )
    crop_path = run_root / "artifacts/crops/tables/table-0001.png"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop_path.write_bytes(b"fixture-table-crop")
    write_json(
        run_root / "artifacts/index.json",
        {
            "artifacts": [
                {
                    "artifact_id": "artifact-crop-table-0001",
                    "kind": "region_crop",
                    "path": "artifacts/crops/tables/table-0001.png",
                    "page_index": 0,
                }
            ]
        },
    )
    return run_root


def standard_dist_root() -> Path:
    return Path(__file__).resolve().parents[3] / "standard_packages" / "dist"
