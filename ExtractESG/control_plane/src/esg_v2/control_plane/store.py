from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from esg_v2.control_plane.outcomes import stage_outcome
from esg_v2.control_plane.progress import classify_error, elapsed_seconds, stage_label
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
            # Preserve pre-upgrade rows if historical callers queued duplicates;
            # new submissions are always checked under BEGIN IMMEDIATE below.
            try:
                connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_active_native
                    ON pipeline_tasks(task_type,native_job_id)
                    WHERE status IN ('queued','running') AND native_job_id IS NOT NULL""")
            except sqlite3.IntegrityError:
                pass
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(pipeline_tasks)")}
            pins_existed = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ir_input_pins'").fetchone() is not None
            connection.execute("""CREATE TABLE IF NOT EXISTS ir_input_pins (
                owner_id TEXT NOT NULL, ir_run_id TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(owner_id, ir_run_id))""")
            if "outcome_json" not in columns:
                connection.execute("ALTER TABLE pipeline_tasks ADD COLUMN outcome_json TEXT")
            # One-time historical backfill. Repeating it would undo explicit,
            # guarded reference release whenever the service restarts.
            if not pins_existed:
                for row in connection.execute("SELECT task_id,task_type,operation,payload_json FROM pipeline_tasks").fetchall():
                    for run_id in self._ir_references(json.loads(row["payload_json"]), row["task_type"], row["operation"]):
                        connection.execute("INSERT OR IGNORE INTO ir_input_pins VALUES (?,?,?)", (row["task_id"], run_id, utc_now()))
            if "worker_started" not in columns:
                connection.execute("ALTER TABLE pipeline_tasks ADD COLUMN worker_started TEXT")
            for name, kind in {"progress_json": "TEXT", "timing_json": "TEXT", "error_detail_json": "TEXT",
                               "revision": "INTEGER NOT NULL DEFAULT 0", "archived_at": "TEXT", "archive_reason": "TEXT"}.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE pipeline_tasks ADD COLUMN {name} {kind}")

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
                native_id = specification.get("native_job_id")
                existing = connection.execute(
                    "SELECT task_id FROM pipeline_tasks WHERE task_type=? AND native_job_id=? AND status IN ('queued','running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,position LIMIT 1",
                    (task_type.value, native_id)).fetchone() if native_id else None
                if existing:
                    if specification.get("deduplicate_native", False):
                        task_ids.append(str(existing["task_id"]))
                        continue
                    raise ValueError("Native task already has an active queue entry")
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
                for run_id in self._ir_references(specification["payload"], task_type.value, str(specification["operation"])):
                    connection.execute("INSERT OR IGNORE INTO ir_input_pins VALUES (?,?,?)", (task_id, run_id, now))
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

    def active_native_task(self, native_job_id: str, task_type: PipelineTaskType = PipelineTaskType.TARGETED_EXTRACTION) -> PipelineTask | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_tasks WHERE task_type=? AND native_job_id=? AND status IN ('queued','running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,position LIMIT 1",
                (task_type.value, native_job_id)).fetchone()
        return self._record(row) if row else None

    def find_plan_step(self, key):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_tasks WHERE json_extract(payload_json, '$._plan_step_key')=? LIMIT 1", (key,)).fetchone()
        return self._record(row) if row else None

    def list(self, limit: int = 300, *, include_archived: bool = False) -> list[PipelineTask]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pipeline_tasks
                WHERE archived_at IS NULL OR ?
                ORDER BY
                    CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    CASE WHEN status IN ('running','queued') THEN position ELSE 0 END,
                    created_at DESC
                LIMIT ?
                """,
                (int(include_archived), limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def clear_history(self) -> list[str]:
        """Archive every terminal visible row; dependency journals and pins survive."""

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
                f"SELECT task_id FROM pipeline_tasks WHERE status IN ({placeholders}) AND archived_at IS NULL",
                terminal_statuses,
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            if task_ids:
                connection.execute(
                    f"UPDATE pipeline_tasks SET archived_at=?,archive_reason=?,revision=revision+1 WHERE status IN ({placeholders}) AND archived_at IS NULL",
                    (utc_now(), "批量归档历史任务", *terminal_statuses),
                )
                for task_id in task_ids:
                    self._insert_event(connection, task_id, "archived", "info", "历史任务已归档；依赖与成果保留", {"operation": "archive"})
        return task_ids

    def archive(self, task_id: str, reason: str, *, expected_revision: int | None = None) -> PipelineTask:
        return self._archive_action(task_id, reason, restore=False, expected_revision=expected_revision)

    def restore(self, task_id: str, reason: str, *, expected_revision: int | None = None) -> PipelineTask:
        return self._archive_action(task_id, reason, restore=True, expected_revision=expected_revision)

    def _archive_action(self, task_id: str, reason: str, *, restore: bool, expected_revision: int | None) -> PipelineTask:
        if not reason.strip():
            raise ValueError("请填写操作原因")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pipeline_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if expected_revision is not None and row["revision"] != expected_revision:
                raise ValueError("记录已经变化，请刷新后重试")
            if not restore and row["status"] in {"queued", "running"}:
                raise ValueError("活动任务须先取消，再归档")
            if bool(row["archived_at"]) != (not restore):
                stage = "restored" if restore else "archived"
                connection.execute("UPDATE pipeline_tasks SET archived_at=?,archive_reason=?,revision=revision+1,updated_at=? WHERE task_id=?",
                    (None if restore else utc_now(), reason.strip(), utc_now(), task_id))
                self._insert_event(connection, task_id, stage, "info", stage_label(stage), {"reason": reason.strip()})
        return self.get(task_id)

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
                SET status = ?, stage = ?, message = ?, started_at = ?, updated_at = ?, revision=revision+1
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
            self._insert_event(connection, str(row['task_id']), 'starting', 'info', '统一队列开始执行',
                {'event_type':'state_changed', 'status':'running'})
        task = self.get(str(row["task_id"]))
        return task

    def update(self, task_id: str, **changes: Any) -> PipelineTask:
        if "error" in changes and "error_detail_json" not in changes:
            changes["error_detail_json"] = classify_error(str(changes["error"])).model_dump_json() if changes["error"] else None
        allowed = {
            "status", "stage", "message", "progress_current", "progress_total",
            "cancel_requested", "worker_pid", "started_at", "finished_at", "error",
            "native_job_id", "outcome_json", "progress_json", "timing_json", "error_detail_json",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported task fields: {', '.join(sorted(unknown))}")
        assignments = ["updated_at = ?"]
        if set(changes) - {"worker_pid", "timing_json"}:
            assignments.append("revision = revision + 1")
        values: list[Any] = [utc_now()]
        if "worker_pid" in changes:
            assignments.append("worker_started = ?")
            values.append(self.process_started(changes["worker_pid"]) if changes["worker_pid"] else None)
        for key, value in changes.items():
            if isinstance(value, Enum):
                value = value.value
            if key == "cancel_requested":
                value = int(bool(value))
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(task_id)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE pipeline_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(task_id)
            business = {key: value for key, value in changes.items() if key not in {"worker_pid", "timing_json"}}
            if business:
                row = connection.execute("SELECT stage,message,revision FROM pipeline_tasks WHERE task_id=?", (task_id,)).fetchone()
                self._insert_event(connection, task_id, row["stage"], "error" if changes.get("error") else "info",
                    row["message"], {"event_type": "state_changed", "revision": row["revision"], "changes": business})
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
                if connection.execute("SELECT 1 FROM ir_input_pins WHERE owner_id=? LIMIT 1", (row["task_id"],)).fetchone():
                    continue  # Frozen inputs require an explicit version transition.
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

    def pin_ir(self, owner_id: str, ir_run_id: str) -> None:
        from esg_v2.storage.package_layout import require_package_dir_name
        require_package_dir_name(ir_run_id)
        with self._lock, self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO ir_input_pins VALUES (?,?,?)", (owner_id, ir_run_id, utc_now()))

    def pinned_ir_references(self, run_ids: list[str] | None = None) -> set[str]:
        with self._connect() as connection:
            values = {str(row[0]) for row in connection.execute("SELECT DISTINCT ir_run_id FROM ir_input_pins")}
        return values & set(run_ids) if run_ids is not None else values

    def record_outcome(self, task_id: str, result: dict[str, Any] | None = None) -> PipelineTask:
        task = self.get(task_id)
        outcome = stage_outcome(task, result)
        return self.update(task_id, outcome_json=outcome.model_dump_json())

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
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute(
                "SELECT task_id,worker_pid,worker_started,native_job_id FROM pipeline_tasks WHERE status = 'running'"
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows if not self._row_worker_alive(row)]
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                connection.execute(
                    f"""
                    UPDATE pipeline_tasks
                    SET status='interrupted',stage='interrupted',message=?,worker_pid=NULL,worker_started=NULL,
                        error=?,finished_at=?,updated_at=?,revision=revision+1 WHERE task_id IN ({placeholders})
                    """,
                    ("Worker is no longer alive; resume from saved checkpoints", "Previous worker disappeared before reporting a terminal result", now, now, *task_ids),
                )
                for task_id in task_ids:
                    self._insert_event(connection, task_id, 'interrupted', 'warning',
                        '上次执行进程已退出；检查点与成果保留', {'event_type':'state_changed','status':'interrupted'})
        return task_ids

    @staticmethod
    def process_started(pid: int) -> str:
        try:
            output = subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="], text=True, timeout=2).strip()
            birth, state = output.rsplit(None, 1)
            return "" if state.startswith("Z") else birth.strip()
        except (subprocess.SubprocessError, OSError):
            return ""

    @classmethod
    def _row_worker_alive(cls, row) -> bool:
        pid = row["worker_pid"]
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        if row["worker_started"]:
            return cls.process_started(pid) == row["worker_started"]
        # Older queue rows have no birth identity. Only reconnect a command
        # naming this exact task/job; never assume an arbitrary reused PID owns it.
        try:
            command = subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True, timeout=2)
            return row["task_id"] in command or bool(row["native_job_id"] and row["native_job_id"] in command)
        except (subprocess.SubprocessError, OSError):
            return False

    def worker_alive(self, task_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT task_id,worker_pid,worker_started,native_job_id FROM pipeline_tasks WHERE task_id=?", (task_id,)).fetchone()
        return bool(row and self._row_worker_alive(row))

    @staticmethod
    def _ir_references(payload: dict[str, Any], task_type: str, operation: str) -> set[str]:
        if task_type == PipelineTaskType.TARGETED_EXTRACTION.value:
            value = payload.get("ir_run_id") or (payload.get("input") or {}).get("ir_run_id")
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
            self._insert_event(connection, task_id, stage, level, message, detail)

    @staticmethod
    def _insert_event(connection, task_id, stage, level, message, detail=None):
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
                    json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None,
                ),
        )

    def events(self, task_id: str | None, after: int = 0, *, limit: int = 1000) -> list[dict[str, Any]]:
        if task_id is not None:
            self.get(task_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pipeline_events
                WHERE (task_id = ? OR ? IS NULL) AND event_id > ? ORDER BY event_id LIMIT ?
                """,
                (task_id, task_id, after, max(1, min(limit, 2000))),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "schema_version": "execution-event-v1",
                "task_id": row["task_id"],
                "created_at": row["created_at"],
                "stage": row["stage"],
                "stage_label": stage_label(row["stage"]),
                "event_type": "raw_log" if row["level"] == "debug" else "business",
                "level": row["level"],
                "message": row["message"],
                "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
            }
            for row in rows
        ]

    @staticmethod
    def _record(row: sqlite3.Row) -> PipelineTask:
        task = PipelineTask(
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
            outcome=json.loads(row["outcome_json"]) if "outcome_json" in row.keys() and row["outcome_json"] else None,
            progress=json.loads(row["progress_json"]) if "progress_json" in row.keys() and row["progress_json"] else None,
            timing=json.loads(row["timing_json"]) if "timing_json" in row.keys() and row["timing_json"] else {},
            error_detail=json.loads(row["error_detail_json"]) if "error_detail_json" in row.keys() and row["error_detail_json"] else None,
            revision=row["revision"] if "revision" in row.keys() else 0,
            archived_at=row["archived_at"] if "archived_at" in row.keys() else None,
            archive_reason=row["archive_reason"] if "archive_reason" in row.keys() else None,
        )
        task.timing.queue_wait_seconds = elapsed_seconds(task.created_at, task.started_at or task.finished_at)
        if task.started_at:
            task.timing.wall_elapsed_seconds = elapsed_seconds(task.started_at, task.finished_at)
        if task.progress:
            task.progress.status = task.status.value
        if task.error and not task.error_detail:
            task.error_detail = classify_error(task.error, task_type=task.task_type.value)
        outcome = task.outcome or stage_outcome(task)
        task.available_actions = list(outcome.available_actions)
        from esg_v2.control_plane.shared_contracts import AvailableAction
        if task.archived_at:
            task.available_actions = [AvailableAction(action="restore", label="恢复历史记录",
                endpoint=f"/api/pipeline/tasks/{task.task_id}/restore", scope={"task_id": task.task_id})]
        elif task.status.value not in {"running", "queued"}:
            task.available_actions.append(AvailableAction(action="archive", label="归档历史记录",
                endpoint=f"/api/pipeline/tasks/{task.task_id}/archive", scope={"task_id": task.task_id}))
        return task
