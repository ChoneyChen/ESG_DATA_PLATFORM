from __future__ import annotations

import json
from pathlib import Path

from esg_v2.control_plane.contracts import PipelineTaskType
from esg_v2.control_plane.store import PipelineQueueStore
from esg_v2.storage.ir_retention import DocumentIrRetentionManager


OCR_RUN_ID = "ocr-20260830T000000Z-aaaaaaaaaaaa"
PARENT_RUN_ID = "ir-20260830T000001Z-bbbbbbbbbbbb"
CHILD_RUN_ID = "ir-20260830T000002Z-cccccccccccc"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_ir(
    root: Path,
    state_root: Path,
    run_id: str,
    *,
    revision: int,
    readiness: str,
    can_build_evidence: bool,
    parent_run_id: str | None = None,
    task_status: str = "auto_resolved",
    task_blocking: bool = True,
    state_status: str = "done",
) -> None:
    package = root / run_id
    _write_json(
        package / "manifest.json",
        {
            "run_id": run_id,
            "ocr_run_id": OCR_RUN_ID,
            "document_id": "doc-sha256-" + "d" * 64,
            "lineage_id": "irl-" + "e" * 24,
            "ir_revision": revision,
            "parent_ir_run_id": parent_run_id,
            "readiness": readiness,
            "can_build_evidence": can_build_evidence,
            "written_at": f"2026-08-30T00:00:0{revision}+00:00",
        },
    )
    _write_json(
        package / "review" / "index.json",
        {
            "tasks": [
                {
                    "task_id": "review-000001",
                    "status": task_status,
                    "blocking": task_blocking,
                }
            ]
        },
    )
    _write_json(
        package / "quality" / "validation-report.json",
        {
            "readiness": readiness,
            "metrics": {
                "optional_unresolved_count": int(
                    not task_blocking and task_status not in {"auto_resolved", "reviewed", "done"}
                )
            },
            "issues": [],
        },
    )
    _write_json(
        state_root / run_id / "state.json",
        {"run_id": run_id, "status": state_status},
    )


def _manager(tmp_path: Path, *, queue_db: Path | None = None) -> DocumentIrRetentionManager:
    return DocumentIrRetentionManager(
        ir_output_root=tmp_path / "ir",
        ir_state_root=tmp_path / "state",
        cleanup_root=tmp_path / "cleanup",
        pipeline_queue_db=queue_db,
    )


def test_promoted_review_revision_prunes_parent_package_and_state(tmp_path: Path) -> None:
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        PARENT_RUN_ID,
        revision=1,
        readiness="auto_review_pending",
        can_build_evidence=False,
        task_status="deferred",
    )
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        CHILD_RUN_ID,
        revision=2,
        readiness="ready_with_warnings",
        can_build_evidence=True,
        parent_run_id=PARENT_RUN_ID,
    )

    result = _manager(tmp_path).reconcile(CHILD_RUN_ID)

    assert result["action"] == "promoted_and_pruned"
    assert result["retained_run_id"] == CHILD_RUN_ID
    assert result["pruned_run_ids"] == [PARENT_RUN_ID]
    assert not (tmp_path / "ir" / PARENT_RUN_ID).exists()
    assert not (tmp_path / "state" / PARENT_RUN_ID).exists()
    assert (tmp_path / "ir" / CHILD_RUN_ID / "manifest.json").is_file()
    current = json.loads(
        (tmp_path / "cleanup" / "ir-retention" / "current" / f"{OCR_RUN_ID}.json").read_text()
    )
    assert current["retained_run_id"] == CHILD_RUN_ID


def test_equal_quality_newer_revision_replaces_parent(tmp_path: Path) -> None:
    for run_id, revision, parent in (
        (PARENT_RUN_ID, 1, None),
        (CHILD_RUN_ID, 2, PARENT_RUN_ID),
    ):
        _write_ir(
            tmp_path / "ir",
            tmp_path / "state",
            run_id,
            revision=revision,
            readiness="ready_with_warnings",
            can_build_evidence=True,
            parent_run_id=parent,
        )

    result = _manager(tmp_path).reconcile(CHILD_RUN_ID)

    assert result["retained_run_id"] == CHILD_RUN_ID
    assert result["pruned_run_ids"] == [PARENT_RUN_ID]


