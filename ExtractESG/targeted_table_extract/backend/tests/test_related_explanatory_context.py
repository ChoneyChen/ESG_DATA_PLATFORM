"""Anonymous, model-free standard relation and footnote provenance checks."""
import json
from copy import deepcopy

import pytest

from esg_targeted.contracts import EvidenceInventory, EvidencePacket, EvidenceSpan, MetricQuery, QueryIntent, SourceLocator
from esg_targeted.evidence.harvester import LiteralHarvester
from esg_targeted.evidence.related_context import linked_explanatory_packet
from esg_targeted.models.source_facts import SourceInterpretation, project_source_facts
from esg_targeted.grounding.guard import GroundingGuard


def span(identity, text, *, kind="block", object_id=None, group=None, notes=(), index=1):
    block_id = object_id or identity
    locator_kind = "cell" if kind == "table_cell" else "page" if kind == "page_text" else "block"
    return EvidenceSpan(span_id=identity, document_id="doc-synthetic", ir_run_id="ir-synthetic", ir_revision=2,
        span_type=kind, text=text, context_text=text, page_index=index, object_ids=[block_id],
        context_group_id=group or f"block:{block_id}",
        locators=[SourceLocator(ir_object_type=locator_kind, ir_object_id=block_id, pdf_page_index=index)],
        has_table_structure=kind == "table_cell",
        structural_context={"page_id": f"page-real-{index}", "footnote_block_ids": list(notes)})


def packet(spans=(), *, metric_id="method-synthetic", context_only=()):
    groups = list(dict.fromkeys(item.context_group_id for item in spans if item.span_id not in context_only))
    return EvidencePacket(packet_id="packet-synthetic", task_id="task-synthetic", metric={"metric_id": metric_id},
        elements=[{"element_id": "method-statement", "element_code": "method-statement",
            "binding": {"target": "statement_raw"}, "value_contract": {"primary_type": "text"}, "required": True}],
        query=MetricQuery(metric_id=metric_id, query_text="synthetic", lexical_text="synthetic", semantic_text="synthetic",
            lexical_terms=[], data_class="qualitative", element_ids=["method-statement"], intent=QueryIntent()),
        spans=list(spans), candidates=[], retrieval_hits=[], allowed_group_ids=groups,
        context_only_span_ids=list(context_only), page_image_paths=[], retrieval_complete=True, truncated=False,
        model_context=json.dumps({"metric": {"metric_id": metric_id}, "region": {"target_value_cells": []}}),
        alias_map={"groups": {f"G{i}": value for i, value in enumerate(groups, 1)},
            "spans": {f"S{i}": value.span_id for i, value in enumerate(spans, 1)},
            "context": {f"L{i}": value for i, value in enumerate(context_only, 1)},
            "elements": {"E1": "method-statement"}, "visual": {"V1": "existing-real-crop"}},
        source_document_id="doc-synthetic", source_ir_run_id="ir-synthetic", source_ir_revision=2)


def inventory(spans):
    return EvidenceInventory(inventory_id="inventory-synthetic", document_id="doc-synthetic",
        ir_run_id="ir-synthetic", ir_revision=2, spans=list(spans),
        candidates=LiteralHarvester().harvest(list(spans)), page_images={}, stats={})


def linked_sources():
    measured = span("amount", "17.2", kind="table_cell", object_id="cell-amount", group="measured-row",
                    notes=["block-note"], index=3)
    note = span("note", "方法注：使用经校准的仪表直接测量，每年校准2次。", object_id="block-note", index=3)
    sentence = span("note-sentence", "每年校准2次。", kind="sentence", object_id="block-note", index=3)
    neighbor = span("neighbor", "其他事项产生92次检查。", object_id="block-neighbor", index=3)
    page = span("aggregate", "同页所有内容：方法、检查与其他表格。", kind="page_text", object_id="page-real-3", index=3)
    return measured, note, sentence, neighbor, page


