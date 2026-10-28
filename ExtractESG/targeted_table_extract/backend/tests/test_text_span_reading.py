"""Immutable character-span continuation, not model accuracy or golden facts."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_targeted.evidence import source_frames as policy
from esg_targeted.evidence.disclosure import DisclosureSession, REPORT_SOURCE_AGENT_VERSION
from esg_targeted.models.report_semantics import INTERPRETATION_VERSION
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.ir.loader import LoadedDocumentIr
from esg_targeted.evidence.source_frames import next_text_frame, text_state, text_complete, record_text_frame, verify_text_frame, reading_frontier_fingerprint
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.report_semantics import TableUnderstanding, expand_understanding, understanding_template, validate_understanding_payload
from esg_targeted.models.schema_decoding import build_decoding_constraint
from esg_targeted.models.task_context import compact_task_context
from tests.test_navigation_frontier import report_session
from tests.test_report_repair_regressions import RecordingModel
from tests.test_qwen_schema_decoding import local_qwen_tokenizer, _accepts_text


def object_with_text(text, *, span="original-body"):
    locator = {"ir_object_type": "block", "ir_object_id": "original-block", "pdf_page_index": 91,
        "bbox": {"x0": 10, "y0": 10, "x1": 200, "y1": 500}}
    row = {"group": "G1", "group_id": "block:original-block", "span_id": span, "label": "",
           "text": text, "locators": [locator], "cells": [],
           "support_segments": [{"span_id": span, "text": text, "locators": [locator]}]}
    return {"object_id": "original-page", "kind": "narrative", "columns": [], "rows": [row]}


def contexts_only(context):
    return {"rows": [{"group": row["group"], "kind": "context", "context_role": "definition",
                       "context_reason": "Only the displayed original window was assessed"} for row in context["rows"]]}


def test_long_unicode_text_reads_every_character_without_prefix_completion(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = object_with_text("This is ordinary source text. 中文与空白🙂\n" * 130 + "Actual tail disclosure 321 MWh.")
    model = RecordingModel(contexts_only)
    first = session._understand(obj, model, lambda: False)
    assert not first["complete"] and not first["processed_groups"]
    before = reading_frontier_fingerprint(obj, first)
    assert before and before[0][1]
    second = session._understand(obj, model, lambda: False)
    assert reading_frontier_fingerprint(obj, second) != before
    result = second
    for _ in range(30):
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
    assert result["complete"] and result["processed_groups"] == [obj["rows"][0]["group_id"]]
    coverage = result["text_coverage"][obj["rows"][0]["group_id"]]["channels"]["original-body"]
    assert coverage["covered"] == [[0, len(obj["rows"][0]["text"])]] and not coverage["blocked"]
    assert any("Actual tail disclosure 321 MWh." in row["text"] for context in model.contexts for row in context["rows"])
    assert all(len(row["text"]) <= 600 for context in model.contexts for row in context["rows"])
    assert not result["facts"]  # classification replay makes no ESG accuracy claim


def test_extremely_long_source_interval_plan_is_finite_and_unchanged_text_cannot_fake_progress():
    row = object_with_text("甲🙂\n" * 30000)["rows"][0]
    state = text_state(row)
    count = 0
    while not text_complete(state):
        window = next_text_frame(row, state)
        assert window and verify_text_frame(row, window)
        tampered = deepcopy(window); tampered["text"] += "invented"
        assert not verify_text_frame(row, tampered)
        record_text_frame(state, window["source_text_frame"], success=True)
        count += 1
        assert count < 200
    assert count > 100 and state["channels"]["original-body"]["covered"] == [[0, 90000]]


def test_failed_window_preserves_unread_gap_and_can_continue_later_windows(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = object_with_text("Short sentence. " * 120 + "The final source tail.")
    class Failure(RecordingModel):
        def generate_structured(self, **kwargs):
            self.contexts.append(deepcopy(kwargs["context"]))
            if len(self.contexts) == 1:
                raise ModelRunFailure("captured truncation", category="generation_incomplete", raw_output='{"rows":[',
                    telemetry={"prompt_tokens": 20, "generation_tokens": 2400})
            return json.dumps(contexts_only(kwargs["context"])), {"prompt_tokens": 20, "generation_tokens": 10}
    model = Failure(None)
    result = session._understand(obj, model, lambda: False)
    assert not result["complete"] and not result["blocked_read_groups"]
    state = result["text_coverage"][obj["rows"][0]["group_id"]]
    assert state["channels"]["original-body"]["blocked"] == [[0, 600]]
    next_id = reading_frontier_fingerprint(obj, result)[0][1]
    for _ in range(10):
        if not reading_frontier_fingerprint(obj, result):
            break
        result = session._understand(obj, model, lambda: False)
    assert not result["complete"] and not result["processed_groups"]
    assert any("final source tail" in row["text"] for context in model.contexts for row in context["rows"])
    assert any(gap["kind"] == "generation_incomplete" for gap in result["gaps"])
    assert model.contexts[1]["rows"][0]["source_text_frame"]["frame_id"] == next_id
    assert session.runtime_budget.snapshot()["usage"]["format_repairs"] == 0


def test_original_quote_positions_deduplicate_overlap_and_preserve_frozen_identity(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    quote = "The real measured statement is 42 MWh."
    text = "a" * 540 + quote + "b" * 1000
    obj = object_with_text(text)
    def statements(context):
        row = context["rows"][0]
        return {"rows": [{"group": "G1", "kind": "assertion", "statement_fragments": [{"ref": "original-body", "text": quote}]}]
                if quote in row["text"] else [{"group": "G1", "kind": "context", "context_role": "other"}]}
    model = RecordingModel(statements)
    first = session._understand(obj, model, lambda: False)
    fact = first["facts"][0]
    assert fact["physical_sources"][0]["span_id"] == "original-body"
    assert fact["physical_sources"][0]["ir_object_id"] == "original-block"
    assert fact["physical_sources"][0]["char_start"] == 540
    assert fact["physical_sources"][0]["char_end"] == 540 + len(quote)
    second = session._understand(obj, model, lambda: False)
    assert len(second["facts"]) == 1 and second["facts"][0] == fact
    assert second["source_identity"]["ir_revision"] == session.inventory.ir_revision


@pytest.mark.parametrize("attack", ["quote", "identity", "value", "visual"])
def test_undisplayed_tail_is_not_a_legal_quote_identity_value_or_visual_override(attack):
    row = object_with_text("prefix " * 140 + "Hidden Group 2025 reported 987 MWh.")["rows"][0]
    a = row["text"].index("987")
    row["cells"] = [{"target_cell": "T-tail", "cell_id": "cell-tail", "span_id": "original-body", "column": "tail",
                     "raw": "987", "char_start": a, "char_end": a + 3, "locators": row["locators"], "grid_reliable": False}]
    window = next_text_frame(row)
    assert window and not window["cells"]
    context = {"kind": "narrative", "rows": [window], "columns": [{"column": "tail"}],
        "document_context": row["support_segments"], "visual_evidence_supplied": True, "visual_evidence_group_ids": [row["group_id"]],
        "bound_source_identity": {"document_id": "frozen-doc", "ir_run_id": "frozen-ir", "ir_revision": 3}}
    payload = {"rows": [{"group": "G1", "kind": "assertion", "statement_fragments": [{"ref": "original-body", "text": "Hidden Group 2025 reported 987 MWh."}]}]}
    if attack == "identity":
        payload["rows"][0].update(kind="context", context_role="other", fields=[{"name": "reporting_entity", "value": "Hidden Group"}],
            field_refs={"reporting_entity": ["original-body"]})
        # Context carries no publishable identity. Use a visible assertion to
        # exercise the actual identity binding receiver independently.
        payload["rows"][0].update(kind="assertion", statement_fragments=[{"ref": "original-body", "text": "prefix"}])
    if attack == "value":
        payload = {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": ["T-tail"]}]}
    if attack == "visual":
        payload = {"rows": [{"group": "G1", "kind": "quantitative"}], "cell_overrides": [{"group": "G1", "column": "tail", "value": "987"}]}
    parsed, issues = validate_understanding_payload(payload, context)
    rows, errors = expand_understanding(parsed, context)
    if attack == "identity":
        assert rows[0]["fields"] == {} and rows[0]["unverified_identity_raw"]["reporting_entity"] == "Hidden Group"
    elif attack == "quote":
        assert rows[0]["quality_status"] == "unbound" and not rows[0]["physical_sources"]
    else:
        assert not any(row.get("cells") for row in rows) and (issues or errors)


def test_visible_identity_emits_full_original_text_with_actual_window_range():
    text = "intro " * 120 + "Example Group used the disclosed method. " + "tail " * 200
    original = object_with_text(text)["rows"][0]
    state = text_state(original)
    window = next_text_frame(original, state);record_text_frame(state, window["source_text_frame"], success=True)
    window = next_text_frame(original, state)
    context = {"kind": "narrative", "rows": [window], "bound_source_identity": {"document_id": "doc", "ir_run_id": "ir", "ir_revision": 7}}
    parsed = TableUnderstanding(rows=[{"group": "G1", "kind": "assertion", "fields": [{"name": "reporting_entity", "value": "Example Group"}],
        "field_refs": {"reporting_entity": ["original-body"]}, "statement_fragments": [{"ref": "original-body", "text": "Example Group used the disclosed method."}]}])
    rows, _ = expand_understanding(parsed, context)
    evidence = rows[0]["identity_evidence"]["reporting_entity"][0]
    assert evidence["text"] == text and evidence["source_identity"]["ir_revision"] == 7
    assert evidence["source_char_ranges"] == [[720, 733]]
    assert "original_text" not in json.dumps(compact_task_context(context))


def test_tail_quantitative_cell_is_read_only_in_its_actual_character_window(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    text = "ordinary intro " * 80 + "Example Group 2025 used 987 MWh."
    obj = object_with_text(text)
    row = obj["rows"][0]; start = text.index("987")
    row["cells"] = [{"target_cell": "T-tail", "cell_id": "tail-occurrence", "column": "tail",
        "span_id": "original-body", "char_start": start, "char_end": start + 3,
        "raw": "987", "unit": "MWh", "locators": row["locators"]}]
    obj["columns"] = [{"column": "tail"}]
    def respond(context):
        visible = context["rows"][0]
        if not visible["cells"]:
            return contexts_only(context)
        return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": ["T-tail"], "unit": "MWh",
            "fields": [{"name": "reporting_entity", "value": "Example Group"}, {"name": "reporting_period", "value": "2025"}],
            "field_refs": {"reporting_entity": ["original-body"], "reporting_period": ["original-body"]}}]}
    model = RecordingModel(respond)
    result = session._understand(obj, model, lambda: False)
    assert not result["facts"] and not result["complete"]
    for _ in range(8):
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
    assert result["complete"] and len(result["facts"]) == 1
    fact = result["facts"][0]
    assert fact["value_raw"] == "987" and fact["unit_raw"] == "MWh"
    assert fact["identity_raw"] == {"reporting_entity": "Example Group", "reporting_period": "2025"}
    assert fact["physical_sources"][0]["char_start"] == start
    assert fact["physical_sources"][0]["char_end"] == start + 3
    assert fact["identity_evidence"]["reporting_entity"][0]["text"] == text
    assert fact["identity_evidence"]["reporting_entity"][0]["source_identity"]["ir_revision"] == session.inventory.ir_revision
    assert set(fact["identity_raw"]).isdisjoint({"source_text_frame", "original_text", "frame_id"})


def test_wrong_character_position_cannot_borrow_another_repeated_number(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = object_with_text("actual 987 followed by wrong xxx. " + "normal " * 180)
    row = obj["rows"][0]; start = row["text"].index("xxx")
    row["cells"] = [{"target_cell": "T-wrong", "cell_id": "wrong-occurrence", "span_id": "original-body",
        "raw": "987", "char_start": start, "char_end": start + 3, "locators": row["locators"]}]
    assert not next_text_frame(row)["cells"]
    model = RecordingModel(contexts_only)
    result = session._understand(obj, model, lambda: False)
    for _ in range(8):
        if not reading_frontier_fingerprint(obj, result):
            break
        result = session._understand(obj, model, lambda: False)
    assert not result["complete"] and not result["facts"] and not result["processed_groups"]
    assert any("source_text_cells_not_presented" in gap["reason"] for gap in result["gaps"])


def test_dense_original_text_cells_continue_in_32_cell_windows_without_repeating_fact_ids(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    numbers = [str(1000 + index) for index in range(70)]
    text = "ordinary prefix " * 45 + " ".join(numbers)
    obj = object_with_text(text); row = obj["rows"][0]
    row["cells"] = [{"target_cell": "T-" + number, "cell_id": "C-" + number, "span_id": "original-body",
        "column": number, "raw": number, "char_start": text.index(number), "char_end": text.index(number) + len(number),
        "locators": row["locators"]} for number in numbers]
    obj["columns"] = [{"column": number} for number in numbers]
    def respond(context):
        cells = context["rows"][0]["cells"]
        return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": [cell["target_cell"] for cell in cells]}]} if cells else contexts_only(context)
    model = RecordingModel(respond)
    result = session._understand(obj, model, lambda: False)
    action_ids = [reading_frontier_fingerprint(obj, result)]
    stable = {}
    for _ in range(8):
        for fact in result["facts"]:
            assert fact["source_fact_id"] == stable.setdefault(fact["value_raw"], fact["source_fact_id"])
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
        action_ids.append(reading_frontier_fingerprint(obj, result))
    assert result["complete"] and len(result["facts"]) == 70
    assert len({fact["source_fact_id"] for fact in result["facts"]}) == 70
    assert [len(context["rows"][0]["cells"]) for context in model.contexts] == [0, 32, 32, 6]
    assert len({json.dumps(action) for action in action_ids}) == len(action_ids)
    original_cells = {cell["cell_id"]: cell for cell in row["cells"]}
    # The provider view omits locators/character offsets; receiver verification
    # uses the frozen original cell catalog, never model-supplied replacements.
    assert all(verify_text_frame(row, {**context["rows"][0],
        "cells": [original_cells[cell["cell_id"]] for cell in context["rows"][0]["cells"]]}) for context in model.contexts)


def test_partial_quantitative_selection_keeps_unselected_cells_in_the_next_frontier(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    numbers = [str(1000 + index) for index in range(35)]
    text = "ordinary prefix " * 45 + " ".join(numbers)
    obj = object_with_text(text); row = obj["rows"][0]
    row["cells"] = [{"target_cell": "T-" + n, "cell_id": "C-" + n, "span_id": "original-body", "column": n,
        "raw": n, "char_start": text.index(n), "char_end": text.index(n) + len(n), "locators": row["locators"]} for n in numbers]
    obj["columns"] = [{"column": n} for n in numbers]
    def only_first(context):
        cells = context["rows"][0]["cells"]
        return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": [cells[0]["target_cell"]]}]} if cells else contexts_only(context)
    model = RecordingModel(only_first)
    result = session._understand(obj, model, lambda: False)
    result = session._understand(obj, model, lambda: False)
    state = result["text_coverage"][row["group_id"]]
    assert state["presented_cell_ids"] == ["C-1000"] and not state["structure_seen_cell_ids"]
    next_frame = next_text_frame(row, state)
    assert "C-1001" in next_frame["source_text_frame"]["cell_ids"] and "C-1000" not in next_frame["source_text_frame"]["cell_ids"]
    assert next_frame["source_text_frame"]["frame_id"] != model.contexts[-1]["rows"][0]["source_text_frame"]["frame_id"]
    stable = {fact["value_raw"]: fact["source_fact_id"] for fact in result["facts"]}
    for _ in range(40):
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
        for fact in result["facts"]:
            assert stable.setdefault(fact["value_raw"], fact["source_fact_id"]) == fact["source_fact_id"]
    assert result["complete"] and len(result["facts"]) == 35
    assert len(model.contexts) == 36 and len(set(stable.values())) == 35


@pytest.mark.parametrize("target", ["T-invented", "T-1000"])
def test_unknown_or_already_consumed_cell_does_not_advance_quantitative_coverage(tmp_path, target):
    session, _, _, _ = report_session(tmp_path)
    text = "ordinary prefix " * 45 + "1000 1001"
    obj = object_with_text(text); row = obj["rows"][0]
    row["cells"] = [{"target_cell": "T-" + n, "cell_id": "C-" + n, "span_id": "original-body", "column": n,
        "raw": n, "char_start": text.index(n), "char_end": text.index(n) + len(n), "locators": row["locators"]} for n in ["1000", "1001"]]
    obj["columns"] = [{"column": n} for n in ["1000", "1001"]]
    count = [0]
    def respond(context):
        if not context["rows"][0]["cells"]:
            return contexts_only(context)
        count[0] += 1
        return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": ["T-1000" if count[0] == 1 else target]}]}
    model = RecordingModel(respond)
    result = session._understand(obj, model, lambda: False)
    result = session._understand(obj, model, lambda: False)
    first = result["facts"][0]
    result = session._understand(obj, model, lambda: False)
    assert result["text_coverage"][row["group_id"]]["presented_cell_ids"] == ["C-1000"]
    assert result["facts"] == [first] and not result["complete"]
    assert any(gap["kind"] == "protocol" for gap in result["gaps"])


def test_different_window_meanings_keep_each_cell_interpretation_and_identity_frozen(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    energy = "Energy Group 2025 energy 100 MWh."
    water = "Water Group 2024 water 200 m3."
    text = "a" * 540 + energy + "b" * 470 + water + "c" * 350
    obj = object_with_text(text); row = obj["rows"][0]
    row["cells"] = [{"target_cell": "T-" + value, "cell_id": "C-" + value, "span_id": "original-body", "column": value,
        "raw": value, "unit": unit, "char_start": text.index(value), "char_end": text.index(value) + 3,
        "locators": row["locators"]} for value, unit in [("100", "MWh"), ("200", "m3")]]
    obj["columns"] = [{"column": value} for value in ["100", "200"]]
    def respond(context):
        cells = context["rows"][0]["cells"]
        if not cells:
            return contexts_only(context)
        is_energy = cells[0]["raw"] == "100"
        return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": [cell["target_cell"] for cell in cells],
            "source_row_label": "energy" if is_energy else "water", "unit": "MWh" if is_energy else "m3",
            "fields": [{"name": "reporting_entity", "value": "Energy Group" if is_energy else "Water Group"},
                {"name": "reporting_period", "value": "2025" if is_energy else "2024"}],
            "field_refs": {"reporting_entity": ["original-body"], "reporting_period": ["original-body"]}}]}
    model = RecordingModel(respond)
    first = session._understand(obj, model, lambda: False)["facts"][0]
    second = session._understand(obj, model, lambda: False)
    retained = next(fact for fact in second["facts"] if fact["value_raw"] == "100")
    assert retained == first  # complete immutable interpretation, not just raw
    fact = next(fact for fact in second["facts"] if fact["value_raw"] == "200")
    assert fact["unit_raw"] == "m3" and fact["identity_raw"] == {"reporting_entity": "Water Group", "reporting_period": "2024"}
    assert fact["semantics"]["source_interpretation"]["interpreted_row_label"] == "water"
    assert first["semantics"]["source_interpretation"]["interpreted_row_label"] == "energy"
    assert len({reading["source_id"] for reading in second["rows"] if reading["kind"] == "quantitative"}) == 2


@pytest.mark.parametrize("split_sentence", [False, True])
def test_real_inventory_object_compiler_keeps_original_span_offsets_beyond_5000_char_menu(tmp_path, split_sentence):
    text = "ordinary original text " * 235 + ("；" if split_sentence else "") + "Example Group 2025 reported 987 MWh."
    ir = LoadedDocumentIr(root=tmp_path, manifest={"run_id": "ir-real-path", "document_id": "doc-real-path", "ir_revision": 9},
        document={}, sections_by_id={}, pages=[{"page": {"page_id": "page-real", "page_index": 0},
        "blocks": [{"block_id": "block-real", "text": text, "bbox": {"x0": 0, "y0": 0, "x1": 100, "y1": 100}}]}],
        tables=[], figures=[], artifacts=[])
    inventory = EvidenceInventoryBuilder().build(ir)
    groups = {"G1": inventory.spans[0].context_group_id}
    span_refs = {f"S{i}": span.span_id for i, span in enumerate(inventory.spans, 1)}
    targets = EvidenceRegionCompiler._target_value_cells(inventory.spans, inventory.candidates, [],
        {value: key for key, value in span_refs.items()}, {value: key for key, value in groups.items()})
    packet = SimpleNamespace(spans=inventory.spans, candidates=inventory.candidates, allowed_group_ids=list(groups.values()),
        alias_map={"groups": groups, "spans": span_refs}, model_context=json.dumps({"region": {"target_value_cells": targets}}))
    session, _, _, _ = report_session(tmp_path / "session");session.inventory = inventory
    obj = session._objects(packet)["page-real"]; row = obj["rows"][0]
    assert len(row["text"]) == 5000 and len(row["support_segments"][0]["text"]) == len(text) > 5000
    cell = next(cell for cell in row["cells"] if "987" in cell["raw"])
    original_span = next(span for span in inventory.spans if span.span_id == cell["span_id"])
    assert cell["span_id"] == inventory.candidates[0].span_id
    assert cell["raw"] == original_span.text[cell["char_start"]:cell["char_end"]]
    assert (cell["span_id"] != row["span_id"]) is split_sentence
    def respond(context):
        cells = context["rows"][0]["cells"]
        return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": [cell["target_cell"] for cell in cells]}]} if cells else contexts_only(context)
    model = RecordingModel(respond)
    result = session._understand(obj, model, lambda: False)
    for _ in range(32):
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
    assert result["complete"] and len(result["facts"]) == 1
    fact = result["facts"][0]
    assert "987" in fact["value_raw"] and fact["unit_raw"] == "MWh"
    assert fact["document_id"] == "doc-real-path" and fact["ir_revision"] == 9
    assert fact["physical_sources"][0]["span_id"] == cell["span_id"]
    assert fact["physical_sources"][0]["char_start"] == cell["char_start"]
    assert (cell["char_start"] > 5000) is not split_sentence
    assert fact["quality_status"] == "needs_confirmation" and fact["identity_raw"] == {}


def test_structure_classification_has_independent_seen_cells_and_never_creates_numeric_facts(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    text = "ordinary prefix " * 45 + "1234"
    obj = object_with_text(text); row = obj["rows"][0];start = text.index("1234")
    row["cells"] = [{"target_cell": "T1", "cell_id": "C1", "span_id": "original-body", "column": "c1",
        "raw": "1234", "char_start": start, "char_end": start + 4, "locators": row["locators"]}]
    model = RecordingModel(contexts_only)
    result = session._understand(obj, model, lambda: False)
    result = session._understand(obj, model, lambda: False)
    state = result["text_coverage"][row["group_id"]]
    assert not state["presented_cell_ids"] and state["structure_seen_cell_ids"] == ["C1"]
    assert result["complete"] and not result["facts"]  # read structure, no claimed indicator


@pytest.mark.parametrize("kind", ["assertion", "quantitative"])
def test_new_ir_page_text_fallback_preserves_full_original_reference_and_reads_tail(tmp_path, kind):
    # Independently constructed source IR; no failed report/model output replay.
    quote = "Actual original tail statement." if kind == "assertion" else "Example Group 2025 reported 987 MWh."
    text = "ordinary original source " * (320 if kind == "assertion" else 230) + quote
    ir = LoadedDocumentIr(root=tmp_path, manifest={"run_id": "ir-new-fallback", "document_id": "doc-new-fallback", "ir_revision": 11},
        document={}, sections_by_id={}, pages=[{"page": {"page_id": "new-page-text", "page_index": 0, "text": text}, "blocks": []}],
        tables=[], figures=[], artifacts=[])
    inventory = EvidenceInventoryBuilder().build(ir)
    original = inventory.spans[0]
    assert original.span_type == "page_text" and original.text == text
    groups = {"G1": original.context_group_id};span_refs = {"S1": original.span_id}
    targets = EvidenceRegionCompiler._target_value_cells(inventory.spans, inventory.candidates, [],
        {value: key for key, value in span_refs.items()}, {value: key for key, value in groups.items()})
    packet = SimpleNamespace(spans=inventory.spans, candidates=inventory.candidates, allowed_group_ids=list(groups.values()),
        alias_map={"groups": groups, "spans": span_refs}, model_context=json.dumps({"region": {"target_value_cells": targets}}))
    session, _, _, _ = report_session(tmp_path / "fresh-case");session.inventory = inventory
    obj = session._objects(packet)["new-page-text"]; row = obj["rows"][0]
    assert len(row["text"]) == 5000 and row["span_id"] == original.span_id
    assert row["support_segments"][0]["text"] == text
    assert row["support_segments"][0]["locators"] == [locator.model_dump(mode="json") for locator in original.locators]
    def respond(context):
        visible = context["rows"][0]
        if kind == "assertion" and quote in visible["text"]:
            return {"rows": [{"group": "G1", "kind": "assertion", "statement_fragments": [{"ref": original.span_id, "text": quote}]}]}
        if kind == "quantitative" and visible["cells"]:
            return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": [cell["target_cell"] for cell in visible["cells"]]}]}
        return contexts_only(context)
    model = RecordingModel(respond);first = session._understand(obj, model, lambda: False)
    assert not first["complete"] and not first["processed_groups"] and not first["facts"]
    result = first
    for _ in range(30):
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
    assert result["complete"] and len(result["facts"]) == 1
    assert any(quote in reading["text"] for context in model.contexts for reading in context["rows"])
    channel = result["text_coverage"][row["group_id"]]["channels"][original.span_id]
    assert channel["covered"] == [[0, len(text)]]
    fact = result["facts"][0]
    assert fact["document_id"] == "doc-new-fallback" and fact["ir_run_id"] == "ir-new-fallback" and fact["ir_revision"] == 11
    assert fact["physical_sources"][0]["ir_object_id"] == "new-page-text"
    assert fact["physical_sources"][0]["span_id"] == original.span_id
    assert fact["physical_sources"][0]["char_start"] > 5000
    assert fact["statement_raw"] == quote if kind == "assertion" else "987" in fact["value_raw"] and fact["unit_raw"] == "MWh"


def test_long_row_without_original_span_stays_unverifiable_after_all_known_windows(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = object_with_text("original-looking but unreferenced " * 190)
    row = obj["rows"][0];row.pop("span_id");row["support_segments"] = []
    model = RecordingModel(contexts_only);result = session._understand(obj, model, lambda: False)
    for _ in range(30):
        if not reading_frontier_fingerprint(obj, result):
            break
        result = session._understand(obj, model, lambda: False)
    assert not result["complete"] and not result["processed_groups"] and not result["facts"]
    assert any("source_text_span_unverifiable" in gap["reason"] for gap in result["gaps"])
    assert all(not reading.get("support_segments") for context in model.contexts for reading in context["rows"])


def test_overlapping_quantitative_rows_accumulate_each_returned_physical_cell_once(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    text = "ordinary prefix " * 45 + "1000 1001"
    obj = object_with_text(text); row = obj["rows"][0]
    row["cells"] = [{"target_cell": "T-" + n, "cell_id": "C-" + n, "span_id": "original-body", "column": n,
        "raw": n, "char_start": text.index(n), "char_end": text.index(n) + len(n), "locators": row["locators"]} for n in ["1000", "1001"]]
    obj["columns"] = [{"column": n} for n in ["1000", "1001"]]
    def respond(context):
        if not context["rows"][0]["cells"]:
            return contexts_only(context)
        return {"rows": [{"group": "G1", "kind": "quantitative", "target_cells": ["T-1000"]},
            {"group": "G1", "kind": "quantitative", "target_cells": ["T-1000", "T-1001"]}]}
    model = RecordingModel(respond)
    result = session._understand(obj, model, lambda: False)
    result = session._understand(obj, model, lambda: False)
    assert result["complete"] and sorted(fact["value_raw"] for fact in result["facts"]) == ["1000", "1001"]
    assert len({fact["source_anchor_id"] for fact in result["facts"]}) == 2
    assert result["text_coverage"][row["group_id"]]["presented_cell_ids"] == ["C-1000", "C-1001"]


def test_ambiguous_real_span_keeps_both_hashes_unread_with_explicit_gap(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = object_with_text("Original short text.")
    row = obj["rows"][0]
    row["support_segments"].append({**row["support_segments"][0], "text": "Conflicting original text."})
    state = text_state(row)
    assert len(state["channels"]) == 2
    assert all(channel["ambiguous_reference"] for channel in state["channels"].values())
    assert not next_text_frame(row, state) and not text_complete(state)
    model = RecordingModel(contexts_only)
    result = session._understand(obj, model, lambda: False)
    assert not result["complete"] and not result["processed_groups"] and not model.contexts
    assert any("source_text_reference_conflict" in gap["reason"] for gap in result["gaps"])


@pytest.mark.parametrize("field", ["source_ref", "frame_id"])
def test_window_reference_and_fingerprint_are_not_self_certified(field):
    row = object_with_text("original " * 200)["rows"][0]
    window = next_text_frame(row)
    window["source_text_frame"][field] = "invented"
    assert not verify_text_frame(row, window)


def test_saved_coverage_resumes_next_real_window_and_resets_on_source_drift(tmp_path):
    session, _, _, _ = report_session(tmp_path)
    obj = object_with_text("unchanged source " * 150)
    first = session._understand(obj, RecordingModel(contexts_only), lambda: False)
    first_frame = reading_frontier_fingerprint(obj, first)
    session.sources.clear()  # resume from the persisted neutral archive
    model = RecordingModel(contexts_only)
    resumed = session._understand(obj, model, lambda: False)
    assert model.contexts[0]["rows"][0]["source_text_frame"]["start"] == 520
    assert reading_frontier_fingerprint(obj, resumed) != first_frame
    changed = deepcopy(obj);changed["rows"][0]["text"] += " changed"
    changed["rows"][0]["support_segments"][0]["text"] += " changed"
    state = text_state(changed["rows"][0], resumed["text_coverage"][obj["rows"][0]["group_id"]])
    assert not state["channels"]["original-body"]["covered"] and not state["presented_cell_ids"]
    assert next_text_frame(changed["rows"][0], state)["source_text_frame"]["start"] == 0


@pytest.mark.parametrize("prior_agent,prior_semantics", [("report-fact-agent-v6", "report-semantics-v5"),
    ("report-fact-agent-v7", "report-semantics-v6")])
def test_old_complete_neutral_cache_is_preserved_but_cannot_complete_new_span_reading(tmp_path, prior_agent, prior_semantics):
    session, _, _, store = report_session(tmp_path)
    obj = object_with_text("real quoted statement. " + "ordinary " * 180)
    def assertion(context):
        return {"rows": [{"group": "G1", "kind": "assertion", "statement_fragments": [{"ref": "original-body", "text": "real quoted statement."}]}]}
    old = session._understand(obj, RecordingModel(assertion), lambda: False)
    old.update(version=prior_semantics, source_key="historical-" + prior_semantics, complete=True)
    old["source_identity"]["interpretation_version"] = prior_semantics
    shared = store.report_session_dir(session.request.report_session_id) / ("sources/historical-" + prior_semantics + ".json")
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(json.dumps(old)); original = shared.read_bytes()
    store.write_json(session.job_id, "agent/sources/original-page.json", old)
    store.write_json(session.job_id, "agent/state.json", {"version": prior_agent, "planning_rounds": 99})
    checkpoint = {"semantic_pipeline_version": prior_agent, "outcome": {"status": "not_found"}}
    store.write_json(session.job_id, "checkpoints/anonymous.json", checkpoint)
    fresh = DisclosureSession(package=session.package, metrics=session.package.metrics, request=session.request,
        artifact_store=store, job_id=session.job_id, max_context_chars=32000, inventory=session.inventory)
    fresh.document_context = deepcopy(session.document_context)
    model = RecordingModel(contexts_only)
    result = fresh._understand(obj, model, lambda: False)
    assert fresh.agent["version"] == REPORT_SOURCE_AGENT_VERSION and len(model.contexts) == 1
    assert result["version"] == INTERPRETATION_VERSION and not result["complete"]
    # Existing workflow checks this semantic version before checkpoint reuse;
    # old successful not_found decisions cannot bypass the new read policy.
    assert store.read_json(session.job_id, "checkpoints/anonymous.json") == checkpoint
    assert checkpoint["semantic_pipeline_version"] != fresh.agent["version"]
    assert shared.read_bytes() == original
    histories = list((store.job_dir(session.job_id) / "agent/source-history").glob("*.json"))
    assert any(json.loads(path.read_text())["facts"] == old["facts"] for path in histories)


def test_actual_page92_original_spans_can_be_forced_into_small_windows_without_relabeling_ir(monkeypatch, tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/planner/page92_text_sources.json").read_text())
    # Force the real small source blocks into windows for a boundary test. The
    # recorded original text, refs and locators remain unchanged; no golden fact.
    monkeypatch.setattr(policy, "TEXT_WINDOW_CHARS", 60)
    monkeypatch.setattr(policy, "TEXT_WINDOW_OVERLAP", 10)
    session, _, _, _ = report_session(tmp_path)
    obj = {"object_id": fixture["object_id"], "kind": "narrative", "columns": [], "rows": fixture["rows"]}
    model = RecordingModel(contexts_only)
    result = session._understand(obj, model, lambda: False)
    assert not result["complete"]
    for _ in range(40):
        if result["complete"]:
            break
        result = session._understand(obj, model, lambda: False)
    assert result["complete"] and len(result["processed_groups"]) == 19
    for row in obj["rows"]:
        for item in policy.text_channels(row):
            if row["group_id"] in result["text_coverage"]:
                channel = result["text_coverage"][row["group_id"]]["channels"][item["key"]]
                assert channel["covered"] == [[0, len(item["text"])]]
    assert not result["facts"]
    assert all(row["source_text_frame"]["source_ref"] in {segment["span_id"] for original in obj["rows"] for segment in original["support_segments"]}
               for context in model.contexts for row in context["rows"] if row.get("source_text_frame"))


def test_window_receiver_schema_is_enforced_by_real_local_qwen_mask(local_qwen_tokenizer):
    row = object_with_text("ordinary " * 180)["rows"][0]
    window = next_text_frame(row)
    schema = understanding_template(context={"kind": "narrative", "rows": [window]})
    processor, _ = build_decoding_constraint(local_qwen_tokenizer, schema)
    good = {"rows": [{"group": "G1", "kind": "assertion", "statement_fragments": [{"ref": "original-body", "text": "ordinary"}]}]}
    assert _accepts_text(processor, local_qwen_tokenizer, json.dumps(good))
    bad = deepcopy(good);bad["rows"][0]["statement_fragments"][0]["ref"] = window["source_text_frame"]["frame_id"]
    assert not _accepts_text(processor, local_qwen_tokenizer, json.dumps(bad))
