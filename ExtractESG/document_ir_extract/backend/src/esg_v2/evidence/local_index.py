from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from esg_v2.evidence.contracts import EvidenceAtom


@dataclass(frozen=True)
class LocalSearchHit:
    atom_id: str
    lane: str
    rank: int
    raw_score: float
    matched_terms: tuple[str, ...]


class LocalEvidenceIndex:
    """Portable SQLite FTS5 index. It never opens a network connection."""

    def __init__(self, path: Path):
        self.path = path

    def build(self, atoms: list[EvidenceAtom]) -> dict[str, object]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "CREATE TABLE atoms (atom_id TEXT PRIMARY KEY, atom_type TEXT NOT NULL, "
                "page_index INTEGER NOT NULL, source_text TEXT NOT NULL, search_text TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE atom_fts USING fts5(atom_id UNINDEXED, search_text, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            trigram_available = True
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE atom_trigram USING fts5(atom_id UNINDEXED, search_text, tokenize='trigram')"
                )
            except sqlite3.OperationalError:
                trigram_available = False
            rows = [
                (
                    atom.atom_id,
                    atom.atom_type,
                    min(atom.location.page_indices),
                    atom.source_text,
                    atom.search_text,
                )
                for atom in atoms
            ]
            connection.executemany("INSERT INTO atoms VALUES (?, ?, ?, ?, ?)", rows)
            connection.executemany(
                "INSERT INTO atom_fts(atom_id, search_text) VALUES (?, ?)",
                [(row[0], row[4]) for row in rows],
            )
            if trigram_available:
                connection.executemany(
                    "INSERT INTO atom_trigram(atom_id, search_text) VALUES (?, ?)",
                    [(row[0], row[4]) for row in rows],
                )
            connection.commit()
            return {
                "atom_count": len(rows),
                "fts5": True,
                "trigram": trigram_available,
                "sqlite_version": sqlite3.sqlite_version,
            }
        finally:
            connection.close()

    @staticmethod
    def capabilities() -> dict[str, object]:
        connection = sqlite3.connect(":memory:")
        fts5 = True
        trigram = True
        try:
            try:
                connection.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(text)")
            except sqlite3.OperationalError:
                fts5 = False
            try:
                connection.execute("CREATE VIRTUAL TABLE trigram_probe USING fts5(text, tokenize='trigram')")
            except sqlite3.OperationalError:
                trigram = False
        finally:
            connection.close()
        return {
            "sqlite_version": sqlite3.sqlite_version,
            "fts5": fts5,
            "trigram": trigram,
        }

    def search(self, terms: Iterable[str], *, top_k: int = 30) -> list[LocalSearchHit]:
        normalized_terms = self._terms(terms)
        if not normalized_terms:
            return []
        connection = sqlite3.connect(self.path)
        try:
            lanes: list[LocalSearchHit] = []
            lanes.extend(self._exact_lane(connection, normalized_terms, top_k))
            lanes.extend(self._fts_lane(connection, "atom_fts", "bm25", normalized_terms, top_k))
            if self._table_exists(connection, "atom_trigram"):
                lanes.extend(self._fts_lane(connection, "atom_trigram", "trigram", normalized_terms, top_k))
            return lanes
        finally:
            connection.close()

    @staticmethod
    def _exact_lane(
        connection: sqlite3.Connection,
        terms: list[str],
        top_k: int,
    ) -> list[LocalSearchHit]:
        rows = connection.execute("SELECT atom_id, search_text FROM atoms").fetchall()
        scored = []
        for atom_id, text in rows:
            lowered = text.casefold()
            matched = tuple(term for term in terms if term.casefold() in lowered)
            if matched:
                score = sum(max(1.0, len(term) / 4) for term in matched)
                scored.append((atom_id, score, matched))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [
            LocalSearchHit(atom_id=item[0], lane="exact", rank=index + 1, raw_score=item[1], matched_terms=item[2])
            for index, item in enumerate(scored[:top_k])
        ]

    @staticmethod
    def _fts_lane(
        connection: sqlite3.Connection,
        table: str,
        lane: str,
        terms: list[str],
        top_k: int,
    ) -> list[LocalSearchHit]:
        aggregate: dict[str, tuple[float, set[str]]] = {}
        for term in terms:
            if table == "atom_trigram" and len(term) < 3:
                continue
            query = '"' + term.replace('"', '""') + '"'
            try:
                rows = connection.execute(
                    f"SELECT atom_id, bm25({table}) FROM {table} WHERE {table} MATCH ? "
                    f"ORDER BY bm25({table}) LIMIT ?",
                    (query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for atom_id, score in rows:
                current_score, matched = aggregate.get(atom_id, (0.0, set()))
                aggregate[atom_id] = (current_score + abs(float(score)), matched | {term})
        ordered = sorted(aggregate.items(), key=lambda item: (-item[1][0], item[0]))[:top_k]
        return [
            LocalSearchHit(
                atom_id=atom_id,
                lane=lane,
                rank=index + 1,
                raw_score=score,
                matched_terms=tuple(sorted(matched)),
            )
            for index, (atom_id, (score, matched)) in enumerate(ordered)
        ]

    @staticmethod
    def _terms(terms: Iterable[str]) -> list[str]:
        output = []
        for term in terms:
            normalized = re.sub(r"\s+", " ", str(term)).strip()
            if len(normalized) >= 2:
                output.append(normalized)
        return list(dict.fromkeys(output))

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
