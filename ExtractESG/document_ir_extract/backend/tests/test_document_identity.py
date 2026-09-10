from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from esg_v2.document.catalog import DocumentIrCatalog
from esg_v2.document.identity import (
    document_id_from_sha256,
    resolve_document_identity,
)
from esg_v2.document.versioning import DocumentIrVersionManager


def _write_manifest(root: Path, run_id: str, payload: dict[str, object]) -> None:
    run = root / run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"run_id": run_id, **payload}), encoding="utf-8")


def test_document_identity_is_content_addressed_and_rejects_crossed_pdf(tmp_path: Path) -> None:
    first = tmp_path / "first-name.pdf"
    renamed = tmp_path / "renamed.pdf"
    other = tmp_path / "other.pdf"
    first.write_bytes(b"same-pdf-bytes")
    renamed.write_bytes(first.read_bytes())
    other.write_bytes(b"different-pdf-bytes")
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    manifest = {"source": {"display_name": first.name, "sha256": digest}}

    left = resolve_document_identity(ocr_run_id="ocr-one", ocr_manifest=manifest, pdf_path=str(first))
    right = resolve_document_identity(ocr_run_id="ocr-two", ocr_manifest=manifest, pdf_path=str(renamed))

    assert left.document_id == right.document_id == document_id_from_sha256(digest)
    assert left.document_label == first.name
    with pytest.raises(ValueError, match="does not match the immutable OCR source"):
        resolve_document_identity(ocr_run_id="ocr-three", ocr_manifest=manifest, pdf_path=str(other))


def test_independent_builds_start_separate_lineages_at_revision_one(tmp_path: Path) -> None:
    manager = DocumentIrVersionManager(tmp_path)
    document_id = document_id_from_sha256("a" * 64)

    first = manager.next_revision(
        "ocr-20260823T010101Z-000000000001",
        document_id=document_id,
        root_ir_run_id="ir-20260823T010101Z-000000000001",
    )
    _write_manifest(
        tmp_path,
        "ir-20260823T010101Z-000000000001",
        {
            "ocr_run_id": "ocr-20260823T010101Z-000000000001",
            "document_id": document_id,
            "lineage_id": first.lineage_id,
            "ir_revision": first.revision,
            "parent_ir_run_id": None,
        },
    )
    second_root = manager.next_revision(
        "ocr-20260823T010101Z-000000000001",
        document_id=document_id,
        root_ir_run_id="ir-20260823T010101Z-000000000002",
    )
    child = manager.next_revision(
        "ocr-20260823T010101Z-000000000001",
        "ir-20260823T010101Z-000000000001",
        document_id=document_id,
        root_ir_run_id="ir-20260823T010101Z-000000000003",
    )

    assert first.revision == second_root.revision == 1
    assert first.lineage_id != second_root.lineage_id
    assert child.revision == 2
    assert child.lineage_id == first.lineage_id


def test_document_catalog_groups_same_pdf_across_ocr_runs_and_keeps_other_pdf_separate(tmp_path: Path) -> None:
    ocr_root = tmp_path / "ocr"
    ir_root = tmp_path / "ir"
    digest = "b" * 64
    other_digest = "c" * 64
    for index in (1, 2):
        _write_manifest(
            ocr_root,
            f"ocr-20260823T01010{index}Z-00000000000{index}",
            {
                "source": {"display_name": "Report A.pdf", "sha256": digest},
                "model": "PaddleOCR-VL-1.6",
                "page_count": 80,
                "written_at": f"2026-08-23T01:01:0{index}+00:00",
            },
        )
    _write_manifest(
        ocr_root,
        "ocr-20260823T010103Z-000000000003",
        {
            "source": {"display_name": "Report B.pdf", "sha256": other_digest},
            "model": "PaddleOCR-VL-1.6",
            "page_count": 60,
            "written_at": "2026-08-23T01:01:03+00:00",
        },
    )
    _write_manifest(
        ir_root,
        "ir-20260823T020101Z-000000000001",
        {
            "ocr_run_id": "ocr-20260823T010101Z-000000000001",
            "document_id": document_id_from_sha256(digest),
            "document_label": "Report A.pdf",
            "lineage_id": "irl-111111111111111111111111",
            "ir_revision": 1,
            "parent_ir_run_id": None,
            "readiness": "ready",
            "source": {"pdf_sha256": digest},
            "written_at": "2026-08-23T02:01:01+00:00",
        },
    )

    documents = DocumentIrCatalog(ir_root, ocr_output_root=ocr_root).list_documents()
    report_a = next(item for item in documents if item["source_pdf_sha256"] == digest)
    report_b = next(item for item in documents if item["source_pdf_sha256"] == other_digest)

    assert len(documents) == 2
    assert report_a["ocr_run_count"] == 2
    assert report_a["ir_run_count"] == 1
    assert report_a["lineage_count"] == 1
    assert report_b["status"] == "ocr_only"


def test_catalog_links_hashless_url_ocr_to_the_document_ir_that_consumed_it(tmp_path: Path) -> None:
    ocr_root = tmp_path / "ocr"
    ir_root = tmp_path / "ir"
    ocr_run_id = "ocr-20260823T030101Z-000000000001"
    digest = "d" * 64
    _write_manifest(
        ocr_root,
        ocr_run_id,
        {
            "source": {"display_name": "Remote Report.pdf", "request_reference": "https://example.test/report.pdf"},
            "written_at": "2026-08-23T03:01:01+00:00",
        },
    )
    _write_manifest(
        ir_root,
        "ir-20260823T030201Z-000000000001",
        {
            "ocr_run_id": ocr_run_id,
            "document_id": document_id_from_sha256(digest),
            "document_label": "Remote Report.pdf",
            "lineage_id": "irl-222222222222222222222222",
            "ir_revision": 1,
            "readiness": "ready",
            "source": {"pdf_sha256": digest},
            "written_at": "2026-08-23T03:02:01+00:00",
        },
    )

    documents = DocumentIrCatalog(ir_root, ocr_output_root=ocr_root).list_documents()

    assert len(documents) == 1
    assert documents[0]["document_id"] == document_id_from_sha256(digest)
    assert documents[0]["ocr_run_count"] == 1
    assert documents[0]["ir_run_count"] == 1
