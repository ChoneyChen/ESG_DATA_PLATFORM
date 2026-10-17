"""Engineering regression: anonymous fixed IR, fake providers and tmp artifacts.

These assertions concern retained evidence and isolation boundaries. They do not
assert ESG accuracy of a real report or start an API, queue or model worker.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from esg_standard_packages.contracts import CompiledStandardPackage
from esg_targeted.contracts import JobStatus, ReportExtractionRequest
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.models.errors import ModelRunFailure, ModelTimedOut
from esg_targeted.models.report_semantics import ProtocolIssue, mapping_template, reference_catalog
from esg_targeted.runtime_budget import BudgetExceeded
from esg_targeted.report_facts import adapt_direct_core_facts
from esg_targeted.results.materializer import CoreResultMaterializer
from esg_targeted.results.spec import RECORD_LAYOUT
from esg_targeted.results.stages import collect_stage, publish_stage, publish_stage_from_inputs
from esg_targeted.results.validation import ResultContractError, ResultContractValidator
from esg_targeted.models.response_adapter import DirectFillResponseAdapter
from tests.test_inventory_and_guard import build_pipeline, complete_row
from tests.test_report_agent_loop import setup_session
from tests.test_report_session import make_service


def _object(count=2, *, two_columns=False):
    rows = []
    for index in range(1, count + 1):
        cells = [{"target_cell": f"T{index}a", "cell_id": f"cell-{index}-a", "column": "amount-a",
                  "raw": str(index * 10), "unit": "吨", "grid_reliable": True,
                  "column_headers": ["2024年", "单位A"], "locators": [{"ir_object_type": "cell",
                      "ir_object_id": f"cell-{index}-a", "pdf_page_index": 0}]}]
        if two_columns:
            cells.append({**cells[0], "target_cell": f"T{index}b", "cell_id": f"cell-{index}-b",
                          "column": "amount-b", "raw": str(index * 20), "column_headers": ["2024年", "单位B"],
                          "locators": [{"ir_object_type": "cell", "ir_object_id": f"cell-{index}-b", "pdf_page_index": 0}]})
        rows.append({"group": f"G{index}", "group_id": f"row-{index}", "label": f"匿名指标{index}",
                     "text": f"匿名指标{index}，2024年", "cells": cells})
    return {"object_id": "anonymous-table", "kind": "table", "image_path": None,
            "columns": [{"column": column, "header_path": ["2024年"]} for column in
                        (["amount-a", "amount-b"] if two_columns else ["amount-a"])], "rows": rows}


def _understanding(context):
    return {"rows": [{"group": row["group"], "kind": "quantitative",
                       "source_row_label": row.get("label", "匿名指标"), "unit": "吨", "fields": []}
                      for row in context["rows"]],
            "columns": [{"column": column["column"], "fields": [{"name": "reporting_period", "value": "2024年"}]}
                        for column in context["columns"]]}


class RecordingModel:
    provider_name = "synthetic"
    model_id = "anonymous-fake-model"
    def __init__(self, response):
        self.response, self.contexts = response, []
    def generate_structured(self, **kwargs):
        context = kwargs["context"]
        self.contexts.append(deepcopy(context))
        return json.dumps(self.response(context), ensure_ascii=False), {"prompt_tokens": 20, "generation_tokens": 10}


def _session(tmp_path):
    session, packet, ids, store = setup_session(tmp_path)
    session._current_calls, session._linked_context = [], []
    return session, packet, ids, store


def test_structure_rows_complete_a_read_without_creating_report_facts(tmp_path):
    session, _, _, store = _session(tmp_path)
    obj = _object()
    for row in obj["rows"]:
        row["cells"] = []
    model = RecordingModel(lambda context: {"rows": [
        {"group": row["group"], "kind": "context", "context_role": "header" if index == 0 else "definition",
         "context_reason": "Read supplied structure"} for index, row in enumerate(context["rows"])]})
    source = session._understand(obj, model, lambda: False)
    assert source["complete"] and source["processed_groups"] == ["row-1", "row-2"]
    assert len(source["rows"]) == 2 and all(row["kind"] == "context" for row in source["rows"])
    assert source["facts"] == []
    session._understand(obj, model, lambda: False)
    assert len(model.contexts) == 1
    assert store.read_json("job", "agent/sources/anonymous-table.json")["facts"] == []


def test_bad_row_is_quarantined_without_discarding_legal_sibling(tmp_path):
    session, _, _, store = _session(tmp_path)
    obj = _object()
    def response(context):
        if "original_task" in context:
            return {"rows": [{"group": "G2", "kind": "invented-kind", "fields": None}]}
        payload = _understanding(context)
        payload["rows"][1] = {"group": "G2", "kind": "invented-kind", "fields": None}
        return payload
    source = session._understand(obj, RecordingModel(response), lambda: False)
    assert not source["complete"]
    assert source["processed_groups"] == ["row-1"]
    assert {fact["value_raw"] for fact in source["facts"]} == {"10"}
    assert any(gap["kind"] == "protocol" and "row-2" in gap["group_ids"] for gap in source["gaps"])
    assert all("row-1" not in gap.get("group_ids", []) for gap in source["gaps"])
    assert store.read_json("job", "agent/sources/anonymous-table.json")["facts"] == source["facts"]


def test_length_splits_source_scope_without_format_repair_or_value_duplication(tmp_path):
    session, _, _, _ = _session(tmp_path)
    obj = _object(4)
    class Truncated(RecordingModel):
        def generate_structured(self, **kwargs):
            context = kwargs["context"]
            self.contexts.append(deepcopy(context))
            if len(context.get("rows", [])) > 1:
                raise ModelRunFailure("output cap", category="generation_incomplete", raw_output='{"rows":[',
                                      telemetry={"finish_reason": "length", "generation_tokens": 4096})
            assert "original_task" not in context, "Truncation must split; it is not a syntax repair task"
            return json.dumps(_understanding(context)), {"prompt_tokens": 20, "generation_tokens": 10}
    model = Truncated(None)
    source = session._understand(obj, model, lambda: False)
    assert source["complete"] and len(source["facts"]) == 4
    assert {fact["value_raw"] for fact in source["facts"]} == {"10", "20", "30", "40"}
    assert len({fact["source_fact_id"] for fact in source["facts"]}) == 4
    assert all("original_task" not in context for context in model.contexts)
    assert session.runtime_budget.snapshot()["usage"]["format_repairs"] == 0


def test_explicit_processed_group_and_cell_reread_preserves_unselected_cells(tmp_path):
    session, _, _, _ = _session(tmp_path)
    obj = _object(two_columns=True)
    model = RecordingModel(_understanding)
    initial = session._understand(obj, model, lambda: False)
    before = {fact["source_fact_id"]: deepcopy(fact) for fact in initial["facts"]}
    grouped = session._understand(obj, model, lambda: False, group_ids=["row-1"], context_kind="cell")
    assert len(grouped["facts"]) == 4
    reread = session._understand(obj, model, lambda: False, cell_ids=["cell-1-a"], context_kind="cell")
    assert len(model.contexts) == 3
    assert [row["group_id"] for row in model.contexts[1]["rows"]] == ["row-1"]
    assert [cell["cell_id"] for row in model.contexts[2]["rows"] for cell in row["cells"]] == ["cell-1-a"]
    assert len(reread["facts"]) == 4 and {fact["value_raw"] for fact in reread["facts"]} == {"10", "20", "40"}
    assert {fact["source_fact_id"]: fact for fact in reread["facts"]} == before
    assert {cell["cell_id"] for row in reread["rows"] for cell in row["cells"]} == {
        "cell-1-a", "cell-1-b", "cell-2-a", "cell-2-b"}


def test_old_topic_failure_does_not_mark_new_topic_incomplete(tmp_path):
    old, packet, _, store = _session(tmp_path)
    first, second = old.package.metrics
    first = first.model_copy(update={"disclosure_topic": "topic.failed", "fact_grain": "test.quantity"})
    second = second.model_copy(update={"disclosure_topic": "topic.healthy", "fact_grain": "test.quantity"})
    package = old.package.model_copy(update={"metrics": [first, second]})
    session = DisclosureSession(package=package, metrics=[first, second], request=old.request,
        artifact_store=store, job_id="two-topic-job", max_context_chars=32000, inventory=old.inventory)
    for member in (first, second):
        session.expand(member, packet, lambda metric: (None, [], None, packet))
    packet = session._target_packet(packet)
    rounds = {"topic.failed": 0, "topic.healthy": 0}
    def response(context):
        if "candidate_objects" in context:
            topic = context["topic_id"]
            rounds[topic] += 1
            if rounds[topic] > 1:
                return {"actions": [{"tool": "finish"}]}
            return {"actions": [{"tool": "inspect_object", "object_id": "invalid-physical-reference" if topic == "topic.failed" else context["candidate_objects"][0]["object_id"]}]}
        if "rows" in context:
            return _understanding(context)
        return {"matches": [], "coverage": [{"metric_id": metric["metric_id"], "status": "reviewed", "reason": "Bounded object assessed"}
                                               for metric in context["metrics"]]}
    model = RecordingModel(response)
    session._current_calls = []
    session._run_topic("topic.failed", packet, model, lambda: False)
    assert session.agent["steps"][0]["status"] == "failed"
    healthy = session._run_topic("topic.healthy", packet, model, lambda: False)
    persisted = store.read_json("two-topic-job", "agent/topics/topic.healthy.json")
    assert not healthy["failed"] and healthy["missing_context"] == []
    assert persisted["complete"]
    assert all(gap.get("topic_id") != "topic.healthy" for gap in session.agent.get("gaps", []))
    assert any(gap.get("topic_id") == "topic.failed" for gap in session.agent.get("gaps", []))


def test_protocol_problem_cannot_request_local_ir_repair(tmp_path):
    session, packet, ids, store = _session(tmp_path)
    plans = []
    def response(context):
        assert "candidate_objects" in context
        plans.append(context)
        if len(plans) == 1:
            return {"actions": [{"tool": "request_local_repair", "object_id": context["candidate_objects"][0]["object_id"],
                "metric_id": ids[0], "gap_kind": "header", "reason": "Protocol parser rejected null IDs"}]}
        return {"actions": [{"tool": "finish"}]}
    session.decide(packet=packet, model=RecordingModel(response), guard_engine=GroundingGuard(), cancel_check=lambda: False)
    assert not store.exists("job", "agent/repair-requests.json")
    assert session.agent.get("pending_repairs", []) == []
    assert session.agent["steps"][0]["status"] == "failed"
    assert session.agent["steps"][0]["failure_category"] == "protocol"


@pytest.mark.parametrize("category", ["timeout", "budget"])
def test_mapping_slice_failure_retains_legal_siblings_and_resumes_fragments(tmp_path, category):
    session, _, _, store = _session(tmp_path)
    session._current_topic = "test.topic"
    source = {"rows": [{"source_id": f"row-{index}:1", "group_id": f"row-{index}", "kind": "quantitative",
        "fields": {}, "semantics": {}, "unit": "吨", "cells": [{"column": "amount-a", "fields": {}, "unit": "吨"}]}
        for index in range(21)], "errors": [], "linked_context": []}
    members = session.package.metrics
    successful = []
    class Partial(RecordingModel):
        def generate_structured(self, **kwargs):
            context = kwargs["context"]
            self.contexts.append(deepcopy(context))
            if len(self.contexts) == 2:
                if category == "timeout":
                    raise ModelTimedOut(600)
                raise BudgetExceeded("Synthetic package budget boundary", {}, boundary="package")
            successful.extend(row["source_id"] for row in context["source_rows"])
            return json.dumps({"matches": [{"source_id": row["source_id"], "metric_id": members[0].metric_id, "columns": ["amount-a"], "match": "match", "reason": "Explicit synthetic source support"}
                                           for row in context["source_rows"]],
                "coverage": [{"metric_id": metric["metric_id"], "status": "reviewed", "reason": "Slice assessed"} for metric in context["metrics"]]}), {
                    "prompt_tokens": 20, "generation_tokens": 10}
    partial = Partial(None)
    mapping = session._map({"object_id": "anonymous-table"}, source, members, partial, lambda: False)
    assert {match.source_id for match in mapping.matches} == set(successful) and successful
    assert all(item.status == "uncertain" for item in mapping.coverage)
    fragments = list((store.job_dir("job") / "agent/mapping-fragments/test.topic").glob("*.json"))
    retained = {match["source_id"] for path in fragments for match in json.loads(path.read_text())["mapping"]["matches"]}
    assert retained == set(successful)
    retry = RecordingModel(lambda context: {"matches": [{"source_id": row["source_id"], "metric_id": members[0].metric_id, "columns": ["amount-a"], "match": "match", "reason": "Explicit synthetic source support"}
        for row in context["source_rows"]], "coverage": [{"metric_id": metric["metric_id"], "status": "reviewed", "reason": "Slice assessed"}
        for metric in context["metrics"]]})
    completed = session._map({"object_id": "anonymous-table"}, source, members, retry, lambda: False)
    assert len(completed.matches) == 21 and all(item.status == "reviewed" for item in completed.coverage)
    retry_ids = {row["source_id"] for context in retry.contexts for row in context["source_rows"]}
    assert not retry_ids.intersection(successful), "Legal saved siblings must not invoke the model again"


def test_format_repair_contains_original_bounded_task_and_allowed_reference_context(tmp_path):
    session, _, ids, _ = _session(tmp_path)
    context = {"object_id": "anonymous-table", "source_rows": [{"source_id": "source-row-1", "kind": "assertion", "cells": []}],
        "metrics": [{"metric_id": ids[0]}], "linked_context": [{"text": "Anonymous verbatim statement"}]}
    context["allowed_refs"] = reference_catalog(context)
    schema = mapping_template(context=context)
    model = RecordingModel(lambda repair: {"matches": [], "coverage": []})
    session._repair('broken output with null IDs', [ProtocolIssue(stage="schema", path="matches.0.metric_id", message="ID must not be null")],
        model=model, task="metric_mapping", context=context, template=schema, cancel_check=lambda: False)
    repair = model.contexts[0]
    assert repair["task"] == "metric_mapping" and repair["original_task"] == context
    assert repair["original_task"]["allowed_refs"]["metrics"] == [ids[0]]
    assert repair["original_task"]["allowed_refs"]["sources"] == ["source-row-1"]
    assert "allowed_contract" not in repair, "The contract is passed once through the provider template, not duplicated in evidence"
    assert repair["format_errors"][0]["path"] == "matches.0.metric_id"
    assert session.runtime_budget.snapshot()["usage"]["format_repairs"] == 1


def test_mapping_length_splits_rows_and_metric_cards_without_format_repair(tmp_path):
    session, _, _, _ = _session(tmp_path)
    session._current_topic = "test.topic"
    source = {"rows": [{"source_id": f"row-{index}:1", "group_id": f"row-{index}", "kind": "quantitative",
        "fields": {}, "cells": [{"column": "amount-a", "fields": {}, "unit": "吨"}]} for index in range(4)], "errors": []}
    members = session.package.metrics
    class Length(RecordingModel):
        def generate_structured(self, **kwargs):
            context = kwargs["context"]
            self.contexts.append(deepcopy(context))
            assert "original_task" not in context
            if len(context["source_rows"]) > 1 or len(context["metrics"]) > 1:
                raise ModelRunFailure("output cap", category="generation_incomplete", raw_output='{"matches":[',
                    telemetry={"finish_reason": "length", "generation_tokens": 4096})
            return json.dumps({"matches": [{"source_id": context["source_rows"][0]["source_id"],
                "metric_id": context["metrics"][0]["metric_id"], "columns": ["amount-a"], "match": "match"}],
                "coverage": [{"metric_id": context["metrics"][0]["metric_id"], "status": "reviewed", "reason": "Slice assessed"}]}), {
                    "prompt_tokens": 20, "generation_tokens": 10}
    model = Length(None)
    mapping = session._map({"object_id": "anonymous-table"}, source, members, model, lambda: False)
    expected = {(row["source_id"], member.metric_id) for row in source["rows"] for member in members}
    assert {(match.source_id, match.metric_id) for match in mapping.matches} == expected
    assert len(mapping.matches) == len(expected) and all(coverage.status == "reviewed" for coverage in mapping.coverage)
    assert session.runtime_budget.snapshot()["usage"]["format_repairs"] == 0


def _stage_session(tmp_path):
    service = make_service(tmp_path / "session")
    metric_ids = [f"esrs.2023-set1.e2-4.dp{index:02d}" for index in (2, 3, 4)]
    created = service.create(ReportExtractionRequest.model_validate({
        "input": {"kind": "document_ir", "ir_run_id": "ir-fixture-e2-4"},
        "standards": [{"package_id": "esrs.2023-set1.e2-4", "package_version": "2.0.0", "metric_ids": metric_ids}]}))
    job_id = created["job_ids"][0]
    child = service.jobs.get(job_id)
    frozen_manifest = json.loads((service.ir_catalog.resolve(child.request.ir_run_id) / "manifest.json").read_text(encoding="utf-8"))
    service.artifacts.write_json(job_id, "input/ir-manifest.json", frozen_manifest)
    package = CompiledStandardPackage.model_validate(service.artifacts.read_json(job_id, "input/standard-package.json"))
    _, selection, regions, _, _ = build_pipeline(tmp_path / "synthetic-source")
    packet = regions[0]
    decision, _ = DirectFillResponseAdapter().parse(json.dumps({"task_id": packet.task_id, "status": "found",
        "rows": [complete_row(packet, "氮氧化物", "125.6吨", "2024", "中国境内运营")], "uncertainty_code": "none"}), packet=packet)
    guard = GroundingGuard().validate(packet, decision)
    assert guard.accepted, guard.feedback
    metric = next(metric for metric in package.metrics if metric.metric_id == metric_ids[0])
    materialized = CoreResultMaterializer().materialize(package=package, metric=metric, packet=selection,
        decision=decision, guard=guard, attempts=1)
    records = {collection: getattr(materialized, collection) for collection in RECORD_LAYOUT}
    validation = ResultContractValidator(package.core).validate(records)
    service.artifacts.write_json(job_id, "checkpoints/validated-anonymous.json", {
        "metric_id": metric_ids[0], "package_source_digest": package.compilation.source_digest,
        "records": records, "outcome": materialized.outcome.model_dump(mode="json"), "contract_validation": validation})
    facts, bindings = adapt_direct_core_facts(records, package, request=child.request, bound_core_ids=set())
    assert len(facts) == 1 and bindings
    candidate = {**facts[0], "source_fact_id": "source-anonymous-unmapped", "source_key": "source-key-unmapped",
                 "source_object_id": "cell-unmapped", "source_row_id": "row-unmapped:1", "value_raw": "77",
                 "physical_sources": [{"ir_object_type": "cell", "ir_object_id": "cell-unmapped", "pdf_page_index": 0}]}
    service.artifacts.write_json(job_id, "agent/sources/anonymous-table.json", {
        "object_id": "anonymous-table", "source_key": "source-key-anonymous", "facts": [*facts, candidate],
        "rows": [], "complete": False, "gaps": []})
    service.artifacts.write_json(job_id, "disclosures/projections/anonymous.json", {"metric_id": metric_ids[0], "core_bindings": bindings})
    service.artifacts.write_json(job_id, "agent/state.json", {"state": "running", "current_metric_id": metric_ids[1],
        "steps": [{"status": "running", "metric_ids": [metric_ids[1]]}], "gaps": []})
    service.artifacts.write_json(job_id, "progress/metric-attempts.json", {metric_ids[0]: {"status": "processed"}, metric_ids[1]: {"status": "running"}})
    service.jobs.update(job_id, status=JobStatus.RUNNING, progress_current=1, progress_total=3)
    return service, created["session_id"], job_id, metric_ids, package, facts, bindings


def test_partial_stage_export_preserves_core_graph_and_unfinished_progress(tmp_path):
    service, _, job_id, metric_ids, package, _, _ = _stage_session(tmp_path)
    active = publish_stage(service.artifacts, job_id, execution_status="running")
    active_counts = active["summary"]["counts"]
    assert {key: active_counts[key] for key in ("processed_metrics", "processing_metrics", "unfinished_metrics", "not_executed_metrics")} == {
        "processed_metrics": 1, "processing_metrics": 1, "unfinished_metrics": 0, "not_executed_metrics": 1}
    assert active["summary"]["processing_metric_ids"] == [metric_ids[1]]
    stopped = publish_stage_from_inputs(service.artifacts.job_dir(job_id), job_id, "interrupted",
        "Synthetic external deadline", export_bundle=True)
    summary = stopped["summary"]
    assert summary["execution_status"] == "interrupted" and summary["result_status"] == "partial"
    assert {key: summary["counts"][key] for key in ("processed_metrics", "processing_metrics", "unfinished_metrics", "not_executed_metrics")} == {
        "processed_metrics": 1, "processing_metrics": 0, "unfinished_metrics": 1, "not_executed_metrics": 1}
    assert summary["unfinished_metric_ids"] == [metric_ids[1]]
    assert summary["not_executed_metric_ids"] == [metric_ids[2]]
    bundle = service.artifacts.read_json(job_id, summary['delivery_artifact']['bundle_relative_path'] + '/exports/extraction-result.json')
    assert not service.artifacts.exists(job_id, 'manifest.json'), 'Terminal materialization must not invent or overwrite the original export'
    assert len(bundle["records"]["quantitative_observations"]) == 1
    assert len(bundle["source_facts"]["facts"]) == 2, "Unmapped candidates remain reviewable without becoming Core"
    ResultContractValidator(package.core).validate(bundle["records"])
    rebuilt = collect_stage(service.artifacts, job_id)
    assert rebuilt["summary"]["counts"] == summary["counts"], "Reading an exported partial Bundle must retain the stage partition"
    assert rebuilt["summary"]["unfinished_metric_ids"] == [metric_ids[1]]
    assert rebuilt["summary"]["not_executed_metric_ids"] == [metric_ids[2]]


def test_missing_frozen_provenance_blocks_bundle_but_retains_reviewable_candidates(tmp_path):
    service, _, job_id, _, _, _, _ = _stage_session(tmp_path)
    (service.artifacts.job_dir(job_id) / "input/ir-manifest.json").unlink()
    with pytest.raises(ResultContractError, match="frozen document and IR identity"):
        publish_stage_from_inputs(service.artifacts.job_dir(job_id), job_id, "interrupted",
            "Synthetic timeout", export_bundle=True)
    snapshot = service.artifacts.read_json(job_id, "progress/snapshot.json")
    assert len(snapshot["source_facts"]["facts"]) == 2
    assert snapshot["summary"]["execution_status"] == "interrupted"
    assert snapshot["summary"]["counts"]["processing_metrics"] == 0
    assert snapshot["summary"]["counts"]["unfinished_metrics"] == 1
    assert "Synthetic timeout" in snapshot["summary"]["errors"]
    assert not service.artifacts.exists(job_id, "manifest.json")
    assert not service.artifacts.exists(job_id, "exports/extraction-result.json")
    assert service.artifacts.exists(job_id, "agent/sources/anonymous-table.json")


def test_running_session_can_review_core_and_candidates_before_final_bundle(tmp_path, monkeypatch):
    from esg_targeted.storage.job_store import JobStore

    service, session_id, job_id, _, _, _, bindings = _stage_session(tmp_path)
    snapshot = publish_stage(service.artifacts, job_id, execution_status="running")
    # A synthetic liveness observation, never a real worker or queue dispatch.
    monkeypatch.setattr(JobStore, "_worker_alive", staticmethod(lambda *_: True))
    service._mutate(session_id, lambda state: state.update(execution_status="running",
        owner_pid=424242, owner_started="synthetic-owner"))
    detail, facts = service.detail(session_id), service.facts(session_id)
    assert detail["execution_status"] == "running" and detail["result_status"] == "partial"
    assert {key: detail["counts"][key] for key in ("processed_metrics", "processing_metrics", "unfinished_metrics", "not_executed_metrics")} == {
        "processed_metrics": 1, "processing_metrics": 1, "unfinished_metrics": 0, "not_executed_metrics": 1}
    assert detail["stages"][0]["counts"] == snapshot["summary"]["counts"]
    assert detail["counts"]["candidate_source_facts"] == len(facts["facts"]) == 2
    assert len(facts["core_bindings"]) == len(bindings)
    assert detail["counts"]["evidence_references"] == len(facts["evidence_references"])
    assert len(facts["source_objects"]) == 1
    assert not service.artifacts.exists(job_id, "manifest.json")
    assert not service.artifacts.exists(job_id, "exports/extraction-result.json")


def test_session_details_facts_and_partial_stage_agree_after_interruption(tmp_path):
    service, session_id, job_id, metric_ids, _, _, bindings = _stage_session(tmp_path)
    stopped = publish_stage_from_inputs(service.artifacts.job_dir(job_id), job_id, "interrupted", "Synthetic timeout", export_bundle=True)
    service.jobs.update(job_id, status=JobStatus.INTERRUPTED, summary=stopped["summary"], error="Synthetic timeout")
    service._mutate(session_id, lambda state: state.update(execution_status="interrupted", owner_pid=None, owner_started=None))
    detail = service.detail(session_id)
    facts = service.facts(session_id)
    assert detail["execution_status"] == "interrupted" and detail["result_status"] == "partial"
    assert {key: detail["counts"][key] for key in ("processed_metrics", "processing_metrics", "unfinished_metrics", "not_executed_metrics")} == {
        "processed_metrics": 1, "processing_metrics": 0, "unfinished_metrics": 1, "not_executed_metrics": 1}
    assert detail["stages"][0]["counts"] == stopped["summary"]["counts"]
    assert detail["counts"]["candidate_source_facts"] == len(facts["facts"]) == 2
    assert detail["counts"]["core_bindings"] == len(facts["core_bindings"]) == len(bindings)
    assert detail["counts"]["evidence_references"] == len(facts["evidence_references"])
    assert facts["source_objects"], "Child source caches must remain reviewable through the report facts API"
    assert detail["counts"]["source_objects"] == len(facts["source_objects"]) == 1
    assert detail["database_status"] == "not_imported"
    assert "Synthetic timeout" in detail["errors"]


def test_manifest_declared_counts_without_bundle_cannot_become_validated_core(tmp_path):
    service = make_service(tmp_path / "session")
    request = ReportExtractionRequest.model_validate({
        "input": {"kind": "document_ir", "ir_run_id": "ir-fixture-e2-4"},
        "standards": [{"package_id": "esrs.2023-set1.e2-4", "package_version": "2.0.0",
                       "metric_ids": ["esrs.2023-set1.e2-4.dp02"]}]})
    created = service.create(request)
    job_id = created["job_ids"][0]
    declared = {"record_counts": {"quantitative_observations": 7, "qualitative_assertions": 0},
                "contract_validation": {"status": "passed"}, "execution_status": "completed"}
    service.artifacts.write_json(job_id, "manifest.json", declared)
    service.jobs.update(job_id, status=JobStatus.COMPLETED, summary=declared)
    service._mutate(created["session_id"], lambda state: state.update(execution_status="finished"))
    snapshot = collect_stage(service.artifacts, job_id)
    assert snapshot["summary"]["counts"]["core_facts"] == 0
    assert all(not rows for rows in snapshot["records"].values())
    detail = service.detail(created["session_id"])
    assert detail["counts"]["core_facts"] == 0
    assert detail["result_status"] in {"not_evaluated", "needs_confirmation"}, "A declared count cannot establish Core availability or proof of non-disclosure"
    assert service.facts(created["session_id"])["facts"] == []


def _without_observations(records):
    removed = {row["observation_id"] for row in records["quantitative_observations"]}
    result = deepcopy(records)
    result["quantitative_observations"] = []
    for collection in ("attribute_values", "dimension_values", "evidence_references"):
        result[collection] = [row for row in result[collection]
                              if row.get("parent_record_id") not in removed and row.get("source_record_id") not in removed]
    for task in result["reporting_tasks"]:
        task.update(result_count=0, evidence_count=0, task_status="partial", found_status="uncertain")
    return result, removed


def test_postprocessing_verdict_survives_export_interruption_without_old_bindings(tmp_path):
    service, session_id, job_id, _, package, _, bindings = _stage_session(tmp_path)
    before = collect_stage(service.artifacts, job_id, use_snapshot=False)
    corrected, removed = _without_observations(before["records"])
    ResultContractValidator(package.core).validate(corrected)
    # A durable family verdict rejected this Core; its physical source remains
    # a legal review candidate. Old exported bindings/evidence still exist.
    service.artifacts.write_json(job_id, "results/metric-source-mappings.json", {"core_bindings": bindings})
    service.artifacts.write_jsonl(job_id, "results/evidence-references.jsonl", before["records"]["evidence_references"])
    service.artifacts.write_json(job_id, "progress/core-postprocessing.json", {
        "package_source_digest": package.compilation.source_digest, "records": corrected,
        "outcomes": [{**outcome, "status": "ambiguous", "semantic_decision_status": "unresolved"}
                     for outcome in before["summary"]["outcomes"]]})
    stopped = publish_stage_from_inputs(service.artifacts.job_dir(job_id), job_id, "interrupted", "Export interrupted", export_bundle=True)
    assert stopped["summary"]["counts"]["core_facts"] == 0
    assert not service.artifacts.exists(job_id, "exports/extraction-result.json")
    facts = service.facts(session_id)
    assert len(facts["facts"]) == 2 and not facts["core_bindings"]
    assert all(row.get("source_record_id") not in removed for row in facts["evidence_references"])
    assert service.detail(session_id)["counts"]["core_facts"] == 0
    assert service.detail(session_id)["counts"]["evidence_references"] == len(facts["evidence_references"])


def test_changed_internal_operand_reopens_selected_postprocessing_target(tmp_path):
    service, session_id, job_id, metric_ids, package, _, _ = _stage_session(tmp_path)
    before = collect_stage(service.artifacts, job_id, use_snapshot=False)
    checkpoint = service.artifacts.read_json(job_id, "checkpoints/validated-anonymous.json")
    checkpoint["records"], _ = _without_observations(checkpoint["records"])
    checkpoint["outcome"].update(status="not_found", found_status="not_found")
    service.artifacts.write_json(job_id, "checkpoints/validated-anonymous.json", checkpoint)
    service.artifacts.write_json(job_id, "progress/core-postprocessing.json", {
        "package_source_digest": package.compilation.source_digest, "records": before["records"],
        "outcomes": before["summary"]["outcomes"]})
    assert collect_stage(service.artifacts, job_id, use_snapshot=False)["summary"]["counts"]["core_facts"] == 1
    service.artifacts.write_json(job_id, "checkpoints/internal-operand.json", {
        "metric_id": "anonymous-internal-operand", "package_source_digest": package.compilation.source_digest,
        "records": {}, "reason": "Internal source identity changed"})
    service.jobs.update(job_id, status=JobStatus.COMPLETED)
    service._mutate(session_id, lambda state: state.update(execution_status="finished"))
    stage = collect_stage(service.artifacts, job_id, use_snapshot=False)
    assert stage["summary"]["counts"]["core_facts"] == 0
    assert metric_ids[0] in stage["summary"]["stale_projection_metric_ids"]
    assert metric_ids[0] not in {outcome["metric_id"] for outcome in stage["summary"]["outcomes"]}
    detail = service.detail(session_id)
    assert detail["result_status"] != "usable"
    assert any(action["action"] == "resume" for action in detail["available_actions"])
    from esg_targeted.inspection import ResultInspectionService
    inspection = ResultInspectionService(service.artifacts).build(service.jobs.get(job_id))
    assert inspection.metrics[0].processing_status == "unfinished"
    assert inspection.metrics[0].found_status == "uncertain", "An old not_found checkpoint cannot conclude a target awaiting recalculation"


def test_stale_dependency_verdict_is_not_applied_to_changed_inputs(tmp_path):
    service, _, job_id, metric_ids, _, _, _ = _stage_session(tmp_path)
    service.artifacts.write_json(job_id, "derivations.json", {"rules": [{"rule_id": "anonymous-rule",
        "target_metric_id": metric_ids[0], "dependency_status": "ready", "dependency_blocks": []}]})
    fresh = collect_stage(service.artifacts, job_id, use_snapshot=False)
    assert fresh["summary"]["outcomes"][0]["dependency_status"] == "ready"
    service.artifacts.write_json(job_id, "checkpoints/internal-operand.json", {"metric_id": "anonymous-internal", "records": {}})
    changed = collect_stage(service.artifacts, job_id, use_snapshot=False)
    assert changed["summary"]["outcomes"][0]["dependency_status"] == "not_applicable"


def test_shared_budget_stops_report_before_entering_later_standard(tmp_path):
    from esg_targeted.contracts import EvidenceBudget
    calls = []
    class BudgetFailureWorkflow:
        def __init__(self, runtime, jobs, artifacts):
            self.jobs = jobs
        def run(self, job_id):
            calls.append(job_id)
            self.jobs.update(job_id, status=JobStatus.FAILED, error="Synthetic report budget exhausted",
                summary={"failure_category": "budget", "budget_boundary": "report"})
    service = make_service(tmp_path, BudgetFailureWorkflow)
    from tests.test_report_session import request
    created = service.create(request().model_copy(update={"budget": EvidenceBudget(max_model_calls=1)}))
    with pytest.raises(BudgetExceeded, match="later standard projections remain unexecuted"):
        service.run(created["session_id"])
    assert calls == [created["job_ids"][0]]
    assert all(service.jobs.get(job_id).status == JobStatus.INTERRUPTED and service.jobs.get(job_id).stage == 'not_executed'
               for job_id in created["job_ids"][1:])
    assert service.detail(created["session_id"])["counts"]["not_executed_metrics"] == 3


def test_direct_pipeline_budget_failure_stops_later_metrics_and_keeps_first_core(tmp_path):
    from esg_targeted.workflow import TargetedExtractionWorkflow
    from tests.test_workflow import FakeEmbeddingProvider, FoundModel
    model_calls = []
    class CountedModel(FoundModel):
        def decide(self, packet, **kwargs):
            model_calls.append(packet.metric["metric_id"])
            return super().decide(packet, **kwargs)
    class OfflineWorkflow:
        def __init__(self, runtime, jobs, artifacts):
            self.workflow = TargetedExtractionWorkflow(runtime, jobs, artifacts,
                embedding_provider_factory=FakeEmbeddingProvider, semantic_model_factory=CountedModel)
        def run(self, job_id):
            self.workflow.run(job_id)
    service = make_service(tmp_path, OfflineWorkflow)
    metric_ids = [f"esrs.2023-set1.e2-4.dp{index:02d}" for index in (2, 3, 4)]
    created = service.create(ReportExtractionRequest.model_validate({
        "input": {"kind": "document_ir", "ir_run_id": "ir-fixture-e2-4"},
        "standards": [{"package_id": "esrs.2023-set1.e2-4", "package_version": "1.0.0", "metric_ids": metric_ids}],
        "budget": {"max_model_calls": 1}}))
    with pytest.raises(BudgetExceeded):
        service.run(created["session_id"])
    assert model_calls == [metric_ids[0]]
    stage = collect_stage(service.artifacts, created["job_ids"][0])
    assert stage["summary"]["counts"]["core_facts"] == 1
    assert stage["summary"]["not_executed_metric_ids"] == metric_ids[1:]
    failed = next(outcome for outcome in stage["summary"]["outcomes"] if outcome["metric_id"] == metric_ids[0])
    assert failed["failure_category"] == "budget" and failed["status"] == "partial"
    assert failed["source_validation_status"] == "passed", "Accepted first-region proof survives a later-region budget failure"
