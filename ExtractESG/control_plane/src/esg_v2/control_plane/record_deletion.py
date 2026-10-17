"""Delete execution records; source artifacts have independent owners.

An active deletion request first cancels execution, then waits for real worker
exit. The persisted request is intent, not an archived task or retained copy.
"""
from __future__ import annotations
import hashlib
import json
import os
import threading
from contextlib import ExitStack
from pathlib import Path

from esg_runtime.lifecycle import LifecycleBlocked, blocker
from esg_runtime.production_references import production_references
from esg_v2.control_plane.contracts import utc_now
from esg_v2.control_plane.task_references import owner_steps, task_id, task_reference_ids, history_values


def fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=True, default=str).encode()).hexdigest()


def delete_action(kind, identity, revision, state):
    prefix = {"processing": "report-processing/runs", "session": "report-sessions", "queue": "pipeline/tasks"}[kind]
    return {"action": "delete", "label": "删除", "method": "DELETE", "endpoint": f"/api/{prefix}/{identity}",
        "payload": {"expected_revision": revision, "identity_sha256": fingerprint(state)},
        "requires_confirmation": True, "requires_reason": False, "calls_model": False, "incurs_cost": False}


def _detach_queue_references(db, task_ids):
    """Prune every owner; a deleted unfinished stage cannot silently dispatch."""
    task_ids={identity for identity in task_ids if isinstance(identity,str) and identity}
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for kind,table,key in (("processing","report_processing_runs","processing_id"),("session","pipeline_plans","plan_id")):
        if table not in tables:continue
        for row in db.execute(f"SELECT {key},state_json FROM {table}").fetchall():
            state=json.loads(row[1]);changed=False;interrupted=False
            for _,step,document in owner_steps(state,kind):
                current=task_id(step.get('task_id'))
                if current in task_ids:
                    native=db.execute('SELECT status,native_job_id,outcome_json FROM pipeline_tasks WHERE task_id=?',(current,)).fetchone()
                    completed=bool(native and native['status'] in {'completed','partial'} and isinstance(native['native_job_id'],str) and native['native_job_id'])
                    if completed:
                        step.update(status=native['status'],run_id=native['native_job_id'])
                        if native['outcome_json']:step['outcome']=json.loads(native['outcome_json'])
                    elif native is None:
                        completed=step.get('status') in {'completed','partial'} and isinstance(step.get('run_id'),str) and bool(step['run_id'])
                    step.pop('task_id',None);step['queue_record_deleted']=True;changed=True
                    if not completed:
                        step.update(status='interrupted',requires_new_task=True)
                        document.update(status='interrupted',error='执行记录已删除，请从报告资产新建任务。');interrupted=True
                old=step.get('history')
                if isinstance(old,list):
                    filtered=[entry for entry in old if task_id(entry) not in task_ids]
                    if filtered!=old:step['history']=filtered;changed=True
            if changed:
                state['updated_at']=utc_now()
                if interrupted and kind=='session' and all(d.get('status') in {'completed','partial','failed','interrupted','cancelled','waiting_review'} for d in state.get('documents',[]) if isinstance(d,dict)):
                    state['status']='interrupted'
                if kind=='processing':
                    state['revision']=state.get('revision',0)+1
                    db.execute(f"UPDATE {table} SET status=?,state_json=? WHERE {key}=?",(state.get('status','interrupted'),json.dumps(state,ensure_ascii=False),row[0]))
                else:db.execute(f"UPDATE {table} SET state_json=? WHERE {key}=?",(json.dumps(state,ensure_ascii=False),row[0]))


def _detach_automation_records(db, kind, identity, *, deleted=False):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_automations'").fetchone(): return
    field = "processing_id" if kind == "processing" else "extraction_plan_id"
    for row in db.execute("SELECT automation_id,revision,state_json FROM production_automations").fetchall():
        state = json.loads(row[2]); changed = False
        for item in state.get("items", []):
            if item.get(field) != identity: continue
            if item.get("status") not in {"completed", "partial", "cancelled", "failed"}: item["status"] = "cancelled"
            item["record_deleted"] = True
            item["message"] = "关联执行记录已删除。" if deleted else "正在删除关联执行记录，自动计划已跳过此报告。"
            if deleted: item[field] = None
            changed = True
        if changed:
            state.update(revision=row[1] + 1, updated_at=utc_now())
            db.execute("UPDATE production_automations SET revision=?,state_json=? WHERE automation_id=?", (state["revision"], json.dumps(state, ensure_ascii=False), row[0]))


