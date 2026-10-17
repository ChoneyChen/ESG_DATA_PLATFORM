"""Report Session upgrade contracts; fixed IR and fake models only."""
import json
import pytest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from esg_targeted.config import Settings
from esg_targeted.contracts import ExtractionRequest, RoleConfig, SemanticAssignment, SemanticDecision, SemanticFactGroup, ModelRunResult
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.ir.loader import DocumentIrLoader
from esg_targeted.models.report_semantics import ObjectMapping, TableUnderstanding, expand_understanding, project_interpretation
from esg_targeted.retrieval.embeddings import CachedQueryEmbeddingService
from esg_targeted.results.validation import ResultContractValidator
from esg_targeted.report_facts import SourceFact, adapt_direct_core_facts
from esg_targeted.results.organization import FactOrganizer
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore
from esg_targeted.workflow import TargetedExtractionWorkflow
from tests.helpers import build_ir_fixture, standard_dist_root, write_json
from tests.test_inventory_and_guard import build_pipeline
from tests.test_report_agent_loop import setup_session
from tests.test_shared_disclosure import ReportModel


def test_two_standard_jobs_share_actual_neutral_understanding_and_independent_mappings(tmp_path):
    inventory, base_packet, _, _, _ = build_pipeline(tmp_path / "ir")
    catalog = StandardPackageCatalog(standard_dist_root())
    store = ArtifactStore(tmp_path / "output")
    all_facts, all_mappings, calls = [], [], []
    for module, selected in (("e1-5", ("dp01", "dp02")), ("e1-6", ("dp07", "dp08"))):
        package = catalog.load("esrs.2023-set1." + module, "2.0.0")
        members = [m for m in package.metrics if any(m.metric_id.endswith(suffix) for suffix in selected)]
        # A synthetic common topic keeps this test focused on cross-package
        # shared source caching; actual package contracts and elements remain.
        members = [m.model_copy(update={"disclosure_topic": "shared-test"}) for m in members]
        package = package.model_copy(update={"metrics": [next((x for x in members if x.metric_id == m.metric_id), m) for m in package.metrics]})
        request = ExtractionRequest(ir_run_id="ir-fixture-e2-4", package_id=package.manifest.package_id,
            package_version="2.0.0", metric_ids=[m.metric_id for m in members], visual_fallback=False,
            report_session_id="report-" + "a" * 32, roles=RoleConfig())
        session = DisclosureSession(package=package, metrics=members, request=request, artifact_store=store,
            job_id=module, max_context_chars=32000, inventory=inventory)
        def prepare(metric):
            elements = [e for e in package.elements if e.metric_id == metric.metric_id]
            packet = EvidenceSelectionCompiler().compile(task_id=module + "-" + metric.metric_id, metric=metric,
                elements=elements, query=base_packet.query.model_copy(update={"metric_id": metric.metric_id}),
                inventory=inventory, hits=base_packet.retrieval_hits, retrieval_complete=True, model_led_review=True)
            return None, [], None, packet
        packet = session.expand(members[0], prepare(members[0])[3], prepare)
        model = ReportModel([m.metric_id for m in members])
        session.decide(packet=packet, model=model, guard_engine=GroundingGuard(), cancel_check=lambda: False)
        calls.extend(model.calls)
        source = next(iter(session.sources.values()))
        all_facts.append(source["facts"])
        mapping = store.read_json(module, "agent/mappings/shared-test-table-0001.json")
        all_mappings.append({c["metric_id"] for c in mapping["coverage"]})
        assert all("standard" not in key and "package" not in key for key in source["source_identity"])
        assert "scope" in store.read_json(module, "agent/calls/0002-source_understanding.json")["input"]["field_definitions"] if module == "e1-5" else True
    assert calls == ["table-0001"]
    assert all_facts[0] == all_facts[1]
    assert all_facts[0] and all(f["source_fact_id"] for f in all_facts[0])
    assert not all_mappings[0].intersection(all_mappings[1])
    assert len(list((store.report_session_dir("report-" + "a" * 32) / "sources").glob("*.json"))) == 1


