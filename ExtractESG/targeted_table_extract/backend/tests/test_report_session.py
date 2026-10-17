from dataclasses import replace
import json
import shutil

import pytest
from pydantic import ValidationError

from esg_targeted.config import settings
from esg_targeted.contracts import JobStatus, ReportExtractionRequest
from esg_targeted.report_session import ReportSessionService
from tests.helpers import build_ir_fixture, standard_dist_root


def make_service(tmp_path, workflow_factory=None):
    ir_root = tmp_path / "ir"
    build_ir_fixture(ir_root)
    runtime = replace(settings, project_root=tmp_path, ir_roots=(ir_root,),
        standard_dist_root=standard_dist_root(), output_root=tmp_path / "output",
        state_db=tmp_path / "jobs.sqlite3", index_cache_root=tmp_path / "indexes")
    return ReportSessionService(runtime, workflow_factory=workflow_factory)


def request():
    return ReportExtractionRequest.model_validate({
        "input": {"kind": "document_ir", "ir_run_id": "ir-fixture-e2-4"},
        "standards": [
            {"package_id": "esrs.2023-set1.e1-5", "package_version": "2.0.0", "metric_ids": ["esrs.2023-set1.e1-5.dp01"]},
            {"package_id": "esrs.2023-set1.e1-6", "package_version": "2.0.0", "metric_ids": ["esrs.2023-set1.e1-6.dp12"]},
            {"package_id": "esrs.2023-set1.e2-4", "package_version": "2.0.0", "metric_ids": ["esrs.2023-set1.e2-4.dp02"]},
        ],
    })


def test_cancel_remains_running_until_live_owner_exits(tmp_path):
    import os
    from esg_targeted.storage.job_store import JobStore
    service = make_service(tmp_path)
    created = service.create(request())
    service._mutate(created['session_id'], lambda state: state.update(execution_status='running',
        owner_pid=os.getpid(), owner_started=JobStore.process_started(os.getpid())))
    pending = service.cancel(created['session_id'])
    assert pending['execution_status'] == 'running' and pending['cancellation_status'] == 'stopping'
    assert pending['available_actions'] == []
    service._mutate(created['session_id'], lambda state: state.update(owner_pid=None, owner_started=None))
    stopped = service.detail(created['session_id'])
    assert stopped['execution_status'] == 'cancelled'


def test_report_request_requires_cloud_authorization_and_excludes_credentials():
    raw = request().model_dump(mode="json")
    raw["role_config"]["semantic_brain"]["provider"] = "qiniu_vlm"
    with pytest.raises(ValidationError, match="authorization"):
        ReportExtractionRequest.model_validate(raw)
    raw["role_config"]["cloud_authorization"]["allowed"] = True
    raw["qiniu_api_key"] = "test-secret"
    parsed = ReportExtractionRequest.model_validate(raw)
    assert "test-secret" not in parsed.model_dump_json()
    assert parsed.role_config.specialist.provider == "local_nuextract"


def test_partial_budget_override_preserves_chosen_completeness_preset():
    from esg_targeted.contracts import EvidenceBudget
    parsed = request().model_copy(update={"completeness": "quick", "budget": EvidenceBudget(call_timeout_seconds=120)})
    resolved = parsed.effective_budget()
    assert (resolved.max_model_calls, resolved.max_input_tokens, resolved.retrieval_object_top_n) == (64, 350000, 2)
    assert resolved.call_timeout_seconds == 120
    assert request().model_copy(update={"completeness": "thorough"}).effective_budget().max_model_calls == 240


def test_one_session_freezes_three_package_projections_and_pins_ir(tmp_path):
    service = make_service(tmp_path)
    created = service.create(request())
    assert len(created["job_ids"]) == 3
    assert {j["request"]["report_session_id"] for j in created["jobs"]} == {created["session_id"]}
    assert all(j["request"]["roles"]["semantic_brain"]["provider"] == "local_qwen" for j in created["jobs"])
    assert (tmp_path / "ir" / ".pins" / f"{created['session_id']}.json").is_file()
    for child in created["jobs"]:
        assert (service.artifacts.job_dir(child["job_id"]) / "input/standard-package.json").is_file()
    ghg = created["jobs"][1]["request"]
    assert ghg["metric_ids"] == ["esrs.2023-set1.e1-6.dp12"]
    assert ghg["internal_metric_ids"]


