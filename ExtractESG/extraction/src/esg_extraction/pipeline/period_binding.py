"""Bind local time evidence; a report's main year is never a default identity."""
import re
from esg_data.periods import parse_period, period_payload
from esg_contracts.facts import ReportedField, EvidenceLocation


def _field(raw, sources, role, state="stated"):
    period = parse_period(raw)
    if not period or not sources:
        return None
    return ReportedField(state=state, raw_text=raw, value=period_payload(period), evidence=[
        EvidenceLocation(object_id=source.object_id, object_type=source.object_type,
            page_index=source.page_index, excerpt=source.text, role=role, bbox=source.bbox)
        for source in sources
    ])


def bind_period(frame, *, row=None, column_id=None, document=None):
    """Local columns > explicit row date > table title > explicit report reference.

    A missing local date returns None, preserving an independently grounded model
    proposal or unknown identity. Titles covering several years never fill cells.
    """
    headers = frame.columns.get(column_id, []) if column_id else []
    printed = " › ".join(headers)
    if parse_period(printed):
        field = _field(printed, frame.column_sources.get(column_id, []), "column_header")
        return field or ReportedField(state="not_read"), ([] if field else ["column_period_source_unconfirmed"])
    if row and frame.kind == "table" and re.match(r"^\s*(?:19|20|21)\d{2}(?:年|\b)", row.label):
        field = _field(row.label, row.label_sources, "row")
        if field:
            return field, []
    any_column_period = any(parse_period(" › ".join(path)) for path in frame.columns.values())
    title_period = parse_period(frame.title)
    if title_period and title_period.kind != "range" and not any_column_period:
        field = _field(frame.title, frame.title_sources, "table", "inherited")
        if field:
            return field, []
    context = document.period_context.model_dump() if document and document.period_context else None
    local_text = " ".join([frame.title or "", row.label if row else "", *(t.text for t in frame.texts if t.object_type != "cell")])
    if (context and context.get("basis") == "reporting_period_statement"
        and context.get("source_id") and context.get("evidence") and context.get("page_index") is not None
        and re.search(r"本报告期|本年度|报告期内|during\s+(?:the\s+)?reporting\s+period", local_text, re.I)
        and not any_column_period and not parse_period(local_text)):
        period = parse_period(context["evidence"])
        if period:
            return ReportedField(state="inherited", raw_text=context["evidence"], value=period_payload(period), evidence=[
                EvidenceLocation(object_id=context["source_id"], object_type="text_block",
                    page_index=context["page_index"], excerpt=context["evidence"], role="report")]), []
    return None, []
