from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from extract_esg.contracts.cloud import CloudTaskResult
from extract_esg.contracts.document import DocumentIR
from extract_esg.contracts.evidence import EvidencePacket
from extract_esg.contracts.extraction import DisclosureObject


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    artifact_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    document_ir_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    report_id TEXT NOT NULL,
    page_index INTEGER NOT NULL,
    native_text TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL,
    PRIMARY KEY (report_id, page_index),
    FOREIGN KEY (report_id) REFERENCES reports(report_id)
);

CREATE TABLE IF NOT EXISTS evidence_packets (
    packet_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    packet_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(report_id)
);

CREATE TABLE IF NOT EXISTS disclosures (
    disclosure_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    disclosure_type TEXT NOT NULL,
    raw_label TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL,
    disclosure_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(report_id)
);

CREATE TABLE IF NOT EXISTS cloud_task_results (
    request_id TEXT PRIMARY KEY,
    report_id TEXT,
    model_id TEXT NOT NULL,
    task_type TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class SqliteStore:
    """Local development persistence.

    This is the local-first database service. It intentionally mirrors the
    domain boundaries so it can later be replaced by Postgres repositories.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def save_document(self, document: DocumentIR) -> None:
        self.init_schema()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reports(report_id, artifact_path, sha256, page_count, document_ir_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document.report_id,
                    str(document.artifact_path),
                    document.sha256,
                    len(document.pages),
                    document.model_dump_json(),
                ),
            )
            for page in document.pages:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO pages(report_id, page_index, native_text, quality_flags_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        document.report_id,
                        page.page_ref.page_index,
                        page.native_text,
                        json.dumps(page.quality_flags, ensure_ascii=False),
                    ),
                )

    def save_packets(self, packets: list[EvidencePacket]) -> None:
        self.init_schema()
        with self.connect() as conn:
            for packet in packets:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO evidence_packets(packet_id, report_id, task_type, packet_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (packet.id, packet.report_id, packet.task_type, packet.model_dump_json()),
                )

    def save_disclosures(self, disclosures: list[DisclosureObject]) -> None:
        self.init_schema()
        with self.connect() as conn:
            for disclosure in disclosures:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO disclosures(
                        disclosure_id, report_id, disclosure_type, raw_label, raw_text,
                        evidence_ids_json, quality_flags_json, disclosure_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        disclosure.id,
                        disclosure.report_id,
                        disclosure.disclosure_type,
                        disclosure.raw_label,
                        disclosure.raw_text,
                        json.dumps(disclosure.evidence_ids, ensure_ascii=False),
                        json.dumps(disclosure.quality_flags, ensure_ascii=False),
                        disclosure.model_dump_json(),
                    ),
                )

    def save_cloud_result(self, result: CloudTaskResult, *, report_id: str | None = None, task_type: str | None = None) -> None:
        self.init_schema()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cloud_task_results(request_id, report_id, model_id, task_type, result_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (result.request_id, report_id, result.model_id, task_type, result.model_dump_json()),
            )

    def list_reports(self) -> list[dict[str, Any]]:
        self.init_schema()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.report_id,
                    r.artifact_path,
                    r.sha256,
                    r.page_count,
                    r.created_at,
                    COUNT(DISTINCT p.packet_id) AS packet_count,
                    COUNT(DISTINCT d.disclosure_id) AS disclosure_count
                FROM reports r
                LEFT JOIN evidence_packets p ON p.report_id = r.report_id
                LEFT JOIN disclosures d ON d.report_id = r.report_id
                GROUP BY r.report_id
                ORDER BY r.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        self.init_schema()
        with self.connect() as conn:
            report = conn.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
            if report is None:
                return None
            pages = conn.execute(
                "SELECT page_index, native_text, quality_flags_json FROM pages WHERE report_id = ? ORDER BY page_index",
                (report_id,),
            ).fetchall()
            disclosures = conn.execute(
                "SELECT disclosure_id, disclosure_type, raw_label, raw_text, quality_flags_json FROM disclosures WHERE report_id = ?",
                (report_id,),
            ).fetchall()
        return {
            "report": dict(report),
            "pages": [dict(row) for row in pages],
            "disclosures": [dict(row) for row in disclosures],
        }

