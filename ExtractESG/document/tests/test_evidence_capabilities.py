import copy
import json
from types import SimpleNamespace as Obj

import pytest

from esg_document.document.evidence_capabilities import table_location_capabilities, valid_page_bbox
from esg_document.document.reader import DocumentIrPackageReader
from esg_document.document.validator import DocumentIrValidator


def page_geometry():
    return {"page_index": 0, "width": 100, "height": 200, "coordinate_system_ids": ["pdf-points"],
            "coordinate_systems": [{"coordinate_system_id": "pdf-points", "page_index": 0,
                                    "unit": "points", "origin": "top_left", "width": 100, "height": 200}]}


def bbox(**changes):
    return {"x0": 10, "y0": 20, "x1": 30, "y1": 60, "unit": "points", "origin": "top_left",
            "coordinate_system_id": "pdf-points", **changes}


@pytest.mark.parametrize("change", [
    {"unit": "pixels"}, {"unit": "unknown"}, {"origin": "bottom_left"}, {"origin": "unknown"},
    {"x0": -1}, {"y0": -1}, {"x1": 101}, {"y1": 201}, {"x1": 10}, {"y1": 20},
    {"x0": float("nan")}, {"y1": float("inf")}, {"x0": True}, {"x0": "10"},
    {"coordinate_system_id": "different-page"}, {"coordinate_system_id": ""},
])
def test_non_null_invalid_boxes_do_not_qualify_for_precise_highlight(change):
    assert not valid_page_bbox(bbox(**change), page_geometry())


@pytest.mark.parametrize("dimensions", [{"width": None}, {"height": 0}, {"height": -1},
                                         {"width": float("nan")}, {"width": True}])
def test_missing_or_invalid_page_size_does_not_qualify(dimensions):
    assert not valid_page_bbox(bbox(), {**page_geometry(), **dimensions})


def test_declared_coordinate_system_must_match_unit_origin_page_and_size():
    page = page_geometry()
    assert valid_page_bbox(bbox(), page)
    assert valid_page_bbox(bbox(coordinate_system_id=None), {"width": 100, "height": 200})
    for change in ({"unit": "pixels"}, {"origin": "bottom_left"}, {"page_index": 1}, {"width": 101}):
        wrong = copy.deepcopy(page)
        wrong["coordinate_systems"][0].update(change)
        assert not valid_page_bbox(bbox(), wrong)
    assert not valid_page_bbox(bbox(), {**page, "coordinate_systems": []})
    assert not valid_page_bbox(bbox(), {**page, "page_index": None})
    assert not valid_page_bbox(bbox(), {**page, "coordinate_system_ids": "pdf-points"})


def test_partial_cell_geometry_retains_only_verified_cells_and_parent_region():
    table = {"table_id": "table", "page_index": 0, "bbox": bbox(),
             "cells": [{"cell_id": "a", "bbox": bbox()}, {"cell_id": "b", "bbox": None}]}
    cap = table_location_capabilities(table, page_geometry(), cell_grid_available=True, page_image_available=True)
    assert cap["table_region"] is True
    assert cap["cell_geometry"] is False
    assert cap["geometry_coverage"] == {"cell_count": 2, "located_cell_count": 1}
    assert cap["cells"] == [{"cell_geometry": True, "table_region": True}, {"cell_geometry": False, "table_region": True}]
    table["cells"][1]["bbox"] = bbox()
    assert table_location_capabilities(table, page_geometry(), cell_grid_available=True,
                                       page_image_available=True)["cell_geometry"] is True
    table["cells"][1]["page_index"] = 1
    assert not table_location_capabilities(table, page_geometry(), cell_grid_available=True,
                                           page_image_available=True)["cell_geometry"]


def test_logical_grid_is_not_precise_geometry_and_global_restrictions_still_apply():
    page = Obj(page_id="page-0001", **page_geometry())
    table = Obj(table_id="table", page_index=0, bbox=bbox(), cells=[Obj(cell_id="cell", bbox=None)])
    doc = Obj(pages=[page], tables=[table], blocks=[], figures=[], spreads=[], sections=[], logical_tables=[],
              review_tasks=[], conflict_groups=[], coordinate_systems=[Obj(**page.coordinate_systems[0])])
    policy = DocumentIrValidator._evidence_policy(doc, [], True)
    caps = next(row["capabilities"] for row in policy["object_availability"] if row["object_id"] == "table")
    assert caps["cell_grid"] is True and caps["table_region"] is True and caps["cell_geometry"] is False
    table.bbox = bbox(unit="pixels")
    caps = DocumentIrValidator._evidence_policy(doc, [], True)["object_availability"][1]["capabilities"]
    assert caps["cell_grid"] is True and caps["table_region"] is False and caps["source_location"] is False
    assert caps["source_reference"] is True
    table.bbox = bbox()
    table.cells[0].bbox = bbox()
    policy = DocumentIrValidator._evidence_policy(doc, [Obj(severity="blocking", code="duplicate_entity_ids")], False)
    caps = policy["object_availability"][1]["capabilities"]
    assert policy["mode"] == "blocked"
    assert not caps["cell_grid"] and not caps["table_region"] and not caps["cell_geometry"]
    assert not caps["source_reference"]


