"""Document evidence port. Requests contain lexical intent and object references."""

from __future__ import annotations
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from esg_data.hashing import digest
from esg_data.literals import parse_measure
from esg_data.units import is_unit_expression
from .search_ranking import query_terms, table_score, text_score
from esg_contracts.evidence import (
    DocumentReference,
    SearchIntent,
    ObjectReference,
    EvidenceFrame,
    EvidenceRow,
    EvidenceCell,
    TextEvidence,
    EvidenceContextLink,
)
from .source import DocumentIrLoader
from .physical import build_table_view, composed_tables, clean_text, column_header_sources, UNIT_HEADER_RE
from .report_period import detect_reporting_period
from .literals import text_spans, numeric_rows
from .text_geometry import native_text_geometry, NativeTextGeometry, measurement_context

CHUNK_ROWS = 32


def _box(row):
    value = row.get("bbox")
    if isinstance(value, dict):
        return {key: value.get(key) for key in ("x0", "y0", "x1", "y1")}
    if isinstance(value, list) and len(value) == 4:
        return dict(zip(("x0", "y0", "x1", "y1"), value))
    return None


def reference_columns(columns):
    """Printed standard codes and page references are not measured quantities."""
    return {column for column, parts in columns.items()
        if re.fullmatch(
            r"\s*(?:(?:GRI|ESRS|CSRD|SASB|ISSB|NSF|HKEX)"
            r"(?:\s+(?:standards?|disclosures?|references?|codes?|[A-Z]?\d[\d.\-]*))*"
            r"|页码|頁碼|参照|參照|references?|pages?(?:\s+(?:no\.?|number))?)\s*",
            " ".join(parts), re.I)}


