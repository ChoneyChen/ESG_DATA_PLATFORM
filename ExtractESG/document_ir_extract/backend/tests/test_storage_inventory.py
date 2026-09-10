from __future__ import annotations

import json
from pathlib import Path

from esg_v2.document.identity import document_id_from_sha256
from esg_v2.storage.inventory import DeletionPlanRequest, StorageInventoryService


OCR_RUN = "ocr-20260827T010101Z-000000000001"
IR_ROOT = "ir-20260827T020101Z-000000000001"
IR_CHILD = "ir-20260827T020201Z-000000000002"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _service(tmp_path: Path) -> StorageInventoryService:
    return StorageInventoryService(
        ocr_output_root=tmp_path / "ocr_output",
        ir_output_root=tmp_path / "document_ir_output",
        ocr_state_root=tmp_path / ".local/jobs/ocr",
        ir_state_root=tmp_path / ".local/jobs/document-ir",
        upload_root=tmp_path / ".local/uploads",
        cleanup_root=tmp_path / ".local/storage-cleanup",
    )


def _write_chain(tmp_path: Path) -> str:
    digest = "a" * 64
    document_id = document_id_from_sha256(digest)
    _write_json(
        tmp_path / "ocr_output" / OCR_RUN / "manifest.json",
        {
            "run_id": OCR_RUN,
            "source": {"display_name": "Report.pdf", "sha256": digest},
            "page_count": 2,
            "written_at": "2026-08-27T01:01:01+00:00",
        },
    )
    _write_json(
        tmp_path / ".local/jobs/ocr" / OCR_RUN / "state.json",
        {"run_id": OCR_RUN, "status": "done", "summary": {"document_id": document_id}},
    )
    (tmp_path / ".local/uploads" / OCR_RUN).mkdir(parents=True)
    (tmp_path / ".local/uploads" / OCR_RUN / "Report.pdf").write_bytes(b"pdf")
    for run_id, parent, revision in ((IR_ROOT, None, 1), (IR_CHILD, IR_ROOT, 2)):
        _write_json(
            tmp_path / "document_ir_output" / run_id / "manifest.json",
            {
                "run_id": run_id,
                "ocr_run_id": OCR_RUN,
                "document_id": document_id,
                "document_label": "Report.pdf",
                "lineage_id": "irl-111111111111111111111111",
                "ir_revision": revision,
                "parent_ir_run_id": parent,
                "readiness": "ready",
                "can_build_evidence": True,
                "written_at": f"2026-08-27T02:0{revision}:01+00:00",
            },
        )
        _write_json(
            tmp_path / ".local/jobs/document-ir" / run_id / "state.json",
            {"run_id": run_id, "ocr_run_id": OCR_RUN, "status": "done"},
        )
    return document_id


def test_inventory_groups_packages_states_uploads_and_ir_revisions(tmp_path: Path) -> None:
    document_id = _write_chain(tmp_path)

    inventory = _service(tmp_path).inventory()

    assert inventory["summary"]["document_count"] == 1
    assert inventory["summary"]["ocr_run_count"] == 1
    assert inventory["summary"]["ir_run_count"] == 2
    document = inventory["documents"][0]
    assert document["document_id"] == document_id
    assert document["ocr_runs"][0]["has_upload"] is True
    assert {item["run_id"] for item in document["ocr_runs"][0]["ir_runs"]} == {IR_ROOT, IR_CHILD}
    assert next(item for item in document["ocr_runs"][0]["ir_runs"] if item["run_id"] == IR_ROOT)[
        "child_ir_run_ids"
    ] == [IR_CHILD]


def test_deletion_plan_blocks_dependencies_without_cascade(tmp_path: Path) -> None:
    _write_chain(tmp_path)
    service = _service(tmp_path)

    ocr_plan = service.create_deletion_plan(
        DeletionPlanRequest.model_validate(
            {"targets": [{"kind": "ocr", "target_id": OCR_RUN}], "cascade": False}
        )
    )
    ir_plan = service.create_deletion_plan(
        DeletionPlanRequest.model_validate(
            {"targets": [{"kind": "ir", "target_id": IR_ROOT}], "cascade": False}
        )
    )

    assert ocr_plan["can_execute"] is False
    assert {item["code"] for item in ocr_plan["blockers"]} == {"ocr_has_ir_dependencies"}
    assert ir_plan["can_execute"] is False
    assert {item["code"] for item in ir_plan["blockers"]} == {"ir_has_child_revisions"}


def test_cascade_plan_moves_complete_chain_and_keeps_cleanup_audit(tmp_path: Path) -> None:
    _write_chain(tmp_path)
    service = _service(tmp_path)
    plan = service.create_deletion_plan(
        DeletionPlanRequest.model_validate(
            {"targets": [{"kind": "ocr", "target_id": OCR_RUN}], "cascade": True}
        )
    )

    assert plan["can_execute"] is True
    assert len(plan["selected_runs"]) == 3
    assert "confirmation_phrase" not in plan
    result = service.execute_deletion_plan(plan["plan_id"])

    assert result["deleted_path_count"] == 7
    assert not (tmp_path / "ocr_output" / OCR_RUN).exists()
    assert not (tmp_path / "document_ir_output" / IR_ROOT).exists()
    assert not (tmp_path / "document_ir_output" / IR_CHILD).exists()
    assert not (tmp_path / ".local/uploads" / OCR_RUN).exists()
    assert (tmp_path / ".local/storage-cleanup/cleanup-audit.jsonl").exists()


def test_incomplete_cleanup_excludes_running_jobs(tmp_path: Path) -> None:
    failed = "ocr-20260827T030101Z-000000000003"
    running = "ocr-20260827T030201Z-000000000004"
    _write_json(tmp_path / ".local/jobs/ocr" / failed / "state.json", {"run_id": failed, "status": "failed"})
    _write_json(tmp_path / ".local/jobs/ocr" / running / "state.json", {"run_id": running, "status": "running"})
    service = _service(tmp_path)

    plan = service.create_deletion_plan(
        DeletionPlanRequest.model_validate(
            {"targets": [{"kind": "incomplete", "target_id": "all"}], "cascade": False}
        )
    )

    assert plan["can_execute"] is True
    assert plan["selected_runs"] == [{"kind": "ocr", "run_id": failed, "status": "failed"}]
