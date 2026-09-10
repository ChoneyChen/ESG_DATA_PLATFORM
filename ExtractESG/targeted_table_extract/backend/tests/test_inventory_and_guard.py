from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.evidence.visual_inputs import FocusedVisualInputBuilder
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.ir.loader import DocumentIrLoader
from esg_targeted.models.response_adapter import (
    DirectFillResponseAdapter,
    ModelResponseError,
)
from esg_targeted.results.materializer import CoreResultMaterializer
from esg_targeted.retrieval.hybrid import HybridRetriever
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler

from tests.helpers import build_ir_fixture, standard_dist_root


def build_pipeline(
    tmp_path,
    *,
    rows=None,
    value_columns=None,
    retrieval_object_top_n=3,
):
    loaded = DocumentIrLoader().load(
        build_ir_fixture(
            tmp_path,
            pollutant_rows=rows,
            value_columns=value_columns,
        )
    )
    inventory = EvidenceInventoryBuilder().build(loaded)
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E2-4_02")
    query = MetricQueryCompiler().compile(package, metric)
    hits = HybridRetriever(inventory).search(query, 20)
    elements_by_id = {item.element_id: item for item in package.elements}
    elements = [
        elements_by_id[item] for item in package.elements_by_metric[metric.metric_id]
    ]
    selection = EvidenceSelectionCompiler(
        max_retrieved_objects=retrieval_object_top_n
    ).compile(
        task_id="task-fixture",
        metric=metric,
        elements=elements,
        query=query,
        inventory=inventory,
        hits=hits,
        retrieval_complete=True,
    )
    regions = EvidenceRegionCompiler(
        max_context_chars=16_000,
        max_groups_per_region=8,
        max_regions=32,
        max_output_rows=8,
        max_span_chars=900,
        max_images_per_region=2,
    ).split(selection)
    return inventory, selection, regions, package, metric


def alias_for_text(packet, text: str, *, candidate: bool = False) -> str:
    if candidate:
        by_id = {item.candidate_id: item for item in packet.candidates}
        return next(
            alias
            for alias, item_id in packet.alias_map["candidates"].items()
            if by_id[item_id].raw_value == text
        )
    by_id = {item.span_id: item for item in packet.spans}
    return next(
        alias
        for alias, item_id in packet.alias_map["spans"].items()
        if by_id[item_id].text == text
    )


def complete_row(packet, pollutant, amount, year, boundary):
    group_alias = next(iter(packet.alias_map["groups"]))
    return {
        "group": group_alias,
        "fields": {
            "additional_breakdown": None,
            "consolidation_scope": None,
            "emission_amount": amount,
            "mass_unit": "吨",
            "pollutant": pollutant,
            "reporting_boundary": boundary,
            "reporting_period": year,
            "threshold_basis": None,
        },
    }


