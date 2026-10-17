import json
import pytest

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard
from esg_targeted.contracts import JobStatus
from esg_targeted.storage.lifecycle import ExtractionLifecycle
from tests.test_report_session import make_service, request


class Guard:
    def __init__(self,blockers=None):self.blockers=blockers or [];self.states=[]
    def check(self,scope):return {"verified":True,"blockers":self.blockers,"dependencies":[]}
    def reserve(self,scope,reason):
        if self.blockers:raise LifecycleBlocked(self.blockers)
        self.states.append("reserved");return "fixture-reservation"
    def update_reservation(self,identity,state,reason):self.states.append(state)


def setup(tmp_path,guard=None):
    service=make_service(tmp_path)
    lifecycle=ExtractionLifecycle(service.jobs,service.artifacts,service,guard=guard or Guard())
    session=service.create(request())
    service.cancel(session["session_id"])
    return service,lifecycle,session


def test_parent_archive_is_atomic_reversible_and_keeps_pins_and_artifacts(tmp_path):
    service,lifecycle,created=setup(tmp_path)
    result=lifecycle.session(created["session_id"],"archive",reason="整理旧会话",expected_revision=0)
    assert result["state"]=="archived"
    assert not service.list() and not service.jobs.list()
    assert service.list(include_archived=True)[0]["session_id"]==created["session_id"]
    assert len(service.jobs.list(include_archived=True))==3
    assert (tmp_path/"ir/.pins"/(created["session_id"]+".json")).exists()
    assert service.artifacts.report_session_path(created["session_id"]).exists()
    with pytest.raises(ValueError,match="恢复"):service.resume(created["session_id"])
    lifecycle.session(created["session_id"],"restore",reason="继续使用",expected_revision=1)
    assert len(service.list())==1 and len(service.jobs.list())==3


def test_projection_cannot_be_archived_or_discarded_independently(tmp_path):
    service,lifecycle,created=setup(tmp_path)
    description=lifecycle.describe_job(created["job_ids"][0])
    assert description["available_actions"][0]["action"]=="open_parent"
    for action in ("archive","discard","restore"):
        with pytest.raises(LifecycleBlocked,match="子成果"):
            lifecycle.job(created["job_ids"][0],action,reason="错误的独立操作")


def test_discarded_session_and_owned_results_can_be_restored_after_restart(tmp_path):
    service,lifecycle,created=setup(tmp_path)
    originals={job_id:(service.artifacts._job_path(job_id)/"input/request.json").read_bytes() for job_id in created["job_ids"]}
    discarded=lifecycle.session(created["session_id"],"discard",reason="丢弃未入库测试")
    assert discarded["state"]=="discarded"
    assert all(not service.artifacts._job_path(job_id).exists() for job_id in created["job_ids"])
    assert service.detail(created["session_id"])["artifacts_available"] is False
    assert all(not service.artifacts._job_path(job_id).exists() for job_id in created["job_ids"])
    restarted=ExtractionLifecycle(service.jobs,service.artifacts,service,guard=Guard())
    restarted.session(created["session_id"],"restore",reason="恢复误丢弃成果")
    assert len(service.jobs.list())==3
    assert all((service.artifacts._job_path(job_id)/"input/request.json").read_bytes()==originals[job_id] for job_id in created["job_ids"])


def test_candidate_or_published_dependencies_block_discard_but_allow_archive(tmp_path):
    guard=Guard([{"code":"published_source_reference","message":"已发布来源，不能清理"}])
    service,lifecycle,created=setup(tmp_path,guard)
    with pytest.raises(LifecycleBlocked,match="已发布"):
        lifecycle.session(created["session_id"],"discard",reason="不允许删除来源")
    assert service.artifacts.report_session_path(created["session_id"]).exists()
    lifecycle.session(created["session_id"],"archive",reason="只隐藏记录")
    assert service.list(include_archived=True)


def test_missing_guard_blocks_discard_and_stale_revision_blocks_archive(tmp_path):
    guard=PublicationReferenceGuard(token=None,url="http://127.0.0.1:1")
    service,lifecycle,created=setup(tmp_path,guard)
    with pytest.raises(LifecycleBlocked):lifecycle.session(created["session_id"],"discard",reason="来源检查不可用")
    lifecycle.session(created["session_id"],"archive",reason="归档")
    with pytest.raises(LifecycleBlocked,match="变化"):
        lifecycle.session(created["session_id"],"restore",reason="恢复",expected_revision=0)


def test_running_master_blocks_archive_until_cancelled(tmp_path):
    service=make_service(tmp_path);created=service.create(request())
    lifecycle=ExtractionLifecycle(service.jobs,service.artifacts,service,guard=Guard())
    with pytest.raises(LifecycleBlocked):lifecycle.session(created["session_id"],"archive",reason="尚未取消")
    service.cancel(created["session_id"])
    assert lifecycle.session(created["session_id"],"archive",reason="已取消")["state"]=="archived"


def test_crash_between_quarantine_and_ledger_update_recovers_parent_and_children(tmp_path):
    service,lifecycle,created=setup(tmp_path)
    paths=[service.artifacts._job_path(job_id) for job_id in created["job_ids"]]
    paths.append(service.artifacts.report_session_path(created["session_id"]))
    receipt=lifecycle.trash.quarantine(paths,scope={"job_ids":created["job_ids"]},reason="模拟文件移动后进程中断",
        metadata={"kind":"report_session","identity":created["session_id"],"job_ids":created["job_ids"]})
    assert service._state(created["session_id"]).get("lifecycle") is None
    restarted=ExtractionLifecycle(service.jobs,service.artifacts,service,guard=Guard())
    assert restarted.describe_session(created["session_id"])["state"]=="discarded"
    assert not service.jobs.list()
    restarted.restore_receipt(receipt["cleanup_id"],reason="恢复中断的回收操作")
    assert restarted.describe_session(created["session_id"])["state"]=="active"
    assert len(service.jobs.list())==3
