"""Document evidence port. Requests contain lexical intent and object references."""

from __future__ import annotations
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from esg_data.hashing import digest
from esg_contracts.evidence import (
    DocumentReference,
    SearchIntent,
    ObjectReference,
    EvidenceFrame,
    EvidenceRow,
    EvidenceCell,
    TextEvidence,
)
from .source import DocumentIrLoader
from .physical import build_table_view, composed_tables, clean_text
from .report_period import detect_reporting_period
from .literals import text_spans, numeric_rows

CHUNK_ROWS = 32


def _box(row):
    value = row.get("bbox")
    if isinstance(value, dict):
        return {key: value.get(key) for key in ("x0", "y0", "x1", "y1")}
    if isinstance(value, list) and len(value) == 4:
        return dict(zip(("x0", "y0", "x1", "y1"), value))
    return None


def _fold(text):
    from esg_data.normalize import fold

    return re.sub(r"\s+", "", fold(text or ""))


def _terms(text):
    return [
        part.strip()
        for part in re.split(r"[。；;，,、|\n]", text)
        if len(part.strip()) >= 2
    ]


class DocumentEvidenceService:
    def __init__(self, resolver, resource_lookup=None):
        self.resolve = resolver
        self.resource_lookup = resource_lookup

    def _document(self, ir_run_id):
        path = Path(self.resolve(ir_run_id)).resolve()
        signature = tuple(
            (p.relative_to(path).as_posix(), p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(path.rglob("*.json"))
            if not any(part.startswith(".") for part in p.relative_to(path).parts)
        )
        return self._load(str(path), signature)

    @staticmethod
    @lru_cache(maxsize=2)
    def _load(path, revision_signature):
        # Structural IR caching is document-local; it stores no model interpretation.
        ir = DocumentIrLoader().load(Path(path), require_ready=True)
        pages = {p["page"]["page_index"]: p for p in ir.pages}
        tables = {}
        raw_tables = {}
        for raw in composed_tables(ir.tables):
            view = build_table_view(raw, pages.get(raw.get("page_index")))
            raw_tables[raw["table_id"]] = raw
            if view:
                tables[view.table_id] = view
        texts = {}
        for page in ir.pages:
            index = page["page"]["page_index"]
            for block in page.get("blocks") or []:
                if block.get("block_type") in {
                    "table_markdown",
                    "table",
                    "figure",
                    "image",
                    "header",
                    "footer",
                }:
                    continue
                text = block.get("text") or ""
                if text.strip() and block.get("block_id"):
                    texts[block["block_id"]] = TextEvidence(
                        object_id=block["block_id"],
                        text=text,
                        page_index=index,
                        bbox=_box(block),
                    )
        fingerprint = hashlib.sha256()
        # Source identity covers saved IR evidence, independent of its directory name.
        for source in sorted(Path(path).rglob("*.json")):
            if any(part.startswith(".") for part in source.relative_to(path).parts):
                continue
            fingerprint.update(source.relative_to(path).as_posix().encode())
            fingerprint.update(source.read_bytes())
        period = detect_reporting_period(
            ir.manifest,
            ir.pages,
            header_years=[y for v in tables.values() for y in v.header_years()],
        )
        document = DocumentReference(
            document_id=ir.document_id,
            ir_run_id=ir.run_id,
            ir_revision=ir.ir_revision,
            content_digest=fingerprint.hexdigest(),
            source_sha256=ir.manifest.get("source", {}).get("pdf_sha256"),
            reporting_year=period.year,
            reporting_period_raw=period.evidence or (str(period.year) if period.year else None),
            period_context={"basis": period.basis, "evidence": period.evidence,
                "source_id": period.source_id,
                "page_index": next((page["page"]["page_index"] for page in ir.pages
                    if any(block.get("block_id") == period.source_id for block in page.get("blocks", []))), None),
                "inheritance": "explicit_reference_only"},
            report_resource=ir.manifest.get("report_resource"),
            label=ir.manifest.get("document_label"),
            limited_evidence=bool(
                ir.manifest.get("evidence_policy", {}).get("mode") == "limited"
            ),
            search_scope={
                "available_pages": sorted(pages),
                "source_page_count": ir.manifest.get("source_page_count") or ir.manifest.get("page_coverage", {}).get("source_page_count"),
                "mode": ir.manifest.get("page_coverage", {}).get("mode", "unknown"),
                "report_wide_absence_proven": False,
            },
        )
        return document, tables, raw_tables, texts

    def document(self, ir_run_id):
        reference = self._document(ir_run_id)[0]
        if self.resource_lookup:
            resource = self.resource_lookup(reference)
            if resource:
                reference = reference.model_copy(update={"report_resource": resource})
        return reference

    def search(self, ir_run_id, intent):
        return self.search_page(ir_run_id, intent).objects

    def search_page(self, ir_run_id, intent):
        intent = SearchIntent.model_validate(intent)
        document, tables, raw, texts = self._document(ir_run_id)
        terms = list(
            dict.fromkeys(
                part for term in intent.positive_terms for part in _terms(term)
            )
        )
        results = []
        for key, table in raw.items():
            view = tables.get(key)
            text = " ".join(
                [
                    str(table.get("title") or ""),
                    *(clean_text(c.get("text")) for c in table.get("cells", [])),
                ]
            )
            direct_score = 8 * self._score(terms, text) + 20 * self._score(
                intent.exact_terms, text
            )
            context = (
                " ".join(
                    filter(
                        None,
                        [
                            view.title,
                            view.caption,
                            view.heading_above,
                            *[n.text for n in view.notes],
                        ],
                    )
                )
                if view
                else ""
            )
            score = (
                direct_score
                + 0.2 * self._score(terms, context)
                + self._score(intent.structural_anchors, text)
            ) * (
                4
                if intent.source_priority and intent.source_priority[0] == "table"
                else 1
            )
            score = self._intent_score(intent, text, score)
            if score:
                rows = max(1, int(table.get("row_count") or 1))
                results.append(
                    ObjectReference(
                        object_id=key,
                        kind="table",
                        page_index=table.get("page_index", 0),
                        score=score,
                        chunk_count=max(1, math.ceil(rows / CHUNK_ROWS)),
                    )
                )
        for key, item in texts.items():
            if intent.data_kind == "quantitative":
                literals = [cell for row in numeric_rows(item) for cell in row.cells if cell.candidate_kind in {"measurement", "unresolved"}]
                if not numeric_rows(item):
                    continue
            score = (
                8 * self._score(terms, item.text)
                + 20 * self._score(intent.exact_terms, item.text)
            ) * (
                4
                if intent.source_priority and intent.source_priority[0] == "text"
                else 1
            )
            score = self._intent_score(intent, item.text, score)
            if intent.data_kind == "quantitative" and "percentage" in intent.expected_candidate_types and any("%" in cell.raw_text or "％" in cell.raw_text for cell in literals):
                score *= 1.5
            if score:
                results.append(
                    ObjectReference(
                        object_id=key,
                        kind="text",
                        page_index=item.page_index,
                        score=score,
                        chunk_count=max(1, math.ceil(len(item.text) / 4000)),
                    )
                )
        from esg_contracts.evidence import ObjectSearchPage
        from esg_data.hashing import digest
        import base64
        ranked = sorted(results, key=lambda r: (-r.score, r.page_index, r.object_id))
        query = digest([document.evidence_identity(), intent.model_dump(mode="json", exclude={"cursor"})])
        offset = 0
        if intent.cursor:
            try:
                cursor = json.loads(base64.urlsafe_b64decode(intent.cursor.encode()))
                if cursor["query"] != query or type(cursor["offset"]) is not int or cursor["offset"] < 0:
                    raise ValueError("Cursor belongs to a different query or document revision")
                offset = cursor["offset"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("Invalid object-search cursor") from exc
        objects = ranked[offset:offset + intent.limit]
        following = offset + len(objects)
        cursor = base64.urlsafe_b64encode(json.dumps({"query":query,"offset":following}).encode()).decode() if following < len(ranked) else None
        return ObjectSearchPage(objects=objects, total=len(ranked), returned=len(objects), next_cursor=cursor,
            query_digest=query, scope=document.search_scope or {})

    @staticmethod
    def _intent_score(intent, text, score):
        if score <= 0:
            return score
        if DocumentEvidenceService._score(intent.excluded_object_terms, text):
            return 0
        if any(
            not DocumentEvidenceService._score(group, text)
            for group in intent.required_term_groups
        ):
            return 0
        groups = [*intent.topic_term_groups, *intent.role_term_groups]
        score += sum(
            2 for group in groups if DocumentEvidenceService._score(group, text)
        )
        score += 0.1 * DocumentEvidenceService._score(intent.context_terms, text)
        # An exclusion in an adjacent table row is not proof that the whole
        # object is irrelevant. Deprioritize it; clause reading makes the decision.
        if DocumentEvidenceService._score(intent.negative_terms, text):
            score *= 0.25
        return score

    @staticmethod
    def _score(terms, text):
        from esg_data.normalize import fold
        import unicodedata

        spaced = unicodedata.normalize("NFKC", fold(text))
        compact = re.sub(r"\s+", "", spaced)
        score = 0
        for term in terms:
            normalized = unicodedata.normalize("NFKC", fold(term)).strip()
            if not normalized:
                continue
            if normalized.isascii():
                pattern = (
                    r"(?<![a-z0-9])"
                    + r"\s*".join(re.escape(part) for part in normalized.split())
                    + r"(?![a-z0-9])"
                )
                hit = bool(re.search(pattern, spaced))
            else:
                hit = re.sub(r"\s+", "", normalized) in compact
            if hit:
                score += min(6, len(term) / 3)
        return score

    def read(self, ir_run_id, reference, chunk_index=0):
        reference = ObjectReference.model_validate(reference)
        document, tables, raw_tables, texts = self._document(ir_run_id)
        if reference.kind == "text":
            item = texts.get(reference.object_id)
            if item is None:
                raise KeyError(reference.object_id)
            count = max(1, math.ceil(len(item.text) / 4000))
            if chunk_index < 0 or chunk_index >= count:
                raise ValueError("Invalid text evidence cursor")
            item = item.model_copy(
                update={
                    "text": item.text[chunk_index * 4000 : (chunk_index + 1) * 4000],
                    "char_start": chunk_index * 4000,
                }
            )
            body = dict(
                chunk_index=chunk_index,
                chunk_count=count,
                object_id=item.object_id,
                kind="text",
                page_index=item.page_index,
                texts=[span.model_dump(mode="json") for span in text_spans(item)],
                rows=[row.model_dump(mode="json") for row in numeric_rows(item)],
            )
        else:
            raw = raw_tables.get(reference.object_id)
            if raw is None:
                raise KeyError(reference.object_id)
            view = tables.get(reference.object_id)
            count = max(
                1, math.ceil(max(1, int(raw.get("row_count") or 1)) / CHUNK_ROWS)
            )
            if chunk_index < 0 or chunk_index >= count:
                raise ValueError("Invalid evidence frame cursor")
            start, end = chunk_index * CHUNK_ROWS, (chunk_index + 1) * CHUNK_ROWS
            columns = (
                {f"c{key}": value for key, value in view.column_paths.items()}
                if view
                else {}
            )
            if not columns:
                columns = {
                    f"c{c.get('col_index', 0)}": [c.get("text") or ""]
                    for c in raw.get("cells", [])
                    if c.get("is_header")
                }
            physical = {c.get("cell_id"): c for c in raw.get("cells", [])}

            def source(cell):
                return TextEvidence(
                    object_type="cell",
                    object_id=cell["cell_id"],
                    text=cell.get("text") or "",
                    page_index=cell.get("page_index", reference.page_index),
                    bbox=_box(cell),
                )

            header_cells = [
                c
                for c in raw.get("cells", [])
                if c.get("is_header")
                or (view and c.get("row_index") in view.header_rows)
            ]
            column_sources = {
                f"c{col}": [
                    source(c)
                    for c in header_cells
                    if c.get("text")
                    and c.get("col_index", 0)
                    <= col
                    < c.get("col_index", 0) + c.get("col_span", 1)
                ]
                for col in range(int(raw.get("column_count") or 0))
            }
            rows = []
            represented = set()
            if view:
                for line in view.lines:
                    if not start <= line.row < end:
                        continue
                    rows.append(
                        EvidenceRow(
                            row_role="data",
                            row_id=line.rid,
                            label=line.label,
                            section=line.section,
                            unit_raw=line.unit,
                            label_sources=[
                                source(c)
                                for c in physical.values()
                                if c.get("text")
                                and c.get("row_index") == line.row
                                and c.get("col_index") in view.label_cols
                            ],
                            unit_sources=[
                                source(c)
                                for c in physical.values()
                                if c.get("text")
                                and c.get("row_index") == line.row
                                and c.get("col_index") == view.unit_col
                            ],
                            cells=[
                                EvidenceCell(
                                    cell_id=cell.cell_id,
                                    column_id=f"c{col}",
                                    raw_text=physical.get(cell.cell_id, {}).get(
                                        "text", cell.text
                                    ),
                                    page_index=physical.get(cell.cell_id, {}).get(
                                        "page_index", reference.page_index
                                    ),
                                    bbox=_box(physical.get(cell.cell_id, {})),
                                )
                                for col, cell in line.values.items()
                                if cell.text.strip()
                            ],
                        )
                    )
                    represented.add(line.row)
            # Preserve rows whose raw literals were not recognized by the numeric parser.
            by_row = {}
            for cell in raw.get("cells", []):
                index = cell.get("row_index", 0)
                if start <= index < end:
                    by_row.setdefault(index, []).append(cell)
            for index, cells in sorted(by_row.items()):
                if index in represented:
                    continue
                cells = sorted(cells, key=lambda c: c.get("col_index", 0))
                if len(cells) < 2:
                    continue
                label = clean_text(cells[0].get("text"))
                values = [
                    EvidenceCell(
                        cell_id=c["cell_id"],
                        column_id=f"c{c.get('col_index', 0)}",
                        raw_text=c.get("text") or "",
                        page_index=c.get("page_index", reference.page_index),
                        bbox=_box(c),
                    )
                    for c in cells[1:]
                    if c.get("cell_id") and (c.get("text") or "").strip()
                ]
                if values:
                    rows.append(
                        EvidenceRow(
                            row_role="header"
                            if view and index in view.header_rows
                            else "unknown",
                            row_id=f"{reference.object_id}:raw-row:{index}",
                            label=label,
                            label_sources=[source(cells[0])],
                            cells=values,
                        )
                    )
            notes = (
                [texts[n.block_id] for n in view.notes if n.block_id in texts]
                if view
                else []
            )
            for cell in raw.get("cells", []):
                if not start <= cell.get("row_index", 0) < end:
                    continue
                text = cell.get("text") or ""
                if text.strip() and cell.get("cell_id"):
                    notes.append(
                        TextEvidence(
                            object_type="cell",
                            object_id=cell["cell_id"],
                            text=text,
                            page_index=cell.get("page_index", reference.page_index),
                            bbox=_box(cell),
                        )
                    )
            body = dict(
                object_id=reference.object_id,
                kind="table",
                page_index=reference.page_index,
                title=(view.title or view.caption or view.heading_above)
                if view
                else raw.get("title"),
                columns=columns,
                column_sources={
                    k: [s.model_dump(mode="json") for s in v]
                    for k, v in column_sources.items()
                },
                title_sources=[
                    t.model_dump(mode="json")
                    for t in texts.values()
                    if t.page_index == reference.page_index
                    and view
                    and (view.title or view.caption or view.heading_above)
                    and (view.title or view.caption or view.heading_above) in t.text
                ],
                rows=[r.model_dump(mode="json") for r in rows],
                texts=[
                    span.model_dump(mode="json")
                    for n in notes
                    for span in text_spans(n)
                ],
                chunk_index=chunk_index,
                chunk_count=count,
            )
        return EvidenceFrame.model_validate({**body, "digest": digest(body)})