def without_target_cells(packet):
    """Build a visual-only packet without a canonical OCR value-cell worklist."""

    context = json.loads(packet.model_context)
    context["region"]["target_value_cells"] = []
    return packet.model_copy(
        update={
            "model_context": json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
        deep=True,
    )


def test_inventory_is_raw_document_ir_not_preassembled_facts(tmp_path) -> None:
    inventory, selection, regions, _, _ = build_pipeline(tmp_path)
    object_types = {
        item.structural_context.get("object_type") for item in inventory.spans
    }
    assert "table_observation" not in object_types
    assert {"table_row", "table_cell", "table_header_context"} <= object_types
    assert selection.page_image_paths
    assert regions[0].budget["semantic_mode"] == "direct_multi_row"
    context = json.loads(regions[0].model_context)
    assert context["decision_scope"]["one_quantity_per_row"] is True
    assert context["visual_sources"]


def test_selection_keeps_all_rows_of_a_retrieved_table(tmp_path) -> None:
    rows = [
        ("氮氧化物", "125.6吨", "2024年", "中国境内运营"),
        ("二氧化硫", "64.2吨", "2024年", "中国境内运营"),
    ]
    _, selection, regions, _, _ = build_pipeline(tmp_path, rows=rows)
    table_groups = [
        item for item in selection.allowed_group_ids if item.startswith("table-row:")
    ]
    assert len(table_groups) == 2
    assert {item.text for item in selection.spans} >= {"氮氧化物", "二氧化硫"}
    assert sum(len(item.allowed_group_ids) for item in regions) >= 2


def test_inventory_derives_complete_two_level_column_headers_without_mutating_ir() -> None:
    cells = [
        {"cell_id": "year", "row_index": 0, "col_index": 5, "col_span": 3, "text": "2024"},
        {"cell_id": "a", "row_index": 1, "col_index": 5, "text": "越秀地產"},
        {"cell_id": "b", "row_index": 1, "col_index": 6, "text": "越秀服務 1"},
        {"cell_id": "c", "row_index": 1, "col_index": 7, "text": "匯總"},
        {"cell_id": "v1", "row_index": 2, "col_index": 5, "text": "0.85", "column_header_path": ["2024"]},
        {"cell_id": "v2", "row_index": 2, "col_index": 6, "text": "0.18", "column_header_path": ["2024"]},
        {"cell_id": "v3", "row_index": 2, "col_index": 7, "text": "1.03", "column_header_path": ["2024"]},
    ]

    enriched = EvidenceInventoryBuilder._with_effective_column_headers(cells, [0])
    by_id = {item["cell_id"]: item for item in enriched}

    assert by_id["v1"]["effective_column_header_path"] == ["2024", "越秀地產"]
    assert by_id["v2"]["effective_column_header_path"] == ["2024", "越秀服務 1"]
    assert by_id["v3"]["effective_column_header_path"] == ["2024", "匯總"]
    assert "effective_column_header_path" not in cells[-1]


def test_task_level_top_n_limits_independent_objects_not_table_rows(tmp_path) -> None:
    rows = [
        ("氮氧化物", "125.6吨", "2024年", "中国境内运营"),
        ("二氧化硫", "64.2吨", "2024年", "中国境内运营"),
    ]
    _, selection, _, _, _ = build_pipeline(
        tmp_path,
        rows=rows,
        retrieval_object_top_n=1,
    )

    assert selection.budget["retrieval_object_limit"] == 1
    assert selection.budget["selected_retrieval_object_count"] == 1
    assert len(selection.budget["selected_retrieval_objects"]) == 1
    selected_kind = selection.budget["selected_retrieval_objects"][0]["kind"]
    if selected_kind == "table":
        assert len(
            [group for group in selection.allowed_group_ids if group.startswith("table-row:")]
        ) == 2


def test_regions_never_mix_visual_objects_or_multiple_images(tmp_path) -> None:
    _, selection, regions, _, _ = build_pipeline(tmp_path)

    assert selection.budget["selected_retrieval_object_count"] >= 1
    assert all(len(region.page_image_paths) <= 1 for region in regions)
    assert all(region.budget["region_image_count"] <= 1 for region in regions)
    assert all(region.budget["region"]["object_kind"] in {"table", "figure", "page"} for region in regions)


def test_table_region_uses_canonical_cells_without_repeated_row_context(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(
        tmp_path,
        rows=[
            ("氮氧化物", "125.6吨", "2024年", "中国境内运营"),
            ("二氧化硫", "64.2吨", "2024年", "中国境内运营"),
        ],
    )
    table_regions = [item for item in regions if item.budget["region"]["kind"] == "table"]

    assert table_regions
    for region in table_regions:
        context = json.loads(region.model_context)
        assert len(region.model_context) < 10_000
        assert all(item["type"] == "table_cell" for item in context["evidence"])
        assert all(item["context"] is None for item in context["evidence"])


def test_table_region_preserves_multiple_value_columns_for_one_model_fill(tmp_path) -> None:
    ir_root = build_ir_fixture(
        tmp_path,
        value_columns=[("2023", "1.21"), ("2024", "1.04")],
    )
    crop_path = ir_root / "artifacts/crops/tables/table-0001.png"
    Image.new("RGB", (800, 220), "white").save(crop_path)
    loaded = DocumentIrLoader().load(ir_root)
    inventory = EvidenceInventoryBuilder().build(loaded)
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    metric = next(
        item for item in package.metrics if item.source_datapoint_id == "E2-4_02"
    )
    query = MetricQueryCompiler().compile(package, metric)
    elements_by_id = {item.element_id: item for item in package.elements}
    selection = EvidenceSelectionCompiler(max_retrieved_objects=3).compile(
        task_id="task-multi-column",
        metric=metric,
        elements=[
            elements_by_id[item]
            for item in package.elements_by_metric[metric.metric_id]
        ],
        query=query,
        inventory=inventory,
        hits=HybridRetriever(inventory).search(query, 20),
        retrieval_complete=True,
    )
    regions = EvidenceRegionCompiler(
        max_context_chars=16_000,
        max_groups_per_region=8,
        max_regions=32,
        max_output_rows=8,
        max_span_chars=900,
        max_images_per_region=1,
    ).split(selection)

    table_regions = [item for item in regions if item.budget["region"]["kind"] == "table"]
    assert len(table_regions) == 1
    context = json.loads(table_regions[0].model_context)
    target_cells = context["region"]["target_value_cells"]
    assert {cell["visible_value"] for cell in target_cells} == {"1.21", "1.04"}
    assert {cell["id"] for cell in target_cells} == {"T1", "T2"}
    assert context["region"]["target_value_columns"] == [2, 3]
    assert context["region"]["visual_policy"] == "full_object"
    evidence_values = {item["text"] for item in context["evidence"]}
    assert {"1.21", "1.04"} <= evidence_values

    focused = FocusedVisualInputBuilder(tmp_path / "model-inputs").build_all(
        table_regions
    )
    statuses = [item.budget["focused_visual"] for item in focused]
    assert all(item["status"] == "preserved_full_object" for item in statuses), statuses
    assert all(len(item.page_image_paths) == 1 for item in focused)
    assert all(Path(item.page_image_paths[0]).is_file() for item in focused)
    with Image.open(focused[0].page_image_paths[0]) as rendered:
        assert rendered.width == 800
        assert rendered.height == 220


def test_two_value_cells_in_one_physical_row_remain_two_grounded_facts(tmp_path) -> None:
    ir_root = build_ir_fixture(
        tmp_path,
        value_columns=[("2023", "1.21"), ("2024", "1.04")],
    )
    loaded = DocumentIrLoader().load(ir_root)
    inventory = EvidenceInventoryBuilder().build(loaded)
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E2-4_02")
    query = MetricQueryCompiler().compile(package, metric)
    elements_by_id = {item.element_id: item for item in package.elements}
    selection = EvidenceSelectionCompiler(max_retrieved_objects=3).compile(
        task_id="task-two-cells",
        metric=metric,
        elements=[elements_by_id[item] for item in package.elements_by_metric[metric.metric_id]],
        query=query,
        inventory=inventory,
        hits=HybridRetriever(inventory).search(query, 20),
        retrieval_complete=True,
    )
    packets = EvidenceRegionCompiler(
        max_context_chars=16_000,
        max_groups_per_region=8,
        max_regions=32,
        max_output_rows=8,
        max_span_chars=900,
    ).split(selection)
    packet = next(
        item for item in packets if item.budget["region"]["kind"] == "table"
    )
    context = json.loads(packet.model_context)
    rows = []
    for target in context["region"]["target_value_cells"]:
        fields = {item["code"]: None for item in context["elements"]}
        fields["emission_amount"] = target["visible_value"]
        rows.append(
            {
                "target_cell": target["id"],
                "group": target["group"],
                "fields": fields,
            }
        )
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "found",
                "rows": rows,
                "uncertainty_code": "none",
            },
            ensure_ascii=False,
        ),
        packet=packet,
    )
    guard = GroundingGuard().validate(packet, decision)

    assert len(decision.fact_groups) == 2
    assert len({item.group_ref_id for item in decision.fact_groups}) == 2
    assert guard.accepted, guard.feedback


