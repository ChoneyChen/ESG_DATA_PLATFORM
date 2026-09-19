from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from esg_targeted.models.response_adapter import (
    DirectFillResponseAdapter,
    ModelResponseError,
    PERIOD_HEADER_RE,
    decode_first_json_object,
)
from esg_targeted.models.template import DirectFillTemplateCompiler

from tests.test_inventory_and_guard import build_pipeline, complete_row


def test_template_group_is_a_scalar_alias(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    group = DirectFillTemplateCompiler().compile(packet)["row_groups"][0]["group"]
    assert isinstance(group, str)
    assert group in packet.alias_map["groups"]


def test_row_group_classification_expands_to_distinct_cell_facts(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = next(item for item in regions if json.loads(item.model_context)["region"]["target_value_cells"])
    context = json.loads(packet.model_context)
    first = context["region"]["target_value_cells"][0]
    second = {**first, "id": "T2", "cell_id": f"{first['cell_id']}-sibling"}
    context["region"]["target_value_cells"] = [first, second]
    packet = packet.model_copy(update={
        "model_context": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        "alias_map": {
            **packet.alias_map,
            "target_cells": {**packet.alias_map.get("target_cells", {}), "T2": second["cell_id"]},
        },
    }, deep=True)
    fields = {item["code"]: None for item in context["elements"]}
    payload = {
        "task_id": packet.task_id,
        "status": "found",
        "row_groups": [{
            "group": first["group"],
            "metric_match": "match",
            "interpretation_note": "同一物理行，按列展开。",
            "context_refs": [],
            "shared_fields": fields,
            "values": [
                {"target_cell": first["id"], "fields": {**fields, "emission_amount": first["visible_value"]}},
                {"target_cell": second["id"], "fields": {**fields, "emission_amount": second["visible_value"]}},
            ],
        }],
        "uncertainty_code": "none",
        "skipped_targets": [],
        "missing_context": [],
    }
    decision, _, actions = DirectFillResponseAdapter().parse_with_diagnostics(
        json.dumps(payload, ensure_ascii=False), packet=packet
    )
    assert len(decision.fact_groups) == 2
    assert len({group.group_ref_id for group in decision.fact_groups}) == 2
    assert "expand_physical_row_groups" in actions


def test_target_cell_visible_value_is_losslessly_resolved_to_unique_t_alias(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = next(item for item in regions if json.loads(item.model_context)["region"]["target_value_cells"])
    context = json.loads(packet.model_context)
    target = context["region"]["target_value_cells"][0]
    fields = {item["code"]: None for item in context["elements"]}
    fields["emission_amount"] = target["visible_value"]
    row = {"target_cell": target["visible_value"], "group": target["group"], "fields": fields}
    decision, _, actions = DirectFillResponseAdapter().parse_with_diagnostics(
        json.dumps({"task_id": packet.task_id, "status": "found", "rows": [row], "uncertainty_code": "none"}, ensure_ascii=False),
        packet=packet,
    )
    assert len(decision.fact_groups) == 1
    assert "resolve_target_cell_from_unique_visible_value_or_span" in actions


def test_typed_entity_is_not_duplicated_into_legacy_breakdown() -> None:
    contract = SimpleNamespace(column_headers=["2025", "紫金"], unit_context="GWh")
    elements = {
        "reporting_period": {"semantic_role": "period"},
        "reporting_entity": {
            "element_code": "reporting_entity", "semantic_role": "dimension",
            "binding": {"target": "example.dimension.reporting-entity"},
        },
        "additional_breakdown": {"semantic_role": "breakdown"},
        "energy_unit": {"semantic_role": "unit"},
    }
    fields = {"reporting_entity": "紫金", "additional_breakdown": None}
    actions = []
    DirectFillResponseAdapter._fill_structural_fields(fields, contract, elements, actions)
    assert fields == {
        "reporting_entity": "紫金", "additional_breakdown": None,
        "reporting_period": "2025", "energy_unit": "GWh",
    }

    # Existing packages without typed entities keep their proven header repair.
    fields = {"additional_breakdown": None}
    del elements["reporting_entity"]
    DirectFillResponseAdapter._fill_structural_fields(fields, contract, elements, [])
    assert fields["additional_breakdown"] == "紫金"


def test_json_decoder_accepts_known_mlx_special_token() -> None:
    payload = decode_first_json_object('{"status":"not_found"}<|im_end|>')
    assert payload == {"status": "not_found"}


def test_json_decoder_losslessly_unwraps_singleton_object_array() -> None:
    payload = decode_first_json_object('[{"status":"not_found","rows":[]}]')
    assert payload == {"status": "not_found", "rows": []}


def test_json_decoder_rejects_multi_object_array() -> None:
    with pytest.raises(ModelResponseError, match="exactly one JSON object"):
        decode_first_json_object('[{"status":"not_found"},{"status":"found"}]')


def test_json_decoder_rejects_unexpected_trailing_text() -> None:
    with pytest.raises(ModelResponseError, match="unexpected content"):
        decode_first_json_object('{"status":"not_found"} invented')


def test_json_decoder_losslessly_closes_missing_row_object() -> None:
    payload = decode_first_json_object(
        '{"status":"found","rows":[{"group":"G1","fields":{"x":1}],'
        '"uncertainty_code":"none"}<|im_end|>'
    )
    assert payload["rows"][0]["fields"] == {"x": "1"}


def test_json_decoder_does_not_invent_unclosed_string_content() -> None:
    with pytest.raises(ModelResponseError, match="invalid model JSON"):
        decode_first_json_object('{"status":"found","rows":[{"group":"G1')


def test_adapter_salvages_only_complete_rows_from_truncated_generation(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    context = json.loads(packet.model_context)
    target = context["region"]["target_value_cells"][0]
    fields = {item["code"]: None for item in context["elements"]}
    fields["emission_amount"] = target["visible_value"]
    complete = json.dumps(
        {
            "target_cell": target["id"],
            "group": target["group"],
            "fields": fields,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    truncated = (
        '{"task_id":'
        + json.dumps(packet.task_id)
        + ',"status":"found","rows":['
        + complete
        + ',{"target_cell":null,"group":"G1","fields":{"emission_amount":"77'
    )

    decision, _, actions = DirectFillResponseAdapter().parse_with_diagnostics(
        truncated,
        packet=packet,
        visual_used=True,
    )

    assert decision.status == "partial"
    assert len(decision.fact_groups) == 1
    assert actions == ("salvage_complete_rows_from_truncated_output",)


def test_adapter_does_not_salvage_an_incomplete_first_row(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]

    with pytest.raises(ModelResponseError, match="invalid model JSON"):
        DirectFillResponseAdapter().parse_with_diagnostics(
            '{"task_id":"task-fixture","status":"found","rows":'
            '[{"target_cell":"T1","group":"G1","fields":{"emission_amount":"77',
            packet=packet,
            visual_used=True,
        )


def test_adapter_binds_direct_scalars_to_provenance_modes(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    row = complete_row(
        packet,
        "氮氧化物",
        "125.6吨",
        "2024",
        "中国境内运营",
    )
    payload = {
        "task_id": packet.task_id,
        "status": "found",
        "rows": [row],
        "uncertainty_code": "none",
    }
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(payload, ensure_ascii=False), packet=packet
    )
    modes = {item.source_mode for item in decision.fact_groups[0].assignments}
    assert {"candidate", "span"} <= modes


def test_adapter_accepts_native_nuextract_numeric_scalars(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    row = complete_row(packet, "氮氧化物", 125.6, 2024, "中国境内运营")
    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "found",
                "rows": [row],
                "uncertainty_code": "none",
            },
            ensure_ascii=False,
        ),
        packet=packet,
    )

    values = {
        item.element_id.rsplit(".", 1)[-1]: item.value_raw
        for item in decision.fact_groups[0].assignments
    }
    amount = next(
        item
        for item in decision.fact_groups[0].assignments
        if item.element_id.endswith("element.emission_amount")
    )
    assert values["emission_amount"] == "125.6"
    assert values["reporting_period"] == "2024"
    assert amount.source_mode == "candidate"


def test_adapter_losslessly_unwraps_single_group_alias_array(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    context = json.loads(packet.model_context)
    target = context["region"]["target_value_cells"][0]
    fields = {item["code"]: None for item in context["elements"]}
    fields["emission_amount"] = target["visible_value"]

    decision, _, actions = DirectFillResponseAdapter().parse_with_diagnostics(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "found",
                "rows": [
                    {
                        "target_cell": target["id"],
                        "group": [target["group"]],
                        "fields": fields,
                    }
                ],
                "uncertainty_code": "none",
            },
            ensure_ascii=False,
        ),
        packet=packet,
    )

    assert len(decision.fact_groups) == 1
    assert "unwrap_single_group_alias" in actions


def test_adapter_uses_target_cell_to_fill_explicit_stacked_header_dimensions(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    context = json.loads(packet.model_context)
    target = context["region"]["target_value_cells"][0]
    target["column_headers"] = ["2024", "越秀服務 1"]
    target_span_id = packet.alias_map["spans"][target["span"]]
    packet = packet.model_copy(
        update={
            "model_context": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            "spans": [
                item.model_copy(
                    update={
                        "context_text": item.context_text + " 2024 越秀服務 1",
                        "structural_context": {
                            **item.structural_context,
                            "column_header_path": ["2024", "越秀服務 1"],
                        },
                    }
                )
                if item.span_id == target_span_id
                else item
                for item in packet.spans
            ],
        },
        deep=True,
    )
    fields = {
        item["code"]: None for item in context["elements"]
    }
    fields["emission_amount"] = target["visible_value"]
    fields["pollutant"] = "氮氧化物"
    fields["mass_unit"] = "吨"
    decision, _, actions = DirectFillResponseAdapter().parse_with_diagnostics(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "found",
                "rows": [
                    {
                        "target_cell": target["id"],
                        "group": target["group"],
                        "fields": fields,
                    }
                ],
                "uncertainty_code": "none",
            },
            ensure_ascii=False,
        ),
        packet=packet,
    )
    values = {
        item.element_id.rsplit(".", 1)[-1]: item.value_raw
        for item in decision.fact_groups[0].assignments
    }
    assert values["reporting_period"] == "2024"
    assert values["additional_breakdown"] == "越秀服務 1"
    assert "fill_reporting_period_from_target_cell_structure" in actions
    assert "fill_additional_breakdown_from_target_cell_structure" in actions


def test_target_cell_structure_corrects_neighbouring_period_and_breakdown(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    context = json.loads(packet.model_context)
    target = context["region"]["target_value_cells"][0]
    target["column_headers"] = ["2024", "越秀服務 1"]
    target_span_id = packet.alias_map["spans"][target["span"]]
    packet = packet.model_copy(
        update={
            "model_context": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            "spans": [
                item.model_copy(
                    update={
                        "context_text": item.context_text + " 2024 越秀服務 1",
                        "structural_context": {
                            **item.structural_context,
                            "column_header_path": ["2024", "越秀服務 1"],
                        },
                    }
                )
                if item.span_id == target_span_id
                else item
                for item in packet.spans
            ],
        },
        deep=True,
    )
    fields = {item["code"]: None for item in context["elements"]}
    fields.update(
        {
            "emission_amount": target["visible_value"],
            "reporting_period": "2023",
            "additional_breakdown": "越秀地產",
        }
    )
    decision, _, actions = DirectFillResponseAdapter().parse_with_diagnostics(
        json.dumps(
            {
                "task_id": packet.task_id,
                "status": "found",
                "rows": [
                    {
                        "target_cell": target["id"],
                        "group": target["group"],
                        "fields": fields,
                    }
                ],
                "uncertainty_code": "none",
            },
            ensure_ascii=False,
        ),
        packet=packet,
    )
    values = {
        item.element_id.rsplit(".", 1)[-1]: item.value_raw
        for item in decision.fact_groups[0].assignments
    }
    assert values["reporting_period"] == "2024"
    assert values["additional_breakdown"] == "越秀服務 1"
    assert "correct_reporting_period_from_target_cell_structure" in actions
    assert "correct_additional_breakdown_from_target_cell_structure" in actions


def test_period_header_accepts_year_total_label() -> None:
    assert PERIOD_HEADER_RE.fullmatch("2025合计")


def test_adapter_rejects_unknown_fields_instead_of_creating_schema(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    payload = {
        "task_id": packet.task_id,
        "status": "found",
        "rows": [
            {
                "group": next(iter(packet.alias_map["groups"])),
                "fields": {
                    "invented_field": "x"
                },
            }
        ],
        "uncertainty_code": "none",
    }
    with pytest.raises(ModelResponseError, match="unknown standard field"):
        DirectFillResponseAdapter().parse(
            json.dumps(payload, ensure_ascii=False), packet=packet
        )


def test_adapter_normalizes_lossless_empty_rows_to_not_found(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]

    decision, _ = DirectFillResponseAdapter().parse(
        json.dumps({"task_id": packet.task_id, "rows": []}),
        packet=packet,
    )

    assert decision.status == "not_found"
    assert decision.fact_groups == []


def test_structural_fill_uses_semantic_roles_for_energy_and_ghg_fields() -> None:
    fields = {"energy_amount": "125", "energy_unit": None, "period_label": None}
    elements = {
        "energy_amount": {
            "semantic_role": "value",
            "binding": {"target": "value_raw"},
        },
        "energy_unit": {
            "semantic_role": "unit",
            "binding": {"target": "unit_raw"},
        },
        "period_label": {
            "semantic_role": "period",
            "binding": {"target": "reporting_period_raw"},
        },
    }
    actions: list[str] = []

    DirectFillResponseAdapter._fill_structural_fields(
        fields,
        SimpleNamespace(column_headers=["2025"], unit_context="MWh"),
        elements,
        actions,
    )

    assert fields["energy_unit"] == "MWh"
    assert fields["period_label"] == "2025"
    assert "fill_energy_unit_from_target_cell_structure" in actions
    assert "fill_period_label_from_target_cell_structure" in actions
