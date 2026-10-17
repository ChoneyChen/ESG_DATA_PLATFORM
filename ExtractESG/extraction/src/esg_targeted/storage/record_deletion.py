"""Permanent deletion of extraction execution records, independent of evidence."""
import hashlib
import json
import shutil
import threading
from datetime import datetime, timezone

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard, blocker


def _now(): return datetime.now(timezone.utc).isoformat()
def _digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode()).hexdigest()


class ExtractionRecordDeletion:
    def __init__(self, jobs, artifacts, sessions, cancel_callback, *, guard=None):
        self.jobs, self.artifacts, self.sessions, self.cancel_callback = jobs, artifacts, sessions, cancel_callback
        self.guard = guard or PublicationReferenceGuard()
        self._lock = threading.RLock(); self._stop = threading.Event(); self._thread = None
        with jobs._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS deleted_execution_records (kind TEXT NOT NULL,identity TEXT NOT NULL,deleted_at TEXT NOT NULL,PRIMARY KEY(kind,identity))")
            db.execute("CREATE TABLE IF NOT EXISTS extraction_deletion_requests (kind TEXT NOT NULL,identity TEXT NOT NULL,created_identity TEXT NOT NULL,requested_at TEXT NOT NULL,error TEXT,PRIMARY KEY(kind,identity))")

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread = threading.Thread(target=self._run, name="extraction-record-deletion", daemon=True); self._thread.start()
    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=5)
    def _run(self):
        while not self._stop.wait(1): self.tick()

    def _state(self, kind, identity, db):
        if kind == "session":
            row = db.execute("SELECT state_json FROM report_sessions WHERE session_id=?", (identity,)).fetchone()
            if not row: raise KeyError(identity)
            state = json.loads(row[0]); revision = state["revision"]
            children = state.get("projections", [])
            jobs = []
            for child in children:
                job = db.execute("SELECT * FROM jobs WHERE job_id=?", (child["job_id"],)).fetchone()
                if job: jobs.append(dict(job))
        else:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (identity,)).fetchone()
            if not row: raise KeyError(identity)
            state = dict(row); jobs = [state]
            lifecycle = db.execute("SELECT revision FROM job_lifecycle WHERE job_id=?", (identity,)).fetchone()
            revision = lifecycle[0] if lifecycle else 0
            parent = json.loads(state["request_json"]).get("report_session_id")
            if parent:
                row = db.execute("SELECT state_json FROM report_sessions WHERE session_id=?", (parent,)).fetchone()
                if row: state["_parent_session"] = json.loads(row[0])
        return state, revision, jobs

    def _active(self, kind, state, jobs):
        parent = state.get("_parent_session") if kind == "job" else None
        if parent:
            if parent.get("execution_status") in {"queued", "running"} or self.jobs._worker_alive(str(parent.get("owner_pid") or ""), parent.get("owner_started")):
                return True
            if any(self.jobs.has_live_worker(child["job_id"]) for child in parent.get("projections", [])):
                return True
        alive = [row["job_id"] for row in jobs if self.jobs._worker_alive(row.get("worker_id"), row.get("worker_started"))]
        parent_alive = kind == "session" and self.jobs._worker_alive(str(state.get("owner_pid") or ""), state.get("owner_started"))
        # Never-entered queued projections of a terminal parent have no worker
        # and need no cancellation; they are removed with their owner.
        parent_terminal = kind == "session" and state.get("execution_status") not in {"queued", "running"}
        pending = [row["job_id"] for row in jobs if row["status"] in {"queued", "running"} and not (parent_terminal and row["status"] == "queued")]
        return bool(parent_alive or alive or pending or (kind == "session" and state.get("execution_status") in {"queued", "running"}))

    def describe(self, kind, identity):
        with self.jobs._connect() as db:
            state, revision, _ = self._state(kind, identity, db)
            pending = db.execute("SELECT requested_at,error FROM extraction_deletion_requests WHERE kind=? AND identity=?", (kind, identity)).fetchone()
        prefix = "report-sessions" if kind == "session" else "jobs"
        action = {"action": "delete", "label": "删除", "method": "DELETE", "endpoint": f"/api/{prefix}/{identity}",
            "payload": {"expected_revision": revision, "identity_sha256": _digest(state)},
            "requires_confirmation": True, "requires_reason": False, "calls_model": False, "incurs_cost": False}
        return {"schema_version": "execution-deletion-v1", "identity": identity, "revision": revision,
            "available_actions": [action], "deletion_pending": dict(pending) if pending else None}

    def request(self, kind, identity, *, expected_revision, identity_sha256):
        with self._lock, self.jobs._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state, revision, jobs = self._state(kind, identity, db)
            if revision != expected_revision or identity_sha256 != _digest(state):
                raise LifecycleBlocked([blocker("revision_conflict", "提取任务已变化，请刷新后重试。")])
            if not self._active(kind, state, jobs): return self._delete(kind, identity, state, jobs, db)
            db.execute("INSERT INTO extraction_deletion_requests VALUES(?,?,?,?,?) ON CONFLICT(kind,identity) DO UPDATE SET error=NULL",
                (kind, identity, state.get("created_at", ""), _now(), None))
        self.cancel_callback(kind, identity)
        return {"deleted": False, "status": "deletion_pending", "identity": identity, "message": "正在停止提取，执行退出后自动删除。"}

    @staticmethod
    def _safe_directory(path):
        return not path.is_symlink() and not any(child.is_symlink() for child in path.rglob("*"))

    def _delete(self, kind, identity, state, jobs, db):
        if self._active(kind, state, jobs): raise ValueError("提取执行尚未退出。")
        job_ids = [row["job_id"] for row in jobs]
        references = self.guard.check({"job_ids": job_ids, "ir_run_ids": []}) if job_ids else {"verified": True, "blockers": []}
        preserve = not references.get("verified") or bool(references.get("blockers"))
        paths = [self.artifacts._job_path(job_id) for job_id in job_ids]
        if kind == "session": paths.append(self.artifacts.report_session_path(identity))
        parent = state.get("_parent_session") if kind == "job" else None
        remaining = [projection for projection in parent.get("projections", []) if projection["job_id"] != identity] if parent else []
        if parent and not remaining:
            paths.append(self.artifacts.report_session_path(parent["session_id"]))
        if any(path.exists() and not self._safe_directory(path) for path in paths): preserve = True
        if not preserve and job_ids:
            # This exact output identity is retired. Shared input IR/PDFs are
            # absent from the scope and remain usable for later extraction.
            try:
                reservation = self.guard.reserve({"job_ids": job_ids, "ir_run_ids": []}, "永久删除未入库的提取执行成果。")
                self.guard.update_reservation(reservation, "quarantined", "永久删除提取成果。")
                self.guard.update_reservation(reservation, "purged", "永久删除提取成果。")
            except Exception:
                preserve = True  # Record deletion still proceeds, source bytes stay safe.
        if not preserve:
            for path in paths:
                if path.exists(): shutil.rmtree(path)
        if parent:
            if remaining:
                parent.update(projections=remaining, revision=parent["revision"]+1, updated_at=_now())
                parent.setdefault("deleted_projection_ids", []).append(identity)
                db.execute("UPDATE report_sessions SET state_json=? WHERE session_id=?", (json.dumps(parent, ensure_ascii=False), parent["session_id"]))
            else:
                db.execute("DELETE FROM report_sessions WHERE session_id=?", (parent["session_id"],))
                db.execute("INSERT OR REPLACE INTO deleted_execution_records VALUES('session',?,?)", (parent["session_id"], _now()))
        if kind == "session" or parent and not remaining:
            owner_id = identity if kind == "session" else parent["session_id"]
            for root in self.sessions.settings.ir_roots:
                for path in (root / ".pins").glob(owner_id + "*.json"):
                    if path.is_symlink(): continue
                    try:
                        pin = json.loads(path.read_text(encoding="utf-8"))
                        if pin.get("owner") == owner_id: path.unlink()
                    except (OSError, ValueError): pass
            if kind == "session":
                db.execute("DELETE FROM report_sessions WHERE session_id=?", (identity,))
                db.execute("INSERT OR REPLACE INTO deleted_execution_records VALUES(?,?,?)", (kind, identity, _now()))
        for job_id in job_ids:
            db.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
            db.execute("INSERT OR REPLACE INTO deleted_execution_records VALUES('job',?,?)", (job_id, _now()))
        db.execute("DELETE FROM extraction_deletion_requests WHERE kind=? AND identity=?", (kind, identity))
        return {"deleted": True, "status": "deleted", "identity": identity, "deleted_job_count": len(job_ids),
            "source_artifacts_preserved": preserve, "publication_changed": False}

    def tick(self):
        with self.jobs._connect() as db:
            requests = [dict(row) for row in db.execute("SELECT * FROM extraction_deletion_requests")]
        for request in requests:
            kind, identity = request["kind"], request["identity"]
            try:
                with self._lock, self.jobs._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    try: state, _, jobs = self._state(kind, identity, db)
                    except KeyError:
                        db.execute("DELETE FROM extraction_deletion_requests WHERE kind=? AND identity=?", (kind, identity)); continue
                    if state.get("created_at", "") != request["created_identity"]: raise ValueError("任务身份已变化，删除已停止。")
                    if self._active(kind, state, jobs): continue
                    self._delete(kind, identity, state, jobs, db)
            except Exception as exc:
                with self.jobs._connect() as db:
                    db.execute("UPDATE extraction_deletion_requests SET error=? WHERE kind=? AND identity=?", (str(exc)[:1000], kind, identity))
