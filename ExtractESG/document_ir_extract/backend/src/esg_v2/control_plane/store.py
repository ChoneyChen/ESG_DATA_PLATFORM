from __future__ import annotations

import json
import sqlite3
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from esg_v2.control_plane.contracts import (
    PipelineTask,
    PipelineTaskStatus,
    PipelineTaskType,
    utc_now,
)


class PipelineQueueStore:
    """SQLite control-plane state; business artifacts stay in their own packages."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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
                CREATE TABLE IF NOT EXISTS pipeline_tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    native_job_id TEXT,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    requires_runtime_secret INTEGER NOT NULL DEFAULT 0,
                    worker_pid INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pipeline_queue
                    ON pipeline_tasks(status, position, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_plan_step
                    ON pipeline_tasks(json_extract(payload_json, '$._plan_step_key'))
                    WHERE json_extract(payload_json, '$._plan_step_key') IS NOT NULL;
                CREATE TABLE IF NOT EXISTS pipeline_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail_json TEXT,
                    FOREIGN KEY(task_id) REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_pipeline_events
                    ON pipeline_events(task_id, event_id);
                """
            )

    def create(
        self,
        *,
        task_id: str,
        task_type: PipelineTaskType,
        operation: str,
        title: str,
        payload: dict[str, Any],
        native_job_id: str | None,
        requires_runtime_secret: bool = False,
    ) -> PipelineTask:
        return self.create_many(
            [
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "operation": operation,
                    "title": title,
                    "payload": payload,
                    "native_job_id": native_job_id,
                    "requires_runtime_secret": requires_runtime_secret,
                }
            ]
        )[0]

    def create_many(self, specifications: list[dict[str, Any]]) -> list[PipelineTask]:
        """Append a group of tasks in one transaction and preserve their order."""

        if not specifications:
            raise ValueError("At least one pipeline task specification is required")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(position), 0) AS position FROM pipeline_tasks"
            ).fetchone()
            first_position = int(row["position"] or 0) + 1
            task_ids: list[str] = []
            for offset, specification in enumerate(specifications):
                task_id = str(specification["task_id"])
                task_type = specification["task_type"]
                if not isinstance(task_type, PipelineTaskType):
                    task_type = PipelineTaskType(str(task_type))
                connection.execute(
                    """
                    INSERT INTO pipeline_tasks (
                        task_id,task_type,operation,title,status,position,payload_json,
                        native_job_id,stage,message,requires_runtime_secret,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        task_type.value,
                        str(specification["operation"]),
                        str(specification["title"]),
                        PipelineTaskStatus.QUEUED.value,
                        first_position + offset,
                        json.dumps(
                            specification["payload"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        specification.get("native_job_id"),
                        "queued",
                        "Task queued",
                        int(bool(specification.get("requires_runtime_secret", False))),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO pipeline_events (
                        task_id,created_at,stage,level,message,detail_json
                    ) VALUES (?,?,?,?,?,NULL)
                    """,
                    (
                        task_id,
                        now,
                        "queued",
                        "info",
                        "Task added to the unified queue",
                    ),
                )
                task_ids.append(task_id)
        return [self.get(task_id) for task_id in task_ids]

    def get(self, task_id: str) -> PipelineTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._record(row)

    def find_plan_step(self, key):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_tasks WHERE json_extract(payload_json, '$._plan_step_key')=? LIMIT 1", (key,)).fetchone()
        return self._record(row) if row else None

    def list(self, limit: int = 300) -> list[PipelineTask]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pipeline_tasks
                ORDER BY
                    CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    CASE WHEN status IN ('running','queued') THEN position ELSE 0 END,
                    created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def clear_history(self) -> list[str]:
        """Delete terminal control-plane rows without touching business artifacts."""

        terminal_statuses = (
            PipelineTaskStatus.COMPLETED.value,
            PipelineTaskStatus.PARTIAL.value,
            PipelineTaskStatus.FAILED.value,
            PipelineTaskStatus.CANCELLED.value,
            PipelineTaskStatus.INTERRUPTED.value,
        )
        placeholders = ",".join("?" for _ in terminal_statuses)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT task_id FROM pipeline_tasks WHERE status IN ({placeholders}) AND json_extract(payload_json, '$._plan_step_key') IS NULL",
                terminal_statuses,
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            if task_ids:
                connection.execute(
                    f"DELETE FROM pipeline_tasks WHERE status IN ({placeholders}) AND json_extract(payload_json, '$._plan_step_key') IS NULL",
                    terminal_statuses,
                )
        return task_ids

    def claim_next(self) -> PipelineTask | None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            running = connection.execute(
                "SELECT task_id FROM pipeline_tasks WHERE status = ? LIMIT 1",
                (PipelineTaskStatus.RUNNING.value,),
            ).fetchone()
            if running:
                return None
            row = connection.execute(
                """
                SELECT task_id FROM pipeline_tasks
                WHERE status = ? AND cancel_requested = 0
                ORDER BY position, created_at LIMIT 1
                """,
                (PipelineTaskStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE pipeline_tasks
                SET status = ?, stage = ?, message = ?, started_at = ?, updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    PipelineTaskStatus.RUNNING.value,
                    "starting",
                    "Starting isolated worker",
                    now,
                    now,
                    row["task_id"],
                    PipelineTaskStatus.QUEUED.value,
                ),
            )
        task = self.get(str(row["task_id"]))
        self.add_event(task.task_id, "starting", "info", "Unified scheduler claimed task")
        return task

    def update(self, task_id: str, **changes: Any) -> PipelineTask:
        allowed = {
            "status", "stage", "message", "progress_current", "progress_total",
            "cancel_requested", "worker_pid", "started_at", "finished_at", "error",
            "native_job_id",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported task fields: {', '.join(sorted(unknown))}")
        assignments = ["updated_at = ?"]
        values: list[Any] = [utc_now()]
        for key, value in changes.items():
            if isinstance(value, Enum):
                value = value.value
            if key == "cancel_requested":
                value = int(bool(value))
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(task_id)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE pipeline_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(task_id)
        return self.get(task_id)

    def request_cancel(self, task_id: str) -> PipelineTask:
        task = self.get(task_id)
        if task.status in {
            PipelineTaskStatus.COMPLETED,
            PipelineTaskStatus.PARTIAL,
            PipelineTaskStatus.FAILED,
            PipelineTaskStatus.CANCELLED,
            PipelineTaskStatus.INTERRUPTED,
        }:
            return task
        if task.status == PipelineTaskStatus.QUEUED:
            task = self.update(
                task_id,
                status=PipelineTaskStatus.CANCELLED,
                stage="cancelled",
                message="Queued task cancelled",
                cancel_requested=True,
                finished_at=utc_now(),
            )
        else:
            task = self.update(
                task_id,
                cancel_requested=True,
                stage="stopping",
                message="Termination requested",
            )
        self.add_event(task_id, task.stage, "warning", task.message)
        return task

    def reorder(self, task_ids: list[str]) -> list[PipelineTask]:
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_ids must be unique")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT task_id,status FROM pipeline_tasks WHERE task_id IN (%s)"
                % ",".join("?" for _ in task_ids),
                task_ids,
            ).fetchall() if task_ids else []
            found = {str(row["task_id"]): str(row["status"]) for row in rows}
            missing = [task_id for task_id in task_ids if task_id not in found]
            if missing:
                raise KeyError(",".join(missing))
            not_queued = [task_id for task_id, status in found.items() if status != "queued"]
            if not_queued:
                raise ValueError("Only queued tasks can be reordered")
            existing = connection.execute(
                "SELECT task_id FROM pipeline_tasks WHERE status = 'queued' ORDER BY position,created_at"
            ).fetchall()
            existing_ids = [str(row["task_id"]) for row in existing]
            ordered = [*task_ids, *[item for item in existing_ids if item not in set(task_ids)]]
            now = utc_now()
            for index, task_id in enumerate(ordered, start=1):
                connection.execute(
                    "UPDATE pipeline_tasks SET position = ?, updated_at = ? WHERE task_id = ?",
                    (index, now, task_id),
                )
        return [self.get(task_id) for task_id in ordered]

    def rebase_queued_ir_references(
        self,
        old_run_ids: list[str],
        retained_run_id: str,
        *,
        exclude_native_job_id: str | None = None,
    ) -> list[str]:
        """Move queued consumers from a superseded IR package to its retained successor."""

        old_ids = set(old_run_ids)
        if not old_ids:
            return []
        changed: list[str] = []
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT task_id,task_type,operation,title,payload_json,native_job_id "
                "FROM pipeline_tasks WHERE status = 'queued'"
            ).fetchall()
            for row in rows:
                if exclude_native_job_id and row["native_job_id"] == exclude_native_job_id:
                    continue
                payload = json.loads(row["payload_json"])
                if not self._replace_ir_reference(payload, str(row["task_type"]), str(row["operation"]), old_ids, retained_run_id):
                    continue
                title = str(row["title"])
                for old_run_id in old_ids:
                    title = title.replace(old_run_id, retained_run_id)
                connection.execute(
                    "UPDATE pipeline_tasks SET payload_json=?,title=?,updated_at=? WHERE task_id=?",
                    (
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        title,
                        now,
                        row["task_id"],
                    ),
                )
                changed.append(str(row["task_id"]))
        for task_id in changed:
            self.add_event(
                task_id,
                "queued",
                "info",
                f"Input Document IR was promoted; queued task rebased to {retained_run_id}",
                {"retained_run_id": retained_run_id, "superseded_run_ids": sorted(old_ids)},
            )
        return changed

    def active_ir_references(
        self,
        run_ids: list[str],
        *,
        exclude_native_job_id: str | None = None,
    ) -> set[str]:
        """Return superseded IR IDs still read by a running queue task."""

        candidates = set(run_ids)
        referenced: set[str] = set()
        if not candidates:
            return referenced
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT task_type,operation,payload_json,native_job_id "
                "FROM pipeline_tasks WHERE status = 'running'"
            ).fetchall()
        for row in rows:
            if exclude_native_job_id and row["native_job_id"] == exclude_native_job_id:
                continue
            payload = json.loads(row["payload_json"])
            referenced.update(
                self._ir_references(payload, str(row["task_type"]), str(row["operation"]))
                & candidates
            )
        return referenced

    def recover_interrupted(self) -> list[str]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT task_id FROM pipeline_tasks WHERE status = 'running'"
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            if task_ids:
                connection.execute(
                    """
                    UPDATE pipeline_tasks
                    SET status='interrupted',stage='interrupted',message=?,worker_pid=NULL,
                        finished_at=?,updated_at=? WHERE status='running'
                    """,
                    ("Backend restarted while the task was running", now, now),
                )
        for task_id in task_ids:
            self.add_event(
                task_id,
                "interrupted",
                "warning",
                "Task marked interrupted after backend restart",
            )
        return task_ids

    @staticmethod
    def _ir_references(payload: dict[str, Any], task_type: str, operation: str) -> set[str]:
        if task_type == PipelineTaskType.TARGETED_EXTRACTION.value:
            value = payload.get("ir_run_id")
            return {str(value)} if value else set()
        if task_type != PipelineTaskType.DOCUMENT_IR.value:
            return set()
        key = "parent_ir_run_id" if operation == "repair" else "parent_run_id"
        value = payload.get(key)
        return {str(value)} if value else set()

    @classmethod
    def _replace_ir_reference(
        cls,
        payload: dict[str, Any],
        task_type: str,
        operation: str,
        old_run_ids: set[str],
        retained_run_id: str,
    ) -> bool:
        references = cls._ir_references(payload, task_type, operation)
        if not references.intersection(old_run_ids):
            return False
        if task_type == PipelineTaskType.TARGETED_EXTRACTION.value:
            payload["ir_run_id"] = retained_run_id
        elif operation == "repair":
            payload["parent_ir_run_id"] = retained_run_id
        else:
            payload["parent_run_id"] = retained_run_id
        return True

    def add_event(
        self,
        task_id: str,
        stage: str,
        level: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pipeline_events
                    (task_id,created_at,stage,level,message,detail_json)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    task_id,
                    utc_now(),
                    stage,
                    level,
                    message,
                    json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                ),
            )

    def events(self, task_id: str, after: int = 0) -> list[dict[str, Any]]:
        self.get(task_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pipeline_events
                WHERE task_id = ? AND event_id > ? ORDER BY event_id
                """,
                (task_id, after),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "task_id": row["task_id"],
                "created_at": row["created_at"],
                "stage": row["stage"],
                "level": row["level"],
                "message": row["message"],
                "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
            }
            for row in rows
        ]

    @staticmethod
    def _record(row: sqlite3.Row) -> PipelineTask:
        return PipelineTask(
            task_id=row["task_id"],
            task_type=row["task_type"],
            operation=row["operation"],
            title=row["title"],
            status=row["status"],
            position=row["position"],
            payload=json.loads(row["payload_json"]),
            native_job_id=row["native_job_id"],
            stage=row["stage"],
            message=row["message"],
            progress_current=row["progress_current"],
            progress_total=row["progress_total"],
            cancel_requested=bool(row["cancel_requested"]),
            requires_runtime_secret=bool(row["requires_runtime_secret"]),
            worker_pid=row["worker_pid"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
        )