def test_omitted_rows_are_not_marked_processed_and_resume_only_missing_row(tmp_path):
    session, _, ids, _ = setup_session(tmp_path)
    session._current_calls, session._linked_context = [], []
    obj = {"object_id": "table-missing-row", "image_path": None, "columns": [], "rows": [
        {"group": "G1", "group_id": "row-1", "text": "报告采用GHG Protocol。", "cells": []},
        {"group": "G2", "group_id": "row-2", "text": "报告涵盖所有运营单位。", "cells": []}]}
    for row in obj["rows"]:
        row["support_segments"] = [{"span_id": "span-" + row["group_id"], "text": row["text"], "locators": [
            {"ir_object_type": "block", "ir_object_id": "block-" + row["group_id"], "pdf_page_index": 0}]}]
    seen = []
    class MissingRow(ReportModel):
        def generate_structured(self, **kwargs):
            rows = kwargs["context"]["rows"]
            seen.append([r["group_id"] for r in rows])
            chosen = rows[:1]
            return json.dumps({"rows": [{"group": r["group"], "kind": "assertion", "statement": r["text"]} for r in chosen]}), {}
    model = MissingRow(ids)
    first = session._understand(obj, model, lambda: False)
    assert first["processed_groups"] == ["row-1"] and not first["complete"]
    second = session._understand(obj, model, lambda: False)
    assert second["processed_groups"] == ["row-1", "row-2"] and second["complete"]
    assert seen == [["row-1", "row-2"], ["row-2"]]
    assert len(second["facts"]) == 2


def test_dynamic_query_uses_its_own_vector_and_cache_releases_local_semantic_model(tmp_path):
    embedded, released = [], []
    class Provider:
        dimensions = 2
        def embed_queries(self, texts):
            embedded.extend(texts)
            return np.asarray([[len(text), 1] for text in texts], dtype=np.float32)
        def close(self):
            released.append("embedding")
    service = CachedQueryEmbeddingService(Provider, tmp_path, model_identity="fake", before_load=lambda: released.append("semantic"),
        initial_vectors={"original": np.asarray([0, 1], dtype=np.float32)})
    rewritten = service.vector("changed method query")
    assert embedded == ["changed method query"] and released == ["semantic", "embedding"]
    assert not np.array_equal(rewritten, service.vector("original"))
    other = CachedQueryEmbeddingService(Provider, tmp_path, model_identity="fake", before_load=lambda: released.append("unexpected"))
    assert np.array_equal(other.vector("changed method query"), rewritten)
    assert embedded == ["changed method query"] and other.last_telemetry["cache_hit"]


def test_local_repair_is_a_real_bounded_pending_action_without_waiting_for_worker(tmp_path):
    session, packet, ids, store = setup_session(tmp_path)
    # A protocol failure alone is not an IR defect. This fixture explicitly
    # carries the source header alignment uncertainty that the repair addresses.
    for span in [*packet.spans, *session.inventory.spans]:
        if span.span_type == "table_cell" and span.structural_context.get("table_id") == "table-0001":
            span.structural_context["header_alignment_uncertain"] = True
    class Repair(ReportModel):
        def generate_structured(self, **kwargs):
            context = kwargs["context"]
            if context.get("candidate_objects"):
                return json.dumps({"actions": [{"tool": "request_local_repair", "object_id": context["candidate_objects"][0]["object_id"],
                    "metric_id": ids[0], "gap_kind": "header", "reason": "Column headers are shifted across value columns"}]}), {}
            return json.dumps({"actions": [{"tool": "finish"}]}), {}
    session.decide(packet=packet, model=Repair(ids), guard_engine=GroundingGuard(), cancel_check=lambda: False)
    pending = store.read_json("job", "agent/repair-requests.json")["requests"]
    assert pending[0]["object_ids"] == ["table-0001"] and pending[0]["metric_ids"] == [ids[0]]
    assert pending[0]["next_action"] == "request_local_repair"
    assert session.agent["steps"][0]["status"] == "needs_repair"


def test_protocol_gap_cannot_request_ir_repair_without_observed_structure_defect(tmp_path):
    session, packet, ids, store = setup_session(tmp_path, max_steps=2)
    session.sources["table-0001"] = {"complete": False, "gaps": [{"kind": "protocol", "reason": "Mapper named an unknown column"}]}
    class Repair(ReportModel):
        def generate_structured(self, **kwargs):
            if kwargs["context"].get("candidate_objects"):
                return json.dumps({"actions": [{"tool": "request_local_repair", "object_id": "table-0001",
                    "metric_id": ids[0], "gap_kind": "header", "reason": "Mapper named an unknown column"}]}), {}
            return json.dumps({"actions": [{"tool": "finish"}]}), {}
    session.decide(packet=packet, model=Repair(ids), guard_engine=GroundingGuard(), cancel_check=lambda: False)
    assert not store.exists("job", "agent/repair-requests.json")
    assert not session.agent.get("pending_repairs")
    assert session.agent["steps"][0]["failure_category"] == "protocol"
    assert "observed source/grid/header defect" in session.agent["steps"][0]["missing_context"][0]


