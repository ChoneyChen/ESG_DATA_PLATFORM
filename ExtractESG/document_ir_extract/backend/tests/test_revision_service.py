from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    ArtifactRef,
    ConflictGroup,
    DocumentIrRepairRequest,
    PatchDecisionRequest,
    ReviewRetryRequest,
)
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.revision_service import DocumentIrRevisionService
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.writer import DocumentIrWriter
from esg_v2.models.model_registry import ModelHealthRegistry
from esg_v2.utils.hashing import sha256_file

from test_review_routing import _document, _orchestrator


PARENT_RUN_ID = "ir-20260718T020202Z-000000000001"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        output_root=tmp_path / "ocr",
        document_ir_output_root=tmp_path / "ir",
        upload_root=tmp_path / "uploads",
        qiniu_vlm_model=None,
        max_vlm_reviews_per_ir_run=3,
    )


def _write_human_parent(tmp_path: Path, settings: Settings):
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "disagree").execute(_document(tmp_path), max_auto_review_rounds=2)
    parent_dir = settings.document_ir_output_root / PARENT_RUN_ID
    document.metadata.run_id = PARENT_RUN_ID
    DocumentIrWriter(parent_dir).write(document)
    return document, parent_dir


def test_human_patch_decision_creates_child_revision_without_mutating_parent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parent, parent_dir = _write_human_parent(tmp_path, settings)
    patch = next(item for item in parent.atomic_patches if item.status == "human_required")

    manifest = DocumentIrRevisionService(settings).decide_patch(
        PARENT_RUN_ID,
        patch.patch_id,
        PatchDecisionRequest(action="accept", decided_by="tester", notes="confirmed from page"),
    )
    child_dir = settings.document_ir_output_root / manifest["run_id"]
    child = DocumentIrPackageReader(child_dir, ocr_output_root=settings.output_root).load_document()
    unchanged_parent = DocumentIrPackageReader(parent_dir, ocr_output_root=settings.output_root).load_document()

    assert manifest["parent_ir_run_id"] == PARENT_RUN_ID
    assert manifest["ir_revision"] == 2
    assert child.blocks[0].text == "the metric"
    assert child.review_tasks[0].status == "reviewed"
    assert child.final_decisions[-1].outcome == "human_resolved"
    assert unchanged_parent.blocks[0].text == "teh metric"


def test_rejecting_patch_does_not_close_task_until_human_explicitly_keeps_current_ir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parent, _ = _write_human_parent(tmp_path, settings)
    patch = next(item for item in parent.atomic_patches if item.status == "human_required")
    service = DocumentIrRevisionService(settings)

    rejected_manifest = service.decide_patch(
        PARENT_RUN_ID,
        patch.patch_id,
        PatchDecisionRequest(action="reject", decided_by="tester", notes="The page does not support this patch."),
    )
    rejected = DocumentIrPackageReader(
        settings.document_ir_output_root / rejected_manifest["run_id"],
        ocr_output_root=settings.output_root,
    ).load_document()

    assert rejected.review_tasks[0].status == "human_required"
    assert next(item for item in rejected.atomic_patches if item.patch_id == patch.patch_id).status == "rejected"

    resolved_manifest = service.decide_task(
        rejected_manifest["run_id"],
        rejected.review_tasks[0].task_id,
        PatchDecisionRequest(
            action="keep_current",
            decided_by="tester",
            notes="The source page matches the existing IR text; no correction is required.",
        ),
    )
    resolved = DocumentIrPackageReader(
        settings.document_ir_output_root / resolved_manifest["run_id"],
        ocr_output_root=settings.output_root,
    ).load_document()

    assert resolved.review_tasks[0].status == "reviewed"
    assert resolved.final_decisions[-1].outcome == "human_resolved"
    assert resolved.final_decisions[-1].accepted_patch_ids == []


