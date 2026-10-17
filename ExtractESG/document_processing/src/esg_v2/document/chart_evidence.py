"""Point-level source support, independent from transaction/verifier acceptance."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from esg_v2.document.geometry import bbox_containment


def _numbers(text: str) -> set[Decimal]:
    values = set()
    for token in re.findall(r"(?<![\d.])-?\d+(?:,\d{3})*(?:\.\d+)?", text):
        try:
            values.add(Decimal(token.replace(",", "")))
        except InvalidOperation:
            pass
    return values


def _category_value_pair(text: str, category: str, value: Decimal, categories: set[str]) -> bool:
    # A shared paragraph with many chart numbers cannot prove their pairing.
    quote = re.sub(r"\s+", "", text).casefold()
    label = re.sub(r"\s+", "", category).casefold()
    if not label or quote.count(label) != 1:
        return False
    if any(other != category and re.sub(r"\s+", "", other).casefold() in quote for other in categories if other):
        return False
    remaining = quote.replace(label, "", 1)
    return _numbers(remaining) == {value}


def _located_inside(source, figure) -> bool:
    bbox = getattr(source, "bbox", None)
    if bbox is None or figure.bbox is None:
        return False
    target = figure.bbox
    if bbox.unit != target.unit or bbox.origin != target.origin:
        return False
    if bbox.coordinate_system_id and target.coordinate_system_id and bbox.coordinate_system_id != target.coordinate_system_id:
        return False
    return bbox_containment(bbox, target) >= .75


def assess_chart_points(document, figure, chart, *, task_id: str | None = None,
                        visual_evidence_refs: list[str] | None = None) -> dict[str, Any]:
    """Resolve declared sources and exact category/value text without pixel claims.

    Tracked page/crop images establish context. Numeric presence alone does not
    prove which category it belongs to. No missing geometry or quality is invented.
    """
    blocks = {b.block_id: b for b in document.blocks}
    cells = {c.cell_id: c for t in document.tables for c in t.cells}
    figures = {f.figure_id: f for f in document.figures}
    artifacts = {ref: a for a in document.artifacts for ref in [a.artifact_id, a.path, a.remote_uri] if ref}
    task = next((t for t in document.review_tasks if t.task_id == task_id), None)
    allowed_sources = set(task.review_plan.context_target_ids if task and task.review_plan else []) | {figure.figure_id}
    declared_sources = set(chart.source_figure_ids or [figure.figure_id])
    scope_valid = figure.figure_id in declared_sources and declared_sources <= allowed_sources and all(
        s in figures and figures[s].page_index == figure.page_index for s in declared_sources)

    def artifact_scope(artifact):
        owners = {f.figure_id for f in document.figures if f.crop_artifact_id == artifact.artifact_id or f.image_path == artifact.path}
        if owners and not owners <= declared_sources:
            return "reference_other_figure"
        pages = set(artifact.page_indices)
        if artifact.page_index is not None:
            pages.add(artifact.page_index)
        if pages and figure.page_index not in pages:
            return "reference_other_page"
        return None

    visual_refs = list(dict.fromkeys([*chart.visual_evidence_refs, *(visual_evidence_refs or [])]))
    visual_context = []
    visual_errors = []
    for ref in visual_refs:
        source_figure = figures.get(ref)
        artifact = artifacts.get(ref) or (artifacts.get(source_figure.crop_artifact_id) if source_figure else None)
        if source_figure and source_figure.figure_id not in declared_sources:
            visual_errors.append({"ref": ref, "error": "reference_other_figure"})
            continue
        if artifact is None:
            visual_errors.append({"ref": ref, "error": "unresolved_visual_reference"})
        elif artifact.kind not in {"page_image", "spread_image", "region_crop", "ocr_image", "figure_image"}:
            visual_errors.append({"ref": ref, "error": "reference_is_not_visual_artifact"})
        elif error := artifact_scope(artifact):
            visual_errors.append({"ref": ref, "error": error})
        else:
            visual_context.append({"ref": ref, "artifact_id": artifact.artifact_id,
                                   "status": "visual_context_only_not_point_value_proof"})
    ledger = []
    categories = {point.category for series in chart.series for point in series.points}
    for si, series in enumerate(chart.series):
        for pi, point in enumerate(series.points):
            supported, errors, context = [], [], []
            try:
                wanted = Decimal(str(point.value)) if point.value is not None else None
                if wanted is not None and not wanted.is_finite():
                    wanted = None
            except InvalidOperation:
                wanted = None
            for ref in point.evidence_refs:
                obj = blocks.get(ref) or cells.get(ref)
                if obj is not None:
                    if obj.page_index != figure.page_index:
                        errors.append("reference_other_page")
                        continue
                    if wanted is None or wanted not in _numbers(obj.text):
                        errors.append("reference_does_not_support_value")
                        continue
                    bound = getattr(obj, "figure_id", None)
                    if bound and bound not in declared_sources:
                        errors.append("reference_other_figure")
                        continue
                    scoped = figures.get(bound) if bound else next(
                        (figures[s] for s in declared_sources if scope_valid and _located_inside(obj, figures[s])), None)
                    if scoped and scope_valid and _located_inside(obj, scoped) and _category_value_pair(obj.text, point.category, wanted, categories):
                        supported.append({"ref": ref, "source_object_id": scoped.figure_id,
                                          "source_quote": obj.text,
                                          "bbox": obj.bbox.model_dump(mode="json") if obj.bbox else None})
                    else:
                        context.append({"ref": ref, "source_quote": obj.text,
                                        "reason": "value_present_but_category_value_pair_or_location_unproven"})
                elif ref in artifacts:
                    if error := artifact_scope(artifacts[ref]):
                        errors.append(error)
                    else:
                        context.append({"ref": ref, "reason": "visual_context_only_not_point_value_proof"})
                else:
                    errors.append("unresolved_point_reference")
            if not scope_valid:
                errors.append("undeclared_composite_source_scope")
            if errors:
                status = "scope_conflict" if any("other_" in e or "scope" in e for e in errors) else "unsupported"
            else:
                status = "exact_text" if supported else "source_context_only"
            ledger.append({"series_index": si, "point_index": pi, "category": point.category,
                           "value": point.value, "status": status, "supported_references": supported,
                           "context_references": context, "errors": list(dict.fromkeys(errors))})
    blocking = [p for p in ledger if p["status"] in {"unsupported", "scope_conflict"}]
    count = len(blocking) + len(visual_errors)
    exact = bool(ledger) and not count and all(p["status"] == "exact_text" for p in ledger)
    return {"schema_version": "chart-point-source-support-v1", "point_count": len(ledger),
            "all_points_exactly_supported": exact,
            "quality": "point_supported" if exact else "invalid_reference" if count else "context_only",
            "blocking_count": count, "points": ledger, "visual_context": visual_context,
            "visual_reference_errors": visual_errors, "visual_pixel_values_verified": False,
            "series_metadata_verified": False}
