"""One source policy for chart text, interpretation hints and new visual reads.

The server-authored point ledger is audit metadata. Its JSON is never report
text. Only a supported point's existing canonical text can seed a literal.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re


CHART_READING_VERSION = "chart-point-reading-v1"
POINT_TEXT_POLICY = "exact_chart_point_text"


def chart_policy(structure: dict) -> dict:
    chart = structure.get("chart_spec") or {}
    ledger = chart.get("evidence_support") or {}
    points = ledger.get("points") or []
    return {"version": CHART_READING_VERSION,
            "origin": "exact_text_point" if structure.get("literal_policy") == POINT_TEXT_POLICY else "interpretation_context",
            "point_support": structure.get("chart_point_support"),
            "scope_conflict": any(p.get("status") == "scope_conflict" for p in points if isinstance(p, dict)) or
                any("other_" in str(e.get("error", "")) or "scope" in str(e.get("error", ""))
                    for e in ledger.get("visual_reference_errors", []) if isinstance(e, dict)),
            "ledger": ledger}


def literal_context_only(span) -> bool:
    return span.structural_context.get("literal_policy") == "context_only" or (
        span.span_type == "figure_text" and span.structural_context.get("literal_policy") != POINT_TEXT_POLICY)


def supported_point_sources(ir, figure):
    """Resolve supported refs to existing canonical objects, never ledger text."""
    chart = figure.get("chart_spec") or {}
    ledger = chart.get("evidence_support") or {}
    if ledger.get("schema_version") != "chart-point-source-support-v1":
        return
    sources = {}
    for page in getattr(ir, "pages", []):
        index = page.get("page", {}).get("page_index")
        for block in page.get("blocks", []):
            sources[block.get("block_id")] = (block, index, "block")
    for table in getattr(ir, "tables", []):
        for cell in table.get("cells", []):
            sources[cell.get("cell_id")] = (cell, cell.get("page_index", table.get("page_index")), "cell")
    figures = {item.get("figure_id"): item for item in getattr(ir, "figures", [])}
    declared = set(chart.get("source_figure_ids") or [figure.get("figure_id")])
    for point in ledger.get("points", []):
        if not isinstance(point, dict) or point.get("status") != "exact_text":
            continue
        try:
            si, pi = point["series_index"], point["point_index"]
            if not isinstance(si, int) or not isinstance(pi, int) or si < 0 or pi < 0:
                continue
            selected = chart["series"][si]["points"][pi]
            value = Decimal(str(point.get("value")))
            if not value.is_finite() or selected.get("category") != point.get("category") or Decimal(str(selected.get("value"))) != value:
                continue
        except (KeyError, IndexError, TypeError, InvalidOperation):
            continue
        for reference in point.get("supported_references", []):
            if not isinstance(reference, dict):
                continue
            ref = reference.get("ref")
            resolved = sources.get(ref)
            if not resolved or ref not in selected.get("evidence_refs", []):
                continue
            source, page, kind = resolved
            text = str(source.get("text") or "")
            scoped = figures.get(reference.get("source_object_id"))
            if (page != figure.get("page_index", 0) or not text or text != reference.get("source_quote") or
                reference.get("source_object_id") not in declared or not scoped or scoped.get("page_index", 0) != page or
                source.get("bbox") != reference.get("bbox") or not _located_inside(source.get("bbox"), scoped.get("bbox"))):
                continue
            yield {"ref": ref, "text": text, "kind": kind, "page_index": page,
                   "bbox": source["bbox"], "point": point}


def _located_inside(bbox, target):
    """Check the frozen ledger still refers to the same canonical geometry."""
    if not isinstance(bbox, dict) or not isinstance(target, dict):
        return False
    for key in ("unit", "origin", "coordinate_system_id"):
        if bbox.get(key) != target.get(key) and (key != "coordinate_system_id" or (bbox.get(key) and target.get(key))):
            return False
    try:
        x0, y0, x1, y1 = (float(bbox[k]) for k in ("x0", "y0", "x1", "y1"))
        tx0, ty0, tx1, ty1 = (float(target[k]) for k in ("x0", "y0", "x1", "y1"))
        area = (x1 - x0) * (y1 - y0)
        overlap = max(0, min(x1, tx1) - max(x0, tx0)) * max(0, min(y1, ty1) - max(y0, ty0))
        return x1 > x0 and y1 > y0 and tx1 > tx0 and ty1 > ty0 and overlap / area >= .75
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def point_literals(span, candidates):
    support = span.structural_context.get("chart_point_support") or {}
    if span.structural_context.get("literal_policy") != POINT_TEXT_POLICY:
        return candidates
    try:
        wanted = Decimal(str(support["value"]))
    except (KeyError, InvalidOperation):
        return []
    # The point category is context, even when its label contains digits.
    category = str(support.get("category") or "")
    pattern = r"\s*".join(re.escape(c) for c in re.sub(r"\s+", "", category))
    labels = list(re.finditer(pattern, span.text, re.IGNORECASE)) if pattern else []
    output = []
    for candidate in candidates:
        if candidate.candidate_type == "unit":
            output.append(candidate)
            continue
        if any(candidate.char_start < m.end() and candidate.char_end > m.start() for m in labels):
            continue
        try:
            same = Decimal(str(candidate.normalized_value)) == wanted
        except InvalidOperation:
            same = False
        if same and candidate.candidate_type in {"number", "quantity", "percentage", "year"}:
            output.append(candidate.model_copy(update={"candidate_type": "number"}) if candidate.candidate_type == "year" else candidate)
    return output


def visual_chart_error(row, context):
    policy = row.get("chart_source_policy") or context.get("chart_source_policy") or {}
    if policy.get("scope_conflict"):
        return {"code": "chart_scope_conflict", "message": "Chart source scope conflicts with its point ledger; independent image reading cannot bypass that scope."}
    return None


def is_chart_read(row, context) -> bool:
    return bool(row.get("chart_source_policy") or context.get("chart_source_policy") or
                context.get("source_capabilities", {}).get("source_kind") == "figure")


def chart_visual_audit(row, context):
    policy = row.get("chart_source_policy") or context.get("chart_source_policy") or {}
    return {"reading_policy": CHART_READING_VERSION, "evidence_origin": "independent_visual_read",
            "original_point_support": policy.get("point_support"),
            "original_ledger": policy.get("ledger", {}), "original_point_upgraded": False}


def legacy_chart_source(source) -> bool:
    chart = any(str(row.get("group_id", "")).startswith("figure:") for row in source.get("rows", []))
    return chart and source.get("source_identity", {}).get("chart_reading_policy") != CHART_READING_VERSION
