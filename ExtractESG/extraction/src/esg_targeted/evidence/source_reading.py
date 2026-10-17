"""Bound source reading to supplied text, coordinates and header paths.

No standard, company or metric supplies a value here. Model declarations that
cannot be tied to this reading context remain visible as unverified candidates.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from esg_targeted.evidence.chart_support import visual_chart_error, is_chart_read, chart_visual_audit
from esg_targeted.evidence.units import UNIT_ONLY_RE, normalize_unit_text


def _compact(value: Any) -> str:
    # Preserve reported characters, including case-sensitive entity/unit text.
    # OCR whitespace may differ; model-selected fragments may not rewrite case.
    return re.sub(r"\s+", "", str(value))


def _unique(items):
    output = []
    for item in items:
        if item not in output:
            output.append(item)
    return output


def _segments(value):
    return [item for item in (value or []) if isinstance(item, dict)
            and (item.get("span_id") or item.get("ref")) and item.get("text")]


def _has_real_bbox(locators):
    for locator in locators or []:
        bbox = locator.get("bbox")
        if not isinstance(bbox, dict):
            continue
        values = [bbox.get(axis) for axis in ("x0", "y0", "x1", "y1")]
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            if values[2] > values[0] and values[3] > values[1]:
                return True
    return False


def _unit_supported(unit, text):
    needle, haystack = _compact(unit), _compact(text)
    if not needle:
        return False
    # A visible unit must not be a letter inside a word, or a suffix cut from
    # a larger reported magnitude (吨 is not the reported unit 万吨).
    prefix = r"(?<![A-Za-z])" if needle[0].isascii() and needle[0].isalpha() else r"(?<![千仟万萬亿億百佰十拾])"
    suffix = r"(?![A-Za-z])" if needle[-1].isascii() and needle[-1].isalpha() else ""
    return re.search(prefix + re.escape(needle) + suffix, haystack) is not None


def _explicit_row_unit(source_row):
    """Recognize only a complete, explicit unit bracket on this physical row.

    The raw spelling/magnitude is retained. This never supplies a requested
    standard unit or converts a number, and multiple units remain ambiguous.
    """
    label = str(source_row.get("label") or "")
    units = []
    for raw in re.findall(r"[（(]([^()（）]+)[）)]", label):
        normalized = normalize_unit_text(raw)
        base = re.sub(r"^(?:十亿|十億|千万|千萬|百万|百萬|亿|億|万|萬|千)\s*", "", normalized)
        if (UNIT_ONLY_RE.fullmatch(normalized) or UNIT_ONLY_RE.fullmatch(base) or
            re.fullmatch(r"(?:m[³3]|立方米|人|名|件|起|次)", base, re.IGNORECASE)):
            units.append(raw.strip())
    return units[0] if len(set(units)) == 1 else None


def _visual_binding_error(cell, original, source_row, context):
    if not cell.get("visual_override"):
        return None
    if error := visual_chart_error(source_row, context):
        return error
    changed_value = not original or str(cell.get("value")) != str(original.get("raw"))
    if not changed_value and original.get("grid_reliable", True):
        return None  # identity/unit edits are not a new primary visual value
    unreliable = not original.get("grid_reliable", True) if original else (
        source_row.get("grid_reliable") is False or any(not item.get("grid_reliable", True) for item in source_row.get("cells", [])))
    supplied = context.get("visual_evidence_supplied") is True and source_row.get("group_id") in context.get("visual_evidence_group_ids", [])
    located = _has_real_bbox(original.get("locators")) if original else _has_real_bbox(source_row.get("locators"))
    if unreliable and supplied and located:
        return None
    return {"code": "visual_value_unbound", "original_value": original.get("raw"), "declared_value": cell.get("value"),
            "message": "A changed visual value requires an unreliable grid, this row's supplied image and a real source bbox."}


def document_reading_context(inventory, *, max_pages=3, max_chars=6000) -> list[dict]:
    """Supply bounded front-matter evidence without extracting an entity/year.

    The model still selects the actual identity statement and cites its span.
    A filename, current year or requested package is never an identity source.
    """
    from esg_targeted.evidence.quality import is_navigation_span
    output, used = [], 0
    for span in sorted(inventory.spans, key=lambda item: (item.page_index, item.span_id)):
        if span.page_index >= max_pages or span.span_type != "block" or span.has_table_structure or is_navigation_span(span):
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        text = span.text[:remaining]
        output.append({"span_id": span.span_id, "text": text, "page": span.page_index,
                       "locators": [loc.model_dump(mode="json") for loc in span.locators],
                       "truncated": len(text) < len(span.text)})
        used += len(text)
    return output


def _catalog(source_row, context):
    local = _segments(source_row.get("support_segments"))
    if not local and source_row.get("text") and source_row.get("span_id"):
        local = [{"span_id": source_row["span_id"], "text": source_row["text"],
                  "locators": source_row.get("locators", [])}]
    cell_segments = [{"span_id": cell["span_id"], "text": str(cell.get("raw") or ""),
                      "column_headers": cell.get("column_headers", []), "locators": cell.get("locators", [])}
                     for cell in source_row.get("cells", []) if cell.get("span_id")]
    # A numerical occurrence is a view of its parent span, not a replacement
    # for that span.  Keeping the last item here used to replace an entire
    # sentence with e.g. "14.1%", rejecting exact statement/identity citations.
    # Prefer supplied statement-bearing segments; retain occurrence locations
    # on the cells themselves. Conflicting full segments remain unresolvable.
    window_refs = {item.get("span_id") or item.get("ref") for item in local if item.get("source_text_frame")}
    extras = []
    for key, limit in (("linked_context", 400), ("document_context", 120)):
        for item in _segments(context.get(key))[:24] if source_row.get("source_text_frame") else _segments(context.get(key)):
            if (item.get("span_id") or item.get("ref")) in window_refs:
                continue
            extras.append({**item, "original_text": item["text"], "text": item["text"][:limit]} if source_row.get("source_text_frame") else item)
    full = [*local, *extras]
    catalog, conflicts = {}, set()
    for item in full:
        ref = str(item.get("span_id") or item["ref"])
        previous = catalog.get(ref)
        if previous and _compact(previous["text"]) != _compact(item["text"]):
            conflicts.add(ref)
        elif not previous:
            catalog[ref] = item
    for item in cell_segments:
        ref = str(item["span_id"])
        if ref not in catalog:
            catalog[ref] = item
        elif _compact(item["text"]) not in _compact(catalog[ref]["text"]):
            conflicts.add(ref)
    return local, {ref: item for ref, item in catalog.items() if ref not in conflicts}


def _visible_ranges(segment, excerpt):
    """Map a whitespace-tolerant exact quote to original Unicode positions."""
    text = str(segment.get("text") or "")
    positions = [index for index, char in enumerate(text) if not char.isspace()]
    normalized = "".join(text[index] for index in positions)
    needle = _compact(excerpt)
    ranges, cursor = [], 0
    offset = segment.get("source_text_frame", {}).get("start", 0)
    if not needle:
        return ranges
    while True:
        found = normalized.find(needle, cursor)
        if found < 0:
            break
        ranges.append([offset + positions[found], offset + positions[found + len(needle) - 1] + 1])
        cursor = found + len(needle)
    return ranges


def _statement_locations(segment, ref, excerpt):
    if not segment.get("source_text_frame"):
        return [{**locator, "span_id": ref, "evidence_excerpt": excerpt} for locator in segment.get("locators", [])]
    return [{**locator, "span_id": ref, "evidence_excerpt": excerpt, "char_start": start, "char_end": end}
            for start, end in _visible_ranges(segment, excerpt) for locator in segment.get("locators", [])]


HEADER_IDENTITY_AXES = {"reporting_period", "period", "reporting_year", "reporting_entity",
                        "entity_aggregation_role", "aggregation_role"}


def _header_alignment_uncertain(cell, context):
    if cell.get("header_alignment_uncertain"):
        return True
    return any(column.get("column") == cell.get("column") and
               (column.get("alignment_uncertain") or column.get("header_alignment_uncertain"))
               for column in context.get("columns", []))


def _table_identity_segment(segment):
    # Row text contains mechanically prefixed header labels. A literal quote of
    # that generated row is not an independent confirmation of shifted headers.
    return any(locator.get("ir_object_type") in {"cell", "table", "table_header_context"}
               for locator in segment.get("locators", []))


def _field_binding(fields, refs, *, local, catalog, cell=None, bound_source_identity=None,
                   header_alignment_uncertain=False):
    accepted, unverified, evidence, issues = {}, {}, {}, []
    headers = list((cell or {}).get("column_headers", []))
    for name, raw in fields.items():
        if raw in (None, ""):
            continue
        declared = refs.get(name, []) or []
        # Explicit references must all exist. A guessed reference cannot become
        # valid merely because the same value appears in an unrelated header.
        selected = [catalog[ref] for ref in declared if ref in catalog]
        invalid = [ref for ref in declared if ref not in catalog]
        candidates = selected if declared else local
        needle = _compact(raw)
        matches = [segment for segment in candidates if needle and any(needle in _compact(text) for text in
                   [segment["text"], *segment.get("column_headers", [])])
                   and segment.get("locators")]
        header_axis = name in HEADER_IDENTITY_AXES
        if header_alignment_uncertain and header_axis:
            matches = [segment for segment in matches if not _table_identity_segment(segment)]
        header_match = bool(cell and not declared and not (header_alignment_uncertain and header_axis)
                            and any(needle and needle in _compact(header) for header in headers))
        if name in {"reporting_period", "period", "reporting_year"} and headers and not declared:
            # A neighbouring year in the row is not this cell's period.
            matches = []
        scalar = not isinstance(raw, (dict, list))
        named_axis = not re.fullmatch(r"\d+(?:\.\d+)?", str(name))
        header_years = set() if header_alignment_uncertain else set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", " ".join(headers)))
        stated_years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(raw)))
        period_conflict = name in {"reporting_period", "period", "reporting_year"} and header_years and stated_years and not header_years.intersection(stated_years)
        if scalar and named_axis and not invalid and not period_conflict and (matches or header_match):
            accepted[name] = raw
            evidence[name] = _unique([
                *(_identity_evidence(segment, raw, segment["text"], "source_text", bound_source_identity)
                  for segment in matches if needle in _compact(segment["text"])),
                *(_identity_evidence(segment, raw, header, "column_header", bound_source_identity)
                  for segment in matches for header in segment.get("column_headers", [])
                  if needle in _compact(header)),
                *(_identity_evidence(cell, raw, header, "column_header", bound_source_identity)
                  for header in headers if header_match and needle in _compact(header)),
            ])
        else:
            unverified[name] = raw
            issues.append({"code": "identity_reference_unknown" if invalid else
                           "header_identity_unreliable" if header_alignment_uncertain and header_axis else
                           "period_header_conflict" if period_conflict else "identity_unbound",
                           "axis": name, "declared_value": raw, "refs": declared,
                           "message": "Identity declaration is not supported by the selected report text/header."})
    return accepted, unverified, evidence, issues


def _identity_evidence(segment, raw, text, origin, bound_source_identity):
    """Freeze verified report text, preserving only supplied source identity.

    excerpt stays a compatibility copy of the declared raw value. Consumers
    validate against text and the immutable locators; missing revisions are
    deliberately not invented from this request's current IR revision.
    """
    result = {"evidence_contract_version": "bound-identity-evidence-v1",
              "span_id": segment.get("span_id") or segment.get("ref"),
              "text": str(segment.get("original_text", text)) if origin == "source_text" else str(text),
              "excerpt": str(raw), "evidence_origin": origin,
              "locators": deepcopy(segment.get("locators", []))}
    if origin == "column_header":
        result["column_headers"] = deepcopy(segment.get("column_headers", []))
    if segment.get("source_text_frame") and origin == "source_text":
        result["source_text_frame"] = deepcopy(segment["source_text_frame"])
        result["source_char_ranges"] = _visible_ranges(segment, raw)
    keys = ("document_id", "ir_run_id", "ir_revision")
    if (isinstance(bound_source_identity, dict) and
        all(bound_source_identity.get(key) not in (None, "", "unknown") for key in keys) and
        isinstance(bound_source_identity["ir_revision"], int) and not isinstance(bound_source_identity["ir_revision"], bool)):
        result["source_identity"] = {key: bound_source_identity[key] for key in keys}
    return result


def bind_source_row(expanded: dict, source_row: dict, context: dict) -> dict:
    """Attach verified multi-fragment evidence and sparse identity to one row.

    Called after mechanical expansion. It never changes a numerical value.
    Quantities retain their own cell/occurrence locators. Assertions use only
    statement-bearing segments, never the first row/header locator by default.
    """
    row = deepcopy(expanded)
    if row.get("kind") == "context":
        return row
    local, catalog = _catalog(source_row, context)
    fields, unverified, identity_evidence, issues = _field_binding(
        row.get("fields", {}), row.get("field_refs", {}), local=local, catalog=catalog,
        bound_source_identity=context.get("bound_source_identity"))
    row.update(fields=fields, unverified_identity_raw=unverified, identity_evidence=identity_evidence,
               field_evidence_span_ids={axis: _unique(item["span_id"] for item in items if item.get("span_id"))
                                        for axis, items in identity_evidence.items()})
    row_unit = row.get("unit")
    explicit_unit = _explicit_row_unit(source_row)
    if not row_unit and explicit_unit:
        row["unit"] = row_unit = explicit_unit
        row.setdefault("semantics", {}).setdefault("source_interpretation", {})["unit_origin"] = "physical_row_label"
    unit_texts = [str(source_row.get("label") or ""), *(segment["text"] for segment in local), *(str(cell.get("unit") or "") for cell in source_row.get("cells", []))]
    if row_unit and not any(_unit_supported(row_unit, text) for text in unit_texts if text):
        row["unverified_identity_raw"]["unit"] = row_unit
        row["unit"] = None
        issues.append({"code": "unit_unbound", "message": "Row unit is not present in the bound report text/header."})
    if row.get("kind") == "assertion":
        fragments = row.get("statement_fragments", []) or []
        bound, physical, fragment_issues = [], [], []
        # A whole block and one of its contained fragments must not be pasted
        # into the statement twice. Keep the maximal supported passage and an
        # explicit quality diagnosis; the original response remains archived.
        reduced = []
        for index, fragment in enumerate(fragments):
            ref, text = fragment.get("ref"), fragment.get("text", "")
            segment = catalog.get(ref, {})
            objects = {loc.get("ir_object_id") for loc in segment.get("locators", []) if loc.get("ir_object_id")}
            redundant = False
            for other_index, other in enumerate(fragments):
                other_segment = catalog.get(other.get("ref"), {})
                other_objects = {loc.get("ir_object_id") for loc in other_segment.get("locators", []) if loc.get("ir_object_id")}
                same_source = ref == other.get("ref") or bool(objects & other_objects)
                if (index != other_index and same_source and text and
                    _compact(text) in _compact(other.get("text", "")) and
                    (len(_compact(text)) < len(_compact(other.get("text", ""))) or index > other_index)):
                    redundant = True
                    break
            if not redundant:
                reduced.append(fragment)
        if len(reduced) < len(fragments):
            issues.append({"code": "statement_fragments_redundant",
                           "message": "Repeated or contained passages from the same source were retained once."})
        fragments = reduced
        if fragments:
            for fragment in fragments:
                if hasattr(fragment, "model_dump"):
                    fragment = fragment.model_dump()
                ref, text = str(fragment.get("ref") or ""), str(fragment.get("text") or "")
                segment = catalog.get(ref)
                if segment and text.strip() and _compact(text) in _compact(segment["text"]) and segment.get("locators"):
                    bound.append({"ref": ref, "text": text})
                    physical.extend(_statement_locations(segment, ref, text))
                else:
                    fragment_issues.append({"code": "statement_unbound", "refs": [ref],
                                            "message": "Statement fragment is absent from its named source segment."})
        else:
            statement = row.get("statement")
            matches = [segment for segment in local if statement and _compact(statement) in _compact(segment["text"])
                       and segment.get("locators")]
            if matches:
                segment = matches[0]
                bound.append({"ref": str(segment.get("span_id") or segment.get("ref")), "text": statement})
                physical.extend(_statement_locations(segment, bound[0]["ref"], statement))
            else:
                fragment_issues.append({"code": "statement_unbound", "message": "No exact statement-bearing source segment was selected."})
        if bound and not fragment_issues:
            row["statement"] = "\n".join(fragment["text"] for fragment in bound)
        row["statement_fragments"] = bound
        row["statement_span_ids"] = _unique(fragment["ref"] for fragment in bound)
        row["physical_sources"] = _unique(physical)
        issues.extend(fragment_issues)
        row["quality_status"] = "unbound" if fragment_issues else "needs_confirmation" if issues else "grounded"
    else:
        cells = {cell.get("cell_id"): cell for cell in source_row.get("cells", [])}
        for cell in row.get("cells", []):
            original = cells.get(cell.get("cell_id"), {})
            header_uncertain = _header_alignment_uncertain(original, context)
            own_local = local
            if original.get("span_id"):
                own_local = [segment for segment in local if segment.get("span_id") == original["span_id"]] or local
            combined = {**fields, **cell.get("fields", {})}
            headers = original.get("column_headers", [])
            years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", " ".join(headers)))
            if (not header_uncertain and not combined.get("reporting_period") and len(years) == 1 and
                all(context.get("bound_source_identity", {}).get(key) not in (None, "", "unknown")
                    for key in ("document_id", "ir_run_id", "ir_revision"))):
                # A literal column-header year is a source property, not a
                # report/request default. No date range or accounting method
                # is inferred from it; the full header evidence is preserved.
                combined["reporting_period"] = next(iter(years))
            own_fields, own_unverified, own_evidence, own_issues = _field_binding(
                combined, {**row.get("field_refs", {}), **cell.get("field_refs", {})},
                local=own_local, catalog=catalog, cell=original,
                bound_source_identity=context.get("bound_source_identity"),
                header_alignment_uncertain=header_uncertain)
            if header_uncertain:
                own_issues.append({"code": "header_alignment_uncertain",
                    "message": "Column header alignment is contradictory; identity requires independent source confirmation."})
            cell.update(fields=own_fields, identity_fields=own_fields,
                        column_headers=deepcopy(headers),
                        unverified_identity_raw={**unverified, **own_unverified}, identity_evidence=own_evidence,
                        field_evidence_span_ids={axis: _unique(item["span_id"] for item in items if item.get("span_id"))
                                                 for axis, items in own_evidence.items()},
                        physical_sources=[{**locator, "span_id": original.get("span_id"),
                                           "evidence_excerpt": str(cell.get("value", "")),
                                           **({key: original[key] for key in ("char_start", "char_end") if key in original})}
                                          for locator in original.get("locators", [])],
                        quality_issues=[*issues, *own_issues])
            if not cell.get("unit") and explicit_unit:
                cell["unit"] = explicit_unit
            unit = cell.get("unit")
            unit_texts = [str(original.get("unit") or ""), str(source_row.get("label") or ""), *(segment["text"] for segment in own_local),
                          *original.get("column_headers", [])]
            changed_known_unit = original.get("unit") and _compact(unit or "") != _compact(original["unit"]) and not cell.get("visual_override")
            if unit and (changed_known_unit or not any(_unit_supported(unit, text) for text in unit_texts if text)):
                cell["unverified_identity_raw"]["unit"] = unit
                cell["quality_issues"].append({"code": "unit_unbound", "message": "Unit is not present in the bound report text/header."})
                if not cell.get("visual_override"):
                    cell["unit"] = original.get("unit") or None
            if cell.get("visual_override") and not cell["physical_sources"]:
                cell["physical_sources"] = deepcopy(source_row.get("locators", []))
                cell["quality_issues"].append({"code": "visual_locator_coarse", "message": "Visible value uses a row/image anchor rather than a listed cell."})
            visual_error = _visual_binding_error(cell, original, source_row, context)
            value_error = visual_error
            if not cell.get("visual_override") and (not original or str(cell.get("value")) != str(original.get("raw"))):
                value_error = {"code": "mechanical_value_unbound", "original_value": original.get("raw"),
                               "declared_value": cell.get("value"), "message": "A mechanical value must preserve its exact selected source occurrence."}
            if value_error:
                cell["quality_issues"].append(value_error)
                cell["physical_sources"] = []
            policy = source_row.get("chart_source_policy") or {}
            if cell.get("visual_override") and is_chart_read(source_row, context):
                # A supplied image and bbox bind a new reading opportunity,
                # not proof of a category/value pair or verified pixels.
                audit = chart_visual_audit(source_row, context)
                cell["quality_issues"].append({"code": "chart_visual_candidate_pending",
                    "message": "Independent chart image reading requires confirmation; the original point ledger remains unchanged.",
                    "chart_evidence_audit": audit})
                cell["physical_sources"] = [{**source, "chart_evidence_audit": audit} for source in cell["physical_sources"]]
            elif policy.get("origin") == "exact_text_point":
                cell["physical_sources"] = [{**source, "chart_evidence_audit": {
                    "reading_policy": policy["version"], "evidence_origin": "exact_text_point",
                    "point_support": policy.get("point_support"), "visual_pixel_values_verified": False,
                    "series_metadata_verified": False}} for source in cell["physical_sources"]]
            if not cell["physical_sources"]:
                cell["quality_issues"].append({"code": "value_locator_missing", "message": "Exact value has no physical locator."})
            for axis in ("reporting_period", "reporting_entity"):
                if not cell["fields"].get(axis):
                    cell["quality_issues"].append({"code": "identity_axis_unknown", "axis": axis,
                                                  "message": "Report identity remains unknown; no request default was used."})
            cell["quality_status"] = "unbound" if not cell["physical_sources"] else "needs_confirmation" if cell["quality_issues"] else "grounded"
            if source_row.get("source_text_frame"):
                cell["source_semantics"] = deepcopy(row.get("semantics", {}))
                cell["source_row_label"] = row.get("source_row_label")
            for key in ("span_id", "candidate_id", "char_start", "char_end"):
                if key in original:
                    cell[key] = original[key]
        row["quality_status"] = "needs_confirmation" if issues else "grounded"
    row["quality_issues"] = issues
    return row
