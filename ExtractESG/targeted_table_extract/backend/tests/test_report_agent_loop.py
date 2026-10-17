"""Bounded Agent recovery with fake models and fixed IR; no business runs."""
import json

import pytest

from esg_targeted.contracts import ExtractionRequest
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.storage.artifacts import ArtifactStore
from tests.test_inventory_and_guard import build_pipeline
from tests.test_shared_disclosure import ReportModel


def setup_session(tmp_path, *, search_report=None, max_steps=8):
    inventory, packet, _, package, metric = build_pipeline(tmp_path / "ir")
    first = metric.model_copy(update={"disclosure_topic": "test.topic", "disclosure_role": "anchor"})
    second = first.model_copy(update={"metric_id": "test.metric.second", "disclosure_role": "detail"})
    package = package.model_copy(update={"format_version": "2.0", "metrics": [first, second]})
    request = ExtractionRequest(ir_run_id="ir", package_id="pkg", package_version="2.0.0",
                                visual_fallback=False, agent_max_steps=max_steps)
    store = ArtifactStore(tmp_path / "artifacts")
    session = DisclosureSession(package=package, metrics=[first, second], request=request, artifact_store=store,
                                job_id="job", max_context_chars=32000, inventory=inventory, search_report=search_report)
    packet = session.expand(first, packet, lambda m: (None, [], None, packet))
    return session, packet, [first.metric_id, second.metric_id], store


def run(session, packet, model):
    return session.decide(packet=packet, model=model, guard_engine=GroundingGuard(), cancel_check=lambda: False)


def test_failed_mapping_can_be_remapped_without_reinterpreting_table(tmp_path):
    session, packet, ids, _ = setup_session(tmp_path)
    class Model(ReportModel):
        failed = False
        def generate_structured(self, **kwargs):
            context = kwargs["context"]
            if context.get("task") == "metric_mapping":
                raise RuntimeError("Temporary format repair failure")
            if "source_rows" in context and not self.failed:
                self.failed = True
                return "not json", {"provider": "fake"}
            if "candidate_objects" in context and context.get("understood_objects"):
                pending = next((o for o in context["understood_objects"] if o["mapping_pending"]), None)
                return json.dumps({"actions": [{"tool": "map_object", "object_id": pending["object_id"]}]
                                  if pending else [{"tool": "finish"}]}), {"provider": "fake"}
            return super().generate_structured(**kwargs)
    model = Model(ids)
    decision, _, calls, _ = run(session, packet, model)
    assert decision.status == "found", decision
    assert len(model.calls) == 1
    assert calls == session.agent["model_calls"]
    assert [s["status"] for s in session.agent["steps"]] == ["partial", "completed"]
    assert session.agent["steps"][0]["source_status"] == "complete"
    assert session.agent["steps"][0]["mapping_status"] == "partial"


def test_empty_initial_search_allows_agent_query_and_evidence_read(tmp_path):
    session, packet, ids, _ = setup_session(tmp_path)
    queries = []
    session.search_report = lambda mid, query: (queries.append(query) or packet)
    empty = packet.model_copy(update={"spans": [], "candidates": [], "allowed_group_ids": [],
                                     "context_only_span_ids": [], "alias_map": {"groups": {}, "spans": {}, "candidates": {}}}, deep=True)
    empty.budget["disclosure_topic"] = "test.topic"
    class Model(ReportModel):
        def generate_structured(self, **kwargs):
            if "candidate_objects" in kwargs["context"] and not kwargs["context"]["candidate_objects"] and not kwargs["context"]["previous_results"]:
                return json.dumps({"actions": [{"tool": "search_report", "query": "大气排放 氮氧化物"}]}), {"provider": "fake"}
            return super().generate_structured(**kwargs)
    decision, _, _, _ = run(session, empty, Model(ids))
    assert decision.status == "found", decision
    assert queries == ["大气排放 氮氧化物"]
    assert session.last_packet.allowed_group_ids


