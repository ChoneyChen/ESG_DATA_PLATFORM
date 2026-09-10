"""Model-free regressions for the report shapes that caused E1 false negatives.

The quantities/headers mirror the diagnosed rows; no real model or IR job runs.
"""
from __future__ import annotations

import json

import pytest

from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.ir.loader import LoadedDocumentIr
from esg_targeted.models.response_adapter import DirectFillResponseAdapter
from esg_targeted.retrieval.hybrid import HybridRetriever
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler

from tests.helpers import standard_dist_root


def _packets(tmp_path, label, unit, columns, *, module="e1-6", code="07", extra_rows=()):
    cells = []
    for row, (subject, row_unit, values) in enumerate([(label, unit, columns), *extra_rows]):
        for col, (text, headers) in enumerate([
            (subject, ["指标"]), (row_unit, ["单位"]),
            *[(value, header) for header, value in values],
        ]):
            cells.append({
                "cell_id": f"cell-{row}-{col}", "row_index": row, "col_index": col,
                "text": text, "column_header_path": headers,
                "row_header_path": [subject] if col > 1 else [],
            })
    ir = LoadedDocumentIr(
        root=tmp_path, manifest={"run_id": "regression", "document_id": "regression"},
        document={}, sections_by_id={}, pages=[], figures=[], artifacts=[],
        tables=[{"table_id": "table-regression", "page_index": 0, "cells": cells}],
    )
    inventory = EvidenceInventoryBuilder().build(ir)
    package = StandardPackageCatalog(standard_dist_root()).load(
        f"esrs.2023-set1.{module}", "1.0.0" if module == "e2-4" else "1.1.0"
    )
    metric = next(item for item in package.metrics if item.metric_id.endswith(f".dp{code}"))
    query = MetricQueryCompiler().compile(package, metric)
    by_id = {item.element_id: item for item in package.elements}
    selection = EvidenceSelectionCompiler(max_retrieved_objects=1).compile(
        task_id="task-regression", metric=metric,
        elements=[by_id[item] for item in package.elements_by_metric[metric.metric_id]],
        query=query, inventory=inventory, hits=HybridRetriever(inventory).search(query, 20),
        retrieval_complete=True,
    )
    return EvidenceRegionCompiler(
        max_context_chars=100000, max_output_rows=64, max_groups_per_region=12,
        max_regions=32, max_span_chars=1400,
    ).split(selection)


@pytest.mark.parametrize("label,unit,columns", [
    ("範圍1 溫室氣體排放量", "噸二氧化碳當量", [
        (["2021"], "3,491.21"), (["2022"], "1,795.56"), (["2023"], "1,932.02"),
        (["2024", "越秀地產"], "5,222.86"), (["2024", "越秀服務"], "1,105.97"),
        (["2024", "匯總"], "6,328.83"),
    ]),
    ("• 范围一GHG排放(SCOPE1)", "百万 $ tCO_{{2}}e $", [
        (["2025", "紫金"], "3.37"), (["2025", "藏格"], "0.03"),
        (["2025", "合计"], "3.40"), (["2024"], "2.89"), (["2023"], "3.65"),
        (["2022"], "3.14"), (["2021"], "2.81"), (["2020"], "2.54"),
    ]),
])
def test_scope1_cells_and_headers_reach_model_without_gross_wording(tmp_path, label, unit, columns):
    regions = _packets(tmp_path, label, unit, columns, extra_rows=[
        ("GHG排放总量(SCOPE1+2)", unit, [(["2025"], "99.99")]),
        ("Scope II", unit, [(["2025"], "55.55")]),
    ])
    targets = [cell for region in regions
               for cell in json.loads(region.model_context)["region"]["target_value_cells"]]
    assert {(cell["visible_value"], tuple(cell["column_headers"])) for cell in targets} >= {
        (value, tuple(header)) for header, value in columns
    }
    assert all(cell["unit_context"] == unit for cell in targets)
    # Numeric neighbours remain readable for model classification; no pre-model
    # semantic veto, and normal completion never forces them into output rows.
    assert len(targets) == len(columns) + 2
    assert {"99.99", "55.55"} <= {cell["visible_value"] for cell in targets}


@pytest.mark.parametrize("code", ["02", "03", "04"])
def test_ghg_quantity_does_not_become_pollutant_mass(tmp_path, code):
    regions = _packets(
        tmp_path, "範圍1 溫室氣體排放量", "噸二氧化碳當量",
        [(["2024"], "3,491.21")], module="e2-4", code=code,
    )
    assert not any(region.allowed_group_ids for region in regions)


def test_pollutant_amount_keeps_native_unit_but_not_intensity_row(tmp_path):
    regions = _packets(
        tmp_path, "COD", "吨", [(["2025"], "35.2")], module="e2-4", code="03",
        extra_rows=[("COD", "G/百万元营收", [(["2025"], "2.45")])],
    )
    targets = [cell for region in regions for cell in
               json.loads(region.model_context).get("region", {}).get("target_value_cells", [])]
    assert [cell["visible_value"] for cell in targets] == ["35.2"]


def test_header_grid_contradiction_is_flagged_without_rewriting_source():
    cells = [
        {"row_index": 0, "col_index": 1, "text": "单位"},
        {"row_index": 0, "col_index": 2, "col_span": 2, "text": "2025"},
        {"row_index": 1, "col_index": 1, "text": "紫金"},
        {"row_index": 1, "col_index": 2, "text": "藏格"},
        {"row_index": 2, "col_index": 1, "text": "GWh"},
        {"row_index": 2, "col_index": 2, "text": "326.96"},
    ]
    enriched = EvidenceInventoryBuilder._with_effective_column_headers(cells, [0, 1])
    assert all(item.get("header_alignment_uncertain") for item in enriched)
    assert not any("header_alignment_uncertain" in item for item in cells)


def test_uncertain_header_does_not_overwrite_model_visual_period(tmp_path):
    regions = _packets(tmp_path, "Scope 1 emissions", "tCO2e", [(["2024"], "3.37")])
    packet = regions[0]
    context = json.loads(packet.model_context)
    target = context["region"]["target_value_cells"][0]
    target["header_alignment_uncertain"] = True
    group_id = packet.alias_map["groups"][target["group"]]
    anchor = next(item.span_id for item in packet.spans if item.context_group_id == group_id)
    packet = packet.model_copy(update={
        "model_context": json.dumps(context), "page_image_paths": ["/fixture/complete-table.png"],
        "alias_map": {**packet.alias_map, "visual": {"V1": anchor}},
    })
    fields = {item["code"]: None for item in context["elements"]}
    amount_field = next(item["element_code"] for item in packet.elements
                        if item["binding"]["target"] == "value_raw")
    fields.update({amount_field: "3.37", "reporting_period": "2025", "reporting_entity": "紫金"})
    decision, _ = DirectFillResponseAdapter().parse(json.dumps({
        "task_id": packet.task_id, "status": "found", "uncertainty_code": "none",
        "rows": [{"target_cell": target["id"], "group": target["group"], "fields": fields}],
    }), packet=packet, visual_used=True)
    period = next(item for item in decision.fact_groups[0].assignments
                  if item.element_id.endswith(".reporting_period"))
    assert period.value_raw == "2025"
    assert period.source_mode == "visual"
    assert GroundingGuard().validate(packet, decision).accepted