def test_model_directly_fills_and_materializes_one_complete_row(tmp_path) -> None:
    _, selection, regions, package, metric = build_pipeline(tmp_path)
    packet = regions[0]
    payload = {
        "task_id": packet.task_id,
        "status": "found",
        "rows": [
            complete_row(
                packet,
                "氮氧化物",
                "125.6吨",
                "2024",
                "中国境内运营",
            )
        ],
        "uncertainty_code": "none",
    }
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(payload, ensure_ascii=False), packet=packet
    )
    guard = GroundingGuard().validate(packet, decision)
    assert guard.accepted, guard.feedback
    result = CoreResultMaterializer().materialize(
        package=package,
        metric=metric,
        packet=selection,
        decision=decision,
        guard=guard,
        attempts=1,
    )
    assert result.quantitative_observations[0]["value_numeric"] == "125.6"
    assert result.quantitative_observations[0]["unit_raw"] == "吨"
    assert result.quantitative_observations[0]["reporting_year"] == 2024
    pollutant = next(
        item
        for item in result.dimension_values
        if item["element_id"].endswith("element.pollutant")
    )
    assert pollutant["value_raw"] == "氮氧化物"


def test_guard_accepts_visual_value_not_preharvested_by_regex(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = without_target_cells(regions[0])
    group_alias = next(iter(packet.alias_map["groups"]))
    fields = {
        item["code"]: None for item in json.loads(packet.model_context)["elements"]
    }
    for name, value in {
        "emission_amount": "777.7",
        "mass_unit": "吨",
        "pollutant": "二氧化硫",
        "reporting_boundary": "中国境内运营",
        "reporting_period": "2024",
    }.items():
        fields[name] = value
    payload = {
        "task_id": packet.task_id,
        "status": "found",
        "rows": [{"group": group_alias, "fields": fields}],
        "uncertainty_code": "none",
    }
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(payload, ensure_ascii=False), packet=packet, visual_used=True
    )
    amount_assignment = next(
        item
        for item in decision.fact_groups[0].assignments
        if item.element_id.endswith("element.emission_amount")
    )
    assert amount_assignment.source_mode == "visual"
    guard = GroundingGuard().validate(packet, decision)
    assert guard.accepted, guard.feedback