def test_resume_retains_completed_projection_and_reuses_child_ids(tmp_path):
    calls = []
    class FakeWorkflow:
        def __init__(self, runtime, jobs, artifacts):
            self.jobs, self.artifacts = jobs, artifacts
        def run(self, job_id):
            calls.append(job_id)
            self.jobs.update(job_id, status=JobStatus.PARTIAL if len(calls) == 2 else JobStatus.COMPLETED)
    service = make_service(tmp_path, FakeWorkflow)
    created = service.create(request())
    done = service.run(created["session_id"])
    assert done["execution_status"] == "finished"
    assert done["counts"]["core_facts"] == 0
    assert done["result_status"] == "not_evaluated"
    assert done["available_actions"][0]["action"] == "resume"
    service.resume(created["session_id"])
    resumed = service.run(created["session_id"])
    assert calls == [*created["job_ids"], created["job_ids"][1]]
    # The scheduler completed, but this stub never generated business results.
    assert resumed["counts"]["core_facts"] == 0
    assert resumed["result_status"] not in {"usable", "partial"}


def test_frozen_input_mutation_is_blocked_before_projection_work(tmp_path):
    service = make_service(tmp_path)
    created = service.create(request())
    path = tmp_path / "ir/ir-fixture-e2-4/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["document_label"] = "unexpected rewrite"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Frozen input"):
        service.run(created["session_id"])
    assert service.detail(created["session_id"])["execution_status"] == "failed"


def test_canonical_content_is_frozen_even_when_manifest_is_unchanged(tmp_path):
    service = make_service(tmp_path)
    created = service.create(request())
    path = tmp_path / "ir/ir-fixture-e2-4/canonical/pages/page-0001.json"
    page = json.loads(path.read_text())
    page["page"]["text"] = "Unannounced source replacement"
    path.write_text(json.dumps(page))
    with pytest.raises(ValueError, match="Frozen input package"):
        service.run(created["session_id"])


def test_facts_include_direct_exports_and_quarantine_only_conflicting_identity(tmp_path):
    service = make_service(tmp_path)
    created = service.create(request())
    a, b = created["job_ids"][:2]
    good = {"source_fact_id": "good", "value_raw": "42", "source_object_id": "row-a"}
    first = {"source_fact_id": "collision", "value_raw": "1", "source_object_id": "row-b"}
    service.artifacts.write_json(a, "results/source-facts.json", {"facts": [good, first]})
    service.artifacts.write_json(b, "results/source-facts.json", {"facts": [{**first, "value_raw": "2"}]})
    service.artifacts.write_json(b, "results/metric-source-mappings.json", {"core_bindings": [
        {"source_fact_id": "collision", "core_record_id": "obs-b", "metric_id": "metric-b"}]})
    facts = service.facts(created["session_id"])
    assert facts["facts"] == [good]
    assert facts["gaps"][0]["metric_ids"] == ["metric-b"]
    assert len(facts["quarantined_facts"][0]["variants"]) == 2


def test_completed_limited_session_has_no_impossible_resume_action(tmp_path):
    from esg_targeted.workflow import TargetedExtractionWorkflow
    from tests.test_workflow import FakeEmbeddingProvider, FoundModel
    class OfflineWorkflow:
        def __init__(self, runtime, jobs, artifacts):
            self.workflow = TargetedExtractionWorkflow(runtime, jobs, artifacts,
                embedding_provider_factory=FakeEmbeddingProvider, semantic_model_factory=FoundModel)
        def run(self, job_id):
            self.workflow.run(job_id)
    service = make_service(tmp_path, OfflineWorkflow)
    bounded_request = ReportExtractionRequest.model_validate({
        "input": {"kind": "document_ir", "ir_run_id": "ir-fixture-e2-4"},
        "standards": [{"package_id": "esrs.2023-set1.e2-4", "package_version": "1.0.0",
                       "metric_ids": ["esrs.2023-set1.e2-4.dp02"]}],
    })
    created = service.create(bounded_request)
    service.run(created["session_id"])
    service._mutate(created["session_id"], lambda s: s.update(execution_status="finished",
        plan={**s["plan"], "evidence_limitations": [{"policy": {"mode": "limited"}}]}))
    detail = service.detail(created["session_id"])
    assert detail["counts"]["core_facts"] == 1
    assert detail["result_status"] == "partial"
    assert not any(a["action"] == "resume" for a in detail["available_actions"])


