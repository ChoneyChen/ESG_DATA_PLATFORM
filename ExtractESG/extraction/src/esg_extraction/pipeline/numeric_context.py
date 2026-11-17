"""Bind quantitative labels only through frozen physical source relationships."""
from dataclasses import dataclass
import re


def context_sources(frame):
    sources = {}
    for source in [*frame.texts,*frame.title_sources,
        *(source for row in frame.rows for source in [*row.label_sources,*row.unit_sources]),
        *(source for parts in frame.column_sources.values() for source in parts)]:
        key = source.span_id or f"{source.object_id}@{source.char_start}:{source.char_start+len(source.text)}"
        sources[key] = source
    return sources


def uses_local_context(frame, row):
    return frame.kind == "text" or any(cell.char_start is not None and cell.source_object_id
        for cell in row.cells)


def comparison_text(text):
    """Join physical word wrapping for comparison while retaining quoted bytes."""
    return re.sub(r"(?<=[A-Za-z])[-‐‑](?:\s|›)+(?=[A-Za-z])","-",text)


def closed_sources(frame, sources):
    """Keep an entire linked label, including its wrapped qualifiers."""
    ordered = sorted(sources,key=lambda source:(source.page_index,source.object_id,source.char_start))
    if len(ordered)<2:
        return ordered
    if ordered and len({(source.page_index,source.object_id) for source in ordered}) == 1:
        start = min(source.char_start for source in ordered)
        end = max(source.char_start+len(source.text) for source in ordered)
        parent = next((source for source in frame.texts if source.object_id==ordered[0].object_id
            and source.char_start<=start and source.char_start+len(source.text)>=end),None)
        if parent is not None and all(not parent.text[
            left.char_start+len(left.text)-parent.char_start:right.char_start-parent.char_start].strip()
            for left,right in zip(ordered,ordered[1:])):
            boxes = [source.bbox for source in ordered if source.bbox]
            complete_boxes = len(boxes)==len(ordered) and all(
                isinstance(box.get(key),(int,float)) for box in boxes for key in ("x0","y0","x1","y1"))
            box = {key:operation(item[key] for item in boxes)
                for key,operation in [("x0",min),("y0",min),("x1",max),("y1",max)]} if complete_boxes else parent.bbox
            return [parent.model_copy(update={"span_id":f"{parent.object_id}@{start}:{end}",
                "char_start":start,"text":parent.text[start-parent.char_start:end-parent.char_start],"bbox":box})]
    return ordered


def local_unit_sources(frame, row, cell):
    if not row.unit_raw:
        return []
    for source in frame.texts:
        if source.object_id!=(cell.source_object_id or cell.cell_id) or cell.char_end is None:
            continue
        offset = cell.char_end-source.char_start
        if not 0<=offset<=len(source.text):
            continue
        match = re.match(r"\s*("+re.escape(row.unit_raw)+r")",source.text[offset:])
        if match:
            start = offset+match.start(1)
            absolute = source.char_start+start
            supports = [item for item in frame.texts if item.object_id==source.object_id
                and item.char_start<=absolute and item.char_start+len(item.text)>=absolute+len(row.unit_raw)]
            support = min(supports,key=lambda item:len(item.text))
            return [source.model_copy(update={"char_start":source.char_start+start,
                "text":row.unit_raw,"span_id":f"{source.object_id}@{absolute}:{absolute+len(row.unit_raw)}",
                "bbox":support.bbox})]
    return [source for source in row.unit_sources if source.text==row.unit_raw]


@dataclass
class NumericContext:
    frame: object
    row: object
    match: object
    proven: bool
    flags: list[str]
    label: str


def select_context(frame, match, row, cell, units):
    """A table uses its existing row protocol; local literals select source IDs.

    Relationships are supplied by the document port before candidate filtering
    and splitting. Extraction never infers a new relationship from a narrowed
    set of model-selected values.
    """
    if not uses_local_context(frame,row):
        return None
    bank = context_sources(frame)
    links = [link for link in (getattr(frame,"context_links",None) or []) if link.cell_id==cell.cell_id]
    primary = getattr(match,"label_source_id",None)
    strong = next((link for link in links if link.relation in {"same_clause","row","column"}
        and primary in link.source_ids and all(key in bank for key in link.source_ids)),None)
    labels = closed_sources(frame,[bank[key] for key in strong.source_ids]) if strong else []
    allowed = {key for link in links for key in link.source_ids}
    requested = set(getattr(match,"context_source_ids",[]))
    auxiliary = []
    for link in links:
        if requested.intersection(link.source_ids) and all(key in bank for key in link.source_ids):
            auxiliary.extend(source for source in closed_sources(frame,[bank[key] for key in link.source_ids])
                if source not in auxiliary)
    selected_unit = units.get(match.unit_source_id)
    unit_support = next((source for key,source in bank.items() if selected_unit is not None and key in allowed
        and source.object_id==selected_unit.object_id and source.char_start<=selected_unit.char_start
        and source.char_start+len(source.text)>=selected_unit.char_start+len(selected_unit.text)
        and source.text[selected_unit.char_start-source.char_start:
            selected_unit.char_start-source.char_start+len(selected_unit.text)]==selected_unit.text),None)
    unit_proven = bool(row.unit_raw) or not match.unit_source_id or unit_support is not None
    if selected_unit is not None and unit_support is not None:
        selected_unit = selected_unit.model_copy(update={"bbox":unit_support.bbox})
    flags = []
    if strong is None:
        flags.append("numeric_context_application_unproven")
    if any(key not in allowed or key not in bank for key in getattr(match,"context_source_ids",[])):
        flags.append("numeric_context_source_unlinked")
    if not unit_proven:
        flags.append("numeric_unit_application_unproven")
    if strong is None:
        auxiliary = []
    unit = row.unit_raw or (selected_unit.text if selected_unit and unit_proven else None)
    label = " › ".join(source.text for source in labels)
    section = " › ".join(source.text for source in auxiliary) or None
    local = row.model_copy(update={"label":label,"label_sources":labels,"section":section,
        "unit_raw":unit,"unit_sources":local_unit_sources(frame,row,cell) if row.unit_raw else
            ([selected_unit] if selected_unit and unit_proven else [])})
    scoped = frame.model_copy(update={"title":section,
        "title_sources":auxiliary,"rows":[local],
        "texts":[*labels,*auxiliary],"columns":{},"column_sources":{}})
    # Optional identity proposals cannot reopen the original whole figure.
    local_match = match.model_copy(update={"identity":[],"column_identity":{},
        "unit_source_id":match.unit_source_id if unit_proven else None})
    return NumericContext(scoped,local,local_match,strong is not None and not flags,
        flags,comparison_text(label))
