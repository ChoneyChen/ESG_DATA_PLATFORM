from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard
from esg_document.api.report_processing import processing_router
from esg_pipeline.report_processing import ProcessingRequest
from esg_document.storage.processing_lifecycle import ProcessingLifecycle, processing_lifecycle_router
from test_report_processing import fixture,complete
from test_asset_lifecycle import Guard


def setup(tmp_path,guard=None):
    engine,store,asset,calls,_=fixture(tmp_path)
    service=ProcessingLifecycle(engine,guard or Guard())
    engine.lifecycle_actions=service.describe
    app=FastAPI();app.include_router(processing_router(engine));app.include_router(processing_lifecycle_router(service))
    return engine,store,asset,calls,service,TestClient(app)


def test_processing_archive_restore_keeps_pins_and_reusable_artifact_provenance(tmp_path):
    engine,store,asset,calls,service,client=setup(tmp_path)
    state=complete(engine,store,asset);identity=state["processing_id"]
    original=store.pinned_ir_references()
    archived=client.post(f"/api/report-processing/runs/{identity}/archive",json={"reason":"整理处理记录","expected_revision":state["revision"]})
    assert archived.status_code==200 and archived.json()["lifecycle"]["state"]=="archived"
    assert not engine.list() and engine.list(True)
    assert store.pinned_ir_references()==original
    assert "start_extraction" not in {a["action"] for a in archived.json()["available_actions"]}
    restored=client.post(f"/api/report-processing/runs/{identity}/restore",json={"reason":"继续使用"})
    assert restored.status_code==200 and restored.json()["status"]=="ready"
    assert len(calls)==2


def test_processing_discard_releases_only_its_pins_without_deleting_shared_source(tmp_path):
    engine,store,asset,calls,service,client=setup(tmp_path)
    state=complete(engine,store,asset);identity=state["processing_id"]
    store.pin_ir("another-report-consumer",state["ir_run_id"])
    discarded=service.command(identity,"discard",reason="丢弃测试处理记录")
    assert discarded["status"]=="discarded"
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM report_processing_artifacts WHERE processing_id=?",(identity,)).fetchall()
        assert not db.execute("SELECT 1 FROM ir_input_pins WHERE owner_id=?",(identity,)).fetchall()
    assert state["ir_run_id"] in store.pinned_ir_references()
    assert (engine.roots["ir"]/state["ir_run_id"]/"manifest.json").exists()
    restored=service.command(identity,"restore",reason="恢复处理记录")
    assert restored["status"]=="ready"
    with store._connect() as db:
        assert len(db.execute("SELECT 1 FROM report_processing_artifacts WHERE processing_id=?",(identity,)).fetchall())==2
        assert db.execute("SELECT 1 FROM ir_input_pins WHERE owner_id=?",(identity,)).fetchall()


def test_processing_discard_releases_current_and_uncheckpointed_history_dispatch_pins(tmp_path):
    from esg_pipeline.contracts import PipelineTaskStatus,PipelineTaskType
    engine,store,asset,calls,service,client=setup(tmp_path)
    state=complete(engine,store,asset);identity=state["processing_id"]
    current_key=identity+":ir:0"
    store.pin_ir(current_key,state["ir_run_id"])
    history_key=identity+":ir:8"
    history_run="ir-history-source"
    directory=engine.roots["ir"]/history_run;directory.mkdir();(directory/"manifest.json").write_text('{"fixture":"history"}')
    history=store.create(task_id="history-uncheckpointed",task_type=PipelineTaskType.DOCUMENT_IR,operation="build",title="历史构建",
        payload={"_plan_step_key":history_key},native_job_id=history_run)
    store.update(history.task_id,status=PipelineTaskStatus.COMPLETED)
    store.pin_ir(history_key,history_run);store.pin_ir(history.task_id,history_run)
    store.pin_ir("another-parent:ir:0",history_run)
    discarded=service.command(identity,"discard",reason="统一解除本处理任务拥有的引用")
    assert {pin["owner_id"] for pin in discarded["lifecycle"]["pins"]}>={identity,current_key,history_key,history.task_id}
    with store._connect() as db:
        remaining={r[0] for r in db.execute("SELECT owner_id FROM ir_input_pins")}
    assert remaining=={"another-parent:ir:0"}
    service.command(identity,"restore",reason="恢复完整处理引用")
    with store._connect() as db:
        restored={r[0] for r in db.execute("SELECT owner_id FROM ir_input_pins")}
    assert restored>={identity,current_key,history_key,history.task_id,"another-parent:ir:0"}


def test_processing_restore_does_not_reintroduce_invalid_evidence(tmp_path):
    engine,store,asset,calls,service,client=setup(tmp_path)
    state=complete(engine,store,asset);identity=state["processing_id"]
    service.command(identity,"discard",reason="丢弃测试处理记录")
    (engine.roots["ir"]/state["ir_run_id"]/"payload.txt").write_text("changed content")
    with pytest.raises(LifecycleBlocked,match="完整性"):
        service.command(identity,"restore",reason="不允许恢复被改写来源")
    assert engine.get(identity)["status"]=="discarded"
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM ir_input_pins WHERE owner_id=?",(identity,)).fetchall()


def test_processing_candidates_and_unavailable_guard_block_discard_but_not_archive(tmp_path):
    for index,guard in enumerate([Guard([{"code":"candidate_source_reference","message":"已入候选库"}]),PublicationReferenceGuard(url="http://127.0.0.1:1",token=None)]):
        engine,store,asset,calls,service,client=setup(tmp_path/str(index),guard)
        state=complete(engine,store,asset)
        with pytest.raises(LifecycleBlocked):service.command(state["processing_id"],"discard",reason="不能丢弃来源")
        assert service.command(state["processing_id"],"archive",reason="只归档")["lifecycle"]["state"]=="archived"


def test_processing_active_children_and_stale_revision_block_lifecycle(tmp_path):
    engine,store,asset,calls,service,client=setup(tmp_path)
    state=engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    with pytest.raises(LifecycleBlocked):service.command(state["processing_id"],"archive",reason="未停止")
    state=complete(engine,store,asset)
    service.command(state["processing_id"],"archive",reason="整理")
    with pytest.raises(LifecycleBlocked,match="变化"):
        service.command(state["processing_id"],"restore",reason="旧页面操作",expected_revision=state["revision"])


def test_processing_cancelling_is_not_terminal_for_lifecycle(tmp_path):
    engine,store,asset,calls,service,client=setup(tmp_path)
    state=engine.create(ProcessingRequest(asset_id=asset["asset_id"]))
    state["status"]="cancelling"
    engine._save(state,force=True)
    with pytest.raises(LifecycleBlocked,match="尚未停止"):
        service.command(state["processing_id"],"archive",reason="等待子任务停止")
