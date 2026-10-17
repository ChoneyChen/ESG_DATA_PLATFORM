"""Anonymous scientific identity boundaries; no report artifacts or models."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from esg_targeted.contracts import EvidenceSpan, SourceLocator
from esg_targeted.evidence.harvester import LiteralHarvester
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.source_reading import bind_source_row
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.models.source_facts import SourceInterpretation, project_source_facts


def location(identity, kind="cell"):
    return {"ir_object_type": kind, "ir_object_id": identity, "pdf_page_index": 0}


def source_row():
    return {"group_id": "row-synthetic", "label": "物质甲", "text": "物质甲 2030 合成企业 17.2 吨",
        "support_segments": [{"span_id": "row-text", "text": "物质甲 2030 合成企业 17.2 吨",
            "locators": [location("row-label")]}],
        "cells": [{"cell_id": "amount-cell", "span_id": "amount-span", "column": "c2", "raw": "17.2",
            "unit": "吨", "column_headers": ["2030", "合成企业"], "locators": [location("amount-cell")]}]}


def reading(fields=None):
    return {"source_id": "row-synthetic:1", "group_id": "row-synthetic", "kind": "quantitative",
        "source_row_label": "物质甲", "fields": {}, "semantics": {},
        "cells": [{"cell_id": "amount-cell", "column": "c2", "value": "17.2", "unit": "吨", "fields": fields or {}}]}


def context(uncertain=False):
    return {"bound_source_identity": {"document_id": "doc-synthetic", "ir_run_id": "ir-synthetic", "ir_revision": 1},
        "columns": [{"column": "c2", "alignment_uncertain": uncertain}]}


def test_verified_column_year_is_available_without_inventing_entity():
    cell = bind_source_row(reading(), source_row(), context())["cells"][0]
    assert cell["fields"] == {"reporting_period": "2030"}
    assert cell["value"] == "17.2" and cell["physical_sources"]


@pytest.mark.parametrize("fields", [{}, {"reporting_period": "2030", "reporting_entity": "合成企业"}])
def test_unreliable_column_alignment_never_becomes_asserted_year_or_entity(fields):
    cell = bind_source_row(reading(fields), source_row(), context(True))["cells"][0]
    assert cell["value"] == "17.2" and cell["unit"] == "吨" and cell["physical_sources"]
    assert not cell["fields"]
    assert cell["quality_status"] == "needs_confirmation"
    assert any(issue["code"] == "header_alignment_uncertain" for issue in cell["quality_issues"])
    if fields:
        assert cell["unverified_identity_raw"] == fields


def test_generated_table_row_quote_cannot_confirm_a_known_bad_header():
    expanded = reading({"reporting_period": "2030"})
    expanded["cells"][0]["field_refs"] = {"reporting_period": ["row-text"]}
    cell = bind_source_row(expanded, source_row(), context(True))["cells"][0]
    assert "reporting_period" not in cell["fields"]
    assert any(issue["code"] == "header_identity_unreliable" for issue in cell["quality_issues"])


def test_independent_body_period_remains_valid_while_bad_header_is_preserved():
    expanded = reading({"reporting_period": "2031"})
    expanded["cells"][0]["field_refs"] = {"reporting_period": ["body-period"]}
    ctx = {**context(True), "document_context": [{"span_id": "body-period", "text": "本表数据为2031年度。",
        "locators": [location("body", "block")]}]}
    cell = bind_source_row(expanded, source_row(), ctx)["cells"][0]
    assert cell["fields"]["reporting_period"] == "2031"
    assert cell["column_headers"] == ["2030", "合成企业"]
    assert cell["quality_status"] == "needs_confirmation"


def test_multilevel_header_under_unit_column_is_flagged_without_moving_literals():
    cells = [
        {"cell_id": "label", "row_index": 0, "col_index": 0, "text": "指标"},
        {"cell_id": "unit", "row_index": 0, "col_index": 1, "text": "单位"},
        {"cell_id": "year", "row_index": 0, "col_index": 2, "col_span": 2, "text": "2030"},
        {"cell_id": "shifted", "row_index": 1, "col_index": 1, "text": "合成企业"},
        {"cell_id": "value", "row_index": 2, "col_index": 2, "text": "17.2"},
    ]
    original = deepcopy(cells)
    derived = EvidenceInventoryBuilder._with_effective_column_headers(cells, [0, 1])
    assert all(cell.get("header_alignment_uncertain") for cell in derived)
    assert cells == original
    assert next(cell for cell in derived if cell["cell_id"] == "value")["text"] == "17.2"


def packet(label="物质甲", label_group="row-synthetic"):
    spans = [EvidenceSpan(span_id=identity, document_id="doc-synthetic", ir_run_id="ir-synthetic", ir_revision=1,
        span_type="table_cell", text=text, context_text=text, page_index=0, object_ids=[identity],
        locators=[SourceLocator(**location(identity))], context_group_id=group)
        for identity, text, group in [("label-span", label, label_group), ("amount-span", "17.2", "row-synthetic")]]
    return SimpleNamespace(task_id="task-synthetic", metric={"metric_id": "metric-synthetic"}, spans=spans,
        candidates=LiteralHarvester().harvest(spans), allowed_group_ids=["row-synthetic"],
        retrieval_complete=True, context_only_span_ids=[], page_image_paths=[],
        alias_map={"groups": {"G1": "row-synthetic"}, "spans": {"S1": "label-span", "S2": "amount-span"}, "visual": {}},
        model_context=json.dumps({"region": {"target_value_cells": [{"id": "T1", "group": "G1", "span": "S2",
            "cell_id": "amount-cell", "visible_value": "17.2"}]}}),
        elements=[{"element_id": "amount", "element_code": "amount", "binding": {"target": "value_raw"},
                   "value_contract": {"primary_type": "decimal"}, "required": True},
                  {"element_id": "pollutant", "element_code": "pollutant", "semantic_role": "dimension",
                   "binding": {"target": "pollutant"}, "value_contract": {"primary_type": "identifier"}}])


def interpretation(match="match", label="物质甲", conflict=False):
    return SourceInterpretation.model_validate({"source_rows": [{"source_id": "row-synthetic:1", "kind": "quantitative",
        "group": "G1", "source_row_label": label, "semantics": {"source_interpretation": {"label_conflict": conflict}},
        "cells": [{"target_cell": "T1", "value": "17.2"}],
        "metric_mappings": [{"metric_id": "metric-synthetic", "match": match, "reason": "Explicit synthetic semantic decision"}]}],
        "coverage": [{"metric_id": "metric-synthetic", "status": "reviewed", "reason": "Synthetic bounded review"}]})


def test_confirmed_pollutant_row_projects_literal_identity_with_same_row_evidence():
    evidence = packet()
    decision, audit = project_source_facts(interpretation(), evidence, visual_used=False)
    assert audit["projection_errors"] == []
    assigned = {item.element_id: item for item in decision.fact_groups[0].assignments}
    assert assigned["pollutant"].value_raw == "物质甲"
    assert assigned["pollutant"].evidence_span_ids == ["label-span"]
    assert assigned["pollutant"].support_axis == "pollutant"
    assert assigned["amount"].value_raw == "17.2"
    assert GroundingGuard().validate(evidence, decision).accepted


@pytest.mark.parametrize("match", ["different", "uncertain"])
def test_no_semantic_confirmation_never_creates_pollutant_facts(match):
    decision, _ = project_source_facts(interpretation(match), packet(), visual_used=False)
    assert decision.fact_groups == []


@pytest.mark.parametrize("label,label_group,conflict", [
    ("别的物质", "row-synthetic", False), ("物质甲", "unrelated-row", False),
    ("物质甲", "row-synthetic", True),
])
def test_row_identity_cannot_borrow_unrelated_label_or_hide_semantic_conflict(label, label_group, conflict):
    decision, _ = project_source_facts(interpretation(conflict=conflict), packet(label, label_group), visual_used=False)
    assert decision.fact_groups
    assert all(item.element_id != "pollutant" for item in decision.fact_groups[0].assignments)


def test_chemical_label_case_is_not_silently_normalized():
    decision, _ = project_source_facts(interpretation(label="CO"), packet(label="Co"), visual_used=False)
    assert decision.fact_groups
    assert all(item.element_id != "pollutant" for item in decision.fact_groups[0].assignments)


def test_same_row_header_view_bound_to_another_value_cell_is_not_borrowed():
    evidence = packet()
    evidence.spans[0].structural_context["anchor_cell_id"] = "another-value-cell"
    decision, _ = project_source_facts(interpretation(), evidence, visual_used=False)
    assert decision.fact_groups
    assert all(item.element_id != "pollutant" for item in decision.fact_groups[0].assignments)


def test_substring_of_another_row_label_is_not_a_new_pollutant_identity():
    evidence = packet(label="Copper compounds")
    evidence.spans[0].span_type = "table_row"
    evidence.spans[0].structural_context["cells"] = [{"row_header_path": ["Copper compounds"]}]
    decision, _ = project_source_facts(interpretation(label="Co"), evidence, visual_used=False)
    assert all(item.element_id != "pollutant" for item in decision.fact_groups[0].assignments)


def test_numeric_value_cell_is_not_a_pollutant_label():
    decision, _ = project_source_facts(interpretation(label="17.2"), packet(), visual_used=False)
    assert decision.fact_groups
    assert all(item.element_id != "pollutant" for item in decision.fact_groups[0].assignments)
