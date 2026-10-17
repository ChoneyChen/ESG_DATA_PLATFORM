"""Synthetic immutable source revisions; no live model, API or business data."""
import json
from copy import deepcopy
import shutil

from esg_targeted.ids import stable_id
from esg_targeted.report_facts import SourceFact, _interpretation_digest
from esg_targeted.io import write_json
from esg_targeted.evidence.source_reading import bind_source_row
from esg_targeted.report_facts import source_facts
from esg_targeted.results.stages import collect_stage, publish_stage
from tests.test_report_agent_loop import setup_session
from tests.test_report_session import make_service, request
from tests.test_source_reading import locator
from tests.test_source_reading import expanded_quantity, row_context
from tests.test_shared_disclosure import ReportModel
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.results.materializer import CoreResultMaterializer
from tests.test_workflow import FoundModel, _run


def assertion_object():
    text = "采用实际测量。报告方法：测量。"
    return {"object_id": "page-0001", "kind": "page", "image_path": None, "columns": [], "rows": [
        {"group": "G1", "group_id": "block:body", "text": text, "label": "报告方法", "cells": [],
         "support_segments": [{"span_id": "span-body", "text": text, "locators": [locator("block-body")]}]}]}


class RereadModel:
    provider_name, model_id = "fake", "synthetic"

    def __init__(self, declarations):
        self.declarations = iter(declarations)

    def generate_structured(self, **kwargs):
        return json.dumps({"rows": [{"group": "G1", "kind": "assertion", **next(self.declarations)}]}, ensure_ascii=False), {"provider": "fake"}


def test_explicit_reread_archives_prior_interpretation_and_preserves_raw(tmp_path):
    session, _, _, store = setup_session(tmp_path)
    session._linked_context = []
    obj = assertion_object()
    model = RereadModel([{"statement": "采用实际测量。", "fields": {"method": "实际测量"}},
                         {"statement": "采用实际测量。", "fields": {"method": "测量"}}])
    first = session._understand(obj, model, lambda: False)
    revised = session._understand(obj, model, lambda: False, group_ids=["block:body"], context_kind="identity")
    old, new = first["facts"][0], revised["facts"][0]
    assert first["complete"] and revised["complete"]
    assert old["statement_raw"] == new["statement_raw"] == "采用实际测量。"
    assert old["source_anchor_id"] == new["source_anchor_id"]
    assert old["source_fact_id"] != new["source_fact_id"]
    assert old["identity_raw"] == {"method": "实际测量"}
    assert new["identity_raw"] == {"method": "测量"}
    histories = list((store.job_dir("job") / "agent/source-history").glob("*.json"))
    assert len(histories) == 1
    history = json.loads(histories[0].read_text())
    assert history["facts"] == [old]
    assert history["superseded_source_fact_ids"] == [old["source_fact_id"]]
    assert store.read_json("job", "agent/source-invalidations.json")["source_fact_ids"] == [old["source_fact_id"]]


def test_unbound_reread_reopens_previously_processed_source_group(tmp_path):
    session, _, _, _ = setup_session(tmp_path)
    session._linked_context = []
    obj = assertion_object()
    model = RereadModel([{"statement": "采用实际测量。"}, {"statement": "使用未披露的方法。"}])
    first = session._understand(obj, model, lambda: False)
    assert first["complete"]
    revised = session._understand(obj, model, lambda: False, group_ids=["block:body"], context_kind="identity")
    assert revised["facts"][0]["quality_status"] == "unbound"
    assert revised["facts"][0]["physical_sources"] == []
    assert revised["facts"][0]["statement_raw"] == "使用未披露的方法。"
    assert not revised["complete"]
    assert "block:body" not in revised["processed_groups"]
    assert any(g["kind"] == "uninterpreted_row" and g["group_ids"] == ["block:body"] for g in revised["gaps"])


