"""Explicit release/restoration of standalone queue source pins.

Archive never releases provenance. Report-plan and processing children delegate
to their parent; this service handles terminal standalone records only.
"""
import hashlib
import json
from pathlib import Path

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard, blocker, require_reason
from esg_v2.control_plane.contracts import utc_now
from esg_v2.storage.package_layout import require_package_dir_name


class QueueReferenceLifecycle:
    def __init__(self,store,ir_root,guard=None):
        self.store,self.ir_root=store,Path(ir_root)
        self.guard=guard or PublicationReferenceGuard()
        with store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS queue_reference_releases(task_id TEXT PRIMARY KEY REFERENCES pipeline_tasks(task_id),"
                "pins_json TEXT NOT NULL,scope_json TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL)")

    @staticmethod
    def _blockers(row):
        payload=json.loads(row["payload_json"])
        result=[]
        if row["status"] in {"queued","running"}:
            result.append(blocker("queue_task_active","任务尚未停止，不能解除来源引用。"))
        if (payload.get("_plan_step_key") or payload.get("_processing_id") or payload.get("_plan_id")
                or row["operation"] in {"report_session","report_session_resume"}
                or payload.get("report_session_id") or payload.get("parent_session_id")):
            result.append(blocker("parent_owned_queue_reference","此来源属于报告计划或处理任务，请在所属任务统一管理。"))
        return result

    def describe(self,identity,*,check_references=False):
        with self.store._connect() as db:
            row=db.execute("SELECT * FROM pipeline_tasks WHERE task_id=?",(identity,)).fetchone()
            if not row:raise KeyError(identity)
            released=db.execute("SELECT * FROM queue_reference_releases WHERE task_id=?",(identity,)).fetchone()
            pins=[dict(r) for r in db.execute("SELECT * FROM ir_input_pins WHERE owner_id=?",(identity,))]
        blockers=self._blockers(row)
        if self.store._row_worker_alive(row):blockers.append(blocker("worker_active","执行进程尚未退出。"))
        state="released" if released and released["state"]=="released" else "retained"
        actions=[]
        scope=json.loads(released["scope_json"]) if released else {"job_ids":[row["native_job_id"]] if row["native_job_id"] else [],"ir_run_ids":[r["ir_run_id"] for r in pins]}
        references=self.guard.check(scope) if check_references and scope["ir_run_ids"] else None
        if references:blockers.extend(references.get("blockers",[]))
        if not blockers and (pins or state=="released"):
            action="restore-references" if state=="released" else "release-references"
            actions=[{"action":action,"label":"恢复来源引用" if state=="released" else "检查并解除来源引用",
                "endpoint":f"/api/pipeline/tasks/{identity}/{action}","method":"POST","requires_reason":True,
                "payload":{"expected_revision":released["revision"] if released else 0},"calls_model":False,"incurs_cost":False,
                "requires_reference_check":action=="release-references","preflight_endpoint":f"/api/pipeline/tasks/{identity}/references"}]
        return {"schema_version":"queue-reference-lifecycle-v1","state":state,"revision":released["revision"] if released else 0,
            "available_actions":actions,"blockers":blockers,"pins":pins,
            "dependencies":references.get("dependencies",[]) if references else [],"reference_scope":scope}

    def command(self,identity,action,*,reason,expected_revision=None):
        reason=require_reason(reason)
        with self.store._lock,self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM pipeline_tasks WHERE task_id=?",(identity,)).fetchone()
            if not row:raise KeyError(identity)
            blockers=self._blockers(row)
            if self.store._row_worker_alive(row):blockers.append(blocker("worker_active","执行进程尚未退出。"))
            if blockers:raise LifecycleBlocked(blockers)
            previous=db.execute("SELECT * FROM queue_reference_releases WHERE task_id=?",(identity,)).fetchone()
            revision=previous["revision"] if previous else 0
            if expected_revision is not None and expected_revision!=revision:
                raise LifecycleBlocked([blocker("revision_conflict","引用状态已变化；请刷新后操作。")])
            if action=="release-references":
                if previous and previous["state"]=="released":return self.describe(identity)
                pins=[dict(r) for r in db.execute("SELECT * FROM ir_input_pins WHERE owner_id=?",(identity,))]
                if not pins:raise ValueError("此记录没有可解除的证据引用。")
                snapshots=[]
                for pin in pins:
                    require_package_dir_name(pin["ir_run_id"])
                    manifest=self.ir_root/pin["ir_run_id"]/"manifest.json"
                    if not manifest.is_file():raise LifecycleBlocked([blocker("source_identity_unproven","证据来源不存在，无法确认解除范围。",ir_run_id=pin["ir_run_id"])])
                    snapshots.append({**pin,"manifest_sha256":hashlib.sha256(manifest.read_bytes()).hexdigest()})
                scope={"job_ids":[row["native_job_id"]] if row["native_job_id"] else [],"ir_run_ids":[p["ir_run_id"] for p in pins]}
                references=self.guard.check(scope)
                if not references.get("verified") or references.get("blockers"):
                    raise LifecycleBlocked(references.get("blockers") or [blocker("reference_check_unavailable","来源引用未确认。")])
                db.execute("DELETE FROM ir_input_pins WHERE owner_id=?",(identity,))
                state="released"
            elif action=="restore-references":
                if not previous or previous["state"]!="released":raise ValueError("此记录没有已解除的引用。")
                snapshots=json.loads(previous["pins_json"]);scope=json.loads(previous["scope_json"])
                for pin in snapshots:
                    require_package_dir_name(pin["ir_run_id"])
                    manifest=self.ir_root/pin["ir_run_id"]/"manifest.json"
                    if not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest()!=pin["manifest_sha256"]:
                        raise LifecycleBlocked([blocker("source_restore_required","先恢复原始证据版本，再恢复引用。",ir_run_id=pin["ir_run_id"])])
                for pin in snapshots:
                    db.execute("INSERT OR IGNORE INTO ir_input_pins VALUES(?,?,?)",(pin["owner_id"],pin["ir_run_id"],pin["created_at"]))
                state="retained"
            else:raise ValueError("未知引用操作。")
            db.execute("INSERT INTO queue_reference_releases VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "pins_json=excluded.pins_json,scope_json=excluded.scope_json,state=excluded.state,reason=excluded.reason,revision=excluded.revision,updated_at=excluded.updated_at",
                (identity,json.dumps(snapshots),json.dumps(scope),state,reason,revision+1,utc_now()))
            db.execute("INSERT INTO pipeline_events(task_id,created_at,stage,level,message,detail_json) VALUES(?,?,?,?,?,?)",
                (identity,utc_now(),"lifecycle","info","来源引用已解除" if state=="released" else "来源引用已恢复",json.dumps({"scope":scope,"reason":reason})))
        return self.describe(identity)
