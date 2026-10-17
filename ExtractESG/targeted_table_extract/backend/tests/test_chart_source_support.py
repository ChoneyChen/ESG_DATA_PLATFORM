"""Offline chart source contracts; structure acceptance is not point proof."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from esg_targeted.contracts import EvidenceSpan, ExtractionRequest, SourceLocator, SemanticAssignment
from esg_targeted.evidence.chart_support import CHART_READING_VERSION, POINT_TEXT_POLICY, chart_policy, legacy_chart_source
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.evidence.harvester import LiteralHarvester
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.source_reading import bind_source_row
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.ir.loader import LoadedDocumentIr
from esg_targeted.models.report_semantics import TableUnderstanding, expand_understanding, validate_understanding_payload, understanding_template, reference_catalog
from esg_targeted.report_facts import source_facts
from esg_targeted.storage.artifacts import ArtifactStore
from tests.test_inventory_and_guard import build_pipeline


BBOX = {"x0": 10, "y0": 10, "x1": 90, "y1": 30, "unit": "pt", "origin": "top_left"}
FIGURE_BBOX = {"x0": 0, "y0": 0, "x1": 100, "y1": 100, "unit": "pt", "origin": "top_left"}


def chart_ir(tmp_path, status="source_context_only", *, kind="block", category="Sample A", text=None):
    text = text or f"{category} 125.6 t"
    ref = "point-text"
    source = {"text": text, "bbox": deepcopy(BBOX), "page_index": 0,
              "block_id" if kind == "block" else "cell_id": ref}
    support = {"series_index": 0, "point_index": 0, "category": category, "value": 125.6,
               "status": status, "supported_references": [{"ref": ref, "source_object_id": "figure-a",
                   "source_quote": text, "bbox": deepcopy(BBOX)}] if status == "exact_text" else [],
               "context_references": [], "errors": ["reference_other_figure"] if status == "scope_conflict" else []}
    chart = {"chart_type": "bar", "source_figure_ids": ["figure-a"],
             "series": [{"name": "Example series", "points": [{"category": category, "value": 125.6,
                 "evidence_refs": [ref] if status == "exact_text" else []}]}],
             "evidence_support": {"schema_version": "chart-point-source-support-v1", "quality": "point_supported" if status == "exact_text" else "context_only",
                 "points": [support], "visual_reference_errors": [], "visual_pixel_values_verified": False,
                 "series_metadata_verified": False}}
    figure = {"figure_id": "figure-a", "page_index": 0, "bbox": deepcopy(FIGURE_BBOX),
              "visual_status": "reviewed", "quality_flags": ["agent_chart_structure_reviewed"],
              "chart_spec": chart}
    page = {"page": {"page_index": 0, "page_id": "page-a"}, "blocks": [source] if kind == "block" else []}
    return LoadedDocumentIr(root=tmp_path, manifest={"run_id": "ir-chart", "document_id": "doc-chart", "ir_revision": 2},
        document={}, sections_by_id={}, pages=[page], tables=[{"table_id": "table-a", "page_index": 0, "cells": [source]}] if kind == "cell" else [],
        figures=[figure], artifacts=[])


def chart_packet(ir):
    spans = EvidenceInventoryBuilder()._figure_spans(ir, {0: ir.pages[0]["page"]}, {"figure-a": "supplied-chart.png"})
    candidates = LiteralHarvester().harvest(spans)
    groups = {f"G{i}": span.context_group_id for i, span in enumerate(spans, 1)}
    span_refs = {f"S{i}": span.span_id for i, span in enumerate(spans, 1)}
    targets = EvidenceRegionCompiler._target_value_cells(spans, candidates, [],
        {v: k for k, v in span_refs.items()}, {v: k for k, v in groups.items()})
    packet = SimpleNamespace(spans=spans, candidates=candidates, allowed_group_ids=list(groups.values()),
        alias_map={"groups": groups, "spans": span_refs, "visual": {"V1": spans[0].span_id}},
        model_context=json.dumps({"region": {"target_value_cells": targets}}))
    session = DisclosureSession.__new__(DisclosureSession)
    session.inventory = None
    obj = session._objects(packet)["figure-a"]
    return packet, obj, targets


def visual_context(obj):
    return {**obj, "visual_evidence_supplied": True,
            "visual_evidence_group_ids": [row["group_id"] for row in obj["rows"]],
            "source_capabilities": {"source_kind": "figure"}}


def visual_output(obj, value="125.6"):
    return TableUnderstanding.model_validate({"rows": [{"group": obj["rows"][0]["group"],
        "kind": "quantitative", "source_row_label": "Sample A"}],
        "columns": [{"column": "visual-slot-1"}],
        "cell_overrides": [{"group": obj["rows"][0]["group"], "column": "visual-slot-1", "value": value,
                            "reason": "Independent reading of the supplied image"}]})


@pytest.mark.parametrize("status", ["source_context_only", "unsupported", "scope_conflict"])
def test_unsupported_ledger_values_never_generate_narrative_targets(tmp_path, status):
    ir = chart_ir(tmp_path, status)
    before = deepcopy(ir.figures[0]["chart_spec"])
    packet, obj, targets = chart_packet(ir)
    assert not packet.candidates and not targets
    assert "125.6" not in packet.spans[0].text
    assert packet.spans[0].structural_context["chart_spec"] == before
    assert obj["rows"][0]["cells"] == []
    assert obj["rows"][0]["support_segments"] == []
    assert len(obj["columns"]) == 8
    assert all(column["column"].startswith("visual-slot-") for column in obj["columns"])


def test_numeric_presence_without_category_pair_does_not_create_chart_point_text(tmp_path):
    ir = chart_ir(tmp_path, text="Sample B 125.6 t")
    point = ir.figures[0]["chart_spec"]["evidence_support"]["points"][0]
    point["context_references"] = [{"ref": "point-text", "source_quote": "Sample B 125.6 t"}]
    ir.figures[0]["chart_spec"]["series"][0]["points"][0]["evidence_refs"] = ["point-text"]
    packet, _, targets = chart_packet(ir)
    assert not targets and not packet.candidates


@pytest.mark.parametrize("kind", ["block", "cell"])
def test_exact_point_uses_real_source_text_and_locator_without_visual_inference(tmp_path, kind):
    packet, obj, targets = chart_packet(chart_ir(tmp_path, "exact_text", kind=kind))
    assert len(targets) == 1 and targets[0]["visible_value"].strip() == "125.6 t"
    supported = next(span for span in packet.spans if span.structural_context["literal_policy"] == POINT_TEXT_POLICY)
    assert supported.text == "Sample A 125.6 t"
    assert supported.locators[0].ir_object_id == "point-text"
    assert supported.locators[0].ir_object_type == kind
    row = next(row for row in obj["rows"] if row["cells"])
    assert row["cells"][0]["grid_reliable"] is True
    obj["linked_context"] = [{"span_id": "identity", "text": "Example Group 2024", "locators": [{
        "ir_object_type": "block", "ir_object_id": "identity", "pdf_page_index": 0, "bbox": deepcopy(BBOX)}]}]
    output = TableUnderstanding.model_validate({"rows": [{"group": item["group"],
        "kind": "quantitative" if item is row else "context"} for item in obj["rows"]],
        "columns": [{"column": row["cells"][0]["column"], "fields": {
            "reporting_entity": "Example Group", "reporting_period": "2024"},
            "field_refs": {"reporting_entity": ["identity"], "reporting_period": ["identity"]}}]})
    rows, errors = expand_understanding(output, obj)
    quantity = next(item for item in rows if item["kind"] == "quantitative")
    assert not errors and quantity["cells"][0]["value"] == targets[0]["visible_value"]
    assert quantity["cells"][0]["visual_override"] is False
    assert quantity["cells"][0]["quality_status"] == "grounded"
    fact = source_facts({"source_key": "source-chart", "object_id": "figure-a", "version": "test", "rows": rows},
        {"document_id": "doc-chart", "ir_run_id": "ir-chart", "ir_revision": 2}, obj)[0]
    audit = fact["physical_sources"][0]["chart_evidence_audit"]
    assert audit["evidence_origin"] == "exact_text_point"
    assert audit["visual_pixel_values_verified"] is False and audit["series_metadata_verified"] is False
    assert "chart_evidence_audit" not in fact["identity_raw"] and "evidence_origin" not in fact["identity_raw"]


@pytest.mark.parametrize("change", ["negative_index", "raw_text", "outside_crop", "other_coordinate_system", "missing_ref", "changed_category", "changed_value"])
def test_stale_or_mislocated_exact_ledger_is_not_a_text_source(tmp_path, change):
    ir = chart_ir(tmp_path, "exact_text")
    chart = ir.figures[0]["chart_spec"]
    point = chart["evidence_support"]["points"][0]
    block = ir.pages[0]["blocks"][0]
    if change == "negative_index":
        point["point_index"] = -1
    elif change == "raw_text":
        block["text"] = "Sample A 200 t"
    elif change == "outside_crop":
        block["bbox"].update(x0=150, x1=200)
        point["supported_references"][0]["bbox"] = deepcopy(block["bbox"])
    elif change == "other_coordinate_system":
        block["bbox"]["unit"] = "px"
        point["supported_references"][0]["bbox"] = deepcopy(block["bbox"])
    elif change == "missing_ref":
        chart["series"][0]["points"][0]["evidence_refs"] = []
    else:
        chart["series"][0]["points"][0]["category" if change == "changed_category" else "value"] = "changed" if change == "changed_category" else 200
    assert not chart_packet(ir)[2]


def test_year_shaped_point_value_and_numeric_category_keep_only_actual_measurement(tmp_path):
    ir = chart_ir(tmp_path, "exact_text", category="Series 1", text="Series 1 2024 t")
    chart = ir.figures[0]["chart_spec"]
    chart["series"][0]["points"][0]["value"] = 2024
    chart["evidence_support"]["points"][0]["value"] = 2024
    _, _, targets = chart_packet(ir)
    assert len(targets) == 1 and targets[0]["visible_value"].strip() == "2024 t"


def test_independent_chart_image_value_is_backend_pending_and_audit_is_not_public_identity(tmp_path):
    ir = chart_ir(tmp_path)
    original = deepcopy(ir.figures[0]["chart_spec"]["evidence_support"])
    _, obj, _ = chart_packet(ir)
    ctx = visual_context(obj)
    # Complete, genuinely cited identities cannot make an unverified visual
    # category/value pair grounded. The pending state is forced by the backend.
    support = {"span_id": "identity", "text": "Example Group 2024", "locators": [{
        "ir_object_type": "block", "ir_object_id": "identity-block", "pdf_page_index": 0, "bbox": deepcopy(BBOX)}]}
    ctx["linked_context"] = [support]
    output = visual_output(obj)
    output.columns[0].fields = {"reporting_entity": "Example Group", "reporting_period": "2024"}
    output.columns[0].field_refs = {"reporting_entity": ["identity"], "reporting_period": ["identity"]}
    rows, errors = expand_understanding(output, ctx)
    assert not errors
    cell = rows[0]["cells"][0]
    assert cell["fields"] == {"reporting_entity": "Example Group", "reporting_period": "2024"}
    assert cell["quality_status"] == "needs_confirmation" and cell["physical_sources"]
    assert any(issue["code"] == "chart_visual_candidate_pending" for issue in cell["quality_issues"])
    fact = source_facts({"source_key": "independent", "object_id": "figure-a", "version": "test", "rows": rows},
        {"document_id": "doc-chart", "ir_run_id": "ir-chart", "ir_revision": 2}, ctx)[0]
    assert fact["quality_status"] == "needs_confirmation" and fact["review_status"] == "needs_confirmation"
    audit = fact["physical_sources"][0]["chart_evidence_audit"]
    assert audit["evidence_origin"] == "independent_visual_read" and audit["original_point_upgraded"] is False
    assert audit["original_ledger"] == original
    assert fact["identity_raw"] == {"reporting_entity": "Example Group", "reporting_period": "2024"}
    assert ir.figures[0]["chart_spec"]["evidence_support"] == original


@pytest.mark.parametrize("conflict", ["point", "visual_ref"])
def test_independent_image_cannot_bypass_scope_conflict_at_parse_expand_or_bind(tmp_path, conflict):
    ir = chart_ir(tmp_path, "scope_conflict" if conflict == "point" else "source_context_only")
    if conflict == "visual_ref":
        ir.figures[0]["chart_spec"]["evidence_support"]["visual_reference_errors"] = [{"ref": "other-crop", "error": "reference_other_figure"}]
    _, obj, _ = chart_packet(ir)
    ctx, output = visual_context(obj), visual_output(obj)
    parsed, issues = validate_understanding_payload(output, ctx)
    assert issues and parsed.cell_overrides[0].value is None
    rows, errors = expand_understanding(output, ctx)
    assert errors and rows[0]["cells"] == []
    physical = obj["rows"][0]
    expanded = {"kind": "quantitative", "fields": {}, "cells": [{"value": "125.6", "column": "visual-slot-1",
        "cell_id": None, "fields": {}, "visual_override": True}]}
    bound = bind_source_row(expanded, physical, ctx)["cells"][0]
    assert bound["quality_status"] == "unbound" and bound["physical_sources"] == []
    assert any(issue["code"] == "chart_scope_conflict" for issue in bound["quality_issues"])


def test_virtual_visual_slots_are_accepted_by_the_real_output_contract(tmp_path):
    _, obj, _ = chart_packet(chart_ir(tmp_path))
    schema = understanding_template(context=obj)
    assert "visual-slot-1" in json.dumps(schema)
    output, issues = validate_understanding_payload(visual_output(obj), visual_context(obj))
    assert not issues
    rows, errors = expand_understanding(output, visual_context(obj))
    assert not errors and rows[0]["cells"][0]["value"] == "125.6"
    invalid = visual_output(obj).model_dump()
    invalid["cell_overrides"][0]["column"] = "invented-slot"
    bad, issues = validate_understanding_payload(invalid, visual_context(obj))
    assert not bad.cell_overrides and any("output slot" in issue.message for issue in issues)


@pytest.mark.parametrize("status", ["source_context_only", "exact_text", "scope_conflict"])
def test_chart_source_contract_compiles_with_deployed_strict_decoder(tmp_path, status):
    llguidance = pytest.importorskip("llguidance")
    from esg_targeted.models.schema_decoding import decoding_projection
    _, obj, _ = chart_packet(chart_ir(tmp_path, status))
    schema = decoding_projection(understanding_template(context=obj)).schema
    grammar = llguidance.JsonCompiler(separators=(", ", ": "), whitespace_pattern="").compile(json.dumps(schema), check=True)
    assert not llguidance.LLMatcher.validate_grammar(grammar)


def test_point_audit_references_are_not_promoted_to_legal_span_ids(tmp_path):
    _, obj, _ = chart_packet(chart_ir(tmp_path, "exact_text"))
    real = {segment["span_id"] for row in obj["rows"] for segment in row["support_segments"]}
    assert real <= set(reference_catalog(obj)["segments"])
    assert "point-text" not in reference_catalog(obj)["segments"]


def test_historical_json_literals_rejected_even_when_offsets_and_candidates_match(tmp_path):
    packet, _, _ = chart_packet(chart_ir(tmp_path))
    old = packet.spans[0].model_copy(update={"text": '{"value":125.6,"point_index":0}',
        "structural_context": {"figure_id": "figure-a", "chart_spec": packet.spans[0].structural_context["chart_spec"]}})
    # Simulate old persisted literal candidates, bypassing the new harvester.
    old_candidates = LiteralHarvester()._harvest_span(old, old.text)
    assert old_candidates
    assert EvidenceRegionCompiler._target_value_cells([old], old_candidates, [], {old.span_id: "S1"}, {old.context_group_id: "G1"}) == []
    candidate = next(c for c in old_candidates if c.normalized_value == "125.6")
    guard_packet = SimpleNamespace(spans=[old], candidates=[candidate], context_only_span_ids=[],
        allowed_group_ids=[old.context_group_id], alias_map={"visual": {"V1": old.span_id}}, page_image_paths=["image"])
    assignment = SemanticAssignment(element_id="amount", value_raw="125.6", source_mode="candidate", source_ref_id=candidate.candidate_id,
                                    evidence_span_ids=[old.span_id])
    element = {"element_id": "amount", "binding": {"target": "value_raw"}, "value_contract": {"primary_type": "decimal"}}
    issues = GroundingGuard()._source_issues(guard_packet, old.context_group_id, assignment, element,
        {old.span_id: old}, {candidate.candidate_id: candidate})
    assert any(issue.code == "chart_interpretation_not_literal" for issue in issues)


@pytest.mark.parametrize("status,code", [("source_context_only", "chart_visual_candidate_pending"), ("scope_conflict", "chart_scope_conflict")])
def test_direct_visual_chart_guard_retains_pending_or_rejects_conflict(tmp_path, status, code):
    packet, _, _ = chart_packet(chart_ir(tmp_path, status))
    source = packet.spans[0]
    packet.context_only_span_ids, packet.page_image_paths = [], ["supplied-image"]
    element = {"element_id": "amount", "binding": {"target": "value_raw"}, "value_contract": {"primary_type": "decimal"}}
    assignment = SemanticAssignment(element_id="amount", value_raw="125.6", source_mode="visual", source_ref_id=source.span_id,
                                    evidence_span_ids=[source.span_id])
    issues = GroundingGuard()._source_issues(packet, source.context_group_id, assignment, element,
        {source.span_id: source}, {})
    assert any(issue.code == code for issue in issues)
    assert next(issue.severity for issue in issues if issue.code == code) == ("error" if status == "scope_conflict" else "warning")


def test_only_legacy_chart_cache_is_retired_and_preserved_table_cache_is_unchanged(tmp_path):
    session = DisclosureSession.__new__(DisclosureSession)
    session.store, session.job_id = ArtifactStore(tmp_path), "job-chart"
    session.request = ExtractionRequest(ir_run_id="ir", package_id="pkg", package_version="1.0.0", report_session_id="report-" + "b" * 32)
    chart = {"source_key": "old-chart", "source_identity": {}, "rows": [{"group_id": "figure:figure-a"}], "facts": [{"source_fact_id": "old-point"}]}
    table = {"source_key": "same-table", "source_identity": {"interpretation_version": "report-semantics-v4"},
             "rows": [{"group_id": "table:table-a:row:1"}], "facts": [{"source_fact_id": "table-value"}]}
    modern = {**chart, "source_key": "new-chart", "source_identity": {"chart_reading_policy": CHART_READING_VERSION},
              "facts": [{"source_fact_id": "new-point"}]}
    for name, source in (("old", chart), ("table", table), ("new", modern)):
        session.store.write_json(session.job_id, f"agent/sources/{name}.json", source)
        root = session.store.report_session_dir(session.request.report_session_id) / "sources"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{name}.json").write_text(json.dumps(source))
    session.store.write_json(session.job_id, "agent/source-invalidations.json", {"source_fact_ids": ["prior-job-retired"]})
    session._invalidate_legacy_chart_sources()
    assert session._retired_source_ids() == {"old-point", "prior-job-retired"}
    assert session.store.read_json(session.job_id, "agent/sources/table.json") == table
    assert not legacy_chart_source(table) and not legacy_chart_source(modern)
    history = list((session.store.job_dir(session.job_id) / "agent/source-history").glob("*.json"))
    shared_history = list((session.store.report_session_dir(session.request.report_session_id) / "source-history").glob("*.json"))
    assert len(history) == len(shared_history) == 1
    saved = json.loads(history[0].read_text())
    assert saved["facts"] == chart["facts"] and saved["superseded_reason"] == "chart_reading_policy_changed"


def test_identity_evidence_dto_freezes_full_report_text_and_matching_header_only():
    loc = {"ir_object_id": "body", "document_id": "doc", "ir_run_id": "ir", "ir_revision": 2, "bbox": deepcopy(BBOX)}
    source = {"group_id": "row", "text": "Example Group measured the actual emission.",
        "support_segments": [{"span_id": "body", "text": "Example Group measured the actual emission.", "locators": [loc]}],
        "cells": [{"cell_id": "value", "span_id": "cell", "raw": "42", "column_headers": ["2023", "2024 Example Group"],
                   "locators": [{**loc, "ir_object_id": "cell"}]}]}
    expanded = {"kind": "quantitative", "fields": {}, "cells": [{"cell_id": "value", "value": "42", "fields": {
        "reporting_entity": "Example Group", "reporting_period": "2024"}}]}
    bound = bind_source_row(expanded, source, {})["cells"][0]
    evidence = bound["identity_evidence"]
    body = next(item for item in evidence["reporting_entity"] if item["evidence_origin"] == "source_text")
    assert body == {"evidence_contract_version": "bound-identity-evidence-v1", "span_id": "body",
        "text": source["text"], "excerpt": "Example Group", "evidence_origin": "source_text", "locators": [loc]}
    period = evidence["reporting_period"][0]
    assert period["text"] == "2024 Example Group" and period["excerpt"] == "2024"
    assert period["evidence_origin"] == "column_header" and period["locators"][0]["ir_revision"] == 2
    assert all(item["text"] != "2023" for item in evidence["reporting_period"])
    # Old evidence stays honest: no fabricated source revision or source IDs.
    for segment in source["support_segments"]:
        segment["locators"] = [{"ir_object_id": "body"}]
    source["cells"][0]["locators"] = [{"ir_object_id": "cell"}]
    old = bind_source_row(expanded, source, {})["cells"][0]["identity_evidence"]
    assert all("ir_revision" not in locator for entries in old.values() for item in entries for locator in item["locators"])


def test_identity_evidence_dto_uses_frozen_source_identity_and_preserves_conflicting_locator():
    frozen = {"document_id": "frozen-doc", "ir_run_id": "frozen-ir", "ir_revision": 7}
    physical = {"span_id": "body", "text": "Example Group", "locators": [{"ir_object_id": "body"}]}
    expanded = {"kind": "assertion", "fields": {"reporting_entity": "Example Group"},
                "statement_fragments": [{"ref": "body", "text": "Example Group"}]}
    bound = bind_source_row(expanded, physical, {"bound_source_identity": frozen})
    item = bound["identity_evidence"]["reporting_entity"][0]
    assert item["source_identity"] == frozen
    assert item["locators"] == [{"ir_object_id": "body"}]
    physical["locators"][0]["ir_revision"] = 6
    conflicted = bind_source_row(expanded, physical, {"bound_source_identity": frozen})["identity_evidence"]["reporting_entity"][0]
    assert conflicted["source_identity"]["ir_revision"] == 7 and conflicted["locators"][0]["ir_revision"] == 6
    assert "source_identity" not in bind_source_row(expanded, physical, {})["identity_evidence"]["reporting_entity"][0]


def test_real_understanding_path_emits_frozen_dto_and_shared_source_reuses_it(tmp_path):
    _, _, _, package, metric = build_pipeline(tmp_path / "package")
    ir = chart_ir(tmp_path, "exact_text", category="Example Group")
    _, obj, _ = chart_packet(ir)
    inventory = EvidenceInventoryBuilder().build(ir)
    store = ArtifactStore(tmp_path / "artifacts")
    request = ExtractionRequest(ir_run_id=ir.run_id, package_id="esrs.2023-set1.e2-4", package_version="1.0.0",
        report_session_id="report-" + "c" * 32, visual_fallback=False)
    calls = []
    class Model:
        provider_name = "offline_fixture"
        model_id = "offline_fixture"
        def generate_structured(self, **kwargs):
            ctx = kwargs["context"]
            calls.append(ctx)
            return json.dumps({"rows": [{"group": row["group"], "kind": "quantitative" if row["cells"] else "context",
                "context_role": "definition" if not row["cells"] else None,
                "fields": {"reporting_entity": "Example Group"} if row["cells"] else {}} for row in ctx["rows"]]}), {}
    def session(job_id):
        active = DisclosureSession(package=package, metrics=[metric], request=request, artifact_store=store,
            job_id=job_id, max_context_chars=32000, inventory=inventory)
        active._linked_context = []
        return active
    first = session("first")._understand(obj, Model(), lambda: False)
    assert len(calls) == 1 and first["source_identity"]["chart_reading_policy"] == CHART_READING_VERSION
    item = first["facts"][0]["identity_evidence"]["reporting_entity"][0]
    assert item["source_identity"] == {"document_id": ir.document_id, "ir_run_id": ir.run_id, "ir_revision": ir.ir_revision}
    assert item["text"] == "Example Group 125.6 t" and item["excerpt"] == "Example Group"
    assert item["evidence_contract_version"] == "bound-identity-evidence-v1" and item["locators"][0]["ir_object_id"] == "point-text"
    assert "ir_revision" not in item["locators"][0]  # the real SourceLocator is retained, not rewritten
    second = session("second")._understand(obj, Model(), lambda: False)
    assert len(calls) == 1 and second["facts"] == first["facts"]
