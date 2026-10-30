"""Physical columns, with no report-year filter or target-specific vocabulary."""
import re
from collections import Counter
from esg_data.periods import parse_period, period_payload, period_label
from esg_data.literals import arabic_years
from esg_contracts.evidence import ColumnSemantics, EvidenceStructure


def column_structure(frame):
    columns = []
    data_columns = {cell.column_id for row in frame.rows for cell in row.cells}
    for key in sorted(data_columns):
        path = frame.columns.get(key, [])
        text = " › ".join(path)
        period = parse_period(text)
        # Extra labels may describe another entity, method or value role.
        remainder = re.sub(r"fiscal\s+year|year\s+ended|as\s+of|cumulative|annual|through|until|to|FY|Q[1-4]|第?[一二三四1-4]季度|上半年|下半年|半年度|季度|财年|財年|年度|截至|累计|累計|期末|年|月|日|至|到", "", arabic_years(text), flags=re.I)
        remainder = re.sub(r"[\d\W_]+", "", remainder)
        plain = bool(period and period.kind != "unknown" and not remainder)
        columns.append(ColumnSemantics(column_id=key, header_path=path, period=period,
            period_only=plain, requires_identity_reading=bool(period and not plain)))
    return EvidenceStructure(columns=columns,
        unresolved_columns=[column.column_id for column in columns if column.period is None])


def period_columns(frame, document=None):
    """All physically dated columns. The document's primary year is irrelevant."""
    columns = [column.column_id for column in column_structure(frame).columns
               if column.period and column.period.kind != "unknown"]
    return columns or None


def period_coverage(frames, facts):
    """Potential table cells and actual captures are distinct, not a recall claim."""
    candidates = {}
    for frame in frames:
        if frame.kind != "table":
            continue
        columns = {c.column_id: c for c in column_structure(frame).columns}
        for row in frame.rows:
            if row.row_role == "header":
                continue
            for cell in row.cells:
                if cell.candidate_kind not in {"measurement", "unresolved"} or not re.search(r"[\d零一二三四五六七八九十]", cell.raw_text):
                    continue
                column = columns.get(cell.column_id)
                label = period_label(period_payload(column.period)) if column and column.period else "期间未知"
                candidates[cell.source_object_id or cell.cell_id] = label
    captured_sources = {location.object_id for fact in facts for location in fact.evidence if location.role == "value"}
    return {"observed_candidates": dict(Counter(candidates.values())),
        "captured_facts": dict(Counter(period_label(fact.identity.reporting_period.value) for fact in facts)),
        "unselected_candidate_cells": sorted(set(candidates) - captured_sources),
        "meaning": "Potential cells in inspected evidence; not report-wide recall or non-disclosure proof."}
