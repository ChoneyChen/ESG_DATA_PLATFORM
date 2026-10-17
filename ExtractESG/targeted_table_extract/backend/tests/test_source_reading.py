"""Synthetic, model-free source provenance and exact-value regressions."""
import json
from types import SimpleNamespace

import pytest

from esg_targeted.contracts import EvidenceSpan, SourceLocator, SemanticAssignment, SemanticDecision, SemanticFactGroup
from esg_targeted.evidence.harvester import LiteralHarvester
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.source_reading import bind_source_row, document_reading_context
from esg_targeted.evidence.target_cells import target_cell_contracts
from esg_targeted.models.source_facts import SourceProjectionCompiler
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.report_facts import source_facts


def locator(object_id, index=0):
    return {"ir_object_type": "block", "ir_object_id": object_id,
            "pdf_page_index": index, "printed_page_label": str(index + 1), "bbox": None}


def test_numeric_occurrence_does_not_replace_its_parent_statement_span():
    text = "集团2025年的天然气占比为14.1%。"
    source = {"span_id": "sentence", "text": text,
              "support_segments": [{"span_id": "sentence", "text": text, "locators": [locator("body")]}],
              "cells": [{"cell_id": "occurrence-1", "span_id": "sentence", "raw": "14.1%", "locators": [locator("body")]}]}
    expanded = {"kind": "assertion", "fields": {"reporting_period": "2025"},
                "field_refs": {"reporting_period": ["sentence"]},
                "statement_fragments": [{"ref": "sentence", "text": text}]}
    bound = bind_source_row(expanded, source, {})
    assert bound["quality_status"] == "grounded"
    assert bound["fields"]["reporting_period"] == "2025"
    assert bound["statement"] == text
    assert bound["physical_sources"][0]["evidence_excerpt"] == text
    assert source["cells"][0]["raw"] == "14.1%"


def test_conflicting_full_segments_cannot_silently_overwrite_a_citation():
    source = {"support_segments": [{"span_id": "same", "text": "原始披露", "locators": [locator("body")]}]}
    expanded = {"kind": "assertion", "fields": {}, "statement_fragments": [{"ref": "same", "text": "替代披露"}]}
    result = bind_source_row(expanded, source, {"linked_context": [{"span_id": "same", "text": "替代披露", "locators": [locator("other")]}]})
    assert result["quality_status"] == "unbound"
    assert result["physical_sources"] == []


def span(text, *, index=0, span_id="span-body", block_id="block-body", page_id=None):
    return EvidenceSpan(span_id=span_id, document_id="doc-synthetic", ir_run_id="ir-synthetic", ir_revision=1,
        span_type="block", text=text, context_text=text, page_index=index,
        object_ids=[block_id], locators=[SourceLocator(**locator(block_id, index))],
        context_group_id=f"block:{block_id}", structural_context={"page_id": page_id} if page_id else {})


def test_section_rows_do_not_cross_contaminate_column_headers():
    cells = [
        {"cell_id": "h0", "row_index": 0, "col_index": 0, "text": "指标"},
        {"cell_id": "h1", "row_index": 0, "col_index": 1, "text": "2023"},
        {"cell_id": "h2", "row_index": 0, "col_index": 2, "text": "2024"},
        {"cell_id": "finance", "row_index": 1, "col_index": 0, "col_span": 3, "text": "财务与运营"},
        {"cell_id": "r", "row_index": 2, "col_index": 0, "text": "收入"},
        {"cell_id": "v1", "row_index": 2, "col_index": 1, "text": "100", "column_header_path": ["2023"]},
        {"cell_id": "env", "row_index": 3, "col_index": 0, "col_span": 3, "text": "环境"},
        {"cell_id": "g", "row_index": 4, "col_index": 0, "text": "排放合计"},
        {"cell_id": "v2", "row_index": 4, "col_index": 1, "text": "1.79", "column_header_path": ["2023"]},
    ]
    result = {c["cell_id"]: c for c in EvidenceInventoryBuilder._with_effective_column_headers(cells, [0, 1])}
    assert result["v2"]["effective_column_header_path"] == ["2023"]
    assert result["v2"]["section_header_path"] == ["环境"]
    assert result["v1"]["section_header_path"] == ["财务与运营"]
    assert "section_header_path" not in cells[-1]