def disclosure_inventory(source, cell, columns):
    """Type codes only under the actual disclosure column and standard row."""
    header = " ".join(cell.get("column_header_path") or
        columns.get(f"c{cell.get('col_index', 0)}", []))
    if not re.fullmatch(r"\s*disclosures?\s*", header, re.I):
        return None
    prefixes = set()
    for parent in cell.get("row_header_path", []):
        match = re.match(r"\s*([A-Za-z]+)\s+([A-Z]?\d[\d.]*)\b", parent)
        if match and reference_columns({"standard": [f"{match[1]} {match[2]}"]}):
            prefixes.add(match[2])
    if not prefixes:
        return None
    inventory = numeric_rows(source)
    return [row.model_copy(update={"cells": [value.model_copy(update={
        "candidate_kind": "identifier"}) if not row.unit_raw and any(
            re.fullmatch(re.escape(prefix) + r"-\d+(?:[.-]\d+)*", value.raw_text)
            for prefix in prefixes) else value for value in row.cells]})
        for row in inventory]


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
    @lru_cache(maxsize=16)
    def _load(path, revision_signature):
        # Structural IR caching is document-local; it stores no model interpretation.
        ir = DocumentIrLoader().load(Path(path), require_ready=True)
        pages = {p["page"]["page_index"]: p for p in ir.pages}
        tables = {}
        raw_tables = {}
        for raw in composed_tables(ir.tables, ir.logical_tables):
            view = build_table_view(raw, pages.get(raw.get("page_index")))
            raw_tables[raw["table_id"]] = raw
            if view:
                tables[view.table_id] = view
        texts = {}
        geometries = {}
        forensics_path = Path(path) / "observations/local-pdf/forensics.json"
        local_pages = {}
        if forensics_path.is_file():
            local_pages = {page["page_index"]: page for page in
                json.loads(forensics_path.read_text()).get("forensics", {}).get("pages", [])}
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
                    geometries[block["block_id"]] = native_text_geometry(
                        texts[block["block_id"]], block, local_pages.get(index, {}))
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
                "prepared_pages": sorted(set(pages) | set(ir.manifest.get("evidence_policy", {}).get("excluded_page_indices", []))),
                "source_page_count": ir.manifest.get("source_page_count") or ir.manifest.get("page_coverage", {}).get("source_page_count"),
                "mode": ir.manifest.get("page_coverage", {}).get("mode", "unknown"),
                "report_wide_absence_proven": False,
            },
        )
        object_digests = {}
        for key, raw in raw_tables.items():
            view = tables.get(key)
            object_digests[key] = digest([document.source_sha256 or document.document_id, key,
                [{field:cell.get(field) for field in ("cell_id","row_index","col_index","row_span","col_span","text","bbox","page_index")}
                    for cell in raw.get("cells", [])],
                {"title":view.title,"caption":view.caption,"heading":view.heading_above,
                 "columns":view.column_paths,"notes":[note.text for note in view.notes],
                 "lines":[(line.row,line.label,line.unit,line.section) for line in view.lines]} if view else raw.get("title")])
        object_digests.update({key:digest([document.source_sha256 or document.document_id,
            key,text.text,text.page_index,text.bbox,text.char_start]) for key,text in texts.items()})
        return document, tables, raw_tables, texts, object_digests, geometries, local_pages

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
        document, tables, raw, texts, object_digests, _, _ = self._document(ir_run_id)
        if intent.source_snapshot and intent.source_snapshot != document.snapshot_id:
            raise ValueError("Document snapshot changed; prepare a new extraction")
        terms = query_terms(intent.positive_terms)
        results = []
        for key, table in raw.items():
            view = tables.get(key)
            if intent.data_kind == "quantitative" and view and view.value_cols and not set(view.value_cols) - reference_columns(view.column_paths):
                continue
            score = table_score(intent, terms, table, view)
            if score:
                rows = max(1, int(table.get("row_count") or 1))
                results.append(
                    ObjectReference(
                        object_id=key,
                        content_digest=object_digests[key],
                        kind="table",
                        page_index=table.get("page_index", 0),
                        score=score,
                        chunk_count=max(1, math.ceil(rows / CHUNK_ROWS)),
                    )
                )
        for key, item in texts.items():
            if intent.data_kind == "quantitative":
                literals = [cell for row in numeric_rows(item) for cell in row.cells if cell.candidate_kind in {"measurement", "unresolved"}]
                if not literals:
                    continue
            score = text_score(intent, terms, item.text)
            if intent.data_kind == "quantitative" and "percentage" in intent.expected_candidate_types and any("%" in cell.raw_text or "％" in cell.raw_text for cell in literals):
                score *= 1.5
            if score:
                results.append(
                    ObjectReference(
                        object_id=key,
                        content_digest=object_digests[key],
                        kind="text",
                        page_index=item.page_index,
                        score=score,
                        chunk_count=max(1, len(text_spans(item, size=4000, pack=True))),
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
        objects = [ref.model_copy(update={"source_snapshot":document.snapshot_id}) for ref in ranked[offset:offset + intent.limit]]
        following = offset + len(objects)
        cursor = base64.urlsafe_b64encode(json.dumps({"query":query,"offset":following}).encode()).decode() if following < len(ranked) else None
        return ObjectSearchPage(objects=objects, total=len(ranked), returned=len(objects), next_cursor=cursor,
            query_digest=query, scope=document.search_scope or {})

    def read(self, ir_run_id, reference, chunk_index=0):
        reference = ObjectReference.model_validate(reference)
        document, tables, raw_tables, texts, _, geometries, local_pages = self._document(ir_run_id)
        if reference.source_snapshot and reference.source_snapshot != document.snapshot_id:
            raise ValueError("Document snapshot changed; prepare a new extraction")
        if reference.kind == "text":
            item = texts.get(reference.object_id)
            if item is None:
                raise KeyError(reference.object_id)
            geometry = geometries.get(item.object_id, NativeTextGeometry())
            clauses = text_spans(item)
            inventory = [row.model_copy(update={"cells": [cell.model_copy(update={
                "bbox": geometry.literal_bbox(cell)}) for cell in row.cells]})
                for row in numeric_rows(item)]
            inventory, links = measurement_context(inventory, clauses, geometry)
            chunks = text_spans(item, size=4000, pack=True)
            count = len(chunks)
            if chunk_index < 0 or chunk_index >= count:
                raise ValueError("Invalid text evidence cursor")
            item = chunks[chunk_index]
            spans = {span.span_id: span for span in [*text_spans(item), *geometry.fragments]
                if item.char_start <= span.char_start
                and span.char_start + len(span.text) <= item.char_start + len(item.text)}
            rows = [row for row in inventory if all(cell.char_start is not None
                and item.char_start <= cell.char_start
                and cell.char_end <= item.char_start + len(item.text) for cell in row.cells)]
            cell_ids = {cell.cell_id for row in rows for cell in row.cells}
            source_ids = spans.keys() | {source.span_id for row in rows for source in row.label_sources}
            body = dict(
                chunk_index=chunk_index,
                chunk_count=count,
                object_id=item.object_id,
                kind="text",
                page_index=item.page_index,
                texts=[span.model_dump(mode="json") for span in spans.values()],
                rows=[row.model_dump(mode="json") for row in rows],
                context_links=[link.model_dump(mode="json") for link in links
                    if link.cell_id in cell_ids and set(link.source_ids) <= source_ids],
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
            references = reference_columns(columns)
            unit_columns = {column for column,parts in columns.items()
                if any(UNIT_HEADER_RE.fullmatch(part.strip()) for part in parts)}
            if view and view.unit_col is not None:
                unit_columns = {f"c{view.unit_col}"}
            physical = {c.get("cell_id"): c for c in raw.get("cells", [])}

            def source(cell):
                return TextEvidence(
                    object_type="cell",
                    object_id=cell["cell_id"],
                    text=cell.get("text") or "",
                    page_index=cell.get("page_index", reference.page_index),
                    bbox=_box(cell),
                )

            reference_inventories = {}
            for cell in physical.values():
                inventory = disclosure_inventory(source(cell), cell, columns)
                if inventory is not None:
                    reference_inventories[cell["cell_id"]] = inventory
            reference_only_cells = {identity for identity, inventory in reference_inventories.items()
                if any(value.candidate_kind == "identifier" for row in inventory for value in row.cells)
                and not any(value.candidate_kind in {"measurement", "unresolved"}
                    for row in inventory for value in row.cells)}

            header_cells = [
                c
                for c in raw.get("cells", [])
                if c.get("is_header")
                or (view and c.get("row_index") in view.header_rows)
            ]
            source_cells, _ = column_header_sources(raw, view.header_rows if view else sorted({cell["row_index"] for cell in header_cells}))
            column_sources = {
                f"c{col}": [
                    source(c)
                    for c in source_cells[col]
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
                            label_sources=[source(physical[cell_id]) for cell_id in line.label_cell_ids
                                if cell_id in physical and physical[cell_id].get("text")] or [
                                source(c)
                                for c in physical.values()
                                if c.get("text")
                                and c.get("row_index") == line.row
                                and c.get("col_index") in view.label_cols
                            ],
                            unit_sources=[source(physical[cell_id]) for cell_id in line.unit_cell_ids
                                if cell_id in physical and physical[cell_id].get("text")] or [
                                source(c)
                                for c in physical.values()
                                if c.get("text")
                                and c.get("row_index") == line.row
                                and (c.get("col_index") == view.unit_col or
                                    (line.unit and line.unit in clean_text(c.get("text"))
                                     and c.get("col_index") in view.label_cols))
                            ],
                            cells=[
                                EvidenceCell(
                                    candidate_kind="identifier" if f"c{col}" in references or cell.cell_id in reference_only_cells else "measurement",
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
                        candidate_kind="identifier" if f"c{c.get('col_index', 0)}" in references | unit_columns or c["cell_id"] in reference_only_cells else "measurement",
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
            compound_rows, compound_links, converted = [], [], set()
            context_only_cells = set()
            unit_cells = {identity for line in view.lines for identity in line.unit_cell_ids} if view else set()
            for cell in physical.values():
                column = f"c{cell.get('col_index', 0)}"
                text = cell.get("text") or ""
                if (not start <= cell.get("row_index", 0) < end or cell.get("is_header")
                    or view and cell.get("row_index") in view.header_rows
                    or view and cell.get("col_index") == view.unit_col
                    or cell.get("cell_id") in unit_cells
                    or column in unit_columns
                    or column in references or parse_measure(text) is not None
                    or is_unit_expression(text)):
                    continue
                item = source(cell)
                inventory = reference_inventories.get(cell["cell_id"])
                if inventory is None:
                    inventory = numeric_rows(item)
                eligible = [value for row in inventory for value in row.cells
                    if value.candidate_kind in {"measurement", "unresolved"}]
                if not eligible:
                    if inventory:
                        context_only_cells.add(cell["cell_id"])
                    continue
                first = eligible[0]
                if (not text[:first.char_start].strip()
                    and (not text[first.char_end:].strip()
                        or is_unit_expression(text[first.char_end:]))):
                    continue
                residue = text
                for row in reversed(inventory):
                    value = row.cells[0]
                    residue = residue[:value.char_start] + residue[value.char_end:]
                if (not any(char.isalpha() for char in residue)
                    or is_unit_expression(re.sub(r"[\W_]+", "", residue))):
                    continue
                geometry = native_text_geometry(item.model_copy(update={
                    "bbox": item.bbox or _box(raw)}), cell,
                    local_pages.get(item.page_index, {}))
                inventory, links = measurement_context(inventory, text_spans(item), geometry)
                local = [row.model_copy(update={"row_role": "data", "cells": [
                    value.model_copy(update={"column_id": column, "source_object_type": "cell",
                        "bbox": geometry.literal_bbox(value)}) for value in row.cells]})
                    for row in inventory if any(value.candidate_kind in {"measurement", "unresolved"}
                        for value in row.cells)]
                if not local:
                    continue
                converted.add(cell["cell_id"])
                compound_rows.extend(local)
                compound_links.extend(links)
                if view and cell.get("col_index") in view.value_cols:
                    for row in local:
                        for value in row.cells:
                            compound_links.extend(EvidenceContextLink(cell_id=value.cell_id,
                                source_ids=[header.span_id or f"{header.object_id}@{header.char_start}:{header.char_start + len(header.text)}"],
                                relation="region") for header in column_sources.get(column, []))
            rows = [row.model_copy(update={"cells": [
                cell.model_copy(update={"candidate_kind": "identifier"})
                if cell.cell_id in context_only_cells else cell for cell in row.cells
                if cell.cell_id not in converted]}) for row in rows]
            rows = [row for row in rows if row.cells] + compound_rows
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
                    source(cell).model_dump(mode="json") for cell in header_cells
                    if view and cell.get("col_index") in view.label_cols
                ] + [
                    t.model_dump(mode="json")
                    for t in texts.values()
                    if t.page_index == reference.page_index
                    and view
                    and (view.title or view.caption or view.heading_above)
                    and (view.title or view.caption or view.heading_above) in t.text
                ],
                rows=[r.model_dump(mode="json") for r in rows],
                context_links=[link.model_dump(mode="json") for link in compound_links],
                texts=[
                    span.model_dump(mode="json")
                    for n in notes
                    for span in text_spans(n)
                ],
                chunk_index=chunk_index,
                chunk_count=count,
            )
        if not body.get("context_links"):
            body.pop("context_links", None)
        return EvidenceFrame.model_validate({**body, "digest": digest(body)})
