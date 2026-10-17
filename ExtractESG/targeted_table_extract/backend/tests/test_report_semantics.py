"""Offline contracts for model-owned meaning and deterministic IR expansion."""
import json
import pytest
from types import SimpleNamespace

from esg_targeted.models.report_semantics import (
    ObjectMapping,
    TableUnderstanding,
    expand_understanding,
    project_interpretation,
)
from esg_targeted.evidence.target_cells import target_cell_contracts
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.models.source_facts import project_source_facts
from tests.test_inventory_and_guard import build_pipeline


def context():
    return {
        "rows": [{
            "group": "G1", "group_id": "row-scope2", "label": "范围二排放",
            "text": "紫金 范围二排放。按照GHG Protocol计算范围三排放。纳入上游运输和配送类别。",
            "support_segments": [{"span_id": "scope-text", "text": "紫金 范围二排放。按照GHG Protocol计算范围三排放。纳入上游运输和配送类别。",
                                  "locators": [{"page_index": 0, "object_id": "scope-table", "bbox": [0, 0, 100, 20]}]}],
            "cells": [
                {"target_cell": "T1", "cell_id": "cell-location", "column": "c1",
                 "raw": "1,234.00", "unit": "tCO₂e", "column_headers": ["2024", "位置法", "紫金"],
                 "span_id": "location-cell", "locators": [{"page_index": 0, "object_id": "cell-location", "bbox": [0, 20, 50, 40]}]},
                {"target_cell": "T2", "cell_id": "cell-market", "column": "c2",
                 "raw": "0", "unit": "tCO₂e", "column_headers": ["2024", "市场法", "紫金"],
                 "span_id": "market-cell", "locators": [{"page_index": 0, "object_id": "cell-market", "bbox": [50, 20, 100, 40]}]},
            ],
        }],
    }


def understanding(**extra):
    payload = {
        "rows": [{"group": "G1", "kind": "quantitative", "source_row_label": "范围二排放",
                  "fields": {"ghg_scope": "范围二"},
                  "semantics": {"aggregation_role": "total", "measurement_kind": "absolute"}}],
        "columns": [
            {"column": "c1", "fields": {"reporting_period": "2024", "reporting_entity": "紫金",
                                        "entity_aggregation_role": "reporting_entity", "method": "位置法"}},
            {"column": "c2", "fields": {"reporting_period": "2024", "reporting_entity": "紫金",
                                        "entity_aggregation_role": "reporting_entity", "method": "市场法"}},
        ],
    }
    payload.update(extra)
    return TableUnderstanding.model_validate(payload)


def packet(metric_id="metric.location", *, with_market_cell=True):
    targets = [{"id": c["target_cell"], "cell_id": c["cell_id"], "group": "G1",
                "visible_value": c["raw"], "column_headers": c["column_headers"]}
               for c in context()["rows"][0]["cells"] if with_market_cell or c["column"] != "c2"]
    return SimpleNamespace(
        metric={"metric_id": metric_id}, candidates=[],
        alias_map={"groups": {"G1": "row-scope2"}, "spans": {}},
        model_context=json.dumps({"region": {"target_value_cells": targets}}),
    )


def mapping():
    return ObjectMapping.model_validate({
        "matches": [
            {"source_id": "row-scope2:1", "metric_id": "metric.location", "columns": ["c1"],
             "match": "match", "reason": "Column explicitly uses location-based accounting"},
            {"source_id": "row-scope2:1", "metric_id": "metric.market", "columns": ["c2"],
             "match": "match", "reason": "Column explicitly uses market-based accounting"},
        ],
        "coverage": [{"metric_id": mid, "status": "reviewed", "reason": "This object was assessed"}
                     for mid in ("metric.location", "metric.market")],
    })


def test_model_describes_one_row_and_columns_backend_expands_exact_ir_values():
    rows, errors = expand_understanding(understanding(), context())
    assert not errors
    assert len(rows) == 1
    assert rows[0]["fields"] == {"ghg_scope": "范围二"}
    assert [c["value"] for c in rows[0]["cells"]] == ["1,234.00", "0"]
    assert [c["fields"]["method"] for c in rows[0]["cells"]] == ["位置法", "市场法"]
    assert all(c["fields"]["reporting_entity"] == "紫金" for c in rows[0]["cells"])
    assert all(not c["visual_override"] for c in rows[0]["cells"])