def test_human_review_inbox_explains_one_question_and_only_exposes_guard_safe_patches(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _, parent_dir = _write_human_parent(tmp_path, settings)

    inbox = DocumentIrPackageReader(parent_dir, ocr_output_root=settings.output_root).human_review_inbox()
    bundle = inbox["groups"]["human_required"][0]
    guidance = bundle["guidance"]
    human_patch_ids = [
        patch["patch_id"]
        for patch in bundle["patches"]
        if patch["status"] == "human_required"
    ]

    assert guidance["question"]
    assert guidance["current_risk"]
    assert len(guidance["evidence_checklist"]) >= 3
    assert guidance["failure_category"] == "semantic_decision"
    assert guidance["safe_patch_ids"] == human_patch_ids
    assert guidance["unsafe_patch_ids"] == []


def test_nonretryable_review_is_isolated_in_system_blocked_inbox(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = _document(tmp_path)
    document.metadata.run_id = PARENT_RUN_ID
    task = document.review_tasks[0]
    task.status = "deferred"
    task.failure_class = "repeated_failure"
    task.failure_owner = "model"
    task.retryable = False
    task.failure_fingerprint = "failure-deadbeef"
    task.result = {
        "outcome": "deferred",
        "reason": "automation_proposal_invalid_after_repair",
        "failure_class": task.failure_class,
        "failure_owner": task.failure_owner,
        "retryable": False,
    }
    parent_dir = settings.document_ir_output_root / PARENT_RUN_ID
    DocumentIrWriter(parent_dir).write(document)

    inbox = DocumentIrPackageReader(
        parent_dir,
        ocr_output_root=settings.output_root,
    ).human_review_inbox()
    bundle = inbox["groups"]["system_blocked"][0]

    assert inbox["counts"]["system_blocked"] == 1
    assert inbox["counts"]["blocking_deferred"] == 0
    assert bundle["guidance"]["can_retry_automation"] is False
    assert bundle["guidance"]["failure_class"] == "repeated_failure"
    assert "继续点击重试不会解决" in bundle["guidance"]["recommended_action"]


def test_optional_enhancement_can_be_audited_as_nonmaterial_in_child_revision(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    document = _document(tmp_path)
    document.metadata.run_id = PARENT_RUN_ID
    task = document.review_tasks[0]
    task.blocking = False
    task.status = "deferred"
    task.failure_class = "repeated_failure"
    task.failure_owner = "model"
    task.retryable = False
    task.failure_fingerprint = "stale-fingerprint"
    document.conflict_groups = [
        ConflictGroup(
            conflict_id="conflict-000001",
            target_id=task.target_id,
            conflict_type="optional_visual_enhancement",
            blocking=False,
            routing_disposition="optional_task",
        )
    ]
    parent_dir = settings.document_ir_output_root / PARENT_RUN_ID
    DocumentIrWriter(parent_dir).write(document)

    manifest = DocumentIrRevisionService(settings).decide_task(
        PARENT_RUN_ID,
        task.task_id,
        PatchDecisionRequest(
            action="accept_current_nonmaterial",
            decided_by="tester",
            notes="The current IR preserves all source content; this visual enhancement is noncritical.",
        ),
    )
    child = DocumentIrPackageReader(
        settings.document_ir_output_root / manifest["run_id"],
        ocr_output_root=settings.output_root,
    ).load_document()
    unchanged_parent = DocumentIrPackageReader(
        parent_dir,
        ocr_output_root=settings.output_root,
    ).load_document()

    assert manifest["parent_ir_run_id"] == PARENT_RUN_ID
    assert child.review_tasks[0].status == "reviewed"
    assert child.review_tasks[0].failure_class == "none"
    assert child.review_tasks[0].failure_owner == "none"
    assert child.review_tasks[0].failure_fingerprint is None
    assert child.conflict_groups[0].status == "resolved"
    assert (
        child.conflict_groups[0].routing_disposition
        == "accepted_nonmaterial_difference"
    )
    assert child.quality_report["human_actions"][-1]["action"] == "accept_current_nonmaterial"
    assert unchanged_parent.review_tasks[0].status == "deferred"


def test_blocking_task_cannot_be_accepted_as_nonmaterial(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = _document(tmp_path)
    document.metadata.run_id = PARENT_RUN_ID
    document.review_tasks[0].status = "deferred"
    DocumentIrWriter(settings.document_ir_output_root / PARENT_RUN_ID).write(document)

    with pytest.raises(ValueError, match="Only a non-blocking enhancement"):
        DocumentIrRevisionService(settings).decide_task(
            PARENT_RUN_ID,
            "review-1",
            PatchDecisionRequest(
                action="accept_current_nonmaterial",
                decided_by="tester",
                notes="This must not bypass a blocking review.",
            ),
        )


def test_targeted_repair_request_creates_pending_child_when_model_execution_is_off(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = _document(tmp_path)
    document.metadata.run_id = PARENT_RUN_ID
    parent_dir = settings.document_ir_output_root / PARENT_RUN_ID
    DocumentIrWriter(parent_dir).write(document)

    manifest = DocumentIrRevisionService(settings).repair(
        DocumentIrRepairRequest(
            parent_ir_run_id=PARENT_RUN_ID,
            target_ids=["block-1"],
            reason_code="downstream_mismatch",
            requested_by="tester",
            execute_vlm_reviews=False,
        )
    )
    child_dir = settings.document_ir_output_root / manifest["run_id"]
    child = DocumentIrPackageReader(child_dir, ocr_output_root=settings.output_root).load_document()
    repair_tasks = [item for item in child.review_tasks if "downstream_mismatch" in item.reason_codes]

    assert manifest["parent_ir_run_id"] == PARENT_RUN_ID
    assert manifest["readiness"] == "auto_review_pending"
    assert len(repair_tasks) == 1
    assert repair_tasks[0].scope[0].target_id == "block-1"
    assert repair_tasks[0].target_id == "block-1"
    assert repair_tasks[0].review_plan is not None
    assert repair_tasks[0].prompt_intent == repair_tasks[0].review_plan.question
    assert child.metadata.source_artifacts["repair_request"]["reason_code"] == "downstream_mismatch"


def test_resume_reviews_targets_existing_deferred_task_in_child_revision(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    document = _document(tmp_path)
    document.metadata.run_id = PARENT_RUN_ID
    document.review_tasks[0].status = "deferred"
    document.review_tasks[0].result = {"outcome": "deferred", "reason": "temporary cloud failure"}
    parent_dir = settings.document_ir_output_root / PARENT_RUN_ID
    DocumentIrWriter(parent_dir).write(document)

    calls: dict[str, object] = {}
    telemetry_events: list[dict[str, object]] = []
    logs: list[str] = []

    class FakeOrchestrator:
        def __init__(self, settings, *, api_key=None, telemetry=None):
            calls["api_key"] = api_key

        def execute(self, document, *, review_target_ids=None, max_auto_review_rounds=2, log=None):
            calls["targets"] = review_target_ids
            calls["rounds"] = max_auto_review_rounds
            task = next(item for item in document.review_tasks if item.task_id in review_target_ids)
            task.status = "auto_resolved"
            task.result = {"outcome": "auto_confirmed"}
            return document

    monkeypatch.setattr("esg_v2.document.revision_service.AgentReviewOrchestrator", FakeOrchestrator)
    manifest = DocumentIrRevisionService(settings).resume_reviews(
        PARENT_RUN_ID,
        ReviewRetryRequest(
            task_ids=["review-1"],
            requested_by="tester",
            qiniu_api_key="runtime-key",
        ),
        telemetry=telemetry_events.append,
        log=logs.append,
    )
    child = DocumentIrPackageReader(
        settings.document_ir_output_root / manifest["run_id"],
        ocr_output_root=settings.output_root,
    ).load_document()

    assert manifest["parent_ir_run_id"] == PARENT_RUN_ID
    assert child.review_tasks[0].status == "auto_resolved"
    assert child.metadata.source_artifacts["review_retry_request"]["task_ids"] == ["review-1"]
    assert manifest["review_retry_result"]["selected_count"] == 1
    assert manifest["review_retry_result"]["resolved_count"] == 1
    assert manifest["review_retry_result"]["remaining_count"] == 0
    assert child.quality_report["review_retry_result"]["status_transitions"]["review-1"]["after"] == "auto_resolved"
    assert calls == {"api_key": "runtime-key", "targets": ["review-1"], "rounds": 3}
    assert [event["index"] for event in telemetry_events if event["event"] == "stage_started"] == [1, 2, 3]
    assert logs[-1].startswith("3/3")


def test_resume_reviews_defaults_to_blocking_tasks_only(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    document = _document(tmp_path)
    document.metadata.run_id = PARENT_RUN_ID
    document.review_tasks[0].status = "deferred"
    optional = document.review_tasks[0].model_copy(deep=True)
    optional.task_id = "review-2"
    optional.blocking = False
    optional.status = "deferred"
    document.review_tasks.append(optional)
    DocumentIrWriter(settings.document_ir_output_root / PARENT_RUN_ID).write(document)
    selected: list[str] = []

    class FakeOrchestrator:
        def __init__(self, settings, *, api_key=None, telemetry=None):
            pass

        def execute(self, document, *, review_target_ids=None, max_auto_review_rounds=2, log=None):
            selected.extend(review_target_ids or [])
            return document

    monkeypatch.setattr("esg_v2.document.revision_service.AgentReviewOrchestrator", FakeOrchestrator)
    DocumentIrRevisionService(settings).resume_reviews(
        PARENT_RUN_ID,
        ReviewRetryRequest(requested_by="tester"),
    )

    assert selected == ["review-1"]


def test_legacy_revision_relocates_page_images_into_package_v1_paths(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = _document(tmp_path)
    parent_run_id = "ir-parent-legacy"
    parent_dir = settings.document_ir_output_root / parent_run_id
    page_dir = parent_dir / "page_images"
    page_dir.mkdir(parents=True)
    source = Path(document.pages[0].page_image_path)
    legacy_page = page_dir / "page_0001.png"
    shutil.copy2(source, legacy_page)
    document.metadata.run_id = parent_run_id
    document.pages[0].page_image_path = str(legacy_page)
    document.artifacts = [
        ArtifactRef(
            artifact_id="artifact-page-image-0001",
            kind="page_image",
            path=str(legacy_page),
            media_type="image/png",
            page_index=0,
            sha256=sha256_file(legacy_page),
        )
    ]
    (parent_dir / "manifest.json").write_text(
        json.dumps({"run_id": parent_run_id, "ocr_run_id": "ocr-test", "ir_revision": 1}),
        encoding="utf-8",
    )
    (parent_dir / "document_ir.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")

    manifest = DocumentIrRevisionService(settings).repair(
        DocumentIrRepairRequest(
            parent_ir_run_id=parent_run_id,
            target_ids=["block-1"],
            reason_code="legacy_migration_check",
            requested_by="tester",
            execute_vlm_reviews=False,
        )
    )

    child_dir = settings.document_ir_output_root / manifest["run_id"]
    child = DocumentIrPackageReader(child_dir, ocr_output_root=settings.output_root).load_document()
    assert child.pages[0].page_image_path == "artifacts/page-images/page-0001.png"
    assert child.artifacts[0].path == "artifacts/page-images/page-0001.png"
    assert (child_dir / "artifacts" / "page-images" / "page-0001.png").exists()
