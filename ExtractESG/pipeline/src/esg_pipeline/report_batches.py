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
                CREATE INDEX IF NOT EXISTS idx_extraction_session_replacements
                    ON clause_extraction_plans(json_extract(body,'$.request.replaces_session_id'))
                    WHERE json_extract(body,'$.request.replaces_session_id') IS NOT NULL;
            """)
            if "submitted_count" not in {row["name"] for row in db.execute("PRAGMA table_info(report_extraction_batches)")}:
                db.execute("ALTER TABLE report_extraction_batches ADD COLUMN submitted_count INTEGER")

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
                       SELECT p.id FROM clause_extraction_plans p
                       WHERE json_extract(body,'$.batch_id')=? AND json_extract(body,'$.status')=?
                         AND NOT EXISTS (
                           SELECT 1 FROM extraction_provider_pauses pause
                           WHERE pause.batch_id=json_extract(p.body,'$.batch_id')
                             AND pause.provider IN (json_extract(p.body,'$.request.model_selection.provider'),
                                                    json_extract(p.body,'$.request.document_model_selection.provider'))
                             AND (pause.session_id<>p.id OR NOT coalesce(json_extract(p.body,'$.resume_requested'),0))
                         )
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
                """INSERT INTO report_extraction_batches
                   (id,submission_key,request_digest,name,created_at,submitted_count) VALUES (?,?,?,?,?,?)""",
                (batch_id, request.submission_key, digest(request.model_dump(mode="json")), request.name, now, len(states)),
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
            """SELECT b.id,b.name,b.created_at,b.submitted_count,count(p.id),
                      coalesce(sum(json_extract(p.body,'$.status') IN (?,?,?)),0),
                      coalesce(sum(json_extract(p.body,'$.status')=?),0)
               FROM report_extraction_batches b
               LEFT JOIN clause_extraction_plans p ON json_extract(p.body,'$.batch_id')=b.id
               WHERE (? IS NULL OR b.id=?)
               GROUP BY b.id,b.name,b.created_at ORDER BY b.rowid DESC""",
            (*ACTIVE_STATUSES, WAITING_FOR_WINDOW, batch_id, batch_id),
        )
        pauses = {}
        for entry in db.execute("""SELECT batch_id,provider,session_id,category,phase
                    FROM extraction_provider_pauses WHERE (? IS NULL OR batch_id=?) ORDER BY provider""",
                                (batch_id, batch_id)):
            pause = dict(entry)
            pauses.setdefault(pause.pop("batch_id"), []).append(pause)
        return [
            ExtractionBatchSummary(batch_id=key, name=name, created_at=created,
                                   window_size=REPORT_WINDOW_SIZE, total_count=total,
                                   active_count=active, waiting_count=waiting,
                                   finished_count=total-active-waiting, submitted_count=submitted,
                                   provider_pauses=pauses.get(key, []))
            for key, name, created, submitted, total, active, waiting in rows
        ]

    @staticmethod
    def release_provider(db, batch_id, session_id):
        return db.execute("DELETE FROM extraction_provider_pauses WHERE batch_id=? AND session_id=?",
                          (batch_id, session_id)).rowcount

    def provider_paused(self, batch_id, providers, *, resuming_session=None):
        with self.store._connect() as db:
            return any(row[0] in providers and row[1] != resuming_session for row in db.execute(
                "SELECT provider,session_id FROM extraction_provider_pauses WHERE batch_id=?", (batch_id,),
            ))

    @staticmethod
    def replacements(db, source_ids):
        attempts = {}
        for source_id, attempt_id in db.execute(
            """SELECT json_extract(body,'$.request.replaces_session_id'),id
               FROM clause_extraction_plans
               WHERE json_extract(body,'$.request.replaces_session_id') IN (SELECT value FROM json_each(?))
               ORDER BY rowid""",
            (json.dumps(source_ids),),
        ):
            attempts.setdefault(source_id, []).append(attempt_id)
        return attempts

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
                          json_extract(p.body,'$.admitted_at'),
                          json_extract(p.body,'$.request.replaces_session_id')
                   FROM clause_extraction_plans p
                   LEFT JOIN pipeline_tasks t ON t.task_id=json_extract(p.body,'$.queue_task_id')
                   WHERE json_extract(p.body,'$.batch_id')=?
                   ORDER BY json_extract(p.body,'$.batch_position')""",
                (key,),
            ).fetchall()
            replacements = self.replacements(db, [row[0] for row in rows])
        return ExtractionBatch(
            **summaries[0].model_dump(),
            members=[ExtractionBatchMember(session_id=session, name=name, position=position,
                                           status=status, admitted_at=admitted, replaces_session_id=source,
                                           replacement_session_ids=replacements.get(session, []))
                     for session, name, position, status, admitted, source in rows],
        )