def test_methods_map_by_column_instead_of_copying_scope2_to_both_metrics():
    rows, errors = expand_understanding(understanding(), context())
    source = {"rows": rows, "errors": errors}
    for mid, target, value in (("metric.location", "T1", "1,234.00"), ("metric.market", "T2", "0")):
        projected = project_interpretation(source, mapping(), packet(mid))
        matched = [c for c in projected.source_rows[0].cells if c.metric_mappings[0].match == "match"]
        assert [(c.target_cell, c.value) for c in matched] == [(target, value)]
        assert projected.source_rows[0].metric_mappings[0].match == "different"


def test_same_column_occurrences_bind_their_exact_source_anchor():
    from esg_targeted.report_facts import source_anchor_id

    rows, errors = expand_understanding(understanding(), context())
    row = rows[0]
    row["cells"][1]["column"] = row["cells"][0]["column"]
    source = {"source_key": "anonymous-object-reading", "rows": rows, "errors": errors}
    source["facts"] = [{"source_row_id": row["source_id"], "column": cell["column"],
                        "source_anchor_id": source_anchor_id(source["source_key"], row, cell, index),
                        "source_fact_id": f"fact-occurrence-{index}"}
                       for index, cell in enumerate(row["cells"])]
    projected = project_interpretation(source, mapping(), packet())
    assert [cell.source_fact_id for cell in projected.source_rows[0].cells] == ["fact-occurrence-0", "fact-occurrence-1"]

    # Semantic row ordinals never define the same physical cell's anchor.
    assert source_anchor_id(source["source_key"], {**row, "source_id": "changed-semantic-row:99"}, row["cells"][0], 99) == source["facts"][0]["source_anchor_id"]

    # Legacy same-column duplicates stay unbound even when they have equal values.
    for fact in source["facts"]:
        fact.pop("source_anchor_id")
    projected = project_interpretation(source, mapping(), packet())
    assert [cell.source_fact_id for cell in projected.source_rows[0].cells] == [None, None]


def test_multiple_readable_assertions_keep_distinct_identity_without_numeric_cells():
    output = understanding(rows=[
        {"group": "G1", "kind": "assertion", "statement": "按照GHG Protocol计算范围三排放。"},
        {"group": "G1", "kind": "assertion", "statement": "纳入上游运输和配送类别。"},
    ])
    rows, errors = expand_understanding(output, context())
    assert not errors
    assert [r["source_id"] for r in rows] == ["row-scope2:1", "row-scope2:2"]
    assert [r["statement"] for r in rows] == ["按照GHG Protocol计算范围三排放。", "纳入上游运输和配送类别。"]
    assert all(r["cells"] == [] for r in rows)


def test_unknown_group_is_isolated_and_known_row_numbers_are_preserved():
    output = understanding(rows=[
        *understanding().model_dump()["rows"],
        {"group": "G999", "kind": "quantitative", "source_row_label": "unknown identity"},
    ])
    rows, errors = expand_understanding(output, context())
    assert [r["source_id"] for r in rows] == ["row-scope2:1"]
    assert [c["value"] for c in rows[0]["cells"]] == ["1,234.00", "0"]
    assert "Unknown source group G999" in errors
    projected = project_interpretation({"rows": rows, "errors": errors}, mapping(), packet())
    assert next(c for c in projected.coverage if c.metric_id == "metric.location").status == "reviewed"
    # The invalid unbound row remains an object gap; it does not undo a valid
    # location-method mapping on a different, known physical row.
    scoped = project_interpretation({"rows": rows, "errors": errors, "gaps": [
        {"metric_ids": ["metric.market"], "reason": "Missing market-method column"}]}, mapping(), packet())
    assert next(c for c in scoped.coverage if c.metric_id == "metric.location").status == "reviewed"


