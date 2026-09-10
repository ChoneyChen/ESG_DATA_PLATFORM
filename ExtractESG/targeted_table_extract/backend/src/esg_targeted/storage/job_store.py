from __future__ import annotations

import json
import os
import sqlite3
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
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            for column, declaration in (
                ("worker_id", "TEXT"),
                ("heartbeat_at", "TEXT"),
            ):
                if column not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {declaration}")

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

    def list(self, limit: int = 100) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
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
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
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
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET worker_id = ?, heartbeat_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, now, now, job_id),
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

    def recover_abandoned(self) -> list[str]:
        """Mark jobs abandoned by a previous backend process as resumable."""

        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id, status, worker_id FROM jobs
                WHERE status IN (?, ?)
                ORDER BY created_at
                """,
                (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
            ).fetchall()
            live_running_worker = any(
                row["status"] == JobStatus.RUNNING.value
                and self._worker_alive(row["worker_id"])
                for row in rows
            )
            job_ids = [
                row["job_id"]
                for row in rows
                if (
                    row["status"] == JobStatus.RUNNING.value
                    and not self._worker_alive(row["worker_id"])
                )
                or (
                    row["status"] == JobStatus.QUEUED.value
                    and not live_running_worker
                )
            ]
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                connection.execute(
                    f"""
                    UPDATE jobs
                    SET status = ?, stage = ?, worker_id = NULL, updated_at = ?
                    WHERE job_id IN ({placeholders})
                    """,
                    (
                        JobStatus.INTERRUPTED.value,
                        "interrupted",
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
    def _worker_alive(worker_id: str | None) -> bool:
        if not worker_id:
            return False
        try:
            pid = int(worker_id.split(":", 1)[0])
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            return False
        except PermissionError:
            return True
        return True

    def prepare_resume(self, job_id: str) -> JobRecord:
        resumable = {
            JobStatus.INTERRUPTED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            JobStatus.PARTIAL.value,
        }
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
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
