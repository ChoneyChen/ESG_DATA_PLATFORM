"""Current run state. Atomic transitions, frozen targets and no historical-route adapter."""

import json, sqlite3, os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS run_progress (run_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS run_signals (run_id TEXT PRIMARY KEY, cancel_requested INTEGER NOT NULL DEFAULT 0)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def create(
        self, request, snapshot, document, submission_key=None, engine_fingerprint=None
    ):
        state = {
            "run_id": "extract-"
            + (
                __import__("hashlib").sha256(submission_key.encode()).hexdigest()[:32]
                if submission_key
                else uuid4().hex
            ),
            "schema_version": "extraction-run-1.0",
            "status": "queued",
            "name": request.name,
            "engine_fingerprint": engine_fingerprint,
            "request": request.model_dump(mode="json"),
            "targets": snapshot,
            "document": document,
            "created_at": now(),
            "updated_at": now(),
            "progress": {"current": 0, "total": len(snapshot["clauses"])},
            "cancel_requested": False,
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM runs WHERE run_id=?", (state["run_id"],)
            ).fetchone()
            if row:
                old = json.loads(row[0])
                if any(
                    old[k] != state[k]
                    for k in ("request", "targets", "document", "engine_fingerprint")
                ):
                    raise ValueError(
                        "Submission key already belongs to different input"
                    )
                return self.public(old)
            db.execute(
                "INSERT INTO runs VALUES (?,?)",
                (state["run_id"], json.dumps(state, ensure_ascii=False)),
            )
            db.execute("INSERT INTO run_signals VALUES (?,0)", (state["run_id"],))
        return self.public(state)

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT body FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            progress = self._progress(db, run_id)
        if row is None:
            raise KeyError(run_id)
        return self._merge_progress(json.loads(row[0]), progress)

    @staticmethod
    def _progress(db, run_id):
        row = db.execute("SELECT body FROM run_progress WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def _merge_progress(state, progress):
        updated_at = max(state.get("updated_at", ""), progress.get("updated_at", ""))
        return {**state, **progress, "updated_at": updated_at}

    def progress(self, run_id, **patch):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            body = self._progress(db, run_id)
            body.update(patch, updated_at=now())
            db.execute("INSERT INTO run_progress VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET body=excluded.body",
                (run_id, json.dumps(body, ensure_ascii=False)))

    def is_cancelled(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT cancel_requested FROM run_signals WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row[0])

    def update(self, run_id, **patch):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                raise KeyError(run_id)
            state = json.loads(row[0])
            state.update(patch, updated_at=now())
            db.execute(
                "UPDATE runs SET body=? WHERE run_id=?",
                (json.dumps(state, ensure_ascii=False), run_id),
            )
            if "cancel_requested" in patch:
                db.execute("INSERT INTO run_signals VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET cancel_requested=excluded.cancel_requested",
                    (run_id, int(patch["cancel_requested"])))
            progress = self._progress(db, run_id)
        return self.public(self._merge_progress(state, progress))

    def claim(self, run_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                raise KeyError(run_id)
            state = json.loads(row[0])
            if state["status"] != "queued":
                raise ValueError(
                    "Run is not queued; start a new run with an explicit target snapshot"
                )
            state.update(status="running", execution_continues=False, started_at=state.get("started_at") or now(), updated_at=now())
            from esg_runtime.process import identity

            state.update(worker_pid=os.getpid(), worker_started=identity(os.getpid()))
            db.execute(
                "UPDATE runs SET body=? WHERE run_id=?",
                (json.dumps(state, ensure_ascii=False), run_id),
            )
        return state

    def observe(self, run_id):
        state = self.get(run_id)
        if state["status"] == "running" and state.get("worker_pid"):
            from esg_runtime.process import observed_alive

            if (
                observed_alive(state["worker_pid"], state.get("worker_started"))
                is False
            ):
                with self.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute(
                        "SELECT body FROM runs WHERE run_id=?", (run_id,)
                    ).fetchone()
                    fresh = json.loads(row[0])
                    if (
                        fresh["status"] == "running"
                        and fresh.get("worker_pid") == state["worker_pid"]
                        and fresh.get("worker_started") == state.get("worker_started")
                    ):
                        fresh.update(
                            status="cancelled"
                            if fresh.get("cancel_requested")
                            else "interrupted",
                            updated_at=now(),
                            finished_at=now(),
                        )
                        if fresh["status"] == "interrupted":
                            fresh["error"] = "提取进程已经退出，已写入的原值保留。"
                        db.execute(
                            "UPDATE runs SET body=? WHERE run_id=?",
                            (json.dumps(fresh, ensure_ascii=False), run_id),
                        )
                    state = self._merge_progress(fresh, self._progress(db, run_id))
        return self.public(state)

    def list(self):
        return self.views()

    def views(self, run_ids=None):
        """Control-plane reads exclude frozen clause bodies before JSON decoding."""
        where = '' if run_ids is None else ' WHERE r.run_id IN ('+','.join('?' for _ in run_ids)+')'
        with self.connect() as db:
            rows = db.execute('''SELECT json_remove(r.body,'$.targets') AS body,
                json_extract(r.body,'$.targets.digest') AS target_digest,
                (SELECT json_group_array(key) FROM json_each(r.body,'$.targets.clauses')) AS clause_keys,
                p.body AS progress FROM runs r LEFT JOIN run_progress p ON p.run_id=r.run_id'''
                +where+' ORDER BY r.rowid DESC',run_ids or []).fetchall()
        return [self._merge_progress({**json.loads(row[0]),'target_digest':row[1],
            'clause_keys':json.loads(row[2])},json.loads(row[3]) if row[3] else {}) for row in rows]

    def purge(self, run_ids, remove_artifacts):
        from esg_contracts.sessions import TERMINAL
        from esg_runtime.process import observed_alive

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            states = []
            for run_id in dict.fromkeys(run_ids):
                row = db.execute("SELECT body FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if row:
                    state = json.loads(row[0])
                    if state["status"] not in TERMINAL or (
                        state.get("worker_pid") and observed_alive(state["worker_pid"], state.get("worker_started")) is True
                    ):
                        raise ValueError("执行中的任务不能彻底删除。")
                    states.append(run_id)
            for run_id in states:
                remove_artifacts(run_id)
                for table in ("run_progress", "run_signals", "runs"):
                    db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
        return {"run_ids": list(dict.fromkeys(run_ids)), "purged": True}

    @staticmethod
    def public(state):
        return {k: v for k, v in state.items() if k != "targets"} | {
            "clause_keys": list(state["targets"]["clauses"]) if "targets" in state else state.get("clause_keys",[]),
            "target_digest": state["targets"]["digest"] if "targets" in state else state.get("target_digest"),
            "artifacts_url": f"/api/runs/{state['run_id']}/facts",
        }
