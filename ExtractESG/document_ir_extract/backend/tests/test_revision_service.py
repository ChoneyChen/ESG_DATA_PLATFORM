from __future__ import annotations

import json
import shutil
from pathlib import Path

from esg_v2.config import Settings
from esg_v2.document.contracts import ArtifactRef, DocumentIrRepairRequest, PatchDecisionRequest
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
    assert child.metadata.source_artifacts["repair_request"]["reason_code"] == "downstream_mismatch"


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