def test_logical_continuation_uses_nearest_header_band():
    ir = SimpleNamespace(logical_tables=[{"cells": [
        {"is_header": True, "row_index": 0, "col_index": 1, "text": "2023"},
        {"is_header": True, "row_index": 10, "col_index": 1, "text": "2024"}],
        "cell_mappings": [{"source_cell_id": "c1", "logical_row_index": 2, "logical_col_index": 1},
                          {"source_cell_id": "c2", "logical_row_index": 11, "logical_col_index": 1}]}])
    assert EvidenceInventoryBuilder._logical_headers(ir) == {"c1": ["2023"], "c2": ["2024"]}


def test_page_region_names_real_ir_page_instead_of_zero_based_alias():
    assert EvidenceRegionCompiler._region_key([span("正文", index=6)])[1] == "page-0007"
    assert EvidenceRegionCompiler._region_key([span("正文", index=6, page_id="page-real-id")])[1] == "page-real-id"


def narrative_packet():
    body = span("2024年甲烷强度0.25%，全年电量8.2万度。")
    candidates = LiteralHarvester().harvest([body])
    targets = EvidenceRegionCompiler._target_value_cells([body], candidates, [], {body.span_id: "S1"}, {body.context_group_id: "G1"})
    return SimpleNamespace(metric={"metric_id": "synthetic.amount"}, spans=[body], candidates=candidates,
        elements=[{"element_id": "amount", "element_code": "amount", "binding": {"target": "value_raw"},
                   "value_contract": {"primary_type": "decimal"}}],
        alias_map={"groups": {"G1": body.context_group_id}, "spans": {"S1": body.span_id}, "visual": {}},
        context_only_span_ids=[], model_context=json.dumps({"region": {"target_value_cells": targets}})), targets


def test_narrative_numeric_targets_keep_occurrence_and_cannot_swap_same_paragraph_values():
    packet, targets = narrative_packet()
    assert len(targets) == 2  # the year is identity context, not an amount target
    assert all(t["candidate_id"] and t["span_id"] and t["char_end"] > t["char_start"] for t in targets)
    percent = next(t for t in target_cell_contracts(packet) if "0.25" in t.value_forms)
    assert "8.2" not in percent.value_forms
    compiler = SourceProjectionCompiler(packet, visual_used=False)
    valid = compiler.bind({"group": "G1", "target_cell": percent.alias, "fields": {"amount": "0.25"}})
    assert valid.group_ref_id == percent.cell_id
    with pytest.raises(ValueError, match="allowed|match|compatible|outside"):
        compiler.bind({"group": "G1", "target_cell": percent.alias, "fields": {"amount": "8.2"}})


def row_context():
    return {"group_id": "physical-row", "text": "排放量", "locators": [locator("block-label")],
        "support_segments": [{"span_id": "s-row", "text": "排放量", "locators": [locator("block-label")]}],
        "cells": [{"cell_id": "c-value", "span_id": "s-value", "column": "c1", "raw": "1.80",
                   "unit": "吨", "column_headers": ["2024"], "locators": [locator("cell-value")]}]}


def expanded_quantity(fields=None, cell_fields=None):
    return {"source_id": "physical-row:1", "group_id": "physical-row", "kind": "quantitative",
            "source_row_label": "排放量", "fields": fields or {}, "semantics": {},
            "cells": [{"cell_id": "c-value", "column": "c1", "value": "1.80", "unit": "吨",
                       "fields": cell_fields or {}, "column_headers": ["2024"]}]}


