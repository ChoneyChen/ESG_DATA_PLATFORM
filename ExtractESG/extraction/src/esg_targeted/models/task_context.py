"""Provider views omit storage repetition while canonical binding stays intact."""
from __future__ import annotations

from copy import deepcopy


def repair_diagnostics(issues, *, count=24, message_limit=1000):
    """Bound provider diagnostics; the full issues remain in the call ledger."""
    def bounded(value):
        if isinstance(value, str):
            return value if len(value) <= message_limit else value[:message_limit] + '… [完整诊断已保存]'
        if isinstance(value, dict):
            return {key: bounded(item) for key, item in value.items() if key not in {'input','input_value','input_type'}}
        if isinstance(value, (list, tuple)):
            return [bounded(item) for item in value[:count]]
        return value
    return [bounded(issue.model_dump() if hasattr(issue,'model_dump') else str(issue)) for issue in issues[:count]]


def compact_task_context(context: dict) -> dict:
    value = deepcopy(context)
    if isinstance(value.get("original_task"), dict):
        value["original_task"] = compact_task_context(value["original_task"])
    if "rows" not in value or "kind" not in value:
        return value
    def segment(item, limit=600):
        current = {key: item[key] for key in ("span_id", "ref", "text", "page", "truncated", "source_text_frame") if key in item}
        text = str(current.get("text") or "")
        if len(text) > limit:
            current.update(text=text[:limit], truncated=True)
        return current
    for key in ("document_context", "linked_context", "support_segments"):
        if isinstance(value.get(key), list):
            # A reference menu is not a replacement for its complete source.
            # The server binds declarations against the unchanged full catalog.
            value[key] = [segment(item, 120 if key == "document_context" else 400) for item in value[key]][:24]
    rows = []
    for row in value["rows"]:
        current = {key: row[key] for key in ("group", "group_id", "label", "text", "page", "unit", "grid_reliable", "source_text_frame") if key in row}
        if len(str(current.get("text") or "")) > 1600:
            current.update(text=current["text"][:1600], text_truncated=True)
        current["support_segments"] = [segment(item) for item in row.get("support_segments", [])[:8]]
        if len(row.get("support_segments", [])) > 8:
            current["support_segments_truncated"] = True
        current["cells"] = [{key: cell[key] for key in (
            "target_cell", "cell_id", "column", "raw", "unit", "span_id", "source_kind",
            "column_headers", "section_headers", "grid_reliable") if key in cell}
            for cell in row.get("cells", [])]
        rows.append(current)
    value["rows"] = rows
    columns = {cell.get("column") for row in rows for cell in row.get("cells", [])}
    if isinstance(value.get("columns"), list):
        value["columns"] = [column for column in value["columns"] if column.get("column") in columns]
    for key in ("physical_object_ids", "chart_evidence_support", "chart_spec", "evidence_support"):
        value.pop(key, None)
    return value


def unshown_source_groups(context: dict, prompt_context: dict) -> set[str]:
    """A provider prefix must never complete its undisplayed physical tail."""
    views = {row["group_id"]: row for row in prompt_context.get("rows", [])}
    return {row["group_id"] for row in context.get("rows", [])
            if (views.get(row["group_id"], {}).get("text_truncated") or
                views.get(row["group_id"], {}).get("support_segments_truncated") or
                any(segment.get("truncated") for segment in views.get(row["group_id"], {}).get("support_segments", [])))}


def complete_read_groups(expanded: list[dict]) -> set[str]:
    """Every returned sibling must be bound before its physical group completes."""
    states = {}
    for row in expanded:
        if row["kind"] == "quantitative":
            cells = row.get("cells", [])
            valid = bool(cells) and all(cell.get("physical_sources") and cell.get("quality_status") != "unbound" for cell in cells)
        elif row["kind"] == "assertion":
            valid = bool(row.get("statement")) and row.get("quality_status") != "unbound"
        else:
            valid = bool(row.get("context_role"))
        states[row["group_id"]] = states.get(row["group_id"], True) and bool(valid)
    return {group for group, valid in states.items() if valid}