def test_only_exact_linked_block_becomes_an_independent_readable_source():
    measured, note, sentence, neighbor, page = linked_sources()
    method = packet()
    related = packet([measured], metric_id="measurement-synthetic")
    source_inventory = inventory([measured, note, sentence, neighbor, page])
    snapshots = [deepcopy(value.model_dump(mode="json")) for value in (method, related, source_inventory)]
    result = linked_explanatory_packet(method, [related], source_inventory)
    assert {item.span_id for item in result.spans} == {"note", "note-sentence"}
    assert result.allowed_group_ids == ["block:block-note"]
    assert all(item.structural_context["reading_object_id"] == "block-note" for item in result.spans)
    assert all(item.structural_context["reading_object_type"] == "block" for item in result.spans)
    assert all(item.structural_context["linked_explanatory_metrics"] == ["measurement-synthetic"] for item in result.spans)
    assert all(loc.ir_object_id == "block-note" and loc.ir_object_type == "block" for item in result.spans for loc in item.locators)
    assert result.metric == method.metric and result.query == method.query
    assert result.retrieval_complete == method.retrieval_complete
    assert "92" not in {item.raw_value for item in result.candidates}
    assert [value.model_dump(mode="json") for value in (method, related, source_inventory)] == snapshots


def test_existing_context_is_promoted_without_resetting_references_and_is_idempotent():
    measured, note, sentence, neighbor, page = linked_sources()
    existing = span("existing", "既有方法来源", object_id="block-existing")
    method = packet([existing, note], context_only=["note"])
    method.alias_map["groups"] = {"G7": existing.context_group_id}
    method.alias_map["spans"] = {"S9": existing.span_id, "S3": note.span_id}
    linked_inventory = inventory([measured, note, sentence, neighbor, page])
    result = linked_explanatory_packet(method, [packet([measured], metric_id="measurement-synthetic")], linked_inventory)
    assert result.context_only_span_ids == [] and result.alias_map["context"] == {}
    assert result.alias_map["groups"]["G7"] == existing.context_group_id
    assert result.alias_map["spans"]["S3"] == note.span_id
    assert result.alias_map["spans"]["S9"] == existing.span_id
    assert result.alias_map["elements"] == method.alias_map["elements"]
    assert result.alias_map["visual"] == method.alias_map["visual"]
    assert len(set(result.alias_map["spans"].values())) == len(result.alias_map["spans"])
    again = linked_explanatory_packet(result, [packet([measured], metric_id="measurement-synthetic")], linked_inventory)
    assert again.model_dump(mode="json") == result.model_dump(mode="json")


def test_unselected_or_context_only_measurement_does_not_activate_its_footnotes():
    selected = span("selected", "17.2", kind="table_cell", group="selected-row", notes=["selected-note"])
    omitted = span("omitted", "12.1", kind="table_cell", group="omitted-row", notes=["omitted-note"])
    background = span("background", "8.1", kind="table_cell", group="selected-row", notes=["background-note"])
    related = packet([selected, omitted, background], metric_id="measurement-synthetic", context_only=["background"])
    related.allowed_group_ids = ["selected-row"]
    notes = [span(identity, "真实方法注", object_id=identity) for identity in
             ("selected-note", "omitted-note", "background-note")]
    result = linked_explanatory_packet(packet(), [related], inventory([selected, omitted, background, *notes]))
    assert result.budget["linked_explanatory_evidence"]["block_ids"] == ["selected-note"]
    assert {item.span_id for item in result.spans} == {"selected-note"}


@pytest.mark.parametrize("foreign_location", ["method", "related", "span"])
def test_links_cannot_cross_report_or_fixed_ir_revision(foreign_location):
    measured, note, sentence, neighbor, page = linked_sources()
    method, related = packet(), packet([measured], metric_id="measurement-synthetic")
    if foreign_location == "method":
        method.source_document_id = "different-document"
    elif foreign_location == "related":
        related.source_ir_revision = 1
    else:
        related.spans[0].ir_run_id = "different-run"
    with pytest.raises(ValueError, match="same document|another fixed IR"):
        linked_explanatory_packet(method, [related], inventory([measured, note, sentence, neighbor, page]))


