"""Compact model input; full physical frames remain immutable binding evidence."""

import re
from esg_data.literals import parse_measure
from esg_data.units import is_unit_expression
from .numeric_context import context_sources, printed_table_heading, uses_local_context


def printed_unit_sources(frame):
    """Explicit unit declarations remain selectable source spans in text figures."""
    if frame.kind != "text":
        return {}
    sources, seen = {}, set()
    for item in [*frame.title_sources, *frame.texts]:
        spans = [(match.start(),match.end()) for match in re.finditer(r"[^\n]+",item.text)]
        spans.extend((match.start(1),match.end(1)) for match in re.finditer(
            r"(?:\bunits?\s*[:：]|[單单]位\s*[:：])\s*([^\n()（）]+)",item.text,re.I))
        spans.extend((match.start(1),match.end(1)) for match in re.finditer(
            r"[（(]([^\n()（）]+)[）)]",item.text))
        for start,end in spans:
            raw = item.text[start:end]
            literal = raw.strip()
            if not is_unit_expression(literal):
                continue
            start += len(raw) - len(raw.lstrip())
            key = (item.object_id,item.char_start+start,literal)
            if key in seen:
                continue
            seen.add(key)
            sources[f"u{len(sources)+1}"] = item.model_copy(update={
                "text":literal,"char_start":item.char_start+start})
    return sources


def qualitative_spans(frame):
    """Standalone numerical cells cannot answer a statement target."""
    return [item for item in frame.texts
        if item.object_type != "cell" or parse_measure(item.text) is None]


def reading_frame(frame, *, data_kind=None):
    if data_kind == "qualitative":
        spans = []
        for item in qualitative_spans(frame):
            span = {"span_id": item.span_id or item.object_id, "text": item.text}
            local = statement_scope(frame, item).removeprefix(frame.title or "").strip()
            if local and local != item.text:
                span["context"] = local
            spans.append(span)
        return {"object_id": frame.object_id, "kind": frame.kind,
            "title": frame.title, "texts": spans}

    def text(item):
        return {
            "object_id": item.span_id or item.object_id,
            "span_id": item.span_id or item.object_id,
            "text": item.text,
        }

    sections = {row.section for row in frame.rows if row.section}
    context = [
        item
        for item in frame.texts
        if frame.kind == "text" or item.object_type != "cell" or item.text in sections
    ]
    body = {
        "object_id": frame.object_id,
        "kind": frame.kind,
        "title": frame.title,
        "column_order": list(frame.columns),
        "columns": frame.columns,
        "column_sources": {
            key: ({"source_id":key,"text":" ".join(item.text for item in values)}
                if frame.kind == "text" else {"source_id":key,"parts":[item.text for item in values]})
            for key, values in frame.column_sources.items()
            if values
        },
        "title_sources": [text(item) for item in frame.title_sources],
        "texts": [text(item) for item in context],
        "rows": [
            {
                "row_id": row.row_id,
                "label": row.label,
                "label_source_id": row.row_id,
                "section": row.section,
                "unit_raw": row.unit_raw,
                "cells": [
                    {
                        "column_id": cell.column_id,
                        "cell_id": row.row_id + "." + cell.column_id,
                        "raw_text": cell.raw_text,
                        "candidate_kind": cell.candidate_kind,
                    }
                    for cell in row.cells
                ],
            }
            for row in frame.rows
        ],
    }

    if frame.fragment is not None:
        body["fragment"] = frame.fragment.model_dump(mode="json")
    if data_kind == "quantitative":
        for row,physical in zip(body["rows"],frame.rows):
            if uses_local_context(frame,physical):
                row.pop("label",None)
                row.pop("label_source_id",None)
                row.pop("section",None)
                row["requires_local_context"] = True
                for cell,source in zip(row["cells"],physical.cells):
                    cell["source_cell_id"] = source.cell_id
                    links = [link for link in frame.context_links if link.cell_id==source.cell_id]
                    cell["label_source_options"] = list(dict.fromkeys(key
                        for link in links if link.relation in {"same_clause","row","column"}
                        for key in link.source_ids))
                    cell["context_source_options"] = list(dict.fromkeys(key
                        for link in links if link.relation=="region" for key in link.source_ids))
            elif (heading := printed_table_heading(frame, physical)) is not None:
                row["section"] = heading.text
        if any(uses_local_context(frame,row) for row in frame.rows):
            allowed = {key for link in frame.context_links for key in link.source_ids}
            body["context_sources"] = [{"source_id":key,"text":source.text,
                "object_id":source.object_id,"char_start":source.char_start,"bbox":source.bbox}
                for key,source in context_sources(frame).items() if key in allowed]
            body["context_links"] = [link.model_dump(mode="json") for link in (getattr(frame,"context_links",None) or [])]
        units = printed_unit_sources(frame)
        if units:
            body["unit_sources"] = [
                {"source_id":key,"text":item.text,"object_id":item.object_id,
                 "char_start":item.char_start} for key,item in units.items()]
    return body


def identity_source_ids(frame):
    body = reading_frame(frame)
    sources = {
        item["object_id"]
        for item in [*body.get("texts", []), *body.get("title_sources", [])]
    }
    if frame.rows:
        sources.update(body["column_sources"])
        sources.update(row.row_id for row in frame.rows if row.label_sources)
        sources.update(
            row.row_id + "." + cell.column_id
            for row in frame.rows
            for cell in row.cells
        )
    return sorted(sources)


def statement_scope(frame, text):
    """A statement uses its physical row and title, not other rows' subjects."""
    parts = [frame.title or ""]
    if frame.kind == "table":
        for row in frame.rows:
            source_ids = {source.object_id for source in [*row.label_sources, *row.unit_sources]}
            source_ids.update(cell.source_object_id or cell.cell_id for cell in row.cells)
            if text.object_id in source_ids:
                parts.extend(source.text for source in row.label_sources
                    if source.object_id != text.object_id)
                parts.append(row.section or "")
                for cell in row.cells:
                    if text.object_id == (cell.source_object_id or cell.cell_id):
                        parts.extend(frame.columns.get(cell.column_id, []))
    return " ".join(parts)
