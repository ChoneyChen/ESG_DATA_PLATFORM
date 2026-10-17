from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from esg_targeted.contracts import ExtractionRequest, JobRecord, JobStatus


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress_current INTEGER NOT NULL,
                    progress_total INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    error TEXT,
                    summary_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail_json TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_events_job_id ON events(job_id, event_id);
                CREATE TABLE IF NOT EXISTS job_lifecycle (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
                    state TEXT NOT NULL CHECK(state IN ('active','archived','discarded')),
                    reason TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    cleanup_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_supervision (
                    job_id TEXT PRIMARY KEY,
                    owner_pid INTEGER NOT NULL,
                    owner_started TEXT NOT NULL,
                    watchdog_pid INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    call_sequence INTEGER NOT NULL DEFAULT 0,
                    call_label TEXT,
                    call_deadline REAL,
                    heartbeat_at TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            for column, declaration in (
                ("worker_id", "TEXT"),
                ("heartbeat_at", "TEXT"),
                ("worker_started", "TEXT"),
            ):
                if column not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {declaration}")
            supervision_columns = {row["name"] for row in connection.execute("PRAGMA table_info(execution_supervision)")}
            for column in ("call_active_deadline", "call_wall_safety_deadline"):
                if column not in supervision_columns:
                    connection.execute(f"ALTER TABLE execution_supervision ADD COLUMN {column} REAL")

    def create(self, job_id: str, request: ExtractionRequest, output_dir: str) -> JobRecord:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id,status,stage,progress_current,progress_total,created_at,updated_at,
                    request_json,output_dir
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    JobStatus.QUEUED.value,
                    "queued",
                    0,
                    0,
                    now,
                    now,
                    request.model_dump_json(),
                    output_dir,
                ),
            )
        self.add_event(job_id, "queued", "info", "Extraction job queued")
        return self.get(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        stage: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        error: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> JobRecord:
        updates = ["updated_at = ?"]
        values: list[Any] = [datetime.now(UTC).isoformat()]
        for column, value in (
            ("status", status.value if status else None),
            ("stage", stage),
            ("progress_current", progress_current),
            ("progress_total", progress_total),
            ("error", error),
            ("summary_json", json.dumps(summary, ensure_ascii=False) if summary is not None else None),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                values.append(value)
        values.append(job_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?", values
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._record(row)

    def list(self, limit: int = 100, *, include_archived: bool = False) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT jobs.* FROM jobs LEFT JOIN job_lifecycle l USING(job_id) "
                + ("" if include_archived else "WHERE COALESCE(l.state,'active')='active' ")
                + "ORDER BY jobs.created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._record(row) for row in rows]

    def job_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT job_id FROM jobs ORDER BY created_at DESC").fetchall()
        return [row["job_id"] for row in rows]

    def restore(
        self,
        job_id: str,
        request: ExtractionRequest,
        output_dir: str,
        *,
        status: JobStatus,
        stage: str,
        progress_current: int,
        progress_total: int,
        created_at: datetime,
        updated_at: datetime,
        error: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> bool:
        """Restore a missing terminal job index from an immutable result bundle."""

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    job_id,status,stage,progress_current,progress_total,created_at,updated_at,
                    request_json,output_dir,error,summary_json,cancel_requested
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    job_id,
                    status.value,
                    stage,
                    max(0, progress_current),
                    max(0, progress_total),
                    created_at.astimezone(UTC).isoformat(),
                    updated_at.astimezone(UTC).isoformat(),
                    request.model_dump_json(),
                    output_dir,
                    error,
                    json.dumps(summary, ensure_ascii=False) if summary is not None else None,
                ),
            )
        restored = cursor.rowcount == 1
        if restored:
            self.add_event(
                job_id,
                "catalog_recovery",
                "warning",
                "Job index restored from the immutable result bundle.",
            )
        return restored

    def delete(self, job_id: str) -> None:
        active_statuses = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status,worker_id,worker_started FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if self._worker_alive(row["worker_id"], row["worker_started"]):
                raise ValueError("Cannot delete artifacts while the owning worker is still alive")
            if row["status"] in active_statuses:
                raise ValueError(
                    f"job status {row['status']} cannot be deleted; cancel it first"
                )
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

    def request_cancel(self, job_id: str) -> JobRecord:
        first_request = False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            first_request = not bool(row["cancel_requested"])
            cursor = connection.execute(
                "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE job_id = ?",
                (datetime.now(UTC).isoformat(), job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        if first_request:
            self.add_event(job_id, "cancel", "warning", "Cancellation requested")
        return self.get(job_id)

    def claim(self, job_id: str, worker_id: str) -> JobRecord:
        now = datetime.now(UTC).isoformat()
        pid = int(worker_id.split(":", 1)[0])
        started = self.process_started(pid)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lifecycle = connection.execute("SELECT state FROM job_lifecycle WHERE job_id=?", (job_id,)).fetchone()
            if lifecycle and lifecycle["state"] != "active":
                raise ValueError("Archived or discarded job must be restored before execution")
            row = connection.execute(
                "SELECT worker_id,worker_started FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["worker_id"] and row["worker_id"].split(":", 1)[0] != str(pid) and self._worker_alive(row["worker_id"], row["worker_started"]):
                raise ValueError("Job already has a live worker; reconnect instead of starting a duplicate")
            cursor = connection.execute(
                """
                UPDATE jobs
                SET worker_id = ?, worker_started = ?, heartbeat_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, started, now, now, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        return self.get(job_id)

    def heartbeat(self, job_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE job_id = ?",
                (now, now, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def release_worker(self, job_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET worker_id = NULL, updated_at = ? WHERE job_id = ?",
                (datetime.now(UTC).isoformat(), job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def has_live_worker(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT worker_id,worker_started FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return bool(row and self._worker_alive(row["worker_id"], row["worker_started"]))

    def other_live_worker(self, pid: int) -> tuple[str, int] | None:
        """Known pre-upgrade workers may be alive without the new OS slot."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id,worker_id,worker_started FROM jobs WHERE worker_id IS NOT NULL"
            ).fetchall()
        for row in rows:
            worker_pid = int(row["worker_id"].split(":", 1)[0])
            if worker_pid != pid and self._worker_alive(row["worker_id"], row["worker_started"]):
                if row["worker_started"] or self.process_matches(worker_pid, row["job_id"]):
                    return row["job_id"], worker_pid
        return None

    def release_owned_worker(self, job_id: str, pid: int) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE jobs SET worker_id=NULL WHERE job_id=? AND (worker_id=? OR worker_id LIKE ?)", (job_id, str(pid), f"{pid}:%"))

    def recover_abandoned(self) -> list[str]:
        """Mark jobs abandoned by a previous backend process as resumable."""

        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id, status, worker_id,worker_started FROM jobs
                WHERE status IN (?, ?)
                ORDER BY created_at
                """,
                (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
            ).fetchall()
            live_running_worker = any(
                row["status"] == JobStatus.RUNNING.value
                and self._worker_alive(row["worker_id"], row["worker_started"])
                for row in rows
            )
            job_ids = [
                row["job_id"]
                for row in rows
                if (
                    row["status"] == JobStatus.RUNNING.value
                    and not self._worker_alive(row["worker_id"], row["worker_started"])
                )
                or (
                    row["status"] == JobStatus.QUEUED.value
                    and not self._worker_alive(row["worker_id"], row["worker_started"])
                    and not live_running_worker
                )
            ]
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                connection.execute(
                    f"""
                    UPDATE jobs
                    SET status = ?, stage = ?, worker_id = NULL, error = ?, updated_at = ?
                    WHERE job_id IN ({placeholders})
                    """,
                    (
                        JobStatus.INTERRUPTED.value,
                        "interrupted",
                        "Previous worker is no longer alive; checkpoints are retained for resume.",
                        now,
                        *job_ids,
                    ),
                )
        for job_id in job_ids:
            self.add_event(
                job_id,
                "interrupted",
                "warning",
                "Job was interrupted because its worker is no longer alive; it can be resumed.",
            )
        return job_ids

    @staticmethod
    def _worker_alive(worker_id: str | None, started: str | None = None) -> bool:
        if not worker_id:
            return False
        try:
            pid = int(worker_id.split(":", 1)[0])
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            return False
        except PermissionError:
            return True
        return not started or JobStore.process_started(pid) == started

    @staticmethod
    def process_started(pid: int) -> str:
        """Process birth identity, not a payload fingerprint or PID-only lease."""
        try:
            output = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="], text=True, timeout=2
            ).strip()
            birth, state = output.rsplit(None, 1)
            return "" if state.startswith("Z") else birth.strip()
        except (subprocess.SubprocessError, OSError, ValueError):
            return ""

    @staticmethod
    def process_matches(pid: int, *identifiers: str | None) -> bool:
        """Verify a legacy PID from its known task, without scanning processes."""
        try:
            command = subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True, timeout=2)
            return any(identifier and identifier in command for identifier in identifiers)
        except (subprocess.SubprocessError, OSError):
            return False

    def start_supervision(self, job_id: str, pid: int, started: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            owner = connection.execute("SELECT worker_id,worker_started FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if owner is None:
                raise KeyError(job_id)
            if owner["worker_id"] and owner["worker_id"].split(":", 1)[0] != str(pid) and self._worker_alive(owner["worker_id"], owner["worker_started"]):
                raise ValueError("Job already belongs to another live worker")
            row = connection.execute(
                "SELECT * FROM execution_supervision WHERE job_id=? AND active=1", (job_id,)
            ).fetchone()
            if row and self._worker_alive(str(row["owner_pid"]), row["owner_started"]):
                raise ValueError("Job supervision already belongs to a live worker")
            connection.execute(
                "INSERT INTO execution_supervision(job_id,owner_pid,owner_started,active,heartbeat_at) "
                "VALUES(?,?,?,1,?) ON CONFLICT(job_id) DO UPDATE SET owner_pid=excluded.owner_pid,"
                "owner_started=excluded.owner_started,active=1,watchdog_pid=NULL,call_label=NULL,call_deadline=NULL,"
                "call_active_deadline=NULL,call_wall_safety_deadline=NULL",
                (job_id, pid, started, datetime.now(UTC).isoformat()),
            )
            connection.execute("UPDATE jobs SET worker_id=?,worker_started=? WHERE job_id=?", (f"{pid}:supervised", started, job_id))

    def supervision(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM execution_supervision WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def set_watchdog(self, job_id: str, owner_pid: int, watchdog_pid: int) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE execution_supervision SET watchdog_pid=? WHERE job_id=? AND owner_pid=? AND active=1", (watchdog_pid, job_id, owner_pid))

    def stop_supervision(self, job_id: str, owner_pid: int) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE execution_supervision SET active=0,call_label=NULL,call_deadline=NULL,call_active_deadline=NULL,call_wall_safety_deadline=NULL WHERE job_id=? AND owner_pid=?", (job_id, owner_pid))

    def begin_model_call(self, job_id: str, label: str, deadline: float, *,
                         active_deadline: float | None = None,
                         wall_safety_deadline: float | None = None) -> int | None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE execution_supervision SET call_sequence=call_sequence+1,call_label=?,call_deadline=?,call_active_deadline=?,call_wall_safety_deadline=? "
                "WHERE job_id=? AND owner_pid=? AND active=1",
                (label, deadline, active_deadline, wall_safety_deadline, job_id, os.getpid()),
            )
            if not cursor.rowcount:
                return None
            row = connection.execute("SELECT call_sequence FROM execution_supervision WHERE job_id=?", (job_id,)).fetchone()
        return int(row["call_sequence"])

    def end_model_call(self, job_id: str, sequence: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE execution_supervision SET call_label=NULL,call_deadline=NULL,call_active_deadline=NULL,call_wall_safety_deadline=NULL "
                "WHERE job_id=? AND owner_pid=? AND call_sequence=?",
                (job_id, os.getpid(), sequence),
            )

    def interrupt_owned_worker(self, job_id: str, pid: int, started: str, reason: str, *, cancelled: bool = False) -> bool:
        """A watchdog records one terminal reason before stopping its exact owner."""
        status = JobStatus.CANCELLED if cancelled else JobStatus.INTERRUPTED
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM execution_supervision WHERE job_id=? AND active=1", (job_id,)).fetchone()
            if not row or (row["owner_pid"], row["owner_started"]) != (pid, started):
                return False
            cursor = connection.execute(
                "UPDATE jobs SET status=?,stage=?,error=?,updated_at=? "
                "WHERE job_id=? AND status IN ('running','queued')",
                (status.value, status.value, reason, now, job_id),
            )
            connection.execute("UPDATE execution_supervision SET active=0,call_deadline=NULL,call_active_deadline=NULL,call_wall_safety_deadline=NULL WHERE job_id=?", (job_id,))
            if cursor.rowcount:
                connection.execute("INSERT INTO events(job_id,created_at,stage,level,message) VALUES(?,?,?,?,?)", (job_id, now, status.value, "warning", reason))
        return True

    def prepare_resume(self, job_id: str) -> JobRecord:
        resumable = {
            JobStatus.INTERRUPTED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            JobStatus.PARTIAL.value,
        }
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lifecycle = connection.execute("SELECT state FROM job_lifecycle WHERE job_id=?", (job_id,)).fetchone()
            if lifecycle and lifecycle["state"] != "active":
                raise ValueError("Archived or discarded job must be restored before resume")
            row = connection.execute(
                "SELECT status,worker_id,worker_started FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if self._worker_alive(row["worker_id"], row["worker_started"]):
                raise ValueError("A live worker still owns this job; cancel or reconnect before resuming")
            if row["status"] not in resumable:
                raise ValueError(f"job status {row['status']} is not resumable")
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, error = NULL, summary_json = NULL,
                    cancel_requested = 0, worker_id = NULL, heartbeat_at = NULL,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (JobStatus.QUEUED.value, "queued", now, job_id),
            )
        self.add_event(
            job_id,
            "resume",
            "info",
            "Job queued for checkpoint resume.",
        )
        return self.get(job_id)

    def is_cancel_requested(self, job_id: str) -> bool:
        return self.get(job_id).cancel_requested

    def add_event(
        self,
        job_id: str,
        stage: str,
        level: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO events (job_id,created_at,stage,level,message,detail_json) VALUES (?,?,?,?,?,?)",
                (
                    job_id,
                    datetime.now(UTC).isoformat(),
                    stage,
                    level,
                    message,
                    json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                ),
            )

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE job_id = ? AND event_id > ? ORDER BY event_id",
                (job_id, after),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "job_id": row["job_id"],
                "created_at": row["created_at"],
                "stage": row["stage"],
                "level": row["level"],
                "message": row["message"],
                "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
            }
            for row in rows
        ]

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            status=row["status"],
            stage=row["stage"],
            progress_current=row["progress_current"],
            progress_total=row["progress_total"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            request=ExtractionRequest.model_validate_json(row["request_json"]),
            output_dir=row["output_dir"],
            error=row["error"],
            summary=json.loads(row["summary_json"]) if row["summary_json"] else None,
            cancel_requested=bool(row["cancel_requested"]),
            worker_id=row["worker_id"] if "worker_id" in row.keys() else None,
            heartbeat_at=(
                row["heartbeat_at"] if "heartbeat_at" in row.keys() else None
            ),
        )