def test_worse_candidate_does_not_delete_current_best(tmp_path: Path) -> None:
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        PARENT_RUN_ID,
        revision=1,
        readiness="ready",
        can_build_evidence=True,
    )
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        CHILD_RUN_ID,
        revision=2,
        readiness="repair_required",
        can_build_evidence=False,
        parent_run_id=PARENT_RUN_ID,
        task_status="failed",
    )

    result = _manager(tmp_path).reconcile(CHILD_RUN_ID)

    assert result["action"] == "candidate_not_promoted"
    assert result["retained_run_id"] == PARENT_RUN_ID
    assert (tmp_path / "ir" / PARENT_RUN_ID).is_dir()
    assert (tmp_path / "ir" / CHILD_RUN_ID).is_dir()


def test_queued_consumers_are_rebased_before_parent_is_pruned(tmp_path: Path) -> None:
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    targeted = queue.create(
        task_id="pt-20260830T000003Z-dddddddddddd",
        task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="run",
        title=f"定向抽取 · {PARENT_RUN_ID}",
        payload={"ir_run_id": PARENT_RUN_ID, "package_id": "test", "package_version": "1"},
        native_job_id="targeted-1",
    )
    repair = queue.create(
        task_id="pt-20260830T000004Z-eeeeeeeeeeee",
        task_type=PipelineTaskType.DOCUMENT_IR,
        operation="repair",
        title=f"IR 定向修复 · {PARENT_RUN_ID}",
        payload={"parent_ir_run_id": PARENT_RUN_ID, "target_ids": ["page-0001"]},
        native_job_id="ir-20260830T000004Z-ffffffffffff",
    )
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        PARENT_RUN_ID,
        revision=1,
        readiness="auto_review_pending",
        can_build_evidence=False,
        task_status="deferred",
    )
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        CHILD_RUN_ID,
        revision=2,
        readiness="ready_with_warnings",
        can_build_evidence=True,
        parent_run_id=PARENT_RUN_ID,
    )

    result = _manager(tmp_path, queue_db=queue.path).reconcile(CHILD_RUN_ID)

    assert set(result["rebased_task_ids"]) == {targeted.task_id, repair.task_id}
    assert queue.get(targeted.task_id).payload["ir_run_id"] == CHILD_RUN_ID
    assert queue.get(repair.task_id).payload["parent_ir_run_id"] == CHILD_RUN_ID
    assert not (tmp_path / "ir" / PARENT_RUN_ID).exists()


def test_running_consumer_protects_referenced_revision(tmp_path: Path) -> None:
    queue = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = queue.create(
        task_id="pt-20260830T000005Z-111111111111",
        task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="run",
        title=f"定向抽取 · {PARENT_RUN_ID}",
        payload={"ir_run_id": PARENT_RUN_ID},
        native_job_id="targeted-2",
    )
    queue.update(task.task_id, status="running")
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        PARENT_RUN_ID,
        revision=1,
        readiness="auto_review_pending",
        can_build_evidence=False,
        task_status="deferred",
    )
    _write_ir(
        tmp_path / "ir",
        tmp_path / "state",
        CHILD_RUN_ID,
        revision=2,
        readiness="ready_with_warnings",
        can_build_evidence=True,
        parent_run_id=PARENT_RUN_ID,
    )

    result = _manager(tmp_path, queue_db=queue.path).reconcile(CHILD_RUN_ID)

    assert result["action"] == "promoted_cleanup_deferred"
    assert result["protected_run_ids"] == [PARENT_RUN_ID]
    assert (tmp_path / "ir" / PARENT_RUN_ID).is_dir()
