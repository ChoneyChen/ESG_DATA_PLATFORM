"""Parent-owned, reversible lifecycle of extraction records and artifacts."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard, blocker, require_reason
from esg_runtime.trash import RecoverableTrash


class ExtractionLifecycle:
    def __init__(self, jobs, artifacts, sessions, *, guard=None, retention_seconds=7 * 86400):
        self.jobs, self.artifacts, self.sessions = jobs, artifacts, sessions
        self.guard = guard or PublicationReferenceGuard()
        self._lock = threading.RLock()
        self.trash = RecoverableTrash(artifacts.output_root / ".lifecycle", [artifacts.output_root, *sessions.settings.ir_roots],
            guard=self.guard, retention_seconds=retention_seconds)
        self._recover_ledgers()

    def _recover_ledgers(self):
        """Complete visibility metadata after a crash across the file/SQL boundary.

        This never removes or moves a file. The journal is the durable authority
        for an already completed move; operators can restore partial moves.
        """
        for receipt in reversed(self.trash.list()):
            if receipt["state"] not in {"quarantined","moving","restored","restoring"}:
                continue
            metadata=receipt.get("metadata",{})
            if metadata.get("kind") not in {"job","report_session"}:
                continue
            identity=metadata["identity"]
            try:
                master=self.sessions._state(identity) if metadata["kind"]=="report_session" else None
                jobs=[self.jobs.get(job_id) for job_id in metadata["job_ids"]]
                current=master.get("lifecycle",{"state":"active"}) if master else self._job_state(identity)
            except KeyError:
                continue
            if self._idle_blockers(master,jobs):
                continue
            matching=current.get("cleanup_id")==receipt["cleanup_id"]
            if receipt["state"] in {"quarantined","moving"}:
                if matching or (current.get("cleanup_id") and not matching):
                    continue
                target="discarded";cleanup_id=receipt["cleanup_id"]
            elif matching and current["state"]=="discarded":
                target="active";cleanup_id=None
            else:
                continue
            reason="根据回收日志恢复中断的生命周期记录。"
            with self.jobs._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._write_jobs(db,jobs,target,reason,cleanup_id)
                if master:
                    master["lifecycle"]={"state":target,"cleanup_id":cleanup_id,"reason":reason,
                        "revision":current.get("revision",0)+1,"updated_at":self._time()}
                    master.update(revision=master["revision"]+1,updated_at=self._time())
                    db.execute("UPDATE report_sessions SET state_json=? WHERE session_id=?",(json.dumps(master,ensure_ascii=False),identity))

    @staticmethod
    def _time():
        return datetime.now(timezone.utc).isoformat()

    def _job_state(self, job_id, connection=None):
        if connection:
            row = connection.execute("SELECT * FROM job_lifecycle WHERE job_id=?", (job_id,)).fetchone()
        else:
            with self.jobs._connect() as conn:
                row = conn.execute("SELECT * FROM job_lifecycle WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else {"job_id":job_id,"state":"active","revision":0,"cleanup_id":None}

    def _scope(self, jobs):
        return {"job_ids":[job.job_id for job in jobs], "ir_run_ids":sorted({job.request.ir_run_id for job in jobs})}

    def _effective_state(self,state):
        if state.get("cleanup_id"):
            try:
                receipt=self.trash.get(state["cleanup_id"])
                if receipt["state"]=="purged":
                    return {**state,"state":"purged","artifacts_available":False}
            except (OSError,ValueError):
                return {**state,"recovery_check_unavailable":True}
        return state

    def _idle_blockers(self, state, jobs):
        blockers = []
        if state and state["execution_status"] in {"queued", "running"}:
            blockers.append(blocker("session_active", "报告会话尚未停止；先取消任务并等待执行退出。"))
        if state and self.jobs._worker_alive(str(state.get("owner_pid") or ""), state.get("owner_started")):
            blockers.append(blocker("worker_active", "报告会话仍有活动执行进程。"))
        active = [job.job_id for job in jobs if job.status.value in {"queued","running"} or self.jobs.has_live_worker(job.job_id)]
        if active:
            blockers.append(blocker("projection_active", "相关提取任务尚未停止。", job_ids=active))
        return blockers

    def describe_job(self, job_id, *, check_references=False):
        job = self.jobs.get(job_id)
        state = self._effective_state(self._job_state(job_id))
        parent = job.request.report_session_id
        blockers = self._idle_blockers(None,[job])
        actions = []
        if parent:
            blockers.append(blocker("parent_owned_projection", "此成果属于报告会话；请在所属会话统一归档或丢弃。", session_id=parent))
            actions.append({"action":"open_parent","label":"打开所属报告会话","method":"GET",
                "endpoint":f"/api/report-sessions/{parent}","scope":{"session_id":parent},"calls_model":False,"incurs_cost":False})
        elif not blockers and state["state"]!="purged":
            actions.append(self._action("restore" if state["state"] != "active" else "archive", job_id, "job"))
            if state["state"] != "discarded":
                actions.append(self._action("discard",job_id,"job",requires_reason=True))
        references = self.guard.check(self._scope([job])) if check_references and not parent else None
        if references:
            blockers += references.get("blockers",[])
            if references.get("blockers"):
                actions = [action for action in actions if action["action"] != "discard"]
        for action in actions:
            if action["action"] != "open_parent":
                action["payload"]={"expected_revision":state["revision"]}
        return {"schema_version":"lifecycle-v1",**state,"available_actions":actions,"blockers":blockers,
            "dependencies":references.get("dependencies",[]) if references else [],"reference_check":references}

    @staticmethod
    def _action(action, identity, kind, **extra):
        labels={"archive":"归档记录","restore":"恢复记录","discard":"检查并丢弃未入库成果（进入回收区）"}
        prefix="jobs" if kind=="job" else "report-sessions"
        return {"action":action,"label":labels[action],"method":"POST","endpoint":f"/api/{prefix}/{identity}/{action}",
            "calls_model":False,"incurs_cost":False,"requires_reason":True,
            "requires_reference_check":action=="discard","preflight_endpoint":f"/api/{prefix}/{identity}/lifecycle",
            "requires_confirmation":action=="discard", "confirmation_message":"将未入库成果移入回收区，保留7天，期间可以恢复。",**extra}

    def describe_session(self, session_id, *, check_references=False):
        state = self.sessions._state(session_id)
        lifecycle = self._effective_state(state.get("lifecycle",{"state":"active","revision":0}))
        jobs = [self.jobs.get(p["job_id"]) for p in state["projections"]]
        blockers = self._idle_blockers(state,jobs)
        actions = []
        if not blockers and lifecycle["state"]!="purged":
            actions.append(self._action("restore" if lifecycle["state"] != "active" else "archive",session_id,"session"))
            if lifecycle["state"] != "discarded":
                actions.append(self._action("discard",session_id,"session"))
        references = self.guard.check(self._scope(jobs)) if check_references else None
        if references:
            blockers += references.get("blockers",[])
            if references.get("blockers"):
                actions=[a for a in actions if a["action"]!="discard"]
        for action in actions:
            action["payload"]={"expected_revision":lifecycle.get("revision",0)}
        return {"schema_version":"lifecycle-v1",**lifecycle,"available_actions":actions,"blockers":blockers,
            "dependencies":references.get("dependencies",[]) if references else [],"reference_check":references,
            "owned_job_ids":[job.job_id for job in jobs]}

    def _write_jobs(self, conn, jobs, state, reason, cleanup_id=None):
        for job in jobs:
            previous=self._job_state(job.job_id,conn)
            conn.execute("INSERT INTO job_lifecycle(job_id,state,reason,revision,cleanup_id,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,revision=excluded.revision,cleanup_id=excluded.cleanup_id,updated_at=excluded.updated_at",
                (job.job_id,state,reason,previous["revision"]+1,cleanup_id,self._time()))
            conn.execute("INSERT INTO events(job_id,created_at,stage,level,message,detail_json) VALUES(?,?,?,?,?,?)",
                (job.job_id,self._time(),"lifecycle","info",{"active":"记录已恢复","archived":"记录已归档，来源引用保留","discarded":"未入库成果已进入回收区"}[state],json.dumps({"reason":reason,"cleanup_id":cleanup_id})))

    def _operate(self, identity, action, reason, expected_revision, *, is_session):
        reason=require_reason(reason)
        with self._lock, self.jobs._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            master=self.sessions._state(identity) if is_session else None
            jobs=[self.jobs.get(p["job_id"]) for p in master["projections"]] if master else [self.jobs.get(identity)]
            if not master and jobs[0].request.report_session_id:
                raise LifecycleBlocked([blocker("parent_owned_projection","子成果只能由所属报告会话统一管理。",session_id=jobs[0].request.report_session_id)])
            blockers=self._idle_blockers(master,jobs)
            if blockers:
                raise LifecycleBlocked(blockers)
            lifecycle=master.get("lifecycle",{"state":"active","revision":0}) if master else self._job_state(identity,conn)
            if expected_revision is not None and expected_revision != lifecycle["revision"]:
                raise LifecycleBlocked([blocker("revision_conflict","记录已变化；请刷新后重新操作。")])
            state=lifecycle["state"]
            if action == "restore" and master:
                from esg_targeted.storage.intermediate_cleanup import assert_intermediates_restored
                assert_intermediates_restored(self.artifacts.output_root, identity)
            if action=="archive" and state=="discarded":
                raise ValueError("丢弃的成果应先恢复。")
            target={"archive":"archived","restore":"active","discard":"discarded"}[action]
            cleanup_id=lifecycle.get("cleanup_id")
            receipt=None
            if action=="discard" and state != "discarded":
                paths=[self.artifacts._job_path(job.job_id) for job in jobs]
                if master:
                    paths.append(self.artifacts.report_session_path(identity))
                    for root in self.sessions.settings.ir_roots:
                        for path in (root / ".pins").glob(identity+"*.json"):
                            pin=json.loads(path.read_text(encoding="utf-8"))
                            if pin.get("owner")==identity:
                                paths.append(path)
                receipt=self.trash.quarantine(paths,scope=self._scope(jobs),reason=reason,
                    metadata={"kind":"report_session" if master else "job","identity":identity,"job_ids":[job.job_id for job in jobs]})
                cleanup_id=receipt["cleanup_id"]
            if action=="restore" and state=="discarded":
                receipt=self.trash.restore(cleanup_id,reason=reason)
                cleanup_id=None
            try:
                self._write_jobs(conn,jobs,target,reason,cleanup_id)
                if master:
                    master["lifecycle"]={"state":target,"reason":reason,"revision":lifecycle["revision"]+1,
                        "cleanup_id":cleanup_id,"updated_at":self._time()}
                    master.update(revision=master["revision"]+1,updated_at=self._time())
                    conn.execute("UPDATE report_sessions SET state_json=? WHERE session_id=?",(json.dumps(master,ensure_ascii=False),identity))
                conn.commit()
            except Exception:
                if action=="discard" and receipt:
                    self.trash.restore(receipt["cleanup_id"],reason="数据库更新失败，恢复成果。")
                raise
        return self.describe_session(identity) if is_session else self.describe_job(identity)

    def session(self, identity, action, *, reason, expected_revision=None):
        return self._operate(identity,action,reason,expected_revision,is_session=True)

    def job(self, identity, action, *, reason, expected_revision=None):
        return self._operate(identity,action,reason,expected_revision,is_session=False)

    def restore_receipt(self,cleanup_id,*,reason):
        receipt=self.trash.get(cleanup_id)
        metadata=receipt["metadata"]
        identity=metadata["identity"]
        is_session=metadata["kind"]=="report_session"
        current=self.describe_session(identity) if is_session else self.describe_job(identity)
        if current["state"]=="discarded":
            self._operate(identity,"restore",reason,None,is_session=is_session)
        else:
            # A process may stop between filesystem quarantine and ledger
            # commit. Recover that journal even when the ledger stayed active.
            state=self.sessions._state(identity) if is_session else None
            jobs=[self.jobs.get(p["job_id"]) for p in state["projections"]] if state else [self.jobs.get(identity)]
            blockers=self._idle_blockers(state,jobs)
            if blockers:raise LifecycleBlocked(blockers)
            self.trash.restore(cleanup_id,reason=reason)
        return self.trash.get(cleanup_id)