def test_source_revision_stage_excludes_retired_export_and_stale_checkpoint(tmp_path):
    _, store = _run(tmp_path, FoundModel())
    root = store.job_dir("job-fixture")
    payload = store.read_json("job-fixture", "results/source-facts.json")
    old = payload["facts"][0]
    corrected = SourceFact.model_validate({**old, "source_fact_id": "temporary", "identity_raw": {"reporting_period": "2024"}})
    corrected.source_anchor_id = old.get("source_anchor_id") or stable_id("source-anchor", old["source_key"])
    corrected.interpretation_digest = _interpretation_digest(corrected)
    corrected.source_fact_id = stable_id("source-fact", corrected.source_anchor_id, corrected.interpretation_digest)
    new = corrected.model_dump(mode="json")
    assert old["source_fact_id"] != new["source_fact_id"]
    # The final export predates the reread. Only independently rebuilt durable
    # fragments may be used after a source retirement.
    for path in (root / "exports").glob("*"):
        path.unlink()
    (root / "manifest.json").unlink()
    store.write_json("job-fixture", "agent/sources/fixture.json", {"source_key": old["source_key"],
        "object_id": old["source_object_id"], "facts": [new], "complete": True})
    store.write_json("job-fixture", "agent/source-history/old.json", {"source_key": old["source_key"],
        "object_id": old["source_object_id"], "facts": [old], "interpretation_status": "superseded",
        "superseded_source_fact_ids": [old["source_fact_id"]]})
    store.write_json("job-fixture", "agent/source-invalidations.json", {"source_fact_ids": [old["source_fact_id"]]})
    mapping = store.read_json("job-fixture", "results/metric-source-mappings.json")
    store.write_json("job-fixture", "disclosures/projections/fixture.json", {"metric_id": mapping["core_bindings"][0]["metric_id"],
        "core_bindings": mapping["core_bindings"]})
    stage = collect_stage(store, "job-fixture", use_snapshot=False)
    assert stage["records"]["quantitative_observations"] == []
    assert stage["metric_source_mappings"]["core_bindings"] == []
    assert {f["source_fact_id"] for f in stage["source_facts"]["facts"]} == {new["source_fact_id"]}
    assert old["source_fact_id"] in {f["source_fact_id"] for f in stage["source_facts"]["historical_facts"]}
    assert stage["summary"]["stale_projection_metric_ids"]


def test_session_reread_history_is_distinct_from_current_source_candidates(tmp_path):
    service = make_service(tmp_path)
    created = service.create(request())
    job_id = created["job_ids"][0]
    old = {"source_fact_id": "source-old", "source_anchor_id": "anchor", "source_key": "source",
           "source_object_id": "object", "document_id": "doc", "ir_run_id": "ir-fixture-e2-4", "ir_revision": 1, "value_raw": "42"}
    new = {**old, "source_fact_id": "source-new", "identity_raw": {"reporting_entity": "真实主体"}}
    service.artifacts.write_json(job_id, "results/source-facts.json", {"facts": [old]})
    service.artifacts.write_json(job_id, "agent/sources/current.json", {"source_key": "source", "object_id": "object",
        "source_identity": {"document_id": "doc", "ir_run_id": "ir-fixture-e2-4", "ir_revision": 1}, "facts": [new], "complete": True})
    service.artifacts.write_json(job_id, "agent/source-history/old.json", {"source_key": "source", "object_id": "object", "facts": [old],
        "interpretation_status": "superseded", "superseded_source_fact_ids": [old["source_fact_id"]]})
    service.artifacts.write_json(job_id, "agent/source-invalidations.json", {"source_fact_ids": [old["source_fact_id"]]})
    publish_stage(service.artifacts, job_id, execution_status="interrupted")
    result = service.facts(created["session_id"])
    assert {f["source_fact_id"] for f in result["facts"]} == {new["source_fact_id"]}
    assert {f["source_fact_id"] for f in result["historical_facts"]} == {old["source_fact_id"]}
    assert result["historical_facts"][0]["interpretation_status"] == "superseded"


def test_retirement_after_export_prevents_old_bundle_shortcut(tmp_path):
    _, store = _run(tmp_path, FoundModel())
    old = store.read_json("job-fixture", "results/source-facts.json")["facts"][0]
    mapping = store.read_json("job-fixture", "results/metric-source-mappings.json")
    assert collect_stage(store, "job-fixture")["summary"]["counts"]["core_facts"] == 1
    store.write_json("job-fixture", "disclosures/projections/fixture.json", {
        "metric_id": mapping["core_bindings"][0]["metric_id"], "core_bindings": mapping["core_bindings"]})
    store.write_json("job-fixture", "agent/source-invalidations.json", {"source_fact_ids": [old["source_fact_id"]]})
    # Simulate interruption after the durable invalidation but before the next
    # stage publish. The previous Bundle must not confer current Core authority.
    stage = collect_stage(store, "job-fixture")
    assert stage["summary"]["counts"]["core_facts"] == 0
    assert stage["summary"]["stale_projection_metric_ids"] == [mapping["core_bindings"][0]["metric_id"]]