def test_untrusted_cell_grid_requires_explicit_visual_reading():
    context = {"rows": [{"group": "G1", "group_id": "r", "cells": [{"target_cell": "T1", "cell_id": "c1", "column": "c1",
        "raw": "999", "grid_reliable": False, "locators": [{"page_index": 0, "object_id": "c1", "bbox": [0, 0, 50, 50]}]}]}],
        "visual_evidence_supplied": True, "visual_evidence_group_ids": ["r"]}
    output = TableUnderstanding.model_validate({"rows": [{"group": "G1", "kind": "quantitative"}]})
    rows, errors = expand_understanding(output, context)
    assert rows[0]["cells"] == [] and "supplied image" in errors[0] and "bbox" in errors[0]
    payload = output.model_dump()
    payload["cell_overrides"] = [{"target_cell": "T1", "value": "99", "reason": "Read the supplied crop"}]
    context.update(visual_evidence_supplied=True, visual_evidence_group_ids=["r"])
    context["rows"][0]["cells"][0].update(span_id="span-c1", locators=[{
        "ir_object_type": "cell", "ir_object_id": "c1", "pdf_page_index": 0,
        "bbox": {"x0": 10, "y0": 10, "x1": 30, "y1": 30}}])
    output = TableUnderstanding.model_validate(payload)
    rows, errors = expand_understanding(output, context)
    assert rows[0]["cells"][0]["value"] == "99" and not errors
    assert rows[0]["cells"][0]["quality_status"] != "unbound"


def test_object_limit_keeps_good_sibling_table_on_the_same_page(tmp_path):
    root = build_ir_fixture(tmp_path)
    bad_table = json.loads((root / "canonical/tables/table-0001.json").read_text())
    good_table = {**bad_table, "table_id": "table-good", "cells": [{**c, "cell_id": "good-" + c["cell_id"]} for c in bad_table["cells"]]}
    write_json(root / "canonical/tables/table-good.json", good_table)
    write_json(root / "indexes/tables.json", {"tables": [{"path": "canonical/tables/table-0001.json"}, {"path": "canonical/tables/table-good.json"}]})
    manifest = json.loads((root / "manifest.json").read_text())
    manifest.update(can_build_evidence=False, can_build_limited_evidence=True, evidence_policy={"mode": "limited", "excluded_target_ids": ["table-0001"], "excluded_page_indices": []})
    write_json(root / "manifest.json", manifest)
    ir = DocumentIrLoader().load(root)
    assert [t["table_id"] for t in ir.tables] == ["table-good"] and len(ir.pages) == 1
    inventory = EvidenceInventoryBuilder().build(ir)
    assert any(s.structural_context.get("table_id") == "table-good" for s in inventory.spans)
    assert not any(s.structural_context.get("table_id") == "table-0001" for s in inventory.spans)


@pytest.mark.parametrize('excluded,reference',[(False,True),(False,False),(True,True)])
def test_logical_table_reference_is_independent_of_image_geometry_but_never_overrides_exclusion(tmp_path,excluded,reference):
    root=build_ir_fixture(tmp_path)
    table=json.loads((root/'canonical/tables/table-0001.json').read_text())
    table['bbox']=None
    for cell in table['cells']:cell['bbox']=None
    write_json(root/'canonical/tables/table-0001.json',table)
    manifest=json.loads((root/'manifest.json').read_text())
    manifest.update(can_build_evidence=False,can_build_limited_evidence=True,evidence_policy={
        'mode':'limited','excluded_target_ids':['table-0001'] if excluded else [],'excluded_page_indices':[],
        'object_availability':[{'object_id':'table-0001','capabilities':{
            'source_reference':reference,'source_location':False,'cell_grid':True,'cell_geometry':False,'table_region':False}}]})
    write_json(root/'manifest.json',manifest)
    ir=DocumentIrLoader().load(root)
    assert bool(ir.tables) == (reference and not excluded)
    if ir.tables:
        assert ir.tables[0]['bbox'] is None and all(cell['bbox'] is None for cell in ir.tables[0]['cells'])
        inventory=EvidenceInventoryBuilder().build(ir)
        assert any(span.structural_context.get('table_id')=='table-0001' for span in inventory.spans)


