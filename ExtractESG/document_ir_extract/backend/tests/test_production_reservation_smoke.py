"""Isolated source guards; no report processing, old fixtures or live services."""
import io
import json
import sqlite3
from types import SimpleNamespace

import pytest

from esg_runtime.lifecycle import LifecycleBlocked
from esg_runtime.production_references import production_references
from esg_v2.assets.report_catalog import ReportAssetCatalog
from esg_v2.storage.processing_lifecycle import ProcessingLifecycle


def database(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE pipeline_tasks(task_id TEXT,status TEXT,payload_json TEXT)")
    db.execute("CREATE TABLE production_automations(automation_id TEXT PRIMARY KEY,state_json TEXT)")
    return db


def reserve(db, asset, status="planned", item_status="pending", processing_id=None):
    state = {"schema_version": "production-automation-v1", "automation_id": "synthetic-batch",
        "status": status, "items": [{"item_id": "item-1", "asset_id": asset["asset_id"],
        "source_sha256": asset["sha256"], "status": item_status, "processing_id": processing_id}]}
    db.execute("INSERT OR REPLACE INTO production_automations VALUES (?,?)", (state["automation_id"], json.dumps(state)))
    db.commit()


class Guard:
    def check(self, scope):
        return {"verified": True, "blockers": [], "dependencies": []}


@pytest.mark.parametrize("status", ["planned", "active", "paused", "waiting_credentials", "cancelling"])
def test_pending_frozen_pdf_cannot_be_discarded_but_archive_keeps_its_bytes(tmp_path, status):
    path = tmp_path / "queue.sqlite3"
    with database(path) as db:
        catalog = ReportAssetCatalog(tmp_path / "pdf", tmp_path / "ocr", reference_guard=Guard(), pipeline_queue_db=path)
        asset = catalog.add("synthetic.pdf", io.BytesIO(b"%PDF-1.4 synthetic reservation"))
        source = catalog.resolve(asset["asset_id"])
        reserve(db, asset, status)
        description = catalog.lifecycle_description(asset["asset_id"])
        assert "production_report_reference" in {b["code"] for b in description["blockers"]}
        assert "discard" not in {a["action"] for a in description["available_actions"]}
        with pytest.raises(LifecycleBlocked):
            catalog.lifecycle(asset["asset_id"], "discard", reason="synthetic source guard")
        catalog.lifecycle(asset["asset_id"], "archive", reason="synthetic list visibility")
        assert source.exists() and catalog.resolve(asset["asset_id"]) == source


def test_terminal_batch_releases_pending_reservation(tmp_path):
    with database(tmp_path / "q.sqlite3") as db:
        asset = {"asset_id": "pdf-synthetic", "sha256": "a" * 64}
        for status in ("completed", "partial", "cancelled"):
            reserve(db, asset, status)
            assert production_references(db, asset_id=asset["asset_id"]) == []


def test_invalid_reservation_fails_closed(tmp_path):
    with database(tmp_path / "q.sqlite3") as db:
        db.execute("INSERT INTO production_automations VALUES ('synthetic-batch','{}')")
        with pytest.raises(ValueError, match="引用无法核实"):
            production_references(db, asset_id="pdf-synthetic")


def test_ready_processing_record_still_required_by_awaiting_extraction(tmp_path):
    with database(tmp_path / "q.sqlite3") as db:
        db.row_factory = sqlite3.Row
        reserve(db, {"asset_id": "pdf-synthetic", "sha256": "a" * 64}, "paused", "extracting", "processing-synthetic")
        service = ProcessingLifecycle(SimpleNamespace())
        blockers = service._idle_blockers({"processing_id": "processing-synthetic", "status": "ready", "steps": {}}, db)
        assert service._idle_blockers_cached(blockers)
        assert blockers[0]["code"] == "production_processing_reference"
