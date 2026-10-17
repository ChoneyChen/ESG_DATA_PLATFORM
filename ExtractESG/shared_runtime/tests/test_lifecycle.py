from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard
from esg_runtime.trash import RecoverableTrash


class Guard:
    def __init__(self):
        self.blockers=[]
        self.states=[]
    def check(self,scope):
        return {"verified":True,"blockers":self.blockers,"dependencies":[]}
    def reserve(self,scope,reason):
        if self.blockers:
            raise LifecycleBlocked(self.blockers)
        self.states.append("reserved")
        return "reservation-fixture"
    def update_reservation(self,identity,state,reason):
        self.states.append(state)


def test_reference_guard_unavailable_fails_closed_without_exposing_credential():
    guard=PublicationReferenceGuard(token=None,url="http://127.0.0.1:1")
    result=guard.check({"job_ids":["fixture"]})
    assert result["verified"] is False
    assert result["blockers"][0]["code"] == "reference_check_unavailable"
    with pytest.raises(LifecycleBlocked):
        guard.reserve({"job_ids":["fixture"]},"整理未入库记录")


def test_guard_reads_restricted_local_token_without_returning_it(tmp_path,monkeypatch):
    token=tmp_path/"review-token";token.write_text("fixture-private-token");token.chmod(0o600)
    monkeypatch.delenv("PLATESG_LIFECYCLE_TOKEN",raising=False);monkeypatch.delenv("PLATESG_REVIEW_TOKEN",raising=False)
    monkeypatch.setenv("PLATESG_LIFECYCLE_TOKEN_FILE",str(token))
    import io
    import esg_runtime.lifecycle as module
    captured=[]
    def response(request,timeout):
        captured.append(request.get_header("Authorization"))
        return io.BytesIO(json.dumps({"schema_version":"lifecycle-references-v1","verified":True,"blockers":[],"dependencies":[]}).encode())
    monkeypatch.setattr(module,"urlopen",response)
    result=PublicationReferenceGuard().check({"job_ids":["fixture"]})
    assert result["verified"] is True and captured==["Bearer fixture-private-token"]
    assert "fixture-private-token" not in str(result)
    token.chmod(0o644)
    assert PublicationReferenceGuard().check({"job_ids":["fixture"]})["verified"] is False


def test_guard_preserves_authoritative_unknown_store_diagnostics():
    response={"schema_version":"lifecycle-references-v1","verified":False,"dependencies":[],
        "blockers":[{"code":"additional_source_store_unverified","message":"其他来源目录未核查"}],
        "store_coverage":{"unverified_store_roots":["fixture://older-store"]}}
    guard=PublicationReferenceGuard(transport=lambda operation,payload:response)
    assert guard.check({"job_ids":["fixture"]})==response
    with pytest.raises(LifecycleBlocked,match="未核查"):
        guard.reserve({"job_ids":["fixture"]},"尝试清理")


def test_lost_reservation_response_leaves_recoverable_identity_before_any_file_move(tmp_path):
    from urllib.error import URLError
    owned=tmp_path/"owned";owned.mkdir();source=owned/"report.pdf";source.write_bytes(b"original")
    requests=[]
    def transport(operation,payload):
        requests.append((operation,payload))
        if operation=="reserve":
            # Simulate a committed reservation whose HTTP reply never arrived.
            assert trash.list()[0]["reservation_id"]==payload["reservation_id"]
            raise URLError("fixture lost response")
        return {"reservation_id":operation.split("/")[-1],"state":payload["state"]}
    guard=PublicationReferenceGuard(transport=transport)
    trash=RecoverableTrash(tmp_path/"trash",[owned],guard=guard)
    with pytest.raises(LifecycleBlocked):
        trash.quarantine([source],scope={"job_ids":["fixture"]},reason="尝试回收")
    receipt=trash.list()[0]
    assert receipt["state"]=="reservation_pending" and source.read_bytes()==b"original"
    restarted=RecoverableTrash(tmp_path/"trash",[owned],guard=guard)
    assert restarted.restore(receipt["cleanup_id"],reason="释放未确认的来源保留")["state"]=="restored"
    assert requests[-1][0]=="reservations/"+receipt["reservation_id"]
    assert requests[-1][1]["state"]=="released"
    assert source.read_bytes()==b"original"


def test_pending_reservation_release_failure_keeps_recovery_state(tmp_path):
    from urllib.error import URLError
    owned=tmp_path/"owned";owned.mkdir();source=owned/"report.pdf";source.write_bytes(b"original")
    guard=PublicationReferenceGuard(transport=lambda operation,payload:(_ for _ in ()).throw(URLError("offline")))
    trash=RecoverableTrash(tmp_path/"trash",[owned],guard=guard)
    with pytest.raises(LifecycleBlocked):trash.quarantine([source],scope={"job_ids":["fixture"]},reason="尝试回收")
    receipt=trash.list()[0]
    with pytest.raises(URLError):trash.restore(receipt["cleanup_id"],reason="暂时无法释放")
    assert trash.get(receipt["cleanup_id"])["state"]=="reservation_pending"
    with pytest.raises(ValueError):trash.purge(receipt["cleanup_id"],reason="不能清除未确认的回收")
    assert source.exists()


