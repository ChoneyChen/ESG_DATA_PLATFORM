"""Subdivide bounded evidence without recomputing values or changing locations."""
from esg_data.hashing import digest
from esg_contracts.evidence import FrameFragment
import re


def _child(frame, reason, **updates):
    if frame.kind == 'text' and 'rows' in updates:
        # The numerical reader has each row's exact local quotation already.
        updates['texts'] = []
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


def split_frame(frame, reason="generation_incomplete", *, quantitative=False):
    if frame.kind == 'table' or (quantitative and frame.rows):
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


def budget_frames(frame, max_tokens):
    """Bound row/column selections before a call, preserving every physical value."""
    maximum_rows = max(1,max_tokens//256)
    maximum_cells = max(1,max_tokens//48)
    pending, result = [frame], []
    while pending:
        item = pending.pop(0)
        if len(item.rows)>maximum_rows or sum(len(row.cells) for row in item.rows)>maximum_cells:
            pieces = split_frame(item,'output_budget',quantitative=True)
            if pieces:
                pending[0:0] = pieces
                continue
        result.append(item)
    return result


def remainder_frames(frame, supplements, row_relations):
    """Read omitted quantities in a target table after clause rules excluded others."""
    if frame.fragment and frame.fragment.reason == "selection_remainder":
        return []
    if not any("match" in relations for relations in row_relations.values()):
        return []
    missing = {}
    for match in supplements:
        if "uncertain" in row_relations.get(match.row_id,set()):
            missing.setdefault(match.row_id,set()).update(match.columns)
    return [_child(frame,'selection_remainder',
        rows=[row.model_copy(update={'cells':[cell for cell in row.cells if cell.column_id in missing[row.row_id]]})],
        columns={key:path for key,path in frame.columns.items() if key in missing[row.row_id]},
        column_sources={key:values for key,values in frame.column_sources.items() if key in missing[row.row_id]})
        for row in frame.rows if row.row_id in missing]