@pytest.mark.parametrize("dependency_case", ["ready", "missing_operand"])
def test_single_selected_formula_reads_internal_dependencies_and_keeps_evidence(tmp_path, dependency_case):
    root = build_ir_fixture(tmp_path / "ir", pollutant_rows=[
        ("范围一", "100tCO2e", "2024年", "紫金矿业集团"),
        ("范围二位置法", "200tCO2e", "2024年", "紫金矿业集团"),
        ("范围三", "300tCO2e", "2024年", "紫金矿业集团")])
    settings = Settings(project_root=tmp_path, ir_roots=(root.parent,), standard_dist_root=standard_dist_root(),
        output_root=tmp_path / "output", index_cache_root=tmp_path / "indexes", state_db=tmp_path / "jobs.sqlite3",
        nuextract_model_path=tmp_path / "model", embedding_model_path=tmp_path / "embedding", backend_host="127.0.0.1",
        backend_port=18180, frontend_port=18181, require_ready_ir=True, retrieval_top_k=36, packet_max_chars=160000,
        model_max_tokens=512, model_retries=1)
    settings.ensure_runtime_dirs()
    package = StandardPackageCatalog(standard_dist_root()).load("esrs.2023-set1.e1-6", "1.5.0")
    selected = "esrs.2023-set1.e1-6.dp12"
    source_suffix = {"dp07": "", "dp09": "-2", "dp11": "-3"}
    visited = []
    class DependencyModel:
        provider_name = "local_nuextract"
        model_id = "fake-specialist"
        def close(self):
            pass
        def decide(self, packet, **kwargs):
            mid = packet.metric["metric_id"]
            visited.append(mid)
            suffix = source_suffix.get(mid.rsplit(".", 1)[-1])
            if dependency_case == "missing_operand" and mid.endswith("dp11"):
                suffix = None
            if suffix is None:
                decision = SemanticDecision(task_id=packet.task_id, status="not_found")
            else:
                span = next(s for s in packet.spans if s.span_type == "table_cell" and s.structural_context.get("cell_id") == "cell-value" + suffix)
                group_spans = [s for s in packet.spans if s.context_group_id == span.context_group_id]
                values = {"value_raw": span.text.removesuffix("tCO2e"), "unit_raw": "tCO2e", "reporting_period_raw": "2024年",
                          "reporting_boundary": "集团", "reporting-entity": "紫金矿业"}
                assignments = []
                for element in packet.elements:
                    if element["value_contract"].get("fixed_value") is not None:
                        continue
                    target = element["binding"]["target"]
                    value = values.get(target) or ("紫金矿业" if element["element_code"] == "reporting_entity" else None)
                    if value is None:
                        continue
                    source = next(s for s in group_spans if value in s.text or value in s.context_text)
                    assignments.append(SemanticAssignment(element_id=element["element_id"], source_ref_id=source.span_id,
                        evidence_span_ids=[source.span_id], value_raw=value, source_mode="span"))
                decision = SemanticDecision(task_id=packet.task_id, status="found", fact_groups=[SemanticFactGroup(group_ref_id="cell-value" + suffix,
                    assignments=assignments)])
            return ModelRunResult(decision=decision, raw_output=decision.model_dump_json(), cleaned_output=decision.model_dump_json(), visual_used=False, image_count=0,
                provider=self.provider_name, model_id=self.model_id, timings={})
    jobs, store = JobStore(settings.state_db), ArtifactStore(settings.output_root)
    request = ExtractionRequest(ir_run_id="ir-fixture-e2-4", package_id=package.manifest.package_id, package_version="1.5.0",
        metric_ids=[selected], roles=RoleConfig(), semantic_search=False, visual_fallback=False)
    jobs.create("dependency-job", request, str(store.job_dir("dependency-job")))
    TargetedExtractionWorkflow(settings, jobs, store, semantic_model_factory=DependencyModel).run("dependency-job")
    assert jobs.get("dependency-job").status.value in {"completed", "partial"}, jobs.get("dependency-job").error
    exported = store.read_json("dependency-job", "exports/extraction-result.json")
    records = exported["records"]
    assert {t["metric_id"] for t in records["reporting_tasks"]} == {selected}
    if dependency_case == "missing_operand":
        assert records["quantitative_observations"] == []
        outcome = exported["summary"]["outcomes"][0]
        assert outcome["dependency_status"] == "blocked"
        assert outcome["status"] != "not_found"
        assert any(block["code"] == "dependency_observations_missing" for block in outcome["dependency_blocks"])
        gaps = store.read_json("dependency-job", "results/source-facts.json")["gaps"]
        dependency_gaps = [gap for gap in gaps if gap["kind"] == "dependency"]
        assert dependency_gaps and all(gap["metric_ids"] == [selected] for gap in dependency_gaps)
        assert all(gap["topic_id"] and gap["next_action"] == "review_dependencies" for gap in dependency_gaps)
        from esg_targeted.inspection import ResultInspectionService
        inspection = ResultInspectionService(store).build(jobs.get("dependency-job"))
        assert inspection.metrics[0].dependency_status == "blocked"
        assert inspection.metrics[0].dependency_blocks == outcome["dependency_blocks"]
        assert "derivations" in inspection.downloads
        return
    assert {t["metric_id"] for t in records["quantitative_observations"]} == {selected}
    assert records["quantitative_observations"][0]["value_numeric"] == "600"
    assert records["evidence_references"]
    ResultContractValidator(package.core).validate(records)
    internal = store.read_json("dependency-job", "results/internal-dependencies.json")
    assert len(internal["records"]["quantitative_observations"]) == 3 and internal["records"]["evidence_references"]
    assert set(source_suffix) <= {mid.rsplit(".", 1)[-1] for mid in visited}
    derivations = store.read_json("dependency-job", "derivations.json")
    binding = next(rule for rule in derivations["rules"] if rule["target_metric_id"] == selected)["dependency_bindings"][0]
    assert len(binding["source_observation_ids"]) == 3 and binding["formula_version"]
    sources = store.read_json("dependency-job", "results/source-facts.json")["facts"]
    assert len(sources) == 3 and {s["value_raw"] for s in sources} == {"100", "200", "300"}
    assert all(s["physical_sources"] and s["interpretation_version"] == "direct-core-adapter-v1" for s in sources)
    assert len(internal["core_bindings"]) == 3
    assert exported["summary"]["outcomes"][0]["dependency_status"] == "ready"