def test_restart_restore_is_content_checked_and_does_not_overwrite_new_file(tmp_path):
    owned=tmp_path/"owned";owned.mkdir();source=owned/"report.pdf";source.write_bytes(b"original")
    guard=Guard();trash=RecoverableTrash(tmp_path/"trash",[owned],guard=guard)
    receipt=trash.quarantine([source],scope={"source_sha256":"a"*64},reason="移入回收区")
    assert not source.exists()
    assert guard.states==["reserved","quarantined"]
    restarted=RecoverableTrash(tmp_path/"trash",[owned],guard=guard)
    source.write_bytes(b"new-report")
    with pytest.raises(LifecycleBlocked,match="原路径"):
        restarted.restore(receipt["cleanup_id"],reason="恢复报告")
    assert source.read_bytes()==b"new-report"
    source.unlink()
    restored=restarted.restore(receipt["cleanup_id"],reason="恢复报告")
    assert restored["state"]=="restored"
    assert source.read_bytes()==b"original"
    assert guard.states[-1]=="released"


def test_explicit_purge_requires_grace_and_fresh_reference_check(tmp_path):
    owned=tmp_path/"owned";owned.mkdir();source=owned/"result";source.mkdir();(source/"a").write_text("evidence")
    clock=[datetime(2026,10,1,tzinfo=timezone.utc)];guard=Guard()
    trash=RecoverableTrash(tmp_path/"trash",[owned],guard=guard,clock=lambda:clock[0])
    receipt=trash.quarantine([source],scope={"job_ids":["job-fixture"]},reason="清理测试成果")
    with pytest.raises(LifecycleBlocked,match="保留期"):
        trash.purge(receipt["cleanup_id"],reason="回收期结束")
    clock[0]+=timedelta(days=8)
    guard.blockers=[{"code":"published_source_reference","message":"已发布来源"}]
    with pytest.raises(LifecycleBlocked,match="已发布"):
        trash.purge(receipt["cleanup_id"],reason="清理")
    guard.blockers=[]
    purged=trash.purge(receipt["cleanup_id"],reason="保留期结束且无引用")
    assert purged["state"]=="purged"
    assert guard.states[-1]=="purged"
    assert trash._receipt_path(receipt["cleanup_id"]).exists()
    with pytest.raises(ValueError):trash.restore(receipt["cleanup_id"],reason="恢复")


def test_move_failure_rolls_back_every_path_and_releases_guard(tmp_path,monkeypatch):
    owned=tmp_path/"owned";owned.mkdir()
    first=owned/"first";second=owned/"second";first.write_text("1");second.write_text("2")
    import esg_runtime.trash as module
    original=module.shutil.move
    calls=[0]
    def injected(src,dst):
        calls[0]+=1
        if calls[0]==2:raise OSError("fixture move failure")
        return original(src,dst)
    monkeypatch.setattr(module.shutil,"move",injected)
    guard=Guard();trash=RecoverableTrash(tmp_path/"trash",[owned],guard=guard)
    with pytest.raises(OSError):trash.quarantine([first,second],scope={"job_ids":["fixture"]},reason="测试回滚")
    assert first.read_text()=="1" and second.read_text()=="2"
    assert trash.list()[0]["state"]=="rolled_back"
    assert guard.states[-1]=="released"


def test_symlink_nested_path_and_tampered_receipt_cannot_escape_owned_root(tmp_path):
    owned=tmp_path/"owned";owned.mkdir();outside=tmp_path/"outside";outside.write_text("secret")
    link=owned/"link";link.symlink_to(outside)
    trash=RecoverableTrash(tmp_path/"trash",[owned],guard=Guard())
    with pytest.raises(ValueError,match="符号链接"):
        trash.quarantine([link],scope={"job_ids":["fixture"]},reason="错误路径")
    with pytest.raises(ValueError):trash.quarantine([owned],scope={"job_ids":["fixture"]},reason="错误根目录")
    source=owned/"valid";source.write_text("evidence")
    receipt=trash.quarantine([source],scope={"job_ids":["fixture"]},reason="正确路径")
    receipt["entries"][0]["source"]=str(outside)
    trash._save(receipt)
    with pytest.raises(ValueError):trash.restore(receipt["cleanup_id"],reason="恢复")
    assert outside.read_text()=="secret"
