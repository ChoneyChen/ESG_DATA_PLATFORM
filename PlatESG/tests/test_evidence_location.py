import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PlatESG.backend.evidence_location import candidate_locations


def fixture(tmp_path):
    pdfs, irs = tmp_path / "pdfs", tmp_path / "irs"
    pdfs.mkdir()
    document = pdfs / "report.pdf"
    document.write_bytes(b"%PDF-1.4 fixture bytes")
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    asset = "pdf-" + digest[:24]
    (pdfs / "report-catalog.json").write_text(json.dumps({"assets": [{"sha256": digest, "relative_path": "report.pdf"}]}))
    irs = lambda run: {"ir_run_id":"ir-fixed", "ir_revision":2,"report_resource":{"sha256":digest}}
    return {"upstream_system": "ExtractESG", "source_sha256": digest, "source_artifact_ref": asset,
            "source_ir_run_id": "ir-fixed", "source_ir_revision": 2}, pdfs, irs


def test_exact_source_and_revision_enable_only_verified_locations(tmp_path):
    row, pdfs, irs = fixture(tmp_path)
    result = candidate_locations([deepcopy(row)], [pdfs], irs)[0]["evidence_location"]
    assert result["state"] == "verified"
    assert result["ir_available"] and result["pdf_available"]
    assert result["asset_id"] == row["source_artifact_ref"]
    bad = candidate_locations([{**row, "source_ir_revision": 3}], [pdfs], irs)[0]["evidence_location"]
    assert not bad["ir_available"] and bad["ir_run_id"] is None
    assert bad["pdf_available"]


def test_explicit_synthetic_provenance_never_opens_real_looking_ids(tmp_path):
    row, pdfs, irs = fixture(tmp_path)
    result = candidate_locations([{**row, "upstream_system": "synthetic-demo"}], [pdfs], irs)[0]["evidence_location"]
    assert result["state"] == "synthetic"
    assert not result["ir_available"] and not result["pdf_available"]


def test_replaced_pdf_and_escaping_ir_fail_closed(tmp_path):
    row, pdfs, irs = fixture(tmp_path)
    (pdfs / "report.pdf").write_bytes(b"changed")
    result = candidate_locations([{**row, "source_ir_run_id": "../ir-fixed"}], [pdfs], irs)[0]["evidence_location"]
    assert result["state"] == "unavailable"
    assert result["asset_id"] is None and result["ir_run_id"] is None


def test_source_without_fingerprint_remains_unknown(tmp_path):
    result = candidate_locations([{"source_ir_run_id": "ir-present"}], [], tmp_path)[0]["evidence_location"]
    assert result["state"] == "unknown" and not result["ir_available"]