def test_wrong_year_keys_are_preserved_unverified_without_corrupting_numeric_value():
    row = bind_source_row(expanded_quantity(cell_fields={"2024": "2024", "2023": "1.79"}), row_context(), {})
    cell = row["cells"][0]
    assert cell["value"] == "1.80"
    assert cell["identity_fields"] == {}
    assert cell["unverified_identity_raw"] == {"2024": "2024", "2023": "1.79"}
    assert cell["quality_status"] == "needs_confirmation"


@pytest.mark.parametrize("cell_fields,missing_axes", [({}, {"reporting_period", "reporting_entity"}),
    ({"reporting_period": "2024"}, {"reporting_entity"})])
def test_known_value_with_omitted_identity_stays_needs_confirmation_without_defaults(cell_fields, missing_axes):
    row = bind_source_row(expanded_quantity(cell_fields=cell_fields), row_context(), {})
    cell = row["cells"][0]
    assert cell["value"] == "1.80" and cell["physical_sources"]
    assert cell["quality_status"] == "needs_confirmation"
    assert {issue["axis"] for issue in cell["quality_issues"] if issue["code"] == "identity_axis_unknown"} == missing_axes
    assert not any(axis in cell["identity_fields"] for axis in missing_axes)
    source = {"source_key": "source", "object_id": "table-1", "version": "v", "rows": [row]}
    fact = source_facts(source, {"document_id": "doc", "ir_run_id": "ir", "ir_revision": 1}, {"rows": [row_context()]})[0]
    assert fact["quality_status"] == "needs_confirmation" and fact["quality_issues"] == cell["quality_issues"]


def test_entity_and_period_need_real_evidence_and_do_not_borrow_global_year():
    expanded = expanded_quantity(cell_fields={"reporting_period": "2024", "reporting_entity": "真实集团"})
    expanded["cells"][0]["field_refs"] = {"reporting_entity": ["s-document"]}
    document = {"span_id": "s-document", "text": "报告主体：真实集团；报告期间2025年。", "locators": [locator("block-document", 1)]}
    row = bind_source_row(expanded, row_context(), {"document_context": [document]})
    assert row["cells"][0]["identity_fields"] == {"reporting_period": "2024", "reporting_entity": "真实集团"}
    assert row["cells"][0]["identity_evidence"]["reporting_entity"][0]["locators"][0]["ir_object_id"] == "block-document"
    wrong = expanded_quantity(cell_fields={"reporting_period": "2025"})
    wrong["cells"][0]["field_refs"] = {"reporting_period": ["s-document"]}
    checked = bind_source_row(wrong, row_context(), {"document_context": [document]})["cells"][0]
    assert "reporting_period" not in checked["identity_fields"]
    assert any(issue["code"] == "period_header_conflict" for issue in checked["quality_issues"])


def test_unit_guess_is_retained_unverified_and_reported_unit_is_preserved():
    expanded = expanded_quantity()
    expanded["cells"][0]["unit"] = "元"
    cell = bind_source_row(expanded, row_context(), {})["cells"][0]
    assert cell["unit"] == "吨" and cell["value"] == "1.80"
    assert cell["unverified_identity_raw"]["unit"] == "元"
    assert any(issue["code"] == "unit_unbound" for issue in cell["quality_issues"])


def test_unit_prefix_cannot_be_cut_off_or_borrowed_from_an_english_word():
    physical = row_context()
    physical["cells"][0]["unit"] = "万吨"
    expanded = expanded_quantity()
    cell = bind_source_row(expanded, physical, {})["cells"][0]
    assert cell["value"] == "1.80" and cell["unit"] == "万吨"
    assert cell["unverified_identity_raw"]["unit"] == "吨"
    physical["cells"][0]["unit"] = None
    physical["support_segments"][0]["text"] = "采用GHG Protocol报告排放量。"
    expanded["cells"][0]["unit"] = "t"
    cell = bind_source_row(expanded, physical, {})["cells"][0]
    assert cell["unit"] is None and cell["unverified_identity_raw"]["unit"] == "t"


