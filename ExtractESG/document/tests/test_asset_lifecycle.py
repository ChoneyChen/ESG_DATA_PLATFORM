import io
import json
import sqlite3

import pytest

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard
from esg_document.assets.report_catalog import ReportAssetCatalog


class Guard:
    def __init__(self,blockers=None):self.blockers=blockers or []
    def check(self,scope):return {"verified":True,"blockers":self.blockers,"dependencies":[]}
    def reserve(self,scope,reason):
        if self.blockers:raise LifecycleBlocked(self.blockers)
        return "fixture-reservation"
    def update_reservation(self,*args):return {}


def catalog(tmp_path,guard=None,db=None):
    service=ReportAssetCatalog(tmp_path/"pdf",tmp_path/"ocr",reference_guard=guard or Guard(),pipeline_queue_db=db)
    asset=service.add("报告.pdf",io.BytesIO(b"%PDF-1.4\nexplicit test fixture"))
    return service,asset


def test_pdf_archive_retains_source_and_resolves_archived_asset(tmp_path):
    service,asset=catalog(tmp_path)
    source=service.resolve(asset["asset_id"])
    service.lifecycle(asset["asset_id"],"archive",reason="整理报告列表",expected_revision=0)
    assert source.exists() and service.resolve(asset["asset_id"])==source
    assert not service.refresh()["assets"]
    assert service.refresh(include_archived=True)["assets"][0]["lifecycle"]["state"]=="archived"
    restarted=ReportAssetCatalog(service.root,service.ocr_output_root,reference_guard=Guard())
    restarted.lifecycle(asset["asset_id"],"restore",reason="恢复报告",expected_revision=1)
    assert restarted.refresh()["assets"][0]["lifecycle"]["state"]=="active"


def test_unreferenced_pdf_discard_is_restorable_and_checked_for_conflicts(tmp_path):
    service,asset=catalog(tmp_path)
    source=service.resolve(asset["asset_id"]);original=source.read_bytes()
    result=service.lifecycle(asset["asset_id"],"discard",reason="移入回收区")
    assert result["lifecycle"]["state"]=="discarded" and not source.exists()
    restarted=ReportAssetCatalog(service.root,service.ocr_output_root,reference_guard=Guard())
    restarted.lifecycle(asset["asset_id"],"restore",reason="恢复PDF来源")
    assert source.read_bytes()==original


def test_published_and_unavailable_reference_checks_block_pdf_discard_only(tmp_path):
    for index,guard in enumerate([Guard([{"code":"published_source_reference","message":"正式来源"}]),PublicationReferenceGuard(url="http://127.0.0.1:1",token=None)]):
        service,asset=catalog(tmp_path/str(index),guard)
        with pytest.raises(LifecycleBlocked):service.lifecycle(asset["asset_id"],"discard",reason="不可清理")
        assert service.resolve(asset["asset_id"]).exists()
        assert service.lifecycle(asset["asset_id"],"archive",reason="仅隐藏")["operation"]=="archive"


def test_processing_provenance_and_completed_ocr_block_pdf_discard(tmp_path):
    db=tmp_path/"queue.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE pipeline_tasks(task_id TEXT,status TEXT,payload_json TEXT)")
        conn.execute("CREATE TABLE report_processing_runs(processing_id TEXT,status TEXT,asset_id TEXT)")
    service,asset=catalog(tmp_path,db=db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO report_processing_runs VALUES('processing-fixture','ready',?)",(asset["asset_id"],))
    with pytest.raises(LifecycleBlocked,match="处理记录"):
        service.lifecycle(asset["asset_id"],"discard",reason="存在处理来源")
    run=service.ocr_output_root/"ocr-fixture";run.mkdir(parents=True)
    (run/"manifest.json").write_text(json.dumps({"source":{"sha256":asset["sha256"]},"page_count":1}))
    description=service.lifecycle_description(asset["asset_id"])
    assert "processed_report_reference" in {item["code"] for item in description["blockers"]}


def test_revision_conflict_does_not_change_report_state(tmp_path):
    service,asset=catalog(tmp_path)
    service.lifecycle(asset["asset_id"],"archive",reason="第一次操作")
    with pytest.raises(LifecycleBlocked,match="变化"):
        service.lifecycle(asset["asset_id"],"restore",reason="旧页面操作",expected_revision=0)
    assert service.get(asset["asset_id"])["lifecycle"]["state"]=="archived"


def test_pdf_receipt_can_release_uncertain_reservation_before_catalog_checkpoint(tmp_path):
    from urllib.error import URLError
    released=[]
    def transport(operation,payload):
        if operation=="references":return {"schema_version":"lifecycle-references-v1","verified":True,"blockers":[],"dependencies":[]}
        if operation=="reserve":raise URLError("fixture timeout after acquire")
        released.append(operation)
        return {"reservation_id":operation.split("/")[-1],"state":"released"}
    guard=PublicationReferenceGuard(transport=transport)
    service,asset=catalog(tmp_path,guard)
    with pytest.raises(LifecycleBlocked):service.lifecycle(asset["asset_id"],"discard",reason="响应未确认")
    receipt=service.trash.list()[0]
    assert receipt["state"]=="reservation_pending"
    service.lifecycle(asset["asset_id"],"archive",reason="保留后来归档决定")
    assert service.restore_receipt(receipt["cleanup_id"],reason="释放保留权限")["state"]=="restored"
    assert released==["reservations/"+receipt["reservation_id"]]
    assert service.get(asset["asset_id"])["lifecycle"]["state"]=="archived"
    assert service.resolve(asset["asset_id"]).exists()
