"""Real planner-response replay and anonymous source state transitions.

No model process, database service or golden measurement injection is involved.
The captured eight calls prove a failure policy, not real-report ESG accuracy.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_targeted.contracts import EvidenceGap, SemanticDecision
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.models.report_semantics import (
    AgentPlan, PlannerFrontierError, plan_template, validate_plan_frontier,
)
from esg_targeted.models.schema_decoding import decoding_projection
from esg_targeted.results.materializer import CoreResultMaterializer
from esg_targeted.results.spec import RECORD_LAYOUT
from esg_targeted.results.validation import ResultContractValidator
from esg_targeted.workflow import TargetedExtractionWorkflow
from tests.test_inventory_and_guard import build_pipeline
from tests.test_report_agent_loop import setup_session, run
from tests.test_shared_disclosure import ReportModel


def report_session(tmp_path, *, max_steps=8):
    old, packet, ids, store = setup_session(tmp_path, max_steps=max_steps)
    request = old.request.model_copy(update={"report_session_id": "report-" + "b" * 32})
    session = DisclosureSession(package=old.package, metrics=old.package.metrics, request=request,
        artifact_store=store, job_id="job", max_context_chars=32000, inventory=old.inventory)
    packet = session.expand(session.package.metrics[0], packet, lambda metric: (None, [], None, packet))
    session._current_topic = session.package.metrics[0].disclosure_topic
    return session, packet, ids, store


def initial_frontier(session, packet):
    objects = session._objects(session._target_packet(packet))
    return session._navigation_frontier(objects, {"mappings": {}}, {"seen_queries": []}, 0, set())


def test_generation_and_receiver_share_frontier_and_do_not_expose_table_cell_ids(tmp_path):
    session, packet, _, _ = report_session(tmp_path)
    frontier = initial_frontier(session, packet)
    assert frontier["read_required"] and "search_report" not in frontier["actions"]
    assert "finish" not in frontier["actions"]
    context = {"legal_action_frontier": frontier, "objects": list(frontier["actions"]["inspect_object"]["object_ids"]),
               "metrics": session._navigation_cards(session.package.metrics)}
    schema = plan_template(context)
    obj = frontier["actions"]["inspect_object"]["object_ids"][0]
    valid = {"actions": [{"tool": "inspect_object", "object_id": obj}]}
    invalid = {"actions": [{"tool": "search_report", "query": "another search"}]}
    projection = decoding_projection(schema).schema
    items = projection["properties"]["actions"]["items"]["oneOf"]
    generation_tools = {projection["$defs"][item["$ref"].rsplit("/", 1)[-1]]["properties"]["tool"]["const"] for item in items}
    assert generation_tools == set(frontier["actions"])
    validate_plan_frontier(AgentPlan.model_validate(valid), frontier)
    assert invalid["actions"][0]["tool"] not in generation_tools
    with pytest.raises(PlannerFrontierError):
        validate_plan_frontier(AgentPlan.model_validate(invalid), frontier)
    catalog = session._navigation_catalog(session._objects(session._target_packet(packet)), [obj], packet)
    assert catalog[0]["row_count"] > 0
    assert all("row_ids" not in row and "cell_ids" not in row for row in catalog)
    assert len(json.dumps(catalog)) < len(json.dumps(session._objects(session._target_packet(packet))))


def test_frontier_schemas_compile_with_the_deployed_strict_decoder(tmp_path):
    # Compile only a grammar; this does not create a tokenizer or model process.
    llguidance = pytest.importorskip("llguidance")
    session, packet, _, _ = report_session(tmp_path)
    frontier = initial_frontier(session, packet)
    oid = frontier["actions"]["inspect_object"]["object_ids"][0]
    menus = [frontier, {**frontier, "actions": {"map_object": {"object_ids": [oid]}}},
             {**frontier, "actions": {"search_report": {}, "finish": {}}}]
    for menu in menus:
        context = {"legal_action_frontier": menu, "objects": [oid],
                   "metrics": session._navigation_cards(session.package.metrics)}
        schema = decoding_projection(plan_template(context)).schema
        grammar = llguidance.JsonCompiler(separators=(", ", ": "), whitespace_pattern="").compile(json.dumps(schema), check=True)
        assert not llguidance.LLMatcher.validate_grammar(grammar)


def test_captured_eight_search_only_responses_stop_early_without_source_or_ir_mutation(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/planner/search_only_20261006.json").read_text())
    assert len(fixture["calls"]) == 8
    assert sum(row["input_tokens"] for row in fixture["calls"]) == 103379
    session, packet, _, store = report_session(tmp_path)
    # Exercise every captured output, even though the bounded run stops after
    # the second rejection. New year/wording does not bypass the required read.
    frontier = initial_frontier(session, packet)
    for row in fixture["calls"]:
        with pytest.raises(PlannerFrontierError, match="search_report is unavailable"):
            validate_plan_frontier(AgentPlan.model_validate_json(row["raw_output"]), frontier)
    searches = []
    session.search_report = lambda metric, query: searches.append(query)
    class Replay:
        provider_name = "captured-replay"
        model_id = "no-model-process"
        position = 0
        def generate_structured(self, **kwargs):
            assert "legal_action_frontier" in kwargs["context"]
            row = fixture["calls"][self.position]
            self.position += 1
            return row["raw_output"], {"prompt_tokens": 10, "generation_tokens": 10}
    model = Replay()
    decision, _, calls, _ = run(session, packet, model)
    assert calls == model.position == 2  # no format-repair calls or six wasted rounds
    assert decision.status == "ambiguous" and not decision.fact_groups
    assert searches == [] and session.sources == {}
    assert session.agent["executed_actions"] == 0
    gaps = session.agent["gaps"]
    assert gaps and all(EvidenceGap.model_validate(gap).kind == "planner_stalled" for gap in gaps)
    assert all(gap["failure_stage"] == "planning" and gap["next_action"] == "inspect_object" for gap in gaps)
    assert not store.exists("job", "agent/repair-requests.json")
    assert session.agent["planning_history"][0]["legal_next_actions"]["inspect_object"]["object_ids"]


def test_second_action_is_revalidated_after_search_exposes_an_unread_object(tmp_path):
    session, packet, ids, _ = report_session(tmp_path)
    queries = []
    session.search_report = lambda mid, query: (queries.append(query) or packet)
    empty = packet.model_copy(update={"spans": [], "candidates": [], "allowed_group_ids": [],
        "context_only_span_ids": [], "alias_map": {"groups": {}, "spans": {}, "candidates": {}}}, deep=True)
    empty.budget["disclosure_topic"] = "test.topic"
    class Model(ReportModel):
        def generate_structured(self, **kwargs):
            context = kwargs["context"]
            if "candidate_objects" in context and not context["candidate_objects"] and not context["previous_results"]:
                return '{"actions":[{"tool":"search_report","query":"source method"},{"tool":"finish"}]}', {"provider": "fake"}
            return super().generate_structured(**kwargs)
    model = Model(ids)
    decision, _, _, _ = run(session, empty, model)
    assert decision.status == "found" and len(model.calls) == 1
    assert queries == ["source method"]
    rejection = next(item for item in session.agent["planning_history"] if item["status"] == "illegal_frontier_action")
    assert rejection["rejected_action"]["tool"] == "finish"
    assert rejection["read_required"] and "inspect_object" in rejection["legal_next_actions"]
    assert session.agent["planning_rounds"] == 3 and session.agent["executed_actions"] == 2


def test_planning_stall_does_not_overwrite_an_independent_tool_failure(tmp_path, monkeypatch):
    session, packet, ids, _ = report_session(tmp_path)
    def fail_read(*args, **kwargs):
        raise RuntimeError("independent source transport failure")
    monkeypatch.setattr(session, "_understand", fail_read)
    class Model(ReportModel):
        plans = 0
        def generate_structured(self, **kwargs):
            self.plans += 1
            if self.plans > 1:
                return '{"actions":[{"tool":"search_report","query":"avoid reading"}]}', {"provider": "fake"}
            return super().generate_structured(**kwargs)
    run(session, packet, Model(ids))
    assert session.topic_results["test.topic"]["failure_category"] == "planner_stalled"
    planning = [gap for gap in session.agent["gaps"] if gap["kind"] == "planner_stalled"]
    transport = [gap for gap in session.agent["gaps"] if gap["kind"] == "transport"]
    assert planning and all(gap["failure_stage"] == "planning" for gap in planning)
    assert transport and all(gap["failure_stage"] == "execution" for gap in transport)
    assert transport[0]["reason"] == "independent source transport failure"
    assert session.agent["executed_actions"] == 1


def test_new_query_same_evidence_is_not_progress_and_requires_model_chosen_read(tmp_path):
    session, packet, _, _ = report_session(tmp_path)
    objects = session._objects(session._target_packet(packet))
    fingerprints = session._object_fingerprints(objects)
    assert fingerprints == session._object_fingerprints(deepcopy(objects))
    oid = next(iter(objects))
    changed = deepcopy(objects)
    changed[oid]["rows"][0]["text"] += " independently new source text"
    assert session._object_fingerprints(changed)[oid] != fingerprints[oid]
    nav = {"seen_queries": [{"metric_id": session.package.metrics[0].metric_id, "query": "a query"}],
           "search_requires_read": True}
    frontier = session._navigation_frontier(objects, {"mappings": {}}, nav, 1, set())
    assert frontier["read_required"] and "search_report" not in frontier["actions"]
    with pytest.raises(PlannerFrontierError):
        validate_plan_frontier(AgentPlan.model_validate({"actions": [{"tool": "search_report", "query": "different wording"}]}), frontier)
    assert session.sources == {}  # the scheduler never chooses or reads an object


def test_query_identity_is_per_metric_and_exact_normalized_query(tmp_path):
    session, packet, ids, _ = report_session(tmp_path)
    frontier = initial_frontier(session, packet)
    frontier["actions"] = {"search_report": {}}
    frontier["seen_queries"] = [{"metric_id": ids[0], "query": "a query"}]
    with pytest.raises(PlannerFrontierError):
        validate_plan_frontier(AgentPlan.model_validate({"actions": [{"tool": "search_report", "query": " A   QUERY ", "metric_id": ids[0]}]}), frontier)
    validate_plan_frontier(AgentPlan.model_validate({"actions": [{"tool": "search_report", "query": "a query", "metric_id": ids[1]}]}), frontier)
    validate_plan_frontier(AgentPlan.model_validate({"actions": [{"tool": "search_report", "query": "a query 2025", "metric_id": ids[0]}]}), frontier)


def test_executed_no_novelty_search_is_saved_and_repeated_plan_preserves_prior_facts(tmp_path):
    session, packet, ids, _ = report_session(tmp_path)
    queries = []
    session.search_report = lambda metric, query: (queries.append(query) or packet)
    class Model(ReportModel):
        def generate_structured(self, **kwargs):
            context = kwargs["context"]
            if "candidate_objects" in context and context.get("understood_objects"):
                return json.dumps({"actions": [{"tool": "search_report", "query": "a genuinely new method query"}]}), {"prompt_tokens": 20, "generation_tokens": 10}
            return super().generate_structured(**kwargs)
    decision, _, _, _ = run(session, packet, Model(ids))
    assert queries == ["a genuinely new method query"]
    search = next(step for step in session.agent["steps"] if step["tool"] == "search_report")
    assert search["new_object_count"] == search["new_evidence_count"] == 0
    assert search["evidence_progress"] is False and search["retrieved_object_ids"]
    nav = session.agent["navigation"]["test.topic"]
    assert nav["seen_queries"][0]["query"] == queries[0]
    assert nav["stop_reason"] == "planner_stalled"
    assert decision.status == "partial" and decision.fact_groups  # no accepted fact is discarded
    assert session.agent["interpreted_objects"] == 1
    assert session.agent["executed_actions"] == 2


def test_report_positive_inspect_mapping_path_and_old_v4_state_are_independent(tmp_path):
    session, packet, ids, store = report_session(tmp_path)
    decision, _, _, _ = run(session, packet, ReportModel(ids))
    assert decision.status == "found" and session.sources
    assert session.agent["version"] == "report-fact-agent-v8"
    assert session.agent["executed_actions"] == 1 and session.agent["planning_rounds"] == 2
    store.write_json("old", "agent/state.json", {"version": "report-fact-agent-v4", "model_calls": 900})
    fresh = DisclosureSession(package=session.package, metrics=session.package.metrics, request=session.request,
        artifact_store=store, job_id="old", max_context_chars=32000)
    assert fresh.agent["model_calls"] == 0 and fresh.agent["navigation"] == {}


def test_round_and_action_limits_are_distinct_and_typed(tmp_path):
    session, packet, ids, _ = report_session(tmp_path, max_steps=1)
    run(session, packet, ReportModel(ids))
    assert session.agent["navigation"]["test.topic"]["stop_reason"] == "planner_action_budget"
    assert any(gap["kind"] == "planner_action_budget" for gap in session.agent["gaps"])
    session, packet, _, _ = report_session(tmp_path / "illegal", max_steps=1)
    class Illegal:
        def generate_structured(self, **kwargs):
            return '{"actions":[{"tool":"search_report","query":"repeat"}]}', {"prompt_tokens": 10, "generation_tokens": 10}
    run(session, packet, Illegal())
    assert session.agent["executed_actions"] == 0
    assert any(gap["kind"] == "planner_round_budget" for gap in session.agent["gaps"])


def test_limited_ir_adds_scope_reason_without_hiding_planner_cause(tmp_path, monkeypatch):
    _, packet, _, package, metric = build_pipeline(tmp_path / "ir")
    session, _, _, store = setup_session(tmp_path / "state")
    decision = SemanticDecision(task_id=packet.task_id, status="ambiguous", uncertainty_code="model_output_invalid",
        missing_context=["Planner selected repeated searches without inspecting known sources"])
    result = CoreResultMaterializer().materialize(package=package, metric=metric, packet=packet,
        decision=decision, guard=GroundingGuard().validate(packet, decision), attempts=2)
    result.outcome.failure_category = "planner_stalled"
    from esg_targeted.ids import safe_filename
    store.write_json("job", f"decisions/{safe_filename(metric.metric_id)}.json", {"accepted_decision": decision.model_dump(mode="json")})
    workflow = object.__new__(TargetedExtractionWorkflow)
    workflow.artifact_store = store
    workflow.job_store = SimpleNamespace(get=lambda job_id: SimpleNamespace(request=session.request))
    monkeypatch.setattr("esg_targeted.results.stages.publish_stage", lambda *args, **kwargs: None)
    outcome = workflow._commit_metric_result(job_id="job", checkpoint_path="checkpoints/retained.json", metric=metric,
        package=package, result=result, packet_id=packet.packet_id,
        records={key: [] for key in RECORD_LAYOUT}, record_index={key: {} for key in RECORD_LAYOUT},
        validator=ResultContractValidator(package.core), limited_evidence=True, semantic_pipeline_version="report-fact-agent-v5")
    assert outcome["failure_category"] == "planner_stalled"
    assert decision.missing_context[0] in outcome["uncertainty_reason"]
    assert "unresolved IR pages" in outcome["uncertainty_reason"]
    assert not store.read_json("job", "checkpoints/retained.json")["reusable"]
