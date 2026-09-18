from __future__ import annotations

import json
from pathlib import Path

from esg_v2.document.catalog import DocumentIrCatalog
from esg_v2.document.reader import DocumentIrPackageReader


def test_unresolved_review_worklist_skips_resolved_and_classifies_current_work() -> None:
    reader = object.__new__(DocumentIrPackageReader)
    tasks = [
        {"task_id": "review-human", "status": "human_required", "blocking": True},
        {"task_id": "review-system", "status": "failed", "blocking": True, "retryable": False},
        {"task_id": "review-blocking", "status": "deferred", "blocking": True, "retryable": True},
        {"task_id": "review-optional", "status": "pending", "blocking": False, "retryable": True},
        {"task_id": "review-done", "status": "done", "blocking": True},
    ]
    loaded: list[str] = []
    reader.review_tasks = lambda: tasks  # type: ignore[method-assign]

    def bundle(task_id: str) -> dict[str, object]:
        loaded.append(task_id)
        return {"task": next(item for item in tasks if item["task_id"] == task_id)}

    reader.review_task_bundle = bundle  # type: ignore[method-assign]
    reader._review_guidance = lambda value: {"title": value["task"]["task_id"]}  # type: ignore[method-assign,index]
    reader._unresolved_validation_issues = lambda: []  # type: ignore[method-assign]

    result = reader.unresolved_review_worklist()

    assert result["schema_version"] == "document-ir-review-worklist-v1"
    assert result["counts"] == {
        "human_required": 1,
        "system_blocked": 1,
        "blocking_deferred": 1,
        "optional_deferred": 1,
        "validation_blocked": 0,
    }
    assert [item["group"] for item in result["entries"]] == [
        "human_required",
        "system_blocked",
        "blocking_deferred",
        "optional_deferred",
    ]
    assert loaded == ["review-human", "review-system", "review-blocking", "review-optional"]


def test_legacy_cross_page_contract_failure_is_retryable_without_rewriting_parent() -> None:
    task = {
        "task_id": "review-legacy-spread",
        "status": "deferred",
        "blocking": True,
        "retryable": False,
        "result": {
            "failure_class": "system_contract",
            "reason": "required_review_targets_incomplete_after_guard_confirmation",
        },
    }

    assert DocumentIrPackageReader._can_retry(task)
    assert DocumentIrPackageReader._review_group(task) == "blocking_deferred"


def test_catalog_returns_only_highest_latest_revision_per_lineage(tmp_path: Path) -> None:
    lineage_a = "irl-" + "a" * 24
    lineage_b = "irl-" + "b" * 24
    document_id = "doc-sha256-" + "c" * 64

    def write(run_id: str, lineage_id: str, revision: int, written_at: str) -> None:
        root = tmp_path / run_id
        root.mkdir()
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "ocr_run_id": "ocr-20260830T000000Z-000000000000",
                    "document_id": document_id,
                    "document_label": "Example ESG.pdf",
                    "lineage_id": lineage_id,
                    "ir_revision": revision,
                    "readiness": "review_required",
                    "written_at": written_at,
                }
            ),
            encoding="utf-8",
        )

    write("ir-20260830T000001Z-000000000001", lineage_a, 1, "2026-08-30T00:00:01Z")
    write("ir-20260830T000002Z-000000000002", lineage_a, 2, "2026-08-30T00:00:02Z")
    write("ir-20260830T000003Z-000000000003", lineage_b, 1, "2026-08-30T00:00:03Z")

    rows = DocumentIrCatalog(tmp_path).list_latest_lineage_revisions()

    assert {(row["lineage_id"], row["ir_revision"]) for row in rows} == {
        (lineage_a, 2),
        (lineage_b, 1),
    }