def write_package(root, *, blocked=False):
    """Minimal immutable API package fixture: no canonical snapshot is needed."""
    page = page_geometry()
    page.update(page_id="page-0001", table_ids=["table"], figure_ids=[], structure_edge_ids=[])
    table = {"table_id": "table", "page_index": 0, "bbox": bbox(), "cells": [
        {"cell_id": "a", "page_index": 0, "row_index": 0, "col_index": 0, "text": "2025", "bbox": bbox()},
        {"cell_id": "b", "page_index": 0, "row_index": 1, "col_index": 0, "text": "123", "bbox": None},
    ]}
    files = {
        "manifest.json": {"package_schema_version": "document-ir-package-v1", "readiness": "auto_review_pending",
            "evidence_policy": {"mode": "blocked" if blocked else "limited", "global_failure": blocked,
                "object_availability": [{"object_id": "table", "capabilities": {"cell_grid": not blocked, "page_image": not blocked}}]},
            "entrypoints": {"pages": "pages/index.json", "tables": "tables/index.json",
                            "coordinate_systems": "coordinates.json", "structure_edges": "edges.jsonl"}},
        "pages/index.json": {"pages": [{"page_index": 0, "path": "pages/page-0001.json"}]},
        "pages/page-0001.json": {"page": page, "table_ids": ["table"]},
        "tables/index.json": {"tables": [{"table_id": "table", "path": "tables/table.json"}]},
        "tables/table.json": table,
        "coordinates.json": {"coordinate_systems": page["coordinate_systems"]},
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    (root / "edges.jsonl").write_text("", encoding="utf-8")
    return files


def test_reader_detail_page_and_list_agree_without_reloading_document_or_mutating_package(tmp_path, monkeypatch):
    write_package(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    reader = DocumentIrPackageReader(tmp_path)
    monkeypatch.setattr(reader, "load_document", lambda **kwargs: pytest.fail("Full snapshot parsed for table capability"))
    reads = []
    original = reader._read_relative
    monkeypatch.setattr(reader, "_read_relative", lambda path: (reads.append(path), original(path))[1])
    detail = reader.table("table")
    page = reader.page(0)
    assert detail == page["tables"][0] == reader.tables()[0]
    assert detail["evidence_capabilities"]["cell_grid"] is True
    assert detail["evidence_capabilities"]["cell_geometry"] is False
    assert detail["evidence_capabilities"]["table_region"] is True
    assert detail["cells"][0]["location_capabilities"]["cell_geometry"] is True
    assert detail["cells"][1]["bbox"] is None
    assert reads.count("pages/page-0001.json") == 1
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert reader.manifest["readiness"] == "auto_review_pending"


def test_reader_missing_page_or_blocked_policy_cannot_claim_location(tmp_path):
    write_package(tmp_path, blocked=True)
    reader = DocumentIrPackageReader(tmp_path)
    table = reader.table("table")
    assert not table["evidence_capabilities"]["table_region"]
    assert not table["cells"][0]["location_capabilities"]["cell_geometry"]
    (tmp_path / "pages/page-0001.json").unlink()
    table = DocumentIrPackageReader(tmp_path).table("table")
    assert not table["evidence_capabilities"]["table_region"]
    assert table["cells"][0]["bbox"] is not None


def test_legacy_page_and_detail_receive_same_projection_without_artifact_changes(tmp_path, monkeypatch):
    files = write_package(tmp_path)
    manifest = files["manifest.json"]
    manifest.pop("package_schema_version")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    raw_table = files["tables/table.json"]
    legacy_page = {"page": files["pages/page-0001.json"]["page"], "tables": [raw_table]}
    (tmp_path / "pages/page_0001.json").write_text(json.dumps(legacy_page), encoding="utf-8")
    reader = DocumentIrPackageReader(tmp_path)
    assert reader.table("table") == reader.page(0)["tables"][0]
    assert "evidence_capabilities" not in json.loads((tmp_path / "tables/table.json").read_text())


def test_http_table_list_detail_and_page_use_identical_capability_projection(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    for key, relative in {
        "ESG_PIPELINE_QUEUE_DB": "queue.sqlite3", "ESG_PIPELINE_TASK_DIR": "tasks",
        "ESG_V2_OCR_OUTPUT_DIR": "ocr", "ESG_V2_DOCUMENT_IR_OUTPUT_DIR": "ir",
        "ESG_V2_UPLOAD_DIR": "uploads", "ESG_V2_OCR_JOB_STATE_DIR": "jobs/ocr",
        "ESG_V2_DOCUMENT_IR_JOB_STATE_DIR": "jobs/ir", "ESG_V2_STORAGE_CLEANUP_DIR": "cleanup",
        "ESG_REPORT_ASSET_DIR": "assets",
    }.items():
        monkeypatch.setenv(key, str(tmp_path / relative))
    import esg_document.api.main as api_main
    api_main = importlib.reload(api_main)
    package = tmp_path / "fixture"
    write_package(package)
    monkeypatch.setattr(api_main.document_archive, "_ir_reader", lambda _: DocumentIrPackageReader(package))
    client = TestClient(api_main.app)  # No lifespan: no scheduler or model execution.
    base = "/api/document-ir/jobs/fixture"
    listed = client.get(base + "/tables")
    detailed = client.get(base + "/tables/table")
    page = client.get(base + "/pages/0")
    assert listed.status_code == detailed.status_code == page.status_code == 200
    assert listed.json()[0] == detailed.json() == page.json()["tables"][0]
    assert detailed.json()["evidence_capabilities"]["cell_geometry"] is False
    assert detailed.json()["evidence_capabilities"]["table_region"] is True
