"""Synthetic failures exercise real task ownership without business reports."""
from types import SimpleNamespace
import json

import pytest
from pydantic import ValidationError

from esg_targeted.contracts import JobStatus
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.models.report_semantics import (
    AgentPlan, RowMapping, understanding_template, plan_template,
    validate_understanding_payload, expand_understanding, validate_mapping_payload,
)
from esg_targeted.models.token_admission import InputAdmission
from esg_targeted.runtime_budget import ReportRuntimeBudget
from esg_targeted.storage.artifacts import ArtifactStore
from tests.test_report_session import make_service, request
from tests.test_related_explanatory_context import packet as explanatory_packet, inventory as explanatory_inventory, linked_sources


def test_empty_navigation_is_a_protocol_failure():
    for raw in ({}, {"actions": []}):
        with pytest.raises(ValidationError):
            AgentPlan.model_validate(raw)
    assert AgentPlan.model_validate({"actions": [{"tool": "finish", "reason": "No further legal source"}]}).actions[0].tool == "finish"


def test_mapping_never_defaults_to_a_positive_match():
    with pytest.raises(ValidationError):
        RowMapping.model_validate({"source_id": "row", "metric_id": "metric"})


def test_many_invalid_mapping_rows_preserve_valid_siblings_and_full_diagnostics():
    context = {"metrics": [{"metric_id": "metric"}], "source_rows": [
        {"source_id": "source", "cells": [{"column": "c1"}]}]}
    valid = {"source_id": "source", "metric_id": "metric", "columns": ["c1"],
        "match": "match", "reason": "Explicit synthetic source support"}
    malformed = {**valid, "reason": "x" * 1500}
    result, issues = validate_mapping_payload({"matches": [valid, *[malformed for _ in range(20)]],
        "coverage": [{"metric_id": "metric", "status": "reviewed", "reason": "Bounded assessment"}]}, context)
    assert len(result.matches) == 1 and result.matches[0].reason == valid["reason"]
    assert len(issues) == 20
    assert result.coverage[0].status == "uncertain" and len(result.coverage[0].reason) <= 512
    assert sum(len(issue.message) for issue in issues) > len(result.coverage[0].reason)


def test_package_quota_failure_does_not_cancel_sibling_tasks(tmp_path):
    calls = []
    class Workflow:
        def __init__(self, runtime, jobs, artifacts):
            self.jobs = jobs
        def run(self, job_id):
            calls.append(job_id)
            if len(calls) == 1:
                self.jobs.update(job_id, status=JobStatus.FAILED,
                    summary={"failure_category": "budget", "budget_boundary": "package"},
                    error="Package input token budget exhausted")
            else:
                self.jobs.update(job_id, status=JobStatus.COMPLETED)
    service = make_service(tmp_path, Workflow)
    created = service.create(request())
    result = service.run(created["session_id"])
    assert calls == created["job_ids"]
    assert result["execution_status"] == "finished"
    assert result["jobs"][0]["status"] == "failed"
    assert all(job["status"] == "completed" for job in result["jobs"][1:])
    assert created["plan"]["model_budget_policy"]["shared_report_allowance"] is False


def table_context():
    return {"object_id": "table", "kind": "table", "rows": [
        {"group": "G1", "group_id": "first", "label": "First", "cells": [
            {"target_cell": "T1", "cell_id": "cell1", "column": "c1", "raw": "12.30", "grid_reliable": True}]},
        {"group": "G2", "group_id": "second", "label": "Second", "cells": [
            {"target_cell": "T2", "cell_id": "cell2", "column": "c1", "raw": "42", "grid_reliable": True}]}],
        "columns": [{"column": "c1", "headers": ["2025"]}]}


def test_reliable_physical_row_expands_its_own_values_without_echoed_cell_ids():
    context = table_context()
    schema = understanding_template(context=context)
    assert schema["$defs"]["SemanticRow"]["properties"]["target_cells"]["maxItems"] == 0
    # Old provider cell selection is redundant; even a cross-row echo cannot
    # replace the original value or prevent the physical row being read.
    reading, issues = validate_understanding_payload({"rows": [{"group": "G1",
        "kind": "quantitative", "target_cells": ["T2"]}], "columns": [{"column": "c1"}]}, context)
    assert not issues
    rows, errors = expand_understanding(reading, context)
    assert errors == ["Uninterpreted source rows: G2"]
    assert [cell["value"] for cell in rows[0]["cells"]] == ["12.30"]


