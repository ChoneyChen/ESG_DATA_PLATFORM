import json
import pytest
from esg_standard_packages.compiler import StandardPackageCompiler
from esg_targeted.config import Settings
from esg_targeted.contracts import ExtractionRequest
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.target_cells import target_cell_contracts
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.models.source_facts import parse_source_output, project_source_facts
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore
from esg_targeted.workflow import TargetedExtractionWorkflow
from tests.test_inventory_and_guard import build_pipeline
from tests.helpers import build_ir_fixture, standard_dist_root
from tests.test_workflow import NotFoundModel


class ReportModel(NotFoundModel):
    """Fake separate navigator/understanding/mapping; never copies IR numbers."""
    def __init__(self, ids, partial_source=False):
        self.ids, self.partial_source = ids, partial_source
        self.calls, self.tools = [], []

    def generate_structured(self, **kwargs):
        context = kwargs["context"]
        if isinstance(context, str):
            context = json.loads(context)
        if "candidate_objects" in context:
            self.tools.append("plan")
            obj = next((o for o in context["candidate_objects"] if o["kind"] == "table"), None)
            pending = context.get("legal_action_frontier", {}).get("actions", {}).get("map_object", {}).get("object_ids", [])
            payload = {"actions": [{"tool": "inspect_object", "object_id": obj["object_id"], "reason": "Read the physical table"}]
                       if obj else [{"tool": "map_object", "object_id": pending[0]}] if pending else
                       [{"tool": "finish", "reason": "Bounded evidence assessed"}]}
        elif "rows" in context:
            self.tools.append("understand")
            self.calls.append(context["object_id"])
            payload = {"rows": [{"group": r["group"], "kind": "quantitative", "source_row_label": "氮氧化物",
                                  "unit": "吨", "fields": {"pollutant": "氮氧化物", "reporting_boundary": "中国境内运营"}}
                                 for r in context["rows"]],
                       "columns": [{"column": c["column"], "fields": {"reporting_period": "2024年"}} for c in context["columns"]]}
        else:
            self.tools.append("map")
            supplied_ids = [m["metric_id"] for m in context["metrics"]]
            payload = {"matches": [{"source_id": r["source_id"], "metric_id": mid,
                                    "match": "match" if mid == self.ids[0] else "different", "reason": "Air, not water"}
                                    for r in context["source_rows"] if r.get("cells") for mid in supplied_ids],
                       "coverage": [{"metric_id": mid, "status": "uncertain" if self.partial_source and mid == self.ids[0] else "reviewed",
                                     "reason": "method evidence missing" if self.partial_source and mid == self.ids[0] else "object assessed"} for mid in supplied_ids]}
        return json.dumps(payload), {"provider": "fake", "model_id": "fake"}


def source_output(packet, ids):
    target = next(t for t in target_cell_contracts(packet) if "125.6" in str(t.value_forms))
    return {
        "source_rows": [{
            "source_id": "R1", "kind": "quantitative", "group": target.group_alias,
            "source_row_label": "氮氧化物", "unit": "吨", "fields": {"pollutant": "氮氧化物"},
            "cells": [{"target_cell": target.alias, "value": "125.6吨", "fields": {"reporting_period": "2024年", "reporting_boundary": "中国境内运营"}}],
            "metric_mappings": [{"metric_id": mid, "match": "match" if i == 0 else "different", "reason": "source supports only the first meaning"} for i, mid in enumerate(ids)],
        }],
        "coverage": [{"metric_id": mid, "status": "reviewed", "reason": "object assessed"} for mid in ids],
    }


def test_source_projection_keeps_cell_identity_and_different_mapping(tmp_path):
    _, _, regions, _, metric = build_pipeline(tmp_path)
    packet = next(p for p in regions if target_cell_contracts(p))
    ids = [metric.metric_id, "metric.other"]
    source = parse_source_output(json.dumps(source_output(packet, ids)), [{"metric_id": mid} for mid in ids])
    decision, audit = project_source_facts(source, packet, visual_used=False)
    assert decision.status == "found", audit
    assert GroundingGuard().validate(packet, decision).accepted
    assert decision.fact_groups[0].group_ref_id == "cell-value"
    other = packet.model_copy(update={"metric": {**packet.metric, "metric_id": ids[1]}})
    other_decision, audit = project_source_facts(source, other, visual_used=False)
    assert other_decision.status == "not_found"
    assert other_decision.fact_groups == []
    assert audit["metric_mappings"][0]["match"] == "different"