def test_no_evidence_is_not_claimed_as_complete_search(tmp_path):
    session, packet, ids, _ = setup_session(tmp_path)
    class Model(ReportModel):
        def generate_structured(self, **kwargs):
            return json.dumps({"actions": [{"tool": "finish", "reason": "No object available"}]}), {"provider": "fake"}
    decision, _, _, _ = run(session, packet, Model(ids))
    assert decision.status == "ambiguous"
    assert not session.store.read_json("job", "agent/topics/test.topic.json")["complete"]


@pytest.mark.parametrize("category", ["provider_account_blocked", "cancelled"])
def test_account_or_cancel_failure_stops_without_fallback(tmp_path, category):
    session, packet, ids, store = setup_session(tmp_path)
    class Model(ReportModel):
        def generate_structured(self, **kwargs):
            raise ModelRunFailure("Stop channel", category=category)
    with pytest.raises(ModelRunFailure):
        run(session, packet, Model(ids))
    assert session.agent["model_calls"] == 1
    assert store.read_json("job", "agent/calls/0001-agent_plan.json")["phase"] == "failed"


def test_search_failure_actions_stay_within_configured_budget(tmp_path):
    session, packet, ids, _ = setup_session(tmp_path, max_steps=2)
    class Model(ReportModel):
        def generate_structured(self, **kwargs):
            return json.dumps({"actions": [{"tool": "search_report", "query": "a"},
                                            {"tool": "search_report", "query": "b"}]}), {"provider": "fake"}
    decision, _, _, _ = run(session, packet, Model(ids))
    assert decision.status == "ambiguous"
    assert len(session.agent["steps"]) == 2


def test_agent_selection_keeps_ranked_physical_objects_without_row_gate(tmp_path):
    inventory, packet, _, package, metric = build_pipeline(tmp_path)
    elements = [e for e in package.elements if e.metric_id == metric.metric_id]
    found = EvidenceSelectionCompiler().compile(task_id=packet.task_id, metric=metric, elements=elements,
        query=packet.query, inventory=inventory, hits=packet.retrieval_hits,
        retrieval_complete=True, model_led_review=True)
    assert found.allowed_group_ids
    assert all(o["excluded_group_count"] == 0 for o in found.budget["selected_retrieval_objects"])


def test_source_part_resume_only_reads_remaining_rows_and_clears_transport_gap(tmp_path):
    session, _, ids, _ = setup_session(tmp_path)
    session._current_calls, session._linked_context = [], []
    obj = {"object_id": "large-table", "image_path": None, "columns": [], "rows": [
        {"group": f"G{i}", "group_id": f"row-{i}", "label": "方法说明", "text": "报告采用GHG Protocol。", "cells": [],
         "support_segments": [{"span_id": f"span-row-{i}", "text": "报告采用GHG Protocol。",
                               "locators": [{"type": "block", "id": f"block-row-{i}", "pdf_page_index": 0}]}]}
        for i in range(13)]}
    class Model(ReportModel):
        parts = 0
        observed = []
        def generate_structured(self, **kwargs):
            self.parts += 1
            self.observed.append([r["group_id"] for r in kwargs["context"]["rows"]])
            if self.parts == 2:
                raise RuntimeError("Transport failed in second part")
            return json.dumps({"rows": [{"group": r["group"], "kind": "assertion", "statement": r["text"]}
                                         for r in kwargs["context"]["rows"]]}), {"provider": "fake"}
    model = Model(ids)
    partial = session._understand(obj, model, lambda: False)
    assert len(partial["rows"]) == 8 and not partial["complete"]
    completed = session._understand(obj, model, lambda: False)
    assert len(completed["rows"]) == 13 and completed["complete"]
    assert model.observed == [[f"row-{i}" for i in range(8)], [f"row-{i}" for i in range(8, 13)],
                              [f"row-{i}" for i in range(8, 13)]]