def test_evidence_transition_cannot_override_original_limited_consumption_choice(tmp_path):
    from esg_targeted.contracts import EvidenceTransitionRequest
    service = make_service(tmp_path)
    original = request().model_copy(update={"allow_limited_evidence": False})
    created = service.create(original)
    service.cancel(created["session_id"])
    new_path = tmp_path / "ir/ir-fixture-limited"
    shutil.copytree(tmp_path / "ir/ir-fixture-e2-4", new_path)
    manifest = json.loads((new_path / "manifest.json").read_text())
    manifest.update(run_id=new_path.name, ir_revision=2, can_build_evidence=False,
        can_build_limited_evidence=True, evidence_policy={"mode": "limited"})
    (new_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="does not authorize"):
        service.transition(created["session_id"], EvidenceTransitionRequest(new_ir_run_id=new_path.name,
            affected_object_ids=["table-0001"], metric_ids=["esrs.2023-set1.e1-5.dp01"],
            reason="synthetic repair", requested_by="test-reviewer"))
    assert service.detail(created["session_id"])["job_ids"] == created["job_ids"]


def test_creation_key_is_idempotent_and_cannot_be_reused_for_other_inputs(tmp_path):
    service = make_service(tmp_path)
    first = service.create(request(), idempotency_key="plan-a:0")
    assert service.create(request(), idempotency_key="plan-a:0")["job_ids"] == first["job_ids"]
    changed = request().model_copy(update={"name": "changed input"})
    with pytest.raises(ValueError, match="idempotency key"):
        service.create(changed, idempotency_key="plan-a:0")


def test_evidence_transition_keeps_unaffected_jobs_checkpoints_and_old_sources(tmp_path):
    from esg_targeted.contracts import EvidenceTransitionRequest
    from esg_targeted.ids import safe_filename
    service = make_service(tmp_path)
    created = service.create(request())
    for job_id in created["job_ids"]:
        service.jobs.update(job_id, status=JobStatus.COMPLETED)
    service._mutate(created["session_id"], lambda s: s.update(execution_status="finished"))
    affected_job = created["job_ids"][1]
    for mid in ("esrs.2023-set1.e1-6.dp07", "esrs.2023-set1.e1-6.dp09"):
        service.artifacts.write_json(affected_job, f"checkpoints/{safe_filename(mid)}.json", {
            "metric_id": mid, "records": {"evidence_references": [{"ir_run_id": "ir-fixture-e2-4", "ir_object_id": "cell-scope1" if mid.endswith('07') else 'cell-scope2'}]}})
    old_path = tmp_path / "ir/ir-fixture-e2-4"
    new_path = tmp_path / "ir/ir-fixture-repaired"
    shutil.copytree(old_path, new_path)
    manifest = json.loads((new_path / "manifest.json").read_text())
    manifest.update(run_id=new_path.name, ir_revision=2, parent_ir_run_id=old_path.name)
    (new_path / "manifest.json").write_text(json.dumps(manifest))
    transitioned = service.transition(created["session_id"], EvidenceTransitionRequest(
        new_ir_run_id=new_path.name, affected_object_ids=["cell-scope1"], reason="Correct one source cell", requested_by="test-reviewer"))
    assert transitioned["session_id"] == created["session_id"]
    assert transitioned["job_ids"][0] == created["job_ids"][0]
    assert transitioned["job_ids"][2] == created["job_ids"][2]
    assert transitioned["job_ids"][1] != affected_job
    child = service.artifacts.job_dir(transitioned["job_ids"][1])
    assert not (child / "checkpoints" / f"{safe_filename('esrs.2023-set1.e1-6.dp07')}.json").exists()
    retained = json.loads((child / "checkpoints" / f"{safe_filename('esrs.2023-set1.e1-6.dp09')}.json").read_text())
    assert retained["records"]["evidence_references"][0]["ir_run_id"] == old_path.name
    assert transitioned["request"]["input"]["ir_run_id"] == old_path.name
    assert transitioned["current_ir_run_id"] == new_path.name
    assert transitioned["projection_history"][0]["projections"][1]["job_id"] == affected_job