def test_complete_table_visual_can_emit_unlisted_value_in_existing_row(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    group_alias = next(iter(packet.alias_map["groups"]))
    fields = {
        item["code"]: None for item in json.loads(packet.model_context)["elements"]
    }
    fields.update(
        {
            "emission_amount": "777.7",
            "mass_unit": "吨",
            "pollutant": "二氧化硫",
            "reporting_period": "2024",
        }
    )
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "partial",
                "rows": [
                    {
                        "target_cell": None,
                        "group": group_alias,
                        "fields": fields,
                    }
                ],
                "uncertainty_code": "insufficient_evidence",
            },
            ensure_ascii=False,
        ),
        packet=packet,
        visual_used=True,
    )

    amount = next(
        item
        for item in decision.fact_groups[0].assignments
        if item.element_id.endswith("element.emission_amount")
    )
    assert amount.source_mode == "visual"
    assert GroundingGuard().validate(packet, decision).accepted


def test_text_only_output_cannot_bypass_known_target_cell_contract(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    context = json.loads(packet.model_context)
    group_alias = next(iter(packet.alias_map["groups"]))
    fields = {item["code"]: None for item in context["elements"]}
    fields["emission_amount"] = "777.7"

    with pytest.raises(ModelResponseError, match="target-cell contract"):
        DirectFillResponseAdapter().parse(
            json.dumps(
                {
                    "task_id": packet.task_id,
                    "status": "found",
                    "rows": [
                        {
                            "target_cell": None,
                            "group": group_alias,
                            "fields": fields,
                        }
                    ],
                    "uncertainty_code": "none",
                }
            ),
            packet=packet,
            visual_used=False,
        )


def test_guard_accepts_bound_page_visual_even_without_span_image_path(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = without_target_cells(regions[0])
    visual = next(iter(packet.alias_map["visual"]))
    anchor_id = packet.alias_map["visual"][visual]
    packet = packet.model_copy(
        update={
            "spans": [
                item.model_copy(update={"visual_evidence_paths": []})
                if item.span_id == anchor_id
                else item
                for item in packet.spans
            ]
        },
        deep=True,
    )
    group_alias = next(
        alias
        for alias, group_id in packet.alias_map["groups"].items()
        if group_id
        == next(item.context_group_id for item in packet.spans if item.span_id == anchor_id)
    )
    fields = {
        item["code"]: None for item in json.loads(packet.model_context)["elements"]
    }
    fields["emission_amount"] = "777.7"
    payload = {
        "task_id": packet.task_id,
        "status": "found",
        "rows": [{"group": group_alias, "fields": fields}],
        "uncertainty_code": "none",
    }
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(payload, ensure_ascii=False), packet=packet, visual_used=True
    )
    guard = GroundingGuard().validate(packet, decision)
    assert guard.accepted, guard.feedback


def test_missing_required_context_warns_without_discarding_grounded_value(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    amount = next(
        item for item in packet.candidates if item.candidate_type == "quantity"
    )
    group_alias = next(
        alias
        for alias, group_id in packet.alias_map["groups"].items()
        if group_id == amount.context_group_id
    )
    fields = {
        item["code"]: None for item in json.loads(packet.model_context)["elements"]
    }
    fields["emission_amount"] = amount.raw_value
    payload = {
        "task_id": packet.task_id,
        "status": "found",
        "rows": [{"group": group_alias, "fields": fields}],
        "uncertainty_code": "none",
    }
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(payload, ensure_ascii=False), packet=packet
    )
    guard = GroundingGuard().validate(packet, decision)
    assert guard.accepted, guard.feedback
    assert "required_element_missing" in {item.code for item in guard.issues}
    assert all(item.severity == "warning" for item in guard.issues)


def test_adapter_rejects_value_not_visible_in_selected_row_group(tmp_path) -> None:
    rows = [
        ("氮氧化物", "125.6吨", "2024年", "中国境内运营"),
        ("二氧化硫", "64.2吨", "2024年", "中国境内运营"),
    ]
    _, _, regions, _, _ = build_pipeline(tmp_path, rows=rows)
    packet = next(item for item in regions if len(item.allowed_group_ids) == 2)
    group_aliases = list(packet.alias_map["groups"])
    fields = {
        item["code"]: None for item in json.loads(packet.model_context)["elements"]
    }
    fields["emission_amount"] = "64.2吨"
    payload = {
        "task_id": packet.task_id,
        "status": "partial",
        "rows": [{"group": group_aliases[0], "fields": fields}],
        "uncertainty_code": "insufficient_evidence",
    }
    with pytest.raises(ModelResponseError, match="violates target-cell contract"):
        DirectFillResponseAdapter().parse(
            json.dumps(payload, ensure_ascii=False), packet=packet
        )


def test_guard_warns_when_one_listed_target_cell_is_not_emitted(tmp_path) -> None:
    rows = [
        ("氮氧化物", "125.6吨", "2024年", "中国境内运营"),
        ("二氧化硫", "64.2吨", "2024年", "中国境内运营"),
    ]
    _, _, regions, _, _ = build_pipeline(tmp_path, rows=rows)
    packet = next(item for item in regions if len(item.allowed_group_ids) == 2)
    context = json.loads(packet.model_context)
    first_cell = context["region"]["target_value_cells"][0]
    first_group = first_cell["group"]
    fields = {item["code"]: None for item in context["elements"]}
    fields["emission_amount"] = first_cell["visible_value"]
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "partial",
                "rows": [{"group": first_group, "fields": fields}],
                "uncertainty_code": "insufficient_evidence",
            },
            ensure_ascii=False,
        ),
        packet=packet,
    )

    guard = GroundingGuard().validate(packet, decision)

    assert guard.accepted
    assert "target_value_cell_not_emitted" in {item.code for item in guard.issues}
    assert all(
        item.severity == "warning"
        for item in guard.issues
        if item.code == "target_value_cell_not_emitted"
    )


def test_guard_accepts_lossless_numeric_thousands_separator_normalization(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(
        tmp_path,
        rows=[("硫氧化物", "1,361.92吨", "2024年", "中国境内运营")],
    )
    packet = regions[0]
    context = json.loads(packet.model_context)
    fields = {item["code"]: None for item in context["elements"]}
    fields.update(
        {
            "emission_amount": 1361.92,
            "mass_unit": "吨",
            "pollutant": "硫氧化物",
            "reporting_period": 2024,
            "reporting_boundary": "中国境内运营",
        }
    )
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "found",
                "rows": [
                    {
                        "group": next(iter(packet.alias_map["groups"])),
                        "fields": fields,
                    }
                ],
                "uncertainty_code": "none",
            },
            ensure_ascii=False,
        ),
        packet=packet,
    )

    guard = GroundingGuard().validate(packet, decision)

    assert guard.accepted, guard.feedback
    assert "candidate_value_mismatch" not in {item.code for item in guard.issues}
