"""Keyword retrieval of candidate source tables and text blocks per source group."""
from __future__ import annotations

import re
from dataclasses import dataclass

from esg_targeted.foundation.catalog import GROUPS, METHOD_TERMS, SourceGroup
from esg_targeted.foundation.tables import TableView, clean_text
from esg_targeted.foundation.textnorm import fold


@dataclass
class TableCandidate:
    view: TableView
    groups: dict[str, float]
    hits: dict[str, list[str]]

    @property
    def score(self) -> float:
        return max(self.groups.values()) if self.groups else 0.0


_ASCII_TERM = re.compile(r"^[a-z0-9.\s]{1,5}$")


def has_term(text: str, term: str) -> bool:
    """Short ASCII abbreviations (cod, nox, gj) must not match inside words."""
    if _ASCII_TERM.match(term):
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None
    return term in text


def _score(text: str, group: SourceGroup) -> tuple[float, list[str]]:
    strong = [kw for kw in group.strong if has_term(text, kw)]
    weak = [kw for kw in group.weak if has_term(text, kw)]
    if not strong:
        return 0.0, []
    return 3.0 * len(strong) + min(4, len(weak)), strong + weak


def score_table(view: TableView) -> TableCandidate:
    text = view.search_text()
    groups, hits = {}, {}
    for group in GROUPS:
        score, found = _score(text, group)
        if score >= 3:
            groups[group.key] = score
            hits[group.key] = found
    return TableCandidate(view, groups, hits)


def select_tables(views: list[TableView], *, per_group: int = 5, min_numeric_lines: int = 1) -> list[TableCandidate]:
    candidates = [score_table(v) for v in views if v.numeric_line_count >= min_numeric_lines]
    chosen: dict[str, TableCandidate] = {}
    for group in GROUPS:
        ranked = sorted(
            (c for c in candidates if group.key in c.groups),
            key=lambda c: (-c.groups[group.key], -c.view.numeric_line_count, c.view.page_index),
        )
        for candidate in ranked[:per_group]:
            chosen[candidate.view.table_id] = candidate
    ordered = sorted(chosen.values(), key=lambda c: (c.view.page_index, c.view.table_id))
    for candidate in ordered:
        # A table is understood once for every group it passed; groups whose
        # best tables are elsewhere still keep this table's rows mappable.
        candidate.groups = {k: v for k, v in candidate.groups.items()}
    return ordered


@dataclass
class BlockCandidate:
    block_id: str
    page_number: int
    text: str
    score: float


def select_blocks(pages: list[dict], terms: tuple[str, ...], *, require_number: bool, limit: int = 8,
                  max_chars: int = 420, exclude_tables: bool = True) -> list[BlockCandidate]:
    found = []
    for payload in pages:
        page = payload.get("page") or {}
        for block in payload.get("blocks") or []:
            if exclude_tables and block.get("block_type") in {"table_markdown", "table"}:
                continue
            text = clean_text(block.get("text"))
            if len(text) < 10:
                continue
            lowered = fold(text)
            hit = [t for t in terms if has_term(lowered, t)]
            if not hit:
                continue
            if require_number and not any(ch.isdigit() for ch in text):
                continue
            score = len(hit) + (1 if require_number and "%" in text else 0)
            found.append(BlockCandidate(block["block_id"], int(page.get("page_number") or 0), text[:max_chars], score))
    found.sort(key=lambda b: (-b.score, b.page_number))
    return found[:limit]