def test_shared_retirement_invalidates_other_projection_without_local_retirement_file(tmp_path):
    _, store = _run(tmp_path, FoundModel())
    session_id = "report-" + "1" * 32
    source = store.read_json("job-fixture", "results/source-facts.json")["facts"][0]
    mapping = store.read_json("job-fixture", "results/metric-source-mappings.json")
    shutil.copytree(store.job_dir("job-fixture"), store.output_root / "job-second")
    for job_id in ("job-fixture", "job-second"):
        child_request = store.read_json(job_id, "input/request.json")
        child_request["report_session_id"] = session_id
        store.write_json(job_id, "input/request.json", child_request)
        store.write_json(job_id, "disclosures/projections/shared.json", {
            "metric_id": mapping["core_bindings"][0]["metric_id"], "core_bindings": mapping["core_bindings"]})
        assert collect_stage(store, job_id)["summary"]["counts"]["core_facts"] == 1
    write_json(store.report_session_dir(session_id) / "source-invalidations.json", {"source_fact_ids": [source["source_fact_id"]]})
    for job_id in ("job-fixture", "job-second"):
        assert not store.exists(job_id, "agent/source-invalidations.json")
        stage = collect_stage(store, job_id)
        assert stage["summary"]["counts"]["core_facts"] == 0
        assert stage["summary"]["stale_projection_metric_ids"] == [mapping["core_bindings"][0]["metric_id"]]
        assert stage["source_facts"]["facts"] == []


def test_saved_mapping_is_vetoed_and_latest_shared_reading_reused(tmp_path, monkeypatch):
    session, packet, ids, store = setup_session(tmp_path)
    session_id = "report-" + "2" * 32
    session.request = session.request.model_copy(update={"report_session_id": session_id})
    session._linked_context = []
    obj = assertion_object()
    first = session._understand(obj, RereadModel([{"statement": "采用实际测量。", "fields": {"method": "实际测量"}}]), lambda: False)
    latest = deepcopy(first)
    latest["rows"][0] = bind_source_row({**latest["rows"][0], "fields": {"method": "测量"}}, obj["rows"][0], {})
    latest["facts"] = source_facts(latest, latest["source_identity"], obj)
    old_id, new_id = first["facts"][0]["source_fact_id"], latest["facts"][0]["source_fact_id"]
    assert old_id != new_id
    write_json(store.report_session_dir(session_id) / "sources" / f"{first['source_key']}.json", latest)
    write_json(store.report_session_dir(session_id) / "source-invalidations.json", {"source_fact_ids": [old_id]})
    store.write_json("job", "agent/topics/test.topic.json", {"version": session.agent["version"], "complete": True,
        "mappings": {obj["object_id"]: {"matches": [], "coverage": [
            {"metric_id": mid, "status": "reviewed", "reason": "Previously reviewed bounded object"} for mid in ids]}},
        "extra_groups": [], "missing_context": [], "metric_gaps": {}})
    session.sources = {}
    calls = []
    class FinishModel:
        provider_name, model_id = "fake", "synthetic"
        def generate_structured(self, **kwargs):
            calls.append(kwargs["context"])
            assert "rows" not in kwargs["context"]  # completed source reading remains reusable
            return json.dumps({"actions": [{"tool": "finish"}]}), {"provider": "fake"}
    # No newly selected object is available in this bounded recovery slice.
    # A retired mapping must still be vetoed instead of conferring coverage.
    monkeypatch.setattr(session, "_objects", lambda _: {})
    result = session._run_topic("test.topic", packet, FinishModel(), lambda: False)
    assert result["mappings"] == {} and calls
    before_read = session.agent["model_calls"]
    reused = session._understand(obj, RereadModel([]), lambda: False)
    assert reused["facts"][0]["source_fact_id"] == new_id
    assert session.agent["model_calls"] == before_read
    assert session.sources[obj["object_id"]]["facts"][0]["source_fact_id"] == new_id
    assert store.read_json("job", "agent/sources/page-0001.json")["facts"][0]["source_fact_id"] == new_id