def test_ir_coordinate_missing_from_packet_is_not_silently_reclassified_as_visual():
    rows, errors = expand_understanding(understanding(), context())
    projected = project_interpretation({"rows": rows, "errors": errors}, mapping(), packet(with_market_cell=False))
    assert [(c.target_cell, c.value) for c in projected.source_rows[0].cells] == [("T1", "1,234.00")]


def test_visual_correction_changes_only_named_cell_and_keeps_source_coordinate():
    output = understanding(cell_overrides=[{"target_cell": "T1", "value": "1,234.50", "reason": "OCR decimal misread"}])
    source_context = context()
    source_context["rows"][0]["cells"][0]["grid_reliable"] = False
    source_context.update(visual_evidence_supplied=True, visual_evidence_group_ids=["row-scope2"])
    rows, errors = expand_understanding(output, source_context)
    assert not errors
    cells = rows[0]["cells"]
    assert [(c["cell_id"], c["value"]) for c in cells] == [("cell-location", "1,234.50"), ("cell-market", "0")]
    assert [c["visual_override"] for c in cells] == [True, False]
    assert cells[0]["fields"]["method"] == "位置法"


def test_unlisted_visual_cell_inherits_understood_column_identity_and_unit():
    output = understanding(
        columns=[{"column": "c3", "unit": "tCO₂e", "fields": {
            "reporting_period": "2023", "reporting_entity": "藏格", "method": "位置法"}}],
        cell_overrides=[{"group": "G1", "column": "c3", "value": "42.00", "reason": "Visible cell omitted by OCR"}],
    )
    source_context = context()
    source_context["rows"][0].update(grid_reliable=False,
        locators=[{"page_index": 0, "object_id": "scope-table", "bbox": [0, 0, 100, 50]}])
    source_context.update(visual_evidence_supplied=True, visual_evidence_group_ids=["row-scope2"])
    source_context["rows"][0]["support_segments"].append({"span_id": "visual-header", "text": "2023 藏格 位置法",
        "locators": [{"page_index": 0, "object_id": "scope-table", "bbox": [0, 0, 100, 20]}]})
    rows, errors = expand_understanding(output, source_context)
    assert not errors
    added = rows[0]["cells"][-1]
    assert added["cell_id"] is None
    assert added["value"] == "42.00"
    assert added["unit"] == "tCO₂e"
    assert added["fields"] == {"reporting_period": "2023", "reporting_entity": "藏格", "method": "位置法", "ghg_scope": "范围二"}


def test_unknown_override_target_does_not_overwrite_any_known_ir_number():
    output = understanding(cell_overrides=[{"target_cell": "T999", "value": "999999", "reason": "invalid reference"}])
    rows, errors = expand_understanding(output, context())
    assert [c["value"] for c in rows[0]["cells"]] == ["1,234.00", "0"]
    assert "Unknown target cell T999" in errors


def test_english_quantity_paraphrase_cannot_replace_reliable_ir_raw_value_or_unit():
    from esg_targeted.models.report_semantics import validate_understanding_payload
    from esg_targeted.report_facts import source_facts

    source_context = context()
    source_context["rows"][0]["cells"][0].update(raw="1.79", unit="亿吨")
    payload = understanding(rows=[{"group": "G1", "kind": "quantitative", "target_cells": ["T1"],
        "statement": "1.79 million tonnes"}], cell_overrides=[{"target_cell": "T1",
        "value": "1.79 million tonnes", "unit": "million tonnes", "reason": "English paraphrase"}])
    valid, issues = validate_understanding_payload(payload, source_context)
    assert any(issue.stage == "semantic" for issue in issues)
    rows, _ = expand_understanding(valid, source_context)
    assert rows[0]["cells"][0]["value"] == "1.79"
    assert rows[0]["cells"][0]["unit"] == "亿吨"
    facts = source_facts({"source_key": "anonymous", "object_id": "anonymous-table", "version": "v3", "rows": rows},
        {"document_id": "anonymous-report", "ir_run_id": "anonymous-ir", "ir_revision": 1},
        {"rows": source_context["rows"]})
    assert facts[0]["value_raw"] == "1.79" and facts[0]["unit_raw"] == "亿吨"
    assert facts[0]["statement_raw"] is None


