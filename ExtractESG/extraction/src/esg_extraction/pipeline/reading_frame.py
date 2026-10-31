"""Compact model input; full physical frames remain immutable binding evidence."""


def reading_frame(frame):
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
        if item.object_type != "cell" or item.text in sections
    ]
    body = {
        "object_id": frame.object_id,
        "kind": frame.kind,
        "title": frame.title,
        "column_order": list(frame.columns),
        "columns": frame.columns,
        "column_sources": {
            key: {"source_id": key, "parts": [item.text for item in values]}
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
