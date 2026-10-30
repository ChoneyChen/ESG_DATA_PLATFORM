"""Subdivide bounded evidence without recomputing values or changing locations."""
from esg_data.hashing import digest
from esg_contracts.evidence import FrameFragment
import re


def _child(frame, reason, **updates):
    if frame.kind == "table" and "rows" in updates:
        rows = updates["rows"]
        sources = updates.get("column_sources", frame.column_sources)
        allowed = {source.object_id for items in sources.values() for source in items}
        allowed.update(source.object_id for row in rows for source in [*row.label_sources, *row.unit_sources])
        allowed.update(cell.cell_id for row in rows for cell in row.cells)
        updates["texts"] = [item for item in frame.texts if item.object_type != "cell" or item.object_id in allowed]
    identity = {"parent": frame.digest, **{key: [item.model_dump(mode="json") for item in value]
        for key, value in updates.items() if key in {"rows", "texts"}}}
    return frame.model_copy(update={**updates, "digest": digest(identity),
        "fragment": FrameFragment(parent_digest=frame.digest, reason=reason)})


def split_frame(frame, reason="generation_incomplete"):
    if frame.kind == "table":
        if len(frame.rows) > 1:
            middle = len(frame.rows) // 2
            return [_child(frame, reason, rows=rows) for rows in (frame.rows[:middle], frame.rows[middle:])]
        if len(frame.rows) == 1 and len(frame.rows[0].cells) > 1:
            row = frame.rows[0]
            middle = len(row.cells) // 2
            return [_child(frame, reason, rows=[row.model_copy(update={"cells": cells})],
                columns={key: value for key, value in frame.columns.items() if key in {cell.column_id for cell in cells}},
                column_sources={key: value for key, value in frame.column_sources.items() if key in {cell.column_id for cell in cells}})
                for cells in (row.cells[:middle], row.cells[middle:])]
        return []
    if len(frame.texts) > 1:
        middle = len(frame.texts) // 2
        return [_child(frame, reason, texts=texts) for texts in (frame.texts[:middle], frame.texts[middle:])]
    if frame.texts and len(frame.texts[0].text) > 256:
        source = frame.texts[0]
        middle = len(source.text) // 2
        boundaries = [match.end() for match in re.finditer(r"[。！？.!?](?:\s|$)|\n", source.text)
                      if len(source.text)//4 < match.end() < len(source.text)*3//4]
        if boundaries:
            middle = min(boundaries, key=lambda point: abs(point-middle))
        return [_child(frame, reason, texts=[source.model_copy(update={"text": source.text[start:end],
            "char_start": source.char_start + start, "span_id": (source.span_id or source.object_id) + f"@{start}:{end}"})])
            for start, end in ((0, middle), (middle, len(source.text)))]
    return []