def test_missing_or_mistyped_ir_objects_remain_unresolved_without_invented_sources():
    measured = span("amount", "17.2", kind="table_cell", group="measurement-row",
                    notes=["not-a-block", "orphan-block", "missing-block", "mismatched-locator"])
    impostor = span("impostor", "17.2", kind="table_cell", object_id="not-a-block")
    orphan = span("orphan", "孤立短句", kind="sentence", object_id="orphan-block")
    wrong_locator = span("wrong-locator", "另一真实块的注释", object_id="real-other-block")
    wrong_locator.object_ids = ["mismatched-locator"]
    result = linked_explanatory_packet(packet(), [packet([measured])], inventory([measured, impostor, orphan, wrong_locator]))
    assert result.spans == [] and result.allowed_group_ids == []
    assert result.budget["linked_explanatory_evidence"]["unresolved_block_ids"] == sorted(measured.structural_context["footnote_block_ids"])


def test_duplicate_selected_measurements_share_true_footnote_without_repeating_evidence():
    measured, note, sentence, neighbor, page = linked_sources()
    second = measured.model_copy(update={"span_id": "second-amount", "context_group_id": "second-row"}, deep=True)
    related = [packet([measured], metric_id="measurement-one"), packet([second], metric_id="measurement-two")]
    result = linked_explanatory_packet(packet(), related, inventory([measured, second, note, sentence, neighbor, page]))
    assert len(result.spans) == 2
    assert all(item.structural_context["linked_explanatory_metrics"] == ["measurement-one", "measurement-two"] for item in result.spans)
    assert len({item.candidate_id for item in result.candidates}) == len(result.candidates)


def test_packet_metadata_cannot_invent_a_link_absent_from_fixed_ir():
    measured = span("amount", "17.2", kind="table_cell", group="measurement-row")
    altered = measured.model_copy(update={"structural_context": {"footnote_block_ids": ["block-note"]}}, deep=True)
    note = span("note", "已有真实块，但与此表没有 IR 关联。", object_id="block-note")
    method = packet()
    assert linked_explanatory_packet(method, [packet([altered])], inventory([measured, note])) is method


def test_linked_method_statement_can_bind_to_real_block_after_explicit_semantic_mapping():
    measured, note, sentence, neighbor, page = linked_sources()
    result = linked_explanatory_packet(packet(), [packet([measured], metric_id="measurement-synthetic")],
                                       inventory([measured, note, sentence, neighbor, page]))
    group = next(alias for alias, value in result.alias_map["groups"].items() if value == note.context_group_id)
    source = {"source_id": "method-source", "kind": "assertion", "group": group,
        "statement": note.text, "statement_span_ids": [note.span_id],
        "statement_fragments": [{"ref": note.span_id, "text": note.text}],
        "metric_mappings": [{"metric_id": result.metric["metric_id"], "match": "match", "reason": "Explicit synthetic method judgment"}]}
    interpretation = SourceInterpretation.model_validate({"source_rows": [source],
        "coverage": [{"metric_id": result.metric["metric_id"], "status": "reviewed", "reason": "Synthetic complete review"}]})
    decision, audit = project_source_facts(interpretation, result, visual_used=False)
    assert audit["projection_errors"] == []
    assert decision.fact_groups[0].assignments[0].value_raw == note.text
    assert decision.fact_groups[0].assignments[0].evidence_span_ids == [note.span_id]
    assert GroundingGuard().validate(result, decision).accepted
    source["metric_mappings"][0]["match"] = "uncertain"
    uncertain = SourceInterpretation.model_validate({"source_rows": [source], "coverage": interpretation.coverage})
    assert project_source_facts(uncertain, result, visual_used=False)[0].fact_groups == []


def test_missing_inventory_or_no_selected_links_is_a_noop():
    method = packet([span("existing", "既有来源")])
    assert linked_explanatory_packet(method, [], None) is method
    assert linked_explanatory_packet(method, [packet()], inventory([])) is method