def test_checkpoint_finalization_preserves_source_identity_and_filters_deleted_bindings(tmp_path):
    session, _, ids, store = setup_session(tmp_path)
    source = SourceFact(source_fact_id="source-pinned", source_key="source-key-pinned", document_id="doc-old",
        ir_run_id="ir-old", ir_revision=2, source_object_id="cell-old", source_row_id="row-old", kind="quantitative",
        value_raw="10", unit_raw="吨", identity_raw={"reporting_period": "2023年"},
        interpretation_version="report-semantics-v2").model_dump(mode="json")
    pinned = {"core_record_id": "obs-pinned", "source_fact_id": source["source_fact_id"], "metric_id": ids[0]}
    store.write_json("job", "results/source-facts.json", {"facts": [source], "source_objects": []})
    store.write_json("job", "results/metric-source-mappings.json", {"mappings": [{"metric_id": ids[0], "source_fact_id": "source-pinned"}],
        "core_bindings": [pinned, {**pinned, "core_record_id": "obs-removed"}]})
    session.finalize({"quantitative_observations": [{"observation_id": "obs-pinned", "metric_id": ids[0]}]}, [])
    assert store.read_json("job", "results/source-facts.json")["facts"] == [source]
    mapping = store.read_json("job", "results/metric-source-mappings.json")
    assert mapping["core_bindings"] == [pinned] and mapping["mappings"]


def test_same_source_id_with_changed_payload_is_a_local_contract_gap(tmp_path):
    session, _, ids, store = setup_session(tmp_path)
    source = SourceFact(source_fact_id="source-pinned", source_key="source-key-pinned", document_id="doc-old",
        ir_run_id="ir-old", ir_revision=2, source_object_id="cell-old", source_row_id="row-old", kind="quantitative",
        value_raw="10", interpretation_version="report-semantics-v2").model_dump(mode="json")
    store.write_json("job", "results/source-facts.json", {"facts": [source]})
    session.sources["cell-old"] = {"facts": [{**source, "value_raw": "99"}]}
    session.finalize({"quantitative_observations": []}, [])
    result = store.read_json("job", "results/source-facts.json")
    assert result["facts"] == [source] and result["gaps"][0]["kind"] == "contract"
    assert session.agent["state"] == "partial"