def test_bad_cell_does_not_discard_good_source_cell(tmp_path):
    _, _, regions, _, metric = build_pipeline(tmp_path)
    packet = next(p for p in regions if target_cell_contracts(p))
    payload = source_output(packet, [metric.metric_id])
    payload["source_rows"][0]["cells"].append({"target_cell": "T999", "value": "invented"})
    source = parse_source_output(json.dumps(payload), [{"metric_id": metric.metric_id}])
    decision, audit = project_source_facts(source, packet, visual_used=False)
    assert decision.status == "partial"
    assert len(decision.fact_groups) == 1
    assert len(audit["projection_errors"]) == 1


def test_method_columns_map_each_cell_without_copying_the_whole_row(tmp_path):
    _, _, regions, _, metric = build_pipeline(tmp_path, value_columns=[("2024 位置法", "125.6吨"), ("2024 市场法", "250吨")])
    packet = next(p for p in regions if len(target_cell_contracts(p)) == 2)
    targets = target_cell_contracts(packet)
    ids = [metric.metric_id, "metric.market-method"]
    payload = {
        "source_rows": [{"source_id": "R1", "kind": "quantitative", "group": targets[0].group_alias,
                         "fields": {"pollutant": "氮氧化物"}, "unit": "吨",
                         "metric_mappings": [{"metric_id": mid, "match": "match", "reason": "row has both methods"} for mid in ids],
                         "cells": [{"target_cell": t.alias, "value": value, "metric_mappings": [
                             {"metric_id": mid, "match": "match" if i == j else "different", "reason": "method is defined by this column"}
                             for j, mid in enumerate(ids)
                         ]} for i, (t, value) in enumerate(zip(targets, ["125.6吨", "250吨"]))]}],
        "coverage": [{"metric_id": mid, "status": "reviewed", "reason": "both columns assessed"} for mid in ids],
    }
    source = parse_source_output(json.dumps(payload), [{"metric_id": mid} for mid in ids])
    for mid, expected in zip(ids, ["125.6吨", "250吨"]):
        projected = packet.model_copy(update={"metric": {**packet.metric, "metric_id": mid}})
        decision, audit = project_source_facts(source, projected, visual_used=False)
        assert decision.status == "found", audit
        assert len(decision.fact_groups) == 1
        amount_id = next(e["element_id"] for e in packet.elements if e["element_code"] == "emission_amount")
        assert next(a.value_raw for a in decision.fact_groups[0].assignments if a.element_id == amount_id) == expected


@pytest.mark.parametrize("independent_report_topics", [False, True])
def test_topic_unions_evidence_and_reuses_source_across_metrics_and_resume(tmp_path, independent_report_topics):
    _, packet, _, package, metric = build_pipeline(tmp_path)
    topic = "test.shared"
    first = metric.model_copy(update={"disclosure_topic": topic, "disclosure_role": "anchor"})
    second = metric.model_copy(update={"metric_id": "test.metric.second",
        "disclosure_topic": "test.independent" if independent_report_topics else topic, "disclosure_role": "detail"})
    package = package.model_copy(update={"format_version": "2.0", "metrics": [first, second]})
    second_packet = packet.model_copy(update={"task_id": "second-task", "packet_id": "second-packet", "metric": second.model_dump(mode="json")}, deep=True)
    second_context = json.loads(second_packet.model_context)
    second_context["metric"]["id"] = second.metric_id
    second_packet.model_context = json.dumps(second_context)
    packets = {first.metric_id: packet, second.metric_id: second_packet}
    request = ExtractionRequest(ir_run_id="ir", package_id="pkg", package_version="2.0.0", visual_fallback=False,
        report_session_id="report-" + "a" * 32 if independent_report_topics else None)
    store = ArtifactStore(tmp_path / "artifacts")
    def session():
        return DisclosureSession(package=package, metrics=[first, second], request=request, artifact_store=store, job_id="job", max_context_chars=100000)
    def prepare(m):
        return None, [], None, packets[m.metric_id]
    compiler = EvidenceRegionCompiler(max_context_chars=32000, max_groups_per_region=32, max_regions=32, max_output_rows=32, max_span_chars=900)
    model = ReportModel([first.metric_id, second.metric_id])
    active = session()
    region = next(p for p in compiler.split(active.expand(first, packet, prepare)) if target_cell_contracts(p))
    result = active.decide(packet=region, model=model, guard_engine=GroundingGuard(), cancel_check=lambda: False)
    assert result[0].status == "found", result
    second_region = next(p for p in compiler.split(active.expand(second, second_packet, prepare)) if target_cell_contracts(p))
    result = active.decide(packet=second_region, model=model, guard_engine=GroundingGuard(), cancel_check=lambda: False)
    assert result[0].status == "not_found", result
    assert len(model.calls) == 1
    resumed = session()
    resumed.expand(second, second_packet, prepare)
    assert resumed.decide(packet=second_region, model=model, guard_engine=GroundingGuard(), cancel_check=lambda: False)[2] == 0
    assert len(model.calls) == 1


