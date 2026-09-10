from __future__ import annotations

from esg_targeted.contracts import ExtractionRequest, JobStatus
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.recovery import recover_result_jobs, result_catalog_status


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        ir_run_id="ir-fixture",
        package_id="esrs.2023-set1.e1-5",
        package_version="1.0.0",
        metric_ids=["metric-1", "metric-2"],
        semantic_fill=False,
    )


def test_terminal_result_bundle_restores_missing_job_index(tmp_path) -> None:
    jobs = JobStore(tmp_path / "state" / "jobs.sqlite3")
    artifacts = ArtifactStore(tmp_path / "results")
    artifacts.write_json("job-restored", "input/request.json", _request().model_dump(mode="json"))
    artifacts.write_json(
        "job-restored",
        "manifest.json",
        {
            "job_id": "job-restored",
            "task_count": 2,
            "outcomes": [
                {"status": "found", "guard_accepted": True},
                {"status": "not_found", "guard_accepted": True},
            ],
        },
    )

    assert recover_result_jobs(jobs, artifacts) == ["job-restored"]
    restored = jobs.get("job-restored")
    assert restored.status == JobStatus.COMPLETED
    assert restored.stage == "finished"
    assert restored.progress_current == restored.progress_total == 2
    assert restored.request.package_id == "esrs.2023-set1.e1-5"
    assert jobs.events("job-restored")[0]["stage"] == "catalog_recovery"
    assert recover_result_jobs(jobs, artifacts) == []


def test_incomplete_orphan_is_reported_but_not_guessed(tmp_path) -> None:
    jobs = JobStore(tmp_path / "state" / "jobs.sqlite3")
    artifacts = ArtifactStore(tmp_path / "results")
    artifacts.write_json("job-incomplete", "input/request.json", _request().model_dump(mode="json"))

    assert recover_result_jobs(jobs, artifacts) == []
    status = result_catalog_status(jobs, artifacts)
    assert status["indexed_job_count"] == 0
    assert status["artifact_job_count"] == 1
    assert status["orphan_artifact_job_ids"] == ["job-incomplete"]


def test_catalog_reports_job_records_with_missing_artifacts(tmp_path) -> None:
    jobs = JobStore(tmp_path / "state" / "jobs.sqlite3")
    artifacts = ArtifactStore(tmp_path / "results")
    jobs.create("job-without-bundle", _request(), str(artifacts.output_root / "job-without-bundle"))

    status = result_catalog_status(jobs, artifacts)
    assert status["indexed_job_count"] == 1
    assert status["artifact_job_count"] == 0
    assert status["missing_artifact_job_ids"] == ["job-without-bundle"]


def test_standard_catalog_diagnostics_exposes_invalid_packages(tmp_path) -> None:
    package = tmp_path / "broken-package" / "1.0.0" / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text("{}", encoding="utf-8")

    diagnostics = StandardPackageCatalog(tmp_path).diagnostics()
    assert diagnostics["discovered_package_count"] == 1
    assert diagnostics["valid_package_count"] == 0
    assert diagnostics["invalid_package_count"] == 1
    assert diagnostics["issues"][0]["path"] == str(package.resolve())
