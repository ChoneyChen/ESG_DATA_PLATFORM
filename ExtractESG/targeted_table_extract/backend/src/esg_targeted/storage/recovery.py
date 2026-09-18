from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from esg_targeted.contracts import ExtractionRequest, JobStatus
from esg_targeted.io import read_json
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore


def result_catalog_status(job_store: JobStore, artifact_store: ArtifactStore) -> dict[str, Any]:
    indexed = set(job_store.job_ids())
    artifact_ids = set(artifact_store.existing_job_ids())
    orphan_ids = sorted(artifact_ids - indexed)
    missing_ids = sorted(indexed - artifact_ids)
    return {
        "indexed_job_count": len(indexed),
        "artifact_job_count": len(artifact_ids),
        "orphan_artifact_count": len(orphan_ids),
        "missing_artifact_count": len(missing_ids),
        "orphan_artifact_job_ids": orphan_ids,
        "missing_artifact_job_ids": missing_ids,
        "state_db": str(job_store.path.resolve()),
        "output_root": str(artifact_store.output_root),
    }


def recover_result_jobs(job_store: JobStore, artifact_store: ArtifactStore) -> list[str]:
    """Rebuild missing terminal job rows from self-describing result bundles.

    Only bundles with a valid ``input/request.json`` and a terminal marker are
    eligible. In-progress directories are intentionally left untouched because
    their last runtime status cannot be inferred safely after the mutable state
    database has been lost.
    """

    indexed = set(job_store.job_ids())
    restored: list[str] = []
    for job_id in artifact_store.existing_job_ids():
        if job_id in indexed:
            continue
        root = artifact_store.existing_job_dir(job_id)
        request_path = root / "input" / "request.json"
        manifest_path = root / "manifest.json"
        failure_path = root / "failure.json"
        if not request_path.is_file() or not (manifest_path.is_file() or failure_path.is_file()):
            continue
        try:
            request = ExtractionRequest.model_validate(read_json(request_path))
            summary = read_json(manifest_path) if manifest_path.is_file() else None
            failure = read_json(failure_path) if failure_path.is_file() else None
            if summary is not None and summary.get("job_id") not in {None, job_id}:
                continue
            status, stage, current, total, error = _terminal_state(request, summary, failure)
            created_at, updated_at = _artifact_times(root)
            did_restore = job_store.restore(
                job_id,
                request,
                str(root.resolve()),
                status=status,
                stage=stage,
                progress_current=current,
                progress_total=total,
                created_at=created_at,
                updated_at=updated_at,
                error=error,
                summary=summary,
            )
        except (OSError, TypeError, ValueError):
            continue
        if did_restore:
            restored.append(job_id)
            indexed.add(job_id)
    return restored


def _terminal_state(
    request: ExtractionRequest,
    summary: dict[str, Any] | None,
    failure: dict[str, Any] | None,
) -> tuple[JobStatus, str, int, int, str | None]:
    if summary is not None:
        recorded_status = summary.get("execution_status")
        outcomes = summary.get("outcomes") or []
        completed = all(
            item.get("guard_accepted") is True and item.get("status") != "ambiguous"
            for item in outcomes
        )
        total = max(0, int(summary.get("task_count") or len(outcomes)))
        status = (
            JobStatus(recorded_status)
            if recorded_status in {item.value for item in JobStatus}
            else JobStatus.COMPLETED if completed else JobStatus.PARTIAL
        )
        return (
            status,
            "failed" if status == JobStatus.FAILED else "finished",
            total,
            total,
            "No metric produced an accepted semantic result." if status == JobStatus.FAILED else None,
        )
    error = str((failure or {}).get("error") or "Recovered failed result bundle")
    total = len(request.metric_ids)
    return JobStatus.FAILED, "failed", 0, total, error


def _artifact_times(root: Path) -> tuple[datetime, datetime]:
    mtimes = [path.stat().st_mtime for path in root.rglob("*") if path.is_file()]
    if not mtimes:
        mtimes = [root.stat().st_mtime]
    return (
        datetime.fromtimestamp(min(mtimes), UTC),
        datetime.fromtimestamp(max(mtimes), UTC),
    )