def test_physical_quantity_anchors_survive_semantic_row_order_and_keep_equal_cells_distinct():
    physical = row_context()
    first = deepcopy(physical["cells"][0])
    first.update(cell_id="cell-a", span_id="span-a")
    first["locators"][0]["ir_object_id"] = "cell-a"
    second = deepcopy(first)
    second.update(cell_id="cell-b", span_id="span-b")
    second["locators"][0]["ir_object_id"] = "cell-b"
    physical["cells"] = [first, second]
    rows = []
    for ordinal, cell_id in enumerate(("cell-a", "cell-b"), 1):
        declaration = expanded_quantity(cell_fields={"reporting_period": "2024"})
        declaration["source_id"] = f"physical-row:{ordinal}"
        declaration["cells"][0]["cell_id"] = cell_id
        rows.append(bind_source_row(declaration, physical, {}))
    source = {"source_key": "frozen-reading", "object_id": "table", "version": "v", "rows": rows}
    identity = {"document_id": "doc", "ir_run_id": "ir", "ir_revision": 1}
    first_facts = source_facts(source, identity, {"rows": [physical]})
    source["rows"] = deepcopy(list(reversed(rows)))
    for ordinal, row in enumerate(source["rows"], 1):
        row["source_id"] = f"physical-row:{ordinal}"
    second_facts = source_facts(source, identity, {"rows": [physical]})
    def by_cell(facts, field):
        return {fact["physical_sources"][0]["ir_object_id"]: fact[field] for fact in facts}
    assert {fact["value_raw"] for fact in first_facts} == {"1.80"}
    assert len({fact["source_anchor_id"] for fact in first_facts}) == 2
    assert by_cell(first_facts, "source_anchor_id") == by_cell(second_facts, "source_anchor_id")
    assert by_cell(first_facts, "source_fact_id") != by_cell(second_facts, "source_fact_id")


def test_assertion_anchor_tracks_real_passage_rather_than_semantic_slot_or_equal_raw():
    physical = assertion_object()["rows"][0]
    physical["support_segments"].append({"span_id": "span-second", "text": physical["text"], "locators": [locator("block-second")]})
    def declaration(slot, ref):
        return bind_source_row({"source_id": slot, "group_id": physical["group_id"], "kind": "assertion", "fields": {},
            "statement": "采用实际测量。", "statement_fragments": [{"ref": ref, "text": "采用实际测量。"}], "cells": []}, physical, {})
    identity = {"document_id": "doc", "ir_run_id": "ir", "ir_revision": 1}
    def fact(row):
        return source_facts({"source_key": "source", "object_id": "page", "version": "v", "rows": [row]}, identity, {"rows": [physical]})[0]
    first, reordered, elsewhere = fact(declaration("slot:1", "span-body")), fact(declaration("slot:2", "span-body")), fact(declaration("slot:1", "span-second"))
    assert first["statement_raw"] == reordered["statement_raw"] == elsewhere["statement_raw"]
    assert first["source_anchor_id"] == reordered["source_anchor_id"]
    assert first["source_fact_id"] != reordered["source_fact_id"]
    assert first["source_anchor_id"] != elsewhere["source_anchor_id"]


def test_duplicate_semantic_rows_of_one_physical_fact_do_not_double_count_core(tmp_path):
    session, packet, ids, store = setup_session(tmp_path)
    class DuplicateModel(ReportModel):
        def generate_structured(self, **kwargs):
            raw, telemetry = super().generate_structured(**kwargs)
            if "rows" in kwargs["context"]:
                payload = json.loads(raw)
                payload["rows"] = [row for declaration in payload["rows"] for row in (declaration, deepcopy(declaration))]
                raw = json.dumps(payload, ensure_ascii=False)
            return raw, telemetry
    decision, guard, _, _ = session.decide(packet=packet, model=DuplicateModel(ids), guard_engine=GroundingGuard(), cancel_check=lambda: False)
    assert guard.accepted and decision.status == "found"
    assert len(decision.fact_groups) == 1
    source = next(iter(session.sources.values()))
    facts = source["facts"]
    assert len(facts) == len({fact["source_anchor_id"] for fact in facts}) == 1
    assert facts[0]["quality_status"] == "needs_confirmation"
    assert any(issue["code"] == "duplicate_physical_row_interpretation" for issue in facts[0]["quality_issues"])
    assert facts[0]["semantics"]["source_interpretation"]["alternative_interpretations"]
    result = CoreResultMaterializer().materialize(package=session.package, metric=session.package.metrics[0], packet=session.last_packet,
        decision=decision, guard=guard, attempts=1)
    assert len(result.quantitative_observations) == 1
    bindings = store.read_json("job", f"disclosures/projections/{ids[0]}.json")["core_bindings"]
    assert len(bindings) == 1 and bindings[0]["source_fact_id"] in {fact["source_fact_id"] for fact in facts}
    assert bindings[0]["core_record_id"] == result.quantitative_observations[0]["observation_id"]