def test_narrative_and_visual_cell_selection_are_retained():
    context = table_context()
    context["kind"] = "narrative"
    schema = understanding_template(context=context)
    assert "maxItems" not in schema["$defs"]["SemanticRow"]["properties"]["target_cells"] or schema["$defs"]["SemanticRow"]["properties"]["target_cells"]["maxItems"] > 0
    context["kind"] = "table"
    context["rows"][0]["cells"][0]["grid_reliable"] = False
    assert understanding_template(context=context)["$defs"]["SemanticRow"]["properties"]["target_cells"].get("maxItems") != 0


def test_navigation_schema_removes_unreachable_tool_contracts():
    schema = plan_template(context={"metrics": [{"metric_id": "metric"}],
        "legal_action_frontier": {"actions": {"finish": {}}}})
    assert set(schema["$defs"]) == {"FinishAction"}
    assert schema["properties"]["actions"]["minItems"] == 1


def test_disclosure_call_uses_token_admission_and_refines_before_generation(tmp_path):
    session = object.__new__(DisclosureSession)
    session.request = SimpleNamespace(semantic_provider="fake", roles=None)
    session.store, session.job_id, session.jobs = ArtifactStore(tmp_path), "synthetic", None
    session.runtime_budget = ReportRuntimeBudget(tmp_path / "ledger.json", boundary="package")
    session.agent = {"model_calls": 0}
    session._current_calls = []
    session._event = lambda *args: None
    class Model:
        def input_admission(self, **kwargs):
            return InputAdmission(23, "prepared_engine_input_tokens", exact=True)
        def generate_structured(self, **kwargs):
            self._input_admission_callback(InputAdmission(27, "prepared_engine_input_tokens", exact=True))
            return '{}', {"prompt_tokens": 27, "generation_tokens": 2}
    session._call(Model(), tool="agent_plan", context={"text": "中文" * 100},
        instructions="Read", template={}, max_tokens=20)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    call = next(iter(ledger["calls"].values()))
    assert call["input_tokens"] == 27
    assert call["input_admission"]["tokens"] == 27
    assert call["input_token_basis"] == "provider_prompt_tokens"


def test_standard_explains_relation_connects_selected_measurement_to_exact_method_block():
    measured, note, sentence, neighbor, page = linked_sources()
    packet = explanatory_packet()
    method = SimpleNamespace(metric_id="method-synthetic", disclosure_topic="methods")
    measurement = SimpleNamespace(metric_id="measurement-synthetic")
    session = object.__new__(DisclosureSession)
    session.inventory = explanatory_inventory([measured, note, sentence, neighbor, page])
    session.document_context = []
    session.selected_metrics = {method.metric_id: method, measurement.metric_id: measurement}
    session.topics = {"methods": [method]}
    session.pools = {"methods": {"packets": [packet], "members": [method]}}
    session._agent_topic = lambda _: True
    session.package = SimpleNamespace(relations=[SimpleNamespace(relation_type=SimpleNamespace(value="explains"),
        source_metric_ids=[method.metric_id], target_metric_ids=[measurement.metric_id, "unselected-metric"])])
    prepared = []
    def prepare(metric):
        prepared.append(metric.metric_id)
        return None, [], None, explanatory_packet([measured], metric_id=metric.metric_id)
    linked = session.expand(method, packet, prepare)
    objects = session._objects(session._target_packet(linked))
    assert prepared == [measurement.metric_id]
    assert set(objects) == {"block-note"}
    assert objects["block-note"]["kind"] == "block"
    assert objects["block-note"]["physical_object_ids"] == ["block-note"]
    # Later navigation must retain exact block identity without importing
    # another block or a page aggregate when refreshing canonical spans.
    refreshed = session._include(linked, set(linked.allowed_group_ids))
    assert {s.span_id for s in refreshed.spans} == {note.span_id, sentence.span_id}
    assert set(session._objects(session._target_packet(refreshed))) == {"block-note"}
