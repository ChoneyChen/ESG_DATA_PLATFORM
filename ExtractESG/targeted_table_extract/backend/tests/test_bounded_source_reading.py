"""Real failure replay and anonymous bounded source contracts; no model/DB."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.evidence.source_frames import source_frames, mapping_cards, pending_read_groups
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.report_semantics import (
    INTERPRETATION_VERSION, TableUnderstanding, expand_understanding,
    validate_understanding_payload, understanding_template, mapping_template,
)
from esg_targeted.models.schema_decoding import decoding_projection
from esg_targeted.models.task_context import compact_task_context
from esg_targeted.report_facts import source_facts
from esg_targeted.standards.catalog import StandardPackageCatalog
from tests.helpers import standard_dist_root
from tests.test_navigation_frontier import report_session
from tests.test_report_repair_regressions import RecordingModel, _object, _understanding
from tests.test_qwen_schema_decoding import local_qwen_tokenizer, _accepts_text
from esg_targeted.models.schema_decoding import build_decoding_constraint

FIXTURE = Path(__file__).parent / "fixtures/planner/scientific_v4_source_failure.json"


def captured():
    return json.loads(FIXTURE.read_text())


def table_replay():
    data = captured()
    outputs, physical = {}, {}
    for call in data["table_calls"]:
        context = {**data["table_common"], **call["input"]}
        understanding, issues = validate_understanding_payload(call["raw_output"], context)
        assert not any(issue.stage in {"schema", "syntax"} for issue in issues)
        rows, _ = expand_understanding(understanding, context)
        outputs.update({row["group_id"]: row for row in rows})
        physical.update({row["group_id"]: row for row in context["rows"]})
    return data, list(outputs.values()), list(physical.values())


def test_actual_eighteen_table_responses_preserve_raw_labels_and_one_physical_cell():
    data, rows, physical = table_replay()
    assert len(data["table_calls"]) == 18
    labels = {row["group_id"]: row["label"] for row in physical}
    assert all(row["source_row_label"] == (labels[row["group_id"]] or None) for row in rows)
    source = {"version": INTERPRETATION_VERSION, "source_key": "captured-neutral", "object_id": "captured-table", "rows": rows}
    facts = source_facts(source, data["table_common"]["bound_source_identity"], {"rows": physical})
    assert len(facts) == len({fact["source_anchor_id"] for fact in facts})
    assert len(facts) < 285  # the real previous stage duplicated 148 physical cells
    assert any(issue["code"] == "duplicate_physical_row_interpretation" for fact in facts for issue in fact["quality_issues"])
    assert any(issue["code"] == "source_label_interpretation_conflict" for fact in facts for issue in fact["quality_issues"])
    assert not any("source_interpretation" in fact["identity_raw"] for fact in facts)


def test_actual_combined_ghg_row_expands_all_five_real_years_with_literal_unit_and_unknown_entity():
    data, rows, physical = table_replay()
    row = next(row for row in rows if "范围一、范围二" in (row["source_row_label"] or ""))
    assert len(row["cells"]) == 5
    assert {cell["fields"]["reporting_period"] for cell in row["cells"]} == {"2021", "2022", "2023", "2024", "2025"}
    assert {cell["unit"] for cell in row["cells"]} == {"亿吨二氧化碳当量"}
    actual = next(cell for cell in row["cells"] if cell["fields"]["reporting_period"] == "2025")
    assert actual["value"] == "1.78"
    assert actual["quality_status"] == "needs_confirmation"
    assert "reporting_entity" not in actual["fields"]
    assert not {"scope_2_method", "amount_basis", "gwp_basis", "consolidation_approach", "accounting_standard"} & actual["fields"].keys()
    evidence = actual["identity_evidence"]["reporting_period"][0]
    assert evidence["evidence_contract_version"] == "bound-identity-evidence-v1"
    assert evidence["evidence_origin"] == "column_header" and "2025" in evidence["text"]
    assert evidence["source_identity"] == data["table_common"]["bound_source_identity"]
    assert evidence["locators"]


def test_real_uncertain_mapping_is_cached_without_another_provider_call(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    real = captured()["uncertain_mapping"]
    session._cards = lambda _: real["input"]["metrics"]
    members = [SimpleNamespace(metric_id=card["metric_id"]) for card in real["input"]["metrics"]]
    source = {"version": INTERPRETATION_VERSION, "rows": real["input"]["source_rows"], "errors": []}
    class Replay:
        calls = 0
        def generate_structured(self, **kwargs):
            self.calls += 1
            return real["raw_output"], {"prompt_tokens": 30, "generation_tokens": 20}
    model = Replay()
    obj = {"object_id": real["input"]["object_id"], "rows": []}
    first = session._map(obj, source, members, model, lambda: False)
    second = session._map(obj, source, members, model, lambda: False)
    assert first == second and model.calls == 1
    assert any(item.status == "uncertain" for item in first.coverage)
    session.sources[obj["object_id"]] = source
    frontier = session._navigation_frontier({obj["object_id"]: {**obj, "columns": []}},
        {"mappings": {obj["object_id"]: first}}, {"seen_queries": []}, 1, set())
    assert "map_object" not in frontier["actions"]


def test_real_truncated_definition_cannot_generate_unbounded_source_recovery(tmp_path):
    session, _, _, store = report_session(tmp_path)
    real = captured()["truncated_definition"]
    row = deepcopy(real["input"]["rows"][0])
    # Anonymous additional physical groups exercise the recovery family bound;
    # the provider response is exactly the archived real truncation.
    obj = {**real["input"], "rows": [{**deepcopy(row), "group": f"G{i}", "group_id": f"definition-{i}"} for i in range(4)]}
    class Replay:
        calls = 0
        def generate_structured(self, **kwargs):
            self.calls += 1
            raise ModelRunFailure("captured output truncated", category="generation_incomplete",
                raw_output=real["raw_output"], telemetry=real["telemetry"])
    model = Replay()
    result = session._understand(obj, model, lambda: False)
    assert model.calls <= 3 and not result["complete"]
    assert result["blocked_read_groups"] and any(gap["kind"] == "generation_incomplete" for gap in result["gaps"])
    assert not result["facts"] and session.runtime_budget.snapshot()["usage"]["format_repairs"] == 0
    assert not store.exists("job", "agent/repair-requests.json")


def test_one_action_reads_one_frame_and_following_action_preserves_remaining_rows(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = _object(13)
    model = RecordingModel(_understanding)
    first = session._understand(obj, model, lambda: False)
    assert len(model.contexts) == 1 and len(model.contexts[0]["rows"]) <= 4
    assert not first["complete"] and len(pending_read_groups(obj, first)) == 9
    second = session._understand(obj, model, lambda: False)
    assert len(model.contexts) == 2 and len(second["facts"]) == 8
    assert len(pending_read_groups(obj, second)) == 5


def test_large_physical_row_does_not_mark_unread_cells_complete(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = _object(1)
    base = obj["rows"][0]["cells"][0]
    obj["rows"][0]["cells"] = [{**deepcopy(base), "target_cell": f"T{i}", "cell_id": f"cell-{i}", "raw": str(i)} for i in range(70)]
    model = RecordingModel(_understanding)
    result = session._understand(obj, model, lambda: False)
    assert len(result["facts"]) == 32 and not result["complete"]
    assert obj["rows"][0]["group_id"] not in result["processed_groups"]
    result = session._understand(obj, model, lambda: False)
    assert len(result["facts"]) == 64 and not result["complete"]
    result = session._understand(obj, model, lambda: False)
    assert len(result["facts"]) == 70 and result["complete"]
    assert len({fact["source_anchor_id"] for fact in result["facts"]}) == 70


@pytest.mark.parametrize("tail_source", ["row_text", "support_segment", "extra_segment"])
def test_unshown_tail_cannot_complete_a_physical_narrative_group(tmp_path, tail_source):
    session, _, _, _ = report_session(tmp_path)
    obj = _object(1)
    obj["kind"] = "narrative"
    row = obj["rows"][0]
    row["cells"] = []
    if tail_source == "row_text":
        row["text"] = "Visible introduction. " * 100 + "Tail measurement 123 MWh."
    elif tail_source == "support_segment":
        row["support_segments"] = [{"span_id": "long-source", "text": "Visible introduction. " * 40 + "Tail measurement 123 MWh.", "locators": row["cells"]}]
    else:
        row["support_segments"] = [{"span_id": f"source-{i}", "text": "Intro" if i < 8 else "Tail measurement 123 MWh."} for i in range(9)]
    if tail_source != "row_text":
        # This anonymous narrative is supported by its own original spans;
        # remove the inherited synthetic table label with no narrative ref.
        row["text"] = ""
    model = RecordingModel(lambda context: {"rows": [{"group": "G1", "kind": "context", "context_role": "definition", "context_reason": "Read prefix"}]})
    result = session._understand(obj, model, lambda: False)
    assert not result["complete"] and not result["processed_groups"] and not result["blocked_read_groups"]
    assert result["text_coverage"] and pending_read_groups(obj, result)
    for _ in range(24):
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
    if tail_source == "row_text":
        assert not result["complete"] and not result["processed_groups"]
        assert any("source_text_span_unverifiable" in gap["reason"] for gap in result["gaps"])
    else:
        assert result["complete"]
    assert any("Tail measurement" in row["text"] for ctx in model.contexts for row in ctx["rows"])
    assert not result["facts"]


def test_context_only_mapping_and_bounded_understanding_compile_in_actual_strict_decoder():
    llguidance = pytest.importorskip("llguidance")
    source = captured()["truncated_definition"]["input"]
    schemas = [mapping_template(context={"source_rows": [], "metrics": [{"metric_id": "same-original-id"}]}),
               understanding_template(context=source)]
    for schema in schemas:
        projected = decoding_projection(schema).schema
        grammar = llguidance.JsonCompiler(separators=(", ", ": "), whitespace_pattern="").compile(json.dumps(projected), check=True)
        assert not llguidance.LLMatcher.validate_grammar(grammar)
    assert schemas[0]["properties"]["matches"]["maxItems"] == 0 and "RowMapping" not in schemas[0]["$defs"]
    assert schemas[1]["$defs"]["SemanticRow"]["properties"]["context_reason"]["maxLength"] == 384
    with pytest.raises(ValueError):
        TableUnderstanding(rows=[{"group": "G1", "kind": "context", "context_reason": "x" * 385}])


def test_full_seventeen_original_scope_survives_bounded_offline_mapping(tmp_path):
    data, rows, physical = table_replay()
    catalog = StandardPackageCatalog(standard_dist_root())
    selected = []
    for scope in data["selected_standards"]:
        package = catalog.load(scope["package_id"], scope["package_version"])
        selected.extend(metric for metric in package.metrics if metric.metric_id in scope["metric_ids"])
    assert len(selected) == 16 and {metric.source_datapoint_id for metric in selected} == set(data["selected_source_datapoint_ids"])
    extension_ids = data["selected_extension_ids"]
    assert len(extension_ids) == 1
    ids = [metric.metric_id for metric in selected] + extension_ids
    assert len(set(ids)) == 17
    frames = source_frames(physical)
    assert {row["group_id"] for frame in frames for row in frame} == {row["group_id"] for row in physical}
    assert all(len(frame) <= 4 and all(len(row.get("cells", [])) <= 32 for row in frame) for frame in frames)
    session, _, _, _ = report_session(tmp_path)
    # Only exercise transport/coverage scheduling; this anonymous response is
    # explicitly no extraction accuracy claim and injects no matching fact.
    session._cards = lambda members: [{"metric_id": metric.metric_id} for metric in members]
    model = RecordingModel(lambda context: {"matches": [], "coverage": [
        {"metric_id": metric["metric_id"], "status": "uncertain", "reason": "Independent scientific review is required"} for metric in context["metrics"]]})
    mapping = session._map({"object_id": "offline-all-seventeen"}, {"version": INTERPRETATION_VERSION, "rows": rows[:4], "errors": []},
                           [SimpleNamespace(metric_id=metric) for metric in ids], model, lambda: False)
    assert {item.metric_id for item in mapping.coverage} == set(ids)
    assert not mapping.matches and len(model.contexts) == 3


def test_provider_projection_reduces_anonymous_context_preserving_scientific_contract():
    context = {"kind": "table", "rows": [], "document_context": [
        {"span_id": "synthetic-context", "text": "原始编制背景" * 300, "locators": [{"ir_object_id": "synthetic-block"}]}]}
    original = deepcopy(context)
    provider = compact_task_context(context)
    assert context == original
    assert len(json.dumps(provider["document_context"], ensure_ascii=False)) < len(json.dumps(context["document_context"], ensure_ascii=False)) / 2
    cards = [{"metric_id": "synthetic-mass", "measurement_contract": {
        "unit_dimension": "mass", "canonical_unit_id": "tonne", "template": "dimensional_table",
        "comparison_requirements": ["internal repeated comparison prose"]}}]
    assert mapping_cards(cards)[0]["measurement_contract"] == {
        "unit_dimension": "mass", "canonical_unit_id": "tonne", "template": "dimensional_table"}
    assert "comparison_requirements" in cards[0]["measurement_contract"]
    assert [card["metric_id"] for card in mapping_cards(cards)] == [card["metric_id"] for card in cards]


def test_actual_assertion_fragments_do_not_paste_a_whole_block_and_contained_passage_twice():
    real = captured()["assertion_fragments"]
    understanding, issues = validate_understanding_payload(real["raw_output"], real["input"])
    assert not any(issue.stage in {"syntax", "schema"} for issue in issues)
    rows, _ = expand_understanding(understanding, real["input"])
    reduced = [row for row in rows if any(issue["code"] == "statement_fragments_redundant" for issue in row.get("quality_issues", []))]
    assert len(reduced) >= 2
    for row in reduced:
        assert row["quality_status"] == "needs_confirmation"
        assert row["statement"] == "\n".join(fragment["text"] for fragment in row["statement_fragments"])
        assert len(row["statement_fragments"]) < len(next(item.statement_fragments for item in understanding.rows if item.group == next(
            original["group"] for original in real["input"]["rows"] if original["group_id"] == row["group_id"])))


def test_actual_local_qwen_masks_accept_empty_assessment_and_reject_endless_context_reason(local_qwen_tokenizer):
    schema = mapping_template(context={"source_rows": [], "metrics": [{"metric_id": "M1"}]})
    processor, _ = build_decoding_constraint(local_qwen_tokenizer, schema)
    assert _accepts_text(processor, local_qwen_tokenizer, json.dumps({"matches": [], "coverage": [
        {"metric_id": "M1", "status": "reviewed", "reason": "Definition-only frame assessed"}]}))
    source = captured()["truncated_definition"]["input"]
    schema = understanding_template(context=source)
    processor, _ = build_decoding_constraint(local_qwen_tokenizer, schema)
    group = source["rows"][0]["group"]
    assert _accepts_text(processor, local_qwen_tokenizer, json.dumps({"rows": [{"group": group, "kind": "context",
        "context_role": "definition", "context_reason": "Read source definition"}]}))
    assert not _accepts_text(processor, local_qwen_tokenizer, json.dumps({"rows": [{"group": group, "kind": "context",
        "context_role": "definition", "context_reason": "x" * 385}]}))