def test_binder_cannot_ground_a_rewritten_mechanical_value_on_an_existing_cell():
    expanded = expanded_quantity()
    expanded["cells"][0]["value"] = "1.88"
    cell = bind_source_row(expanded, row_context(), {})["cells"][0]
    assert cell["quality_status"] == "unbound" and cell["physical_sources"] == []
    assert any(issue["code"] == "mechanical_value_unbound" and issue["original_value"] == "1.80"
               for issue in cell["quality_issues"])


@pytest.mark.parametrize("missing", ["reliability", "image", "scope", "bbox"])
def test_changed_visual_value_requires_real_supplied_scope_and_uncertain_grid(missing):
    physical = row_context()
    physical["cells"][0]["grid_reliable"] = False
    physical["cells"][0]["locators"][0]["bbox"] = {"x0": 10, "y0": 10, "x1": 20, "y1": 20}
    context = {"visual_evidence_supplied": True, "visual_evidence_group_ids": [physical["group_id"]]}
    if missing == "reliability":
        physical["cells"][0]["grid_reliable"] = True
    elif missing == "image":
        context["visual_evidence_supplied"] = False
    elif missing == "scope":
        context["visual_evidence_group_ids"] = ["different-row"]
    else:
        physical["cells"][0]["locators"][0]["bbox"] = None
    expanded = expanded_quantity()
    expanded["cells"][0].update(value="1.88", visual_override=True)
    cell = bind_source_row(expanded, physical, context)["cells"][0]
    assert cell["quality_status"] == "unbound" and cell["physical_sources"] == []
    assert any(issue["code"] == "visual_value_unbound" for issue in cell["quality_issues"])


def test_genuine_local_visual_grid_correction_keeps_exact_value_and_locator():
    physical = row_context()
    physical["cells"][0]["grid_reliable"] = False
    physical["cells"][0]["locators"][0]["bbox"] = {"x0": 10, "y0": 10, "x1": 20, "y1": 20}
    expanded = expanded_quantity()
    expanded["cells"][0].update(value="1.88", visual_override=True)
    cell = bind_source_row(expanded, physical, {"visual_evidence_supplied": True,
        "visual_evidence_group_ids": [physical["group_id"]]})["cells"][0]
    assert cell["quality_status"] != "unbound" and cell["value"] == "1.88"
    assert cell["physical_sources"][0]["ir_object_id"] == "cell-value"


def test_unlisted_visual_cell_requires_unreliable_row_with_actual_bbox():
    physical = row_context()
    physical["grid_reliable"] = False
    physical["locators"][0]["bbox"] = {"x0": 5, "y0": 5, "x1": 25, "y1": 25}
    expanded = expanded_quantity()
    expanded["cells"] = [{"cell_id": None, "column": "visual-extra", "value": "42", "unit": None,
                          "fields": {}, "visual_override": True}]
    context = {"visual_evidence_supplied": True, "visual_evidence_group_ids": [physical["group_id"]]}
    cell = bind_source_row(expanded, physical, context)["cells"][0]
    assert cell["quality_status"] == "needs_confirmation" and cell["value"] == "42"
    physical["grid_reliable"] = True
    assert bind_source_row(expanded, physical, context)["cells"][0]["quality_status"] == "unbound"


def test_assertion_fragments_use_body_locators_and_preserve_both_original_passages():
    physical = row_context()
    physical["support_segments"].append({"span_id": "s-body", "text": "采用实际测量。", "locators": [locator("block-body", 4)]})
    linked = {"span_id": "s-note", "text": "注：范围覆盖国内外。", "locators": [locator("block-note", 5)]}
    expanded = {"source_id": "physical-row:2", "group_id": "physical-row", "kind": "assertion", "fields": {},
                "statement": "采用实际测量；范围覆盖国内外", "statement_fragments": [
                    {"ref": "s-body", "text": "采用实际测量。"}, {"ref": "s-note", "text": "范围覆盖国内外。"}], "cells": []}
    row = bind_source_row(expanded, physical, {"linked_context": [linked]})
    assert row["statement"] == "采用实际测量。\n范围覆盖国内外。"
    assert [loc["ir_object_id"] for loc in row["physical_sources"]] == ["block-body", "block-note"]
    assert row["quality_status"] == "grounded"
    assert "block-label" not in {loc["ir_object_id"] for loc in row["physical_sources"]}


