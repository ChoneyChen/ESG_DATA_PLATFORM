import json

import pytest

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard
from esg_pipeline.contracts import PipelineTaskStatus,PipelineTaskType
from esg_pipeline.store import PipelineQueueStore
from esg_document.storage.queue_lifecycle import QueueReferenceLifecycle
from test_asset_lifecycle import Guard


IR="ir-20261004T001000Z-012345678901"


def setup(tmp_path,*,payload=None,guard=None,operation="run"):
    store=PipelineQueueStore(tmp_path/"queue.sqlite3")
    manifest=tmp_path/"ir"/IR/"manifest.json";manifest.parent.mkdir(parents=True);manifest.write_text(json.dumps({"run_id":IR}))
    task=store.create(task_id="task-reference",task_type=PipelineTaskType.TARGETED_EXTRACTION,operation=operation,title="测试引用",
        payload={"ir_run_id":IR,**(payload or {})},native_job_id="job-fixture")
    store.update(task.task_id,status=PipelineTaskStatus.COMPLETED)
    return store,QueueReferenceLifecycle(store,tmp_path/"ir",guard or Guard()),manifest


def test_archive_keeps_pins_explicit_release_and_restart_preserve_disposition(tmp_path):
    store,service,manifest=setup(tmp_path)
    store.archive("task-reference",reason="仅整理列表")
    assert IR in store.pinned_ir_references()
    assert service.command("task-reference","release-references",reason="确认无候选来源")["state"]=="released"
    assert IR not in store.pinned_ir_references()
    restarted=PipelineQueueStore(store.path)
    assert IR not in restarted.pinned_ir_references(),"restart must not undo explicit reference release"
    service=QueueReferenceLifecycle(restarted,tmp_path/"ir",Guard())
    assert service.describe("task-reference")["state"]=="released"
    assert service.command("task-reference","restore-references",reason="恢复原始引用")["state"]=="retained"
    assert IR in restarted.pinned_ir_references()


def test_parent_children_and_active_workers_cannot_release_independently(tmp_path):
    store,service,manifest=setup(tmp_path,payload={"_plan_step_key":"report-parent:0:read"})
    with pytest.raises(LifecycleBlocked,match="所属任务"):
        service.command("task-reference","release-references",reason="错误的子任务操作")
    assert IR in store.pinned_ir_references()
    store2,service2,_=setup(tmp_path/"second")
    store2.update("task-reference",status=PipelineTaskStatus.QUEUED)
    with pytest.raises(LifecycleBlocked,match="尚未停止"):
        service2.command("task-reference","release-references",reason="未停止")


@pytest.mark.parametrize("operation",["report_session","report_session_resume"])
def test_native_report_session_operations_delegate_without_plan_payload(tmp_path,operation):
    store,service,_=setup(tmp_path,operation=operation)
    assert service.describe("task-reference")["blockers"][0]["code"]=="parent_owned_queue_reference"
    with pytest.raises(LifecycleBlocked,match="所属任务"):
        service.command("task-reference","release-references",reason="原生会话必须由父任务管理")
    assert IR in store.pinned_ir_references()


def test_published_reference_or_failed_check_keeps_queue_pin(tmp_path):
    for index,guard in enumerate([Guard([{"code":"published_source_reference","message":"正式来源保留"}]),PublicationReferenceGuard(url="http://127.0.0.1:1",token=None)]):
        store,service,_=setup(tmp_path/str(index),guard=guard)
        with pytest.raises(LifecycleBlocked):service.command("task-reference","release-references",reason="禁止删除来源")
        assert IR in store.pinned_ir_references()


def test_missing_or_changed_ir_prevents_restoring_released_pins(tmp_path):
    store,service,manifest=setup(tmp_path)
    service.command("task-reference","release-references",reason="解除引用")
    original=manifest.read_bytes();manifest.write_text("changed")
    with pytest.raises(LifecycleBlocked,match="先恢复"):
        service.command("task-reference","restore-references",reason="恢复")
    assert IR not in store.pinned_ir_references()
    manifest.write_bytes(original)
    service.command("task-reference","restore-references",reason="恢复原文件")
    assert IR in store.pinned_ir_references()


def test_stale_revision_does_not_release_other_owner_references(tmp_path):
    store,service,_=setup(tmp_path)
    store.pin_ir("another-session",IR)
    service.command("task-reference","release-references",reason="解除自己的引用",expected_revision=0)
    assert IR in store.pinned_ir_references()
    with pytest.raises(LifecycleBlocked,match="状态已变化"):
        service.command("task-reference","restore-references",reason="旧页面恢复",expected_revision=0)
    with store._connect() as db:
        assert db.execute("SELECT 1 FROM ir_input_pins WHERE owner_id='another-session'").fetchone()