def detach_terminal_library_records(db, *, asset_id=None, source_sha256=None, run_ids=(), native_request=None, authorization=None):
    """Detach terminal local owners before deleting a library file.

    Caller holds BEGIN IMMEDIATE and source publication guard. Native extraction
    is deleted through its owner API first; an unconfirmed remote deletion never
    authorizes physical library deletion.
    """
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    run_ids = set(run_ids)
    owners, task_ids, processing_ids, plan_ids = set(), set(), [], []
    def match(state):
        return ((asset_id and state.get("asset_id") == asset_id)
            or (source_sha256 and state.get("source_sha256") == source_sha256)
            or state.get("ocr_run_id") in run_ids or state.get("ir_run_id") in run_ids)
    selected = []
    for kind, table in (("processing", "report_processing_runs"), ("session", "pipeline_plans")):
        if table not in tables: continue
        for row in db.execute(f"SELECT state_json FROM {table}"):
            state = json.loads(row[0])
            documents = state.get("documents", [])
            chosen = [document for document in documents if match(document)] if kind == "session" else []
            if not (match(state) if kind == "processing" else chosen): continue
            if (kind == "processing" and state.get("status") in {"queued", "running", "cancelling"}) or any(document.get("status") not in {"completed", "partial", "failed", "interrupted", "cancelled", "waiting_review"} for document in chosen):
                raise LifecycleBlocked([blocker("active_source_owner", "此报告仍有执行任务，停止后才可删除。")])
            selected.append((kind, state, chosen))
            identity = state["processing_id" if kind == "processing" else "plan_id"]
            if kind == "processing" or len(chosen) == len(documents): owners.add(identity)
            if kind == "processing": task_ids.update(ControlPlaneRecordDeletion._child_ids(kind, state, db))
            else:
                for index, document in enumerate(documents):
                    if document not in chosen: continue
                    frozen_index = document.get("dispatch_document_index", index)
                    for step in document.get("steps", {}).values():
                        if step.get("task_id"): task_ids.add(step["task_id"])
                        task_ids.update(entry.get("task_id") if isinstance(entry, dict) else entry for entry in step.get("history", []))
                    for task in db.execute("SELECT task_id,payload_json FROM pipeline_tasks"):
                        payload = json.loads(task["payload_json"])
                        if str(payload.get("_plan_step_key") or "").startswith(f"{identity}:{frozen_index}:"): task_ids.add(task["task_id"])
    if "pipeline_tasks" in tables:
        for row in db.execute("SELECT * FROM pipeline_tasks"):
            payload = json.loads(row["payload_json"])
            if match(payload) or row["native_job_id"] in run_ids: task_ids.add(row["task_id"])
        from esg_v2.control_plane.store import PipelineQueueStore
        for identity in task_ids:
            row = db.execute("SELECT * FROM pipeline_tasks WHERE task_id=?", (identity,)).fetchone()
            if row and (row["status"] in {"queued", "running"} or PipelineQueueStore._row_worker_alive(row)):
                raise LifecycleBlocked([blocker("active_source_worker", "关联执行进程尚未退出，删除等待中。", task_id=identity)])
    for kind, state, chosen in selected:
        if kind == "session":
            for document in chosen:
                native = document.get("native_session_id")
                if not native: continue
                if not native_request:
                    raise LifecycleBlocked([blocker("native_reference_check_required", "需先删除相关提取任务，再删除库文件。", session_id=native)])
                try:
                    description = native_request("GET", f"/api/report-sessions/{native}/deletion", authorization=authorization)
                    result = native_request("DELETE", f"/api/report-sessions/{native}", description["available_actions"][0]["payload"], authorization=authorization)
                    if result.get("deleted") is not True:
                        raise LifecycleBlocked([blocker("native_worker_active", "关联提取任务尚未停止，删除等待中。")])
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 404: raise
        identity = state["processing_id" if kind == "processing" else "plan_id"]
        if kind == "processing":
            for table in ("report_processing_events", "report_processing_artifacts", "report_processing_runs"):
                if table in tables: db.execute(f"DELETE FROM {table} WHERE processing_id=?", (identity,))
            processing_ids.append(identity)
            _detach_automation_records(db, kind, identity, deleted=True)
        else:
            remaining = []
            for index, document in enumerate(state.get("documents", [])):
                if document in chosen: continue
                document.setdefault("dispatch_document_index", index)
                remaining.append(document)
            if remaining:
                state["documents"] = remaining; state["updated_at"] = utc_now()
                db.execute("UPDATE pipeline_plans SET state_json=? WHERE plan_id=?", (json.dumps(state, ensure_ascii=False), identity))
                chosen_ir = {document.get("ir_run_id") for document in chosen if document.get("ir_run_id")}
                for ir_id in chosen_ir:
                    if not any(document.get("ir_run_id") == ir_id for document in remaining):
                        db.execute("DELETE FROM ir_input_pins WHERE owner_id=? AND ir_run_id=?", (identity, ir_id))
            else:
                if "pipeline_plan_events" in tables: db.execute("DELETE FROM pipeline_plan_events WHERE plan_id=?", (identity,))
                db.execute("DELETE FROM pipeline_plans WHERE plan_id=?", (identity,)); plan_ids.append(identity)
                _detach_automation_records(db, kind, identity, deleted=True)
    _detach_queue_references(db, task_ids)
    owners.update(task_ids)
    for identity in task_ids:
        row = db.execute("SELECT payload_json FROM pipeline_tasks WHERE task_id=?", (identity,)).fetchone()
        if row:
            dispatch = json.loads(row[0]).get("_plan_step_key")
            if dispatch: owners.add(dispatch)
        if "queue_reference_releases" in tables: db.execute("DELETE FROM queue_reference_releases WHERE task_id=?", (identity,))
        if "pipeline_events" in tables: db.execute("DELETE FROM pipeline_events WHERE task_id=?", (identity,))
        db.execute("DELETE FROM pipeline_tasks WHERE task_id=?", (identity,))
    if owners and "ir_input_pins" in tables:
        db.execute("DELETE FROM ir_input_pins WHERE owner_id IN (%s)" % ",".join("?" for _ in owners), tuple(owners))
    return {"processing_ids": processing_ids, "plan_ids": plan_ids, "task_ids": sorted(task_ids)}


