"""Inspection consumes real durable stage contracts; no running backend/model."""
import json
from pathlib import Path

import pytest

from esg_targeted.contracts import JobStatus
from esg_targeted.inspection import ResultInspectionService
from esg_targeted.results.stages import publish_stage
from esg_targeted.storage.artifacts import ArtifactStore
from tests.test_workflow import FoundModel, _run


def stage_fixture(tmp_path, phase):
    jobs, store = _run(tmp_path, FoundModel())
    job = jobs.get("job-fixture")
    root = store.existing_job_dir(job.job_id)
    request = job.request.model_copy(update={"metric_ids": [f"esrs.2023-set1.e2-4.dp0{i}" for i in (2, 3, 4)]})
    job = job.model_copy(update={"request": request, "status": JobStatus.INTERRUPTED,
                                 "error": "Fixture interruption", "summary": None})
    store.write_json(job.job_id, "input/request.json", request.model_dump(mode="json"))
    source = store.read_json(job.job_id, "results/source-facts.json")
    source["facts"][0].update(quality_status="needs_confirmation", review_status="candidate",
        quality_issues=[{"code": "identity_axis_unknown", "axis": "reporting_entity", "message": "Entity needs source confirmation"}])
    store.write_json(job.job_id, "agent/sources/fixture-source.json", {
        "source_key": "fixture-source", "object_id": "table-fixture", "facts": source["facts"], "complete": False,
        "gaps": [{"gap_id": "gap-fixture", "kind": "context", "reason": "Entity needs source confirmation",
                  "metric_ids": [request.metric_ids[0]], "object_id": "table-fixture"}],
    })
    store.write_json(job.job_id, "agent/state.json", {"state": "running", "current_metric_id": request.metric_ids[1],
        "steps": [{"tool": "source_understanding", "status": "running", "metric_ids": [request.metric_ids[1]]}]})
    (root / "manifest.json").unlink()
    # These fixtures model interruption before final family/derivation work.
    # Postprocessing invalidation is exercised separately with its own verdict.
    (root / "progress/core-postprocessing.json").unlink(missing_ok=True)
    for path in (root / "exports").glob("*"):
        path.unlink()
    for path in (root / "results").glob("*"):
        path.unlink()
    if phase != "core":
        for path in (root / "checkpoints").glob("*.json"):
            path.unlink()
    if phase == "mapping":
        store.write_json(job.job_id, "disclosures/projections/fixture.json", {"metric_mappings": [{
            "metric_id": request.metric_ids[0], "source_fact_id": source["facts"][0]["source_fact_id"],
            "match": "uncertain", "reason": "Entity requires confirmation"}]})
    snapshot = publish_stage(store, job.job_id, execution_status="interrupted", error=job.error)
    return job, store, snapshot


@pytest.mark.parametrize("phase", ["understanding", "mapping", "core"])
def test_no_bundle_inspection_preserves_stage_maturity_and_distinct_metric_states(tmp_path, phase):
    job, store, snapshot = stage_fixture(tmp_path, phase)
    root = store.existing_job_dir(job.job_id)
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    inspection = ResultInspectionService(store).build(job)
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after  # read API cannot finalize or mutate a stage
    assert inspection.availability == "partial"
    assert inspection.stage_summary["execution_status"] == "interrupted"
    assert inspection.report_facts[0]["review_status"] == "candidate"
    assert inspection.report_facts[0]["quality_issues"]
    assert inspection.overview.candidate_source_fact_count == snapshot["summary"]["counts"]["candidate_source_facts"]
    assert inspection.metrics[1].processing_status == "unfinished"
    assert inspection.metrics[1].execution_status == "interrupted"
    assert inspection.metrics[2].processing_status == "not_executed"
    assert inspection.metrics[2].status == "not_executed" and inspection.metrics[2].found_status == "not_evaluated"
    assert inspection.overview.not_found_count == 0
    assert inspection.gaps and inspection.evidence_references
    assert "result_package" not in inspection.downloads and "stage_snapshot" in inspection.downloads
    if phase == "core":
        assert inspection.overview.quantitative_fact_count == 1
        assert inspection.metrics[0].processing_status == "evaluated"
        assert inspection.metrics[0].facts[0].evidence
        assert inspection.stage_summary["maturity"] == "core_partial"
    else:
        assert inspection.overview.quantitative_fact_count == 0
        assert all(not metric.facts for metric in inspection.metrics)
        assert inspection.stage_summary["maturity"] == "source_candidates"
    if phase == "mapping":
        assert inspection.metric_source_mappings["mappings"][0]["match"] == "uncertain"


def test_missing_job_inspection_does_not_create_an_artifact_directory(tmp_path):
    jobs, store = _run(tmp_path / "fixture", FoundModel())
    absent_store = ArtifactStore(tmp_path / "absent")
    inspection = ResultInspectionService(absent_store).build(jobs.get("job-fixture"))
    assert inspection.availability == "unavailable"
    assert not absent_store.output_root.exists()


def test_exported_bundle_takes_precedence_over_stale_stage(tmp_path):
    jobs, store = _run(tmp_path, FoundModel())
    store.write_json("job-fixture", "progress/snapshot.json", {"summary": {"counts": {"core_facts": 0}}, "records": {}})
    inspection = ResultInspectionService(store).build(jobs.get("job-fixture"))
    assert inspection.availability == "ready"
    assert inspection.overview.quantitative_fact_count == 1
    assert inspection.stage_summary == {}


def export_browser_fixture(path):
    """Called by the offline browser test driver, using this actual service."""
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        job, store, _ = stage_fixture(Path(temporary), "mapping")
        Path(path).write_text(json.dumps(ResultInspectionService(store).build(job).model_dump(mode="json"), ensure_ascii=False))