def test_invalid_assertion_reference_is_a_visible_unbound_candidate_not_title_fact():
    expanded = {"source_id": "physical-row:2", "group_id": "physical-row", "kind": "assertion", "fields": {},
                "statement": "未出现的说明", "statement_fragments": [{"ref": "fake", "text": "未出现的说明"}], "cells": []}
    row = bind_source_row(expanded, row_context(), {})
    source = {"source_key": "stable-source", "object_id": "page-0001", "version": "synthetic-v1", "rows": [row]}
    facts = source_facts(source, {"document_id": "doc", "ir_run_id": "ir", "ir_revision": 1}, {"rows": [row_context()]})
    assert facts[0]["physical_sources"] == [] and facts[0]["quality_status"] == "unbound"
    assert facts[0]["statement_raw"] == "未出现的说明" and facts[0]["review_status"] == "needs_confirmation"


def test_explicit_fragments_preserve_case_of_reported_characters():
    physical = row_context()
    physical["support_segments"] = [{"span_id": "s-body", "text": "采用 GHG Protocol。", "locators": [locator("body")]}]
    expanded = {"source_id": "physical-row:2", "group_id": "physical-row", "kind": "assertion", "fields": {},
                "statement": "采用 ghg Protocol。", "statement_fragments": [{"ref": "s-body", "text": "采用 ghg Protocol。"}], "cells": []}
    row = bind_source_row(expanded, physical, {})
    assert row["quality_status"] == "unbound" and row["physical_sources"] == []
    expanded["statement_fragments"][0]["text"] = "采用GHG Protocol。"
    assert bind_source_row(expanded, physical, {})["quality_status"] == "grounded"


def test_fragment_guard_rejects_case_rewrite_even_if_legacy_text_matching_accepts_it():
    packet = guard_packet()
    packet.spans[0].text = "采用 GHG Protocol。"
    assignment = SemanticAssignment(element_id="statement", source_ref_id="s-body", evidence_span_ids=["s-body"],
        value_raw="采用 ghg Protocol。", support_fragments=[{"span_id": "s-body", "text": "采用 ghg Protocol。"}])
    guard = GroundingGuard().validate(packet, decision_with(packet, [assignment]))
    assert not guard.accepted and any(issue.code == "support_fragment_invalid" for issue in guard.issues)


def test_context_consumption_does_not_create_fake_assertion_facts():
    assert source_facts({"source_key": "s", "object_id": "o", "version": "v", "rows": [
        {"group_id": "g", "source_id": "g:1", "kind": "context", "context_role": "section"}]},
        {"document_id": "doc", "ir_run_id": "ir", "ir_revision": 1}, {"rows": []}) == []


def test_same_raw_with_revised_report_identity_creates_immutable_source_revision():
    identity = {"document_id": "doc", "ir_run_id": "ir", "ir_revision": 1}
    context = {"rows": [row_context()]}
    source = {"source_key": "frozen-evidence-key", "object_id": "table-1", "version": "synthetic-v1",
              "rows": [expanded_quantity(cell_fields={"reporting_period": "2024"})]}
    first = source_facts(source, identity, context)[0]
    assert first == source_facts(source, identity, context)[0]
    source["rows"][0]["cells"][0]["fields"]["reporting_period"] = "2023"
    revised = source_facts(source, identity, context)[0]
    assert first["value_raw"] == revised["value_raw"] == "1.80"
    assert first["source_anchor_id"] == revised["source_anchor_id"]
    assert first["source_fact_id"] != revised["source_fact_id"]
    assert first["interpretation_digest"] != revised["interpretation_digest"]
    assert first["identity_raw"] == {"reporting_period": "2024"}


