"""Synthetic fixed-revision selection DTOs; no service, report or model calls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esg_targeted.ir.catalog import DocumentIrCatalog
from esg_v2.storage.inventory import StorageInventoryService


RUN = "ir-20261014T010101Z-000000000001"
OCR = "ocr-20261014T000101Z-000000000001"
SHA = "a" * 64


def write_manifest(root: Path, **fields):
    path = root / RUN / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"run_id": RUN, "schema_version": "document-ir-v0.12",
        "document_label": "Synthetic report", "ir_revision": 3, "page_count": 2,
        "readiness": "ready", "ocr_run_id": OCR, **fields}), encoding="utf-8")


def inventory(tmp_path: Path):
    return StorageInventoryService(ocr_output_root=tmp_path / "ocr",
        ir_output_root=tmp_path / "ir", ocr_state_root=tmp_path / "ocr-state",
        ir_state_root=tmp_path / "ir-state", upload_root=tmp_path / "uploads",
        cleanup_root=tmp_path / "cleanup", reference_guard=object())


@pytest.mark.parametrize("full,limited,mode", [
    (True, False, "full"), (False, True, "limited"),
    (False, True, "full"), (False, False, None),
])
def test_catalog_preserves_legacy_readability_and_distinguishes_fixed_capabilities(tmp_path, full, limited, mode):
    write_manifest(tmp_path / "ir", can_build_evidence=full,
        can_build_limited_evidence=limited, evidence_policy={"mode": mode},
        source={"pdf_sha256": SHA.upper(), "display_name": "Synthetic.pdf"})
    row = DocumentIrCatalog((tmp_path / "ir",)).list()[0]
    assert row.can_build_evidence is (full or limited)
    assert row.can_build_full_evidence is full
    assert row.can_build_limited_evidence is limited
    assert row.evidence_policy_mode == mode
    assert row.source_pdf_sha256 == SHA and row.ocr_run_id == OCR
    assert row.ir_revision == 3
    # Eligibility is distinct from the old full-or-limited catalog Boolean.
    eligible = row.can_build_full_evidence or (row.can_build_limited_evidence and row.evidence_policy_mode == "limited")
    assert eligible is (full or (limited and mode == "limited"))
    discovered = inventory(tmp_path)._discover_ir_runs({OCR: {"source_pdf_sha256": SHA}})[RUN]
    assert discovered["can_build_evidence"] is full
    assert discovered["can_build_full_evidence"] is full
    assert discovered["can_build_limited_evidence"] is limited
    assert discovered["evidence_policy_mode"] == mode
    assert discovered["source_pdf_sha256"] == SHA


@pytest.mark.parametrize("source", [{"display_name": "Synthetic.pdf"}, {"pdf_sha256": "invalid"}, "Synthetic.pdf"])
def test_missing_source_fingerprint_is_not_inferred_from_labels_or_linked_ocr(tmp_path, source):
    write_manifest(tmp_path / "ir", can_build_evidence=True, source=source,
        document_id="doc-" + SHA, document_label="Synthetic.pdf")
    row = DocumentIrCatalog((tmp_path / "ir",)).list()[0]
    assert row.source_pdf_sha256 is None
    discovered = inventory(tmp_path)._discover_ir_runs({OCR: {"source_pdf_sha256": SHA}})[RUN]
    assert discovered["source_pdf_sha256"] is None
    assert discovered["ocr_run_id"] == OCR


def test_explicit_source_identity_remains_visible_when_parent_ocr_is_missing(tmp_path):
    write_manifest(tmp_path / "ir", can_build_evidence=True, source={"sha256": SHA,
        "ocr_run_id": OCR}, ocr_run_id=None)
    row = DocumentIrCatalog((tmp_path / "ir",)).list()[0]
    discovered = inventory(tmp_path)._discover_ir_runs({})[RUN]
    assert row.source_pdf_sha256 == discovered["source_pdf_sha256"] == SHA
    assert row.ocr_run_id == discovered["ocr_run_id"] == OCR