@pytest.mark.parametrize("missing", ["image", "scope", "bbox"])
def test_unreliable_visual_value_requires_supplied_image_scope_and_bbox(missing):
    source_context = context()
    source_context["rows"][0]["cells"][0]["grid_reliable"] = False
    source_context.update(visual_evidence_supplied=missing != "image",
                          visual_evidence_group_ids=[] if missing == "scope" else ["row-scope2"])
    if missing == "bbox":
        source_context["rows"][0]["cells"][0]["locators"] = []
    rows, errors = expand_understanding(understanding(cell_overrides=[{"target_cell": "T1", "value": "1,234.50"}]), source_context)
    assert errors
    assert [cell["cell_id"] for cell in rows[0]["cells"]] == ["cell-market"]


def test_same_reliable_raw_override_can_add_supported_identity_without_visual_bypass():
    rows, errors = expand_understanding(understanding(cell_overrides=[{"target_cell": "T1", "value": "1,234.00",
        "fields": {"method": "位置法"}}]), context())
    assert not errors
    assert rows[0]["cells"][0]["fields"]["method"] == "位置法"
    assert not rows[0]["cells"][0]["visual_override"]


def test_agent_visual_correction_passes_existing_guard_and_preserves_ir_cell_identity(tmp_path):
    _, _, regions, _, metric = build_pipeline(tmp_path)
    binding_packet = next(p for p in regions if target_cell_contracts(p))
    binding_packet.budget["agent_version"] = "offline-report-fact-agent"
    target = next(t for t in target_cell_contracts(binding_packet) if "125.6吨" in t.value_forms)
    target_span = next(span for span in binding_packet.spans if span.span_id == target.span_id)
    target_span.structural_context["evidence_capabilities"] = {"cell_grid": False}
    source_context = {"rows": [{"group": target.group_alias, "group_id": target.group_id,
                               "label": "氮氧化物", "cells": [{
                                   "target_cell": target.alias, "cell_id": target.cell_id,
                                   "column": "c1", "raw": "125.6吨", "unit": "吨",
                                   "column_headers": list(target.column_headers),
                                   "grid_reliable": False,
                                   "span_id": target.span_id, "locators": [locator.model_dump(mode="json") for locator in target_span.locators],
                               }]}], "visual_evidence_supplied": True, "visual_evidence_group_ids": [target.group_id]}
    model_understanding = TableUnderstanding.model_validate({
        "rows": [{"group": target.group_alias, "kind": "quantitative", "source_row_label": "氮氧化物",
                  "unit": "吨", "fields": {"pollutant": "氮氧化物", "reporting_boundary": "中国境内运营"}}],
        "columns": [{"column": "c1", "fields": {"reporting_period": "2024年"}}],
        "cell_overrides": [{"target_cell": target.alias, "value": "125.7", "reason": "Visible decimal differs from OCR"}],
    })
    rows, errors = expand_understanding(model_understanding, source_context)
    source_mapping = ObjectMapping.model_validate({
        "matches": [{"source_id": rows[0]["source_id"], "metric_id": metric.metric_id,
                     "match": "match", "reason": "Air pollutant emission row"}],
        "coverage": [{"metric_id": metric.metric_id, "status": "reviewed", "reason": "Object assessed"}],
    })
    interpreted = project_interpretation({"rows": rows, "errors": errors}, source_mapping, binding_packet)
    assert interpreted.source_rows[0].cells[0].target_cell == target.alias
    decision, audit = project_source_facts(interpreted, binding_packet, visual_used=True)
    assert decision.status == "found", audit
    assert decision.fact_groups[0].group_ref_id == target.cell_id
    primary_id = next(e["element_id"] for e in binding_packet.elements if e["binding"]["target"] == "value_raw")
    primary = next(a for a in decision.fact_groups[0].assignments if a.element_id == primary_id)
    assert primary.value_raw == "125.7" and primary.source_mode == "visual"
    assert GroundingGuard().validate(binding_packet, decision).accepted
    # The same override without an actual supplied image must not weaken the
    # mature literal contract or silently accept a changed number.
    without_image, _ = project_source_facts(interpreted, binding_packet, visual_used=False)
    assert without_image.fact_groups == []
    assert without_image.status == "ambiguous"
