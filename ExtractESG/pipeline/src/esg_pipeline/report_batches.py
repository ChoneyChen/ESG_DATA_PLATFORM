"""Durable report admission in the same transaction as session transitions."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from esg_contracts.sessions import ExtractionBatch, ExtractionBatchMember, ExtractionBatchSummary
from esg_data.hashing import digest


REPORT_WINDOW_SIZE = 10
WAITING_FOR_WINDOW = "waiting_for_window"
ACTIVE_STATUSES = ("queued", "running", "cancelling")


class ReportBatches:
    def __init__(self, store):
        self.store = store
        with store._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS report_extraction_batches (
                    id TEXT PRIMARY KEY,
                    submission_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_extraction_batch_members
                    ON clause_extraction_plans(
                        json_extract(body,'$.batch_id'),
                        json_extract(body,'$.status'),
                        json_extract(body,'$.batch_position')
                    );
                CREATE INDEX IF NOT EXISTS idx_extraction_submission
                    ON clause_extraction_plans(json_extract(body,'$.request.submission_key'))
                    WHERE json_extract(body,'$.request.submission_key') IS NOT NULL;
            """)

    @staticmethod
    def refill(db, batch_id, now):
        occupied = db.execute(
            """SELECT count(*) FROM clause_extraction_plans
               WHERE json_extract(body,'$.batch_id')=?
                 AND json_extract(body,'$.status') IN (?,?,?)""",
            (batch_id, *ACTIVE_STATUSES),
        ).fetchone()[0]
        if occupied < REPORT_WINDOW_SIZE:
            db.execute(
                """UPDATE clause_extraction_plans
                   SET body=json_set(body,'$.status','queued','$.admitted_at',?,'$.updated_at',?)
                   WHERE id IN (
                       SELECT id FROM clause_extraction_plans
                       WHERE json_extract(body,'$.batch_id')=?
                         AND json_extract(body,'$.status')=?
                       ORDER BY json_extract(body,'$.batch_position')
                       LIMIT ?
                   )""",
                (now, now, batch_id, WAITING_FOR_WINDOW, REPORT_WINDOW_SIZE - occupied),
            )

    @staticmethod
    def _submission(db, request):
        row = db.execute(
            "SELECT id,request_digest FROM report_extraction_batches WHERE submission_key=?",
            (request.submission_key,),
        ).fetchone()
        if row:
            if row[1] != digest(request.model_dump(mode="json")):
                raise ValueError("批次提交编号对应的内容已变化，请使用新的提交编号。")
            return row[0]
        return None

    def submitted(self, request):
        with self.store._connect() as db:
            return self._submission(db, request)

    def create(self, request, states):
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._submission(db, request)
            if previous:
                return previous
            keys = [item.submission_key for item in request.sessions if item.submission_key]
            if len(set(keys)) != len(keys):
                raise ValueError("同一批次的报告提交编号必须互不相同。")
            if any(db.execute(
                "SELECT 1 FROM clause_extraction_plans WHERE json_extract(body,'$.request.submission_key')=?",
                (key,),
            ).fetchone() for key in keys):
                raise ValueError("报告提交编号已有一份会话，请使用新的编号。")
            batch_id = "extract-batch-" + uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO report_extraction_batches VALUES (?,?,?,?,?)",
                (batch_id, request.submission_key, digest(request.model_dump(mode="json")), request.name, now),
            )
            for position, state in enumerate(states, 1):
                state.update(batch_id=batch_id, batch_position=position, status=WAITING_FOR_WINDOW,
                             created_at=now, updated_at=now)
            db.executemany(
                "INSERT INTO clause_extraction_plans (id,body) VALUES (?,?)",
                [(state["session_id"], json.dumps(state, ensure_ascii=False)) for state in states],
            )
            self.refill(db, batch_id, now)
            return batch_id

    @staticmethod
    def _summaries(db, batch_id=None):
        rows = db.execute(
            """SELECT b.id,b.name,b.created_at,count(p.id),
                      coalesce(sum(json_extract(p.body,'$.status') IN (?,?,?)),0),
                      coalesce(sum(json_extract(p.body,'$.status')=?),0)
               FROM report_extraction_batches b
               LEFT JOIN clause_extraction_plans p ON json_extract(p.body,'$.batch_id')=b.id
               WHERE (? IS NULL OR b.id=?)
               GROUP BY b.id,b.name,b.created_at ORDER BY b.rowid DESC""",
            (*ACTIVE_STATUSES, WAITING_FOR_WINDOW, batch_id, batch_id),
        )
        return [
            ExtractionBatchSummary(batch_id=key, name=name, created_at=created,
                                   window_size=REPORT_WINDOW_SIZE, total_count=total,
                                   active_count=active, waiting_count=waiting,
                                   finished_count=total-active-waiting)
            for key, name, created, total, active, waiting in rows
        ]

    def list(self):
        with self.store._connect() as db:
            return self._summaries(db)

    def get(self, key):
        with self.store._connect() as db:
            db.execute("BEGIN")
            summaries = self._summaries(db, key)
            if not summaries:
                raise KeyError(key)
            rows = db.execute(
                """SELECT p.id,json_extract(p.body,'$.name'),json_extract(p.body,'$.batch_position'),
                          CASE WHEN json_extract(p.body,'$.status')='running' AND t.status<>'running'
                               THEN 'queued' ELSE json_extract(p.body,'$.status') END,
                          json_extract(p.body,'$.admitted_at')
                   FROM clause_extraction_plans p
                   LEFT JOIN pipeline_tasks t ON t.task_id=json_extract(p.body,'$.queue_task_id')
                   WHERE json_extract(p.body,'$.batch_id')=?
                   ORDER BY json_extract(p.body,'$.batch_position')""",
                (key,),
            ).fetchall()
        return ExtractionBatch(
            **summaries[0].model_dump(),
            members=[ExtractionBatchMember(session_id=session, name=name, position=position,
                                           status=status, admitted_at=admitted)
                     for session, name, position, status, admitted in rows],
        )