def test_document_context_is_bounded_verbatim_front_matter_not_filename_inference():
    inventory = SimpleNamespace(spans=[span("报告主体：真实集团。", span_id="s-front"),
                                      span("后文公司示例", index=10, span_id="s-later")])
    context = document_reading_context(inventory, max_chars=8)
    assert len(context) == 1 and context[0]["text"] == "报告主体：真实集"
    assert context[0]["truncated"] and context[0]["locators"][0]["ir_object_id"] == "block-body"


def guard_packet():
    body = span("采用实际测量。", span_id="s-body", block_id="body")
    note = span("注：范围覆盖国内外。报告期间2024年。", index=1, span_id="s-note", block_id="note")
    elements = [{"element_id": "statement", "element_code": "statement", "binding": {"target": "statement_raw", "record_type": "qualitative_assertion"},
                 "value_contract": {"primary_type": "text"}, "semantic_role": "primary"},
                {"element_id": "period", "element_code": "reporting_period", "binding": {"target": "reporting_period_raw"},
                 "value_contract": {"primary_type": "year"}, "semantic_role": "period"}]
    return SimpleNamespace(task_id="task-proof", allowed_group_ids=[body.context_group_id], spans=[body, note], candidates=[], elements=elements,
        alias_map={"groups": {"G1": body.context_group_id}, "spans": {"S1": body.span_id, "S2": note.span_id}, "visual": {}},
        context_only_span_ids=[note.span_id], model_context='{}', retrieval_complete=False, page_image_paths=[], metric={"metric_id": "synthetic.method"})


def decision_with(packet, assignments):
    return SemanticDecision(task_id=packet.task_id, status="found", fact_groups=[SemanticFactGroup(
        group_ref_id=packet.allowed_group_ids[0], assignments=assignments)],
        selected_evidence_span_ids=list(dict.fromkeys(sid for a in assignments for sid in a.evidence_span_ids)))


def test_exact_fragment_proof_allows_multisegment_assertion_without_broad_cross_group_permission():
    packet = guard_packet()
    fragments = [{"span_id": "s-body", "text": "采用实际测量。"}, {"span_id": "s-note", "text": "范围覆盖国内外。"}]
    assignment = SemanticAssignment(element_id="statement", source_ref_id="s-body", evidence_span_ids=["s-body", "s-note"],
        value_raw="采用实际测量。\n范围覆盖国内外。", support_fragments=fragments)
    assert GroundingGuard().validate(packet, decision_with(packet, [assignment])).accepted
    fabricated = assignment.model_copy(update={"value_raw": "采用实际测量。\n使用市场法。"})
    rejected = GroundingGuard().validate(packet, decision_with(packet, [fabricated]))
    assert not rejected.accepted and any(issue.code == "support_fragment_invalid" for issue in rejected.issues)


def test_period_context_needs_exact_matching_axis_proof_and_extra_ref_is_rejected():
    packet = guard_packet()
    statement = SemanticAssignment(element_id="statement", source_ref_id="s-body", evidence_span_ids=["s-body"], value_raw="采用实际测量。")
    period = SemanticAssignment(element_id="period", source_ref_id="s-note", evidence_span_ids=["s-note"], value_raw="2024",
        support_axis="reporting_period", support_fragments=[{"span_id": "s-note", "text": "2024"}])
    assert GroundingGuard().validate(packet, decision_with(packet, [statement, period])).accepted
    for bad in [period.model_copy(update={"support_axis": "reporting_entity"}),
                period.model_copy(update={"evidence_span_ids": ["s-note", "s-body"]}),
                period.model_copy(update={"support_fragments": []})]:
        assert not GroundingGuard().validate(packet, decision_with(packet, [statement, bad])).accepted