def test_v2_packages_compile_and_pollution_anchors_keep_original_elements():
    root = standard_dist_root().parent
    compiler = StandardPackageCompiler()
    for module, count in [("e1-6", 35), ("e1-5", 23), ("e2-4", 20)]:
        core = root / ("core/1.0.0/core.json" if module == "e2-4" else "core/1.1.0/core.json")
        package = compiler.compile(core_path=core, module_dir=root / "packages/esrs/2023-set1" / module / "2.0.0")
        assert len(package.metrics) == count
        assert all(m.disclosure_topic and m.disclosure_role for m in package.metrics)
        if module == "e2-4":
            original = compiler.compile(core_path=core, module_dir=root / "packages/esrs/2023-set1/e2-4/1.0.0")
            for number in (2, 3, 4):
                mid = f"esrs.2023-set1.e2-4.dp{number:02d}"
                assert [e for e in package.elements if e.metric_id == mid] == [e for e in original.elements if e.metric_id == mid]
                metric = next(m for m in package.metrics if m.metric_id == mid)
                assert sum(m.disclosure_topic == metric.disclosure_topic for m in package.metrics) == 1


@pytest.mark.parametrize("partial_source", [False, True])
@pytest.mark.parametrize("report_session", [False, True])
def test_workflow_projects_one_shared_source_without_changing_core_layout(tmp_path, partial_source, report_session):
    ir_root = tmp_path / "ir"
    build_ir_fixture(ir_root)
    settings = Settings(
        project_root=tmp_path, ir_roots=(ir_root,), standard_dist_root=standard_dist_root(),
        output_root=tmp_path / "output", index_cache_root=tmp_path / "indexes",
        state_db=tmp_path / "jobs.sqlite3", nuextract_model_path=tmp_path / "model",
        embedding_model_path=tmp_path / "embedding", backend_host="127.0.0.1",
        backend_port=18180, frontend_port=18181, require_ready_ir=True,
        retrieval_top_k=12, packet_max_chars=160000, model_max_tokens=512, model_retries=1,
    )
    settings.ensure_runtime_dirs()
    root = standard_dist_root().parent
    package = StandardPackageCompiler().compile(
        core_path=root / "core/1.0.0/core.json",
        module_dir=root / "packages/esrs/2023-set1/e2-4/2.0.0",
    )
    ids = ["esrs.2023-set1.e2-4.dp02", "esrs.2023-set1.e2-4.dp03"]
    # Standalone compatibility needs an explicit shared topic. A modern report
    # must interpret the same physical source for independent numeric singletons
    # too, without changing the air/water meanings in its standard package.
    if not report_session:
        package = package.model_copy(update={"metrics": [m.model_copy(update={"disclosure_topic": "test.shared"}) if m.metric_id in ids else m for m in package.metrics]})
    jobs, store = JobStore(settings.state_db), ArtifactStore(settings.output_root)
    request = ExtractionRequest(ir_run_id="ir-fixture-e2-4", package_id=package.manifest.package_id,
                                package_version="2.0.0", metric_ids=ids, semantic_search=False,
                                visual_fallback=False,
                                report_session_id="report-" + "a" * 32 if report_session else None)
    jobs.create("shared-workflow", request, str(store.job_dir("shared-workflow")))
    store.write_json("shared-workflow", "input/standard-package.json", package.model_dump(mode="json"))
    model = ReportModel(ids, partial_source)
    workflow = TargetedExtractionWorkflow(settings, jobs, store, semantic_model_factory=lambda: model)
    workflow.run("shared-workflow")
    assert jobs.get("shared-workflow").status.value in {"completed", "partial"}, jobs.get("shared-workflow").error
    exported = store.read_json("shared-workflow", "exports/extraction-result.json")
    records = exported["records"]
    assert len(records["quantitative_observations"]) == 1
    assert records["quantitative_observations"][0]["metric_id"] == ids[0]
    assert len(model.calls) == len(set(model.calls))
    plan = store.read_json("shared-workflow", "disclosures/plan.json")
    assert all(topic["route"] == "report_fact_agent" for topic in plan["topics"])
    if not report_session:
        assert any(p["shared_reused"] and p["metric_id"] == ids[1] for p in plan["projections"])
    else:
        assert len(model.calls) == 1  # shared physical source across independent topics
    assert exported["source_facts"]["source_objects"]
    if not report_session:
        assert any(m["match"] == "different" and m["metric_id"] == ids[1] for m in exported["metric_source_mappings"]["mappings"])
    else:
        # This fixture contains air evidence only. Independent water retrieval
        # has no object: preserve a scoped gap rather than invent global absence.
        water = next(o for o in store.read_json("shared-workflow", "manifest.json")["outcomes"] if o["metric_id"] == ids[1])
        assert water["status"] == "ambiguous"
    assert "disclosure_views" in exported
    if partial_source:
        outcome = next(o for o in store.read_json("shared-workflow", "manifest.json")["outcomes"] if o["metric_id"] == ids[0])
        assert outcome["status"] == "partial"
    if report_session and not partial_source:
        # A prior routing version cannot bypass v5 semantics by reusing a valid
        # materialized checkpoint or a complete topic mapping. Neutral physical
        # source interpretation can still be reused under its unchanged identity.
        from esg_targeted.ids import safe_filename
        prior_events = jobs.events("shared-workflow")
        prior_event_id = prior_events[-1]["event_id"]
        prior_plans = model.tools.count("plan")
        for mid in ids:
            path = f"checkpoints/{safe_filename(mid)}.json"
            checkpoint = store.read_json("shared-workflow", path)
            checkpoint["semantic_pipeline_version"] = "report-fact-agent-v4"
            store.write_json("shared-workflow", path, checkpoint)
        for path in (store.job_dir("shared-workflow") / "agent/topics").glob("*.json"):
            topic_state = json.loads(path.read_text())
            topic_state["version"] = "report-fact-agent-v4"
            path.write_text(json.dumps(topic_state))
        state = store.read_json("shared-workflow", "agent/state.json")
        state["version"] = "report-fact-agent-v4"
        store.write_json("shared-workflow", "agent/state.json", state)
        workflow.run("shared-workflow")
        assert model.tools.count("plan") > prior_plans
        assert len(model.calls) == 1  # unchanged neutral reading is reusable
        assert not any(event["stage"] == "checkpoint_resume" for event in jobs.events("shared-workflow", after=prior_event_id))
        assert all(store.read_json("shared-workflow", f"checkpoints/{safe_filename(mid)}.json")["semantic_pipeline_version"]
                   == "report-fact-agent-v8" for mid in ids)