class ControlPlaneRecordDeletion:
    def __init__(self, store, processing, plans, native_request, cancel_task, *, automations=None, local_authorization=None):
        self.store, self.processing, self.plans = store, processing, plans
        self.native_request, self.cancel_task = native_request, cancel_task
        self.automations = automations
        self.local_authorization = local_authorization
        self._lock = threading.RLock()
        self._stop = threading.Event(); self._thread = None
        self._authorization = {}
        with store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS record_deletion_requests (kind TEXT NOT NULL, identity TEXT NOT NULL, created_identity TEXT NOT NULL, status TEXT NOT NULL, requested_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT, PRIMARY KEY(kind,identity))")

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="record-deletion-coordinator", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(1):
            self.tick()

    def _owners(self):
        stack = ExitStack()
        if self.automations is not None: stack.enter_context(self.automations._lock)
        stack.enter_context(self.processing._lock); stack.enter_context(self.plans._lock); stack.enter_context(self.store._lock)
        return stack

    def _native_authorization(self, value):
        if value: return value
        if self.local_authorization:
            return self.local_authorization()
        token = os.getenv("PLATESG_LIFECYCLE_TOKEN") or os.getenv("PLATESG_REVIEW_TOKEN")
        if not token:
            default = Path(__file__).resolve().parents[5] / "PlatESG/.local/review-token"
            path = Path(os.getenv("PLATESG_LIFECYCLE_TOKEN_FILE", str(default)))
            if path.is_file() and not path.is_symlink() and not path.stat().st_mode & 0o077:
                token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise LifecycleBlocked([blocker("local_operator_unavailable", "已停止的提取任务等待本机操作权限恢复后完成删除。")])
        return "Bearer " + token

    def _state(self, kind, identity, db):
        if kind == "queue":
            row = db.execute("SELECT * FROM pipeline_tasks WHERE task_id=?", (identity,)).fetchone()
            if not row: raise KeyError(identity)
            return dict(row), row["revision"]
        table, key = ("report_processing_runs", "processing_id") if kind == "processing" else ("pipeline_plans", "plan_id")
        row = db.execute(f"SELECT state_json FROM {table} WHERE {key}=?", (identity,)).fetchone()
        if not row: raise KeyError(identity)
        state = json.loads(row[0])
        return state, state.get("revision", state.get("lifecycle", {}).get("revision", 0))

    def action(self, kind, state):
        identity = state[{"processing": "processing_id", "session": "plan_id", "queue": "task_id"}[kind]]
        revision = state.get("revision", state.get("lifecycle", {}).get("revision", 0))
        return delete_action(kind, identity, revision, state)

    @staticmethod
    def _child_ids(kind, state, db):
        if kind == "queue": return {state["task_id"]}
        ids = set(task_reference_ids(state, 'processing' if kind=='processing' else 'session'))
        identity = state["processing_id" if kind == "processing" else "plan_id"]
        for row in db.execute("SELECT task_id,payload_json FROM pipeline_tasks"):
            payload = json.loads(row["payload_json"])
            if (payload.get("_processing_id") == identity or payload.get("_plan_id") == identity
                    or str(payload.get("_plan_step_key") or "").startswith(identity + ":")):
                ids.add(row["task_id"])
        return ids

    def _active(self, kind, state, db):
        ids = self._child_ids(kind, state, db)
        rows = [db.execute("SELECT * FROM pipeline_tasks WHERE task_id=?", (identity,)).fetchone() for identity in ids]
        children = [row["task_id"] for row in rows if row and (row["status"] in {"queued", "running"} or self.store._row_worker_alive(row))]
        parent_active = state.get("status") in ({"queued", "running", "cancelling"} if kind == "processing" else {"active", "cancelling"})
        return bool(children or parent_active), children

    def _parent_blockers(self, kind, state, db):
        return []

    def request(self, kind, identity, *, expected_revision, identity_sha256, authorization=None):
        with self._lock, self._owners(), self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state, revision = self._state(kind, identity, db)
            if revision != expected_revision or fingerprint(state) != identity_sha256:
                raise LifecycleBlocked([blocker("revision_conflict", "任务状态已变化，请刷新后重试。")])
            blockers = self._parent_blockers(kind, state, db)
            if blockers: raise LifecycleBlocked(blockers)
            active, _ = self._active(kind, state, db)
            if kind != "queue": _detach_automation_records(db, kind, identity)
            if not active:
                return self._delete(kind, state, db, authorization)
            now = utc_now()
            db.execute("INSERT INTO record_deletion_requests VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,identity) DO UPDATE SET status='pending',updated_at=excluded.updated_at,error=NULL",
                (kind, identity, state.get("created_at", ""), "pending", now, now, None))
        if authorization: self._authorization[(kind, identity)] = authorization
        if kind == "processing": self.processing.action(identity, "cancel", None)
        elif kind == "session": self.plans.action(identity, "cancel")
        else: self.cancel_task(identity)
        return {"deleted": False, "status": "deletion_pending", "identity": identity,
                "message": "正在停止任务，执行退出后自动删除。"}

    def _delete(self, kind, state, db, authorization=None):
        identity = state[{"processing": "processing_id", "session": "plan_id", "queue": "task_id"}[kind]]
        active, children = self._active(kind, state, db)
        if active: raise LifecycleBlocked([blocker("worker_active", "执行尚未退出，删除等待中。", task_ids=children)])
        blockers = self._parent_blockers(kind, state, db)
        if blockers: raise LifecycleBlocked(blockers)
        ids = self._child_ids(kind, state, db)
        native_preserved = False
        if kind == "session":
            # Do not hold the local writer transaction during a remote owner
            # command: cancellation may notify this same control-plane service.
            db.commit()
            for document in state.get("documents", []):
                native = document.get("native_session_id")
                if not native: continue
                try:
                    authorization = self._native_authorization(authorization)
                    description = self.native_request("GET", f"/api/report-sessions/{native}/deletion", authorization=authorization)
                    result = self.native_request("DELETE", f"/api/report-sessions/{native}", description["available_actions"][0]["payload"], authorization=authorization)
                    if result.get("deleted") is not True:
                        raise LifecycleBlocked([blocker("native_deletion_pending", "提取执行尚未退出，删除等待中。")])
                    native_preserved |= bool(result.get("source_artifacts_preserved"))
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 404: raise
            db.execute("BEGIN IMMEDIATE")
            refreshed, _ = self._state(kind, identity, db)
            if fingerprint(refreshed) != fingerprint(state):
                raise LifecycleBlocked([blocker("revision_conflict", "提取记录发生变化，请刷新后重试。")])
        owners = {identity, *ids}
        _detach_queue_references(db, ids)
        for task_id in ids:
            row = db.execute("SELECT payload_json FROM pipeline_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row:
                dispatch = json.loads(row[0]).get("_plan_step_key")
                if dispatch: owners.add(dispatch)
        placeholders = ",".join("?" for _ in owners)
        db.execute(f"DELETE FROM ir_input_pins WHERE owner_id IN ({placeholders})", tuple(owners))
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for task_id in ids:
            if "queue_reference_releases" in tables: db.execute("DELETE FROM queue_reference_releases WHERE task_id=?", (task_id,))
            db.execute("DELETE FROM pipeline_events WHERE task_id=?", (task_id,))
            db.execute("DELETE FROM pipeline_tasks WHERE task_id=?", (task_id,))
        if kind == "processing":
            db.execute("DELETE FROM report_processing_events WHERE processing_id=?", (identity,))
            db.execute("DELETE FROM report_processing_artifacts WHERE processing_id=?", (identity,))
            db.execute("DELETE FROM report_processing_runs WHERE processing_id=?", (identity,))
            self.processing._secrets.pop(identity, None)
        elif kind == "session":
            if "pipeline_plan_events" in tables: db.execute("DELETE FROM pipeline_plan_events WHERE plan_id=?", (identity,))
            db.execute("DELETE FROM pipeline_plans WHERE plan_id=?", (identity,))
            self.plans._secrets.pop(identity, None)
        if kind != "queue": _detach_automation_records(db, kind, identity, deleted=True)
        db.execute("DELETE FROM record_deletion_requests WHERE kind=? AND identity=?", (kind, identity))
        self._authorization.pop((kind, identity), None)
        return {"deleted": True, "status": "deleted", "identity": identity,
            "deleted_queue_count": len(ids), "source_artifacts_preserved": True if kind != "session" else native_preserved,
            "publication_changed": False}

    def tick(self):
        with self.store._connect() as db:
            requests = [dict(row) for row in db.execute("SELECT * FROM record_deletion_requests WHERE status IN ('pending','blocked')")]
        for request in requests:
            kind, identity = request["kind"], request["identity"]
            try:
                with self._lock, self._owners(), self.store._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    try: state, _ = self._state(kind, identity, db)
                    except KeyError:
                        db.execute("DELETE FROM record_deletion_requests WHERE kind=? AND identity=?", (kind, identity)); continue
                    if state.get("created_at", "") != request["created_identity"]:
                        raise ValueError("任务身份已变化，自动删除已停止。")
                    if self._active(kind, state, db)[0]: continue
                    self._delete(kind, state, db, self._authorization.get((kind, identity)))
            except Exception as exc:
                with self.store._connect() as db:
                    db.execute("UPDATE record_deletion_requests SET status='blocked',error=?,updated_at=? WHERE kind=? AND identity=?",
                        (str(exc)[:1000], utc_now(), kind, identity))

    def pending(self, kind, identity):
        with self.store._connect() as db:
            row = db.execute("SELECT status,error FROM record_deletion_requests WHERE kind=? AND identity=?", (kind, identity)).fetchone()
        return dict(row) if row else None