def test_direct_source_adapter_uses_only_reported_axes_and_keeps_explicit_bindings():
    package = StandardPackageCatalog(standard_dist_root()).load("esrs.2023-set1.e2-4", "1.0.0")
    metric = "esrs.2023-set1.e2-4.dp02"
    elements = [e for e in package.elements if e.metric_id == metric]
    fixed = next(e for e in elements if e.value_contract.fixed_value is not None)
    observed = next(e for e in elements if e.value_contract.fixed_value is None and e.element_code == "pollutant")
    records = {"quantitative_observations": [{"observation_id": "obs-direct", "metric_id": metric, "value_raw": "125.6", "unit_raw": "吨",
        "reporting_period_raw": "2024年"}], "dimension_values": [
            {"parent_record_id": "obs-direct", "element_id": fixed.element_id, "value_raw": "package-fixed"},
            {"parent_record_id": "obs-direct", "element_id": observed.element_id, "value_raw": "氮氧化物"}],
        "evidence_references": [{"source_record_id": "obs-direct", "evidence_role": "primary", "document_id": "doc", "ir_run_id": "ir", "ir_revision": 1,
            "ir_object_type": "cell", "ir_object_id": "cell-nox", "pdf_page_index": 3}]}
    request = ExtractionRequest(ir_run_id="ir", package_id=package.manifest.package_id, package_version="1.0.0")
    facts, bindings = adapt_direct_core_facts(records, package, bound_core_ids=set(), request=request)
    assert facts[0]["identity_raw"] == {"reporting_period": "2024年", "pollutant": "氮氧化物"}
    assert facts[0]["value_raw"] == "125.6" and facts[0]["unit_raw"] == "吨"
    assert bindings[0]["core_record_id"] == "obs-direct" and bindings[0]["source_fact_id"] == facts[0]["source_fact_id"]
    assert facts == adapt_direct_core_facts(records, package, bound_core_ids=set(), request=request)[0]


def test_logical_table_inherits_headers_but_retains_physical_cell_locators(tmp_path):
    root = build_ir_fixture(tmp_path)
    document = json.loads((root / "canonical/document.json").read_text())
    document["logical_tables"] = [{"logical_table_id": "logical-1", "source_table_ids": ["table-0001"], "composition_mode": "vertical_continuation",
        "status": "verified", "cells": [{"is_header": True, "text": "2024年度", "row_index": 0, "col_index": 1}],
        "cell_mappings": [{"source_cell_id": "cell-value", "logical_col_index": 1, "logical_row_index": 1}]}]
    write_json(root / "canonical/document.json", document)
    ir = DocumentIrLoader().load(root)
    inventory = EvidenceInventoryBuilder().build(ir)
    value = next(s for s in inventory.spans if s.span_type == "table_cell" and s.structural_context.get("cell_id") == "cell-value")
    assert value.structural_context["logical_table_id"] == "logical-1"
    assert "2024年度" in value.structural_context["column_header_path"]
    assert value.locators[0].ir_object_id == "cell-value" and value.locators[0].pdf_page_index == 0
    assert DisclosureSession._reading_key([value])[1] == "logical-1"


def test_same_physical_source_groups_only_with_complete_neutral_identity():
    package = {"metrics": [{"metric_id": "metric", "identity_axes": ["reporting_period", "scope"]}], "elements": [], "core": {}}
    records = {"quantitative_observations": [{"observation_id": fid, "metric_id": "metric", "value_raw": "10", "unit_raw": "吨", "reporting_period_raw": "2024年"}
        for fid in ("a", "b")], "evidence_references": [{"source_record_id": fid, "evidence_role": "primary", "document_id": "doc", "ir_run_id": "ir",
        "ir_revision": 1, "pdf_page_index": 0, "ir_object_type": "cell", "ir_object_id": "same-cell"} for fid in ("a", "b")]}
    _, missing = FactOrganizer().organize(records, package)
    assert missing["logical_measurement_count"] == 2 and missing["facts"]["a"]["missing_identity_axes"] == ["scope"]
    sources = [{"source_fact_id": fid, "identity_raw": {"scope": "范围一", "site": "同一工厂"}} for fid in ("source-a", "source-b")]
    bindings = [{"core_record_id": core, "source_fact_id": "source-" + core} for core in ("a", "b")]
    _, combined = FactOrganizer().organize(records, package, source_facts=sources, source_bindings=bindings)
    assert combined["logical_measurement_count"] == 1 and combined["facts"]["b"]["relation"] == "repeated_disclosure"
    sources[1]["identity_raw"]["site"] = "另一工厂"
    _, distinct = FactOrganizer().organize(records, package, source_facts=sources, source_bindings=bindings)
    assert distinct["logical_measurement_count"] == 2 and not distinct["facts"]["a"]["possible_relations"]
