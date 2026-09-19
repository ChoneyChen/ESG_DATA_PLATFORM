from __future__ import annotations

import json

import pytest

from esg_targeted.models.preflight import PacketPreflightGuard, PromptBudgetExceeded
from esg_targeted.models.qiniu_vlm import QiniuVlmModel
from esg_targeted.models.template import DirectFillTemplateCompiler

from tests.test_inventory_and_guard import build_pipeline


def test_output_contract_is_compiled_from_standard_package_elements(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    context = json.loads(packet.model_context)
    expected = {item["code"] for item in context["elements"]}
    fields = DirectFillTemplateCompiler().compile(packet)["row_groups"][0]["shared_fields"]
    assert set(fields) == expected
    assert "pollution_medium" not in fields  # fixed by the standard package


def test_qiniu_schema_allows_multiple_rows_and_direct_scalar_fields(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0]
    model = QiniuVlmModel(
        api_key="test",
        base_url="https://example.invalid/v1",
        model_id="test-vlm",
        requester=lambda *_: {},
    )
    response_format = model._decision_response_format(packet)
    schema = response_format["json_schema"]["schema"]
    assert schema["properties"]["row_groups"]["maxItems"] > 1
    amount = schema["properties"]["row_groups"]["items"]["properties"]["values"][
        "items"
    ]["properties"]["fields"]["properties"]["emission_amount"]
    assert {item["type"] for item in amount["anyOf"]} == {
        "null",
        "string",
        "number",
        "boolean",
    }
    assert all("properties" not in item for item in amount["anyOf"])


def test_region_packets_are_the_only_model_bounded_layer(tmp_path) -> None:
    rows = [
        (f"污染物{i}", f"{i + 1}.2吨", "2024年", "中国境内运营")
        for i in range(12)
    ]
    _, selection, regions, _, _ = build_pipeline(tmp_path, rows=rows)
    assert len(
        [item for item in selection.allowed_group_ids if item.startswith("table-row:")]
    ) == 12
    assert len(regions) >= 2
    assert all(len(item.allowed_group_ids) <= 8 for item in regions)
    assert all(item.budget["stage"] == "direct_semantic_fill_region" for item in regions)
    assert all(PacketPreflightGuard(24_000).validate(item) for item in regions)


def test_prompt_explicitly_delegates_semantics_and_visual_reading_to_model(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    instructions = DirectFillTemplateCompiler.instructions()
    assert "directly fill" in instructions
    assert "ONE APPLICABLE NUMERIC VALUE CELL OR DATA POINT = ONE values ITEM" in instructions
    assert "PHYSICAL SOURCE ROW" in instructions
    assert "DIRECT scalar" in instructions
    assert "local adapter binds provenance" in instructions
    assert "NOT reporting_boundary" in instructions
    assert "typed fields first" in instructions
    assert "exactly one coherent ESG evidence object" in instructions
    assert "deterministic exact-duplicate merge" in instructions


def test_preflight_rejects_more_than_one_visual_object(tmp_path) -> None:
    _, _, regions, _, _ = build_pipeline(tmp_path)
    packet = regions[0].model_copy(
        update={"page_image_paths": ["table-a.png", "figure-b.png"]},
        deep=True,
    )

    with pytest.raises(PromptBudgetExceeded, match="at most one visual"):
        PacketPreflightGuard(24_000).validate(packet)