@pytest.mark.parametrize("mode,session_id,format_version,semantic_fill,expected", [
    ("auto", "report-" + "a" * 32, "2.0", True, True),
    ("auto", "report-" + "a" * 32, "2.1", True, True),
    ("auto", None, "2.0", True, False),
    ("per_metric", "report-" + "a" * 32, "2.0", True, False),
    ("auto", "report-" + "a" * 32, "1.0", True, False),
    ("auto", "report-" + "a" * 32, "2.0", False, False),
])
def test_report_semantic_ownership_does_not_depend_on_numeric_siblings(
    tmp_path, mode, session_id, format_version, semantic_fill, expected,
):
    _, _, _, package, metric = build_pipeline(tmp_path)
    singleton = metric.model_copy(update={"disclosure_topic": "test.singleton", "fact_grain": None})
    package = package.model_copy(update={"format_version": format_version, "metrics": [singleton]})
    store = ArtifactStore(tmp_path / "artifacts")
    store.write_json("job", "agent/state.json", {"version": "report-fact-agent-v3", "model_calls": 123})
    request = ExtractionRequest(ir_run_id="ir", package_id="pkg", package_version="2.0.0",
        extraction_mode=mode, report_session_id=session_id, semantic_fill=semantic_fill)
    session = DisclosureSession(package=package, metrics=[singleton], request=request,
        artifact_store=store, job_id="job", max_context_chars=100000)
    assert session.eligible(singleton.metric_id) is expected
    if session_id:
        assert session.agent["version"] == "report-fact-agent-v8"
        assert session.agent["model_calls"] == 0  # old routing-state cannot masquerade as current
    else:
        assert session.agent["version"] == "report-fact-agent-v3"
        assert session.agent["model_calls"] == 123


def test_computation_uses_declared_total_without_adding_category_members():
    from types import SimpleNamespace
    from esg_targeted.results.derivation import DerivationExecutor
    observations = [
        {"observation_id": "total", "value_numeric": "100", "unit_raw": "tCO2e", "reporting_period_raw": "2024"},
        {"observation_id": "member", "value_numeric": "60", "unit_raw": "tCO2e", "reporting_period_raw": "2024"},
    ]
    dimensions = {"total": {"role": ("合计", "total")}, "member": {"role": ("类别", "component")}}
    execute = DerivationExecutor()
    grouped = execute._group_operands(observations, ["reporting_period_raw"], dimensions,
                                      SimpleNamespace(required_semantic_discriminators=[]), total_dimension_id="role")
    assert [str(v["value"]) for v in grouped.values()] == ["100"]
    assert not execute._group_operands(observations[1:], ["reporting_period_raw"], dimensions,
                                      SimpleNamespace(required_semantic_discriminators=[]), total_dimension_id="role")
