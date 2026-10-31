"""Evidence binding shared by every data reader. No clause identities are authored here."""

from __future__ import annotations
import re
from esg_data.hashing import digest
from esg_contracts.facts import (
    EvidenceLocation,
    ReportedField,
    ReportedContext,
    SourceFact,
    QuantitativePayload,
    QualitativePayload,
    ClauseMapping,
)
from esg_data.units import canonical_unit_id, unit_dimension, normalize_unit_text
from esg_clauses.reading import check_reading
from esg_clauses.evidence import check_evidence
from esg_data.literals import parse_measure
from .decisions import IdentityProposal
from .period_binding import bind_period
from esg_data.periods import normalize_period

CONTEXT_AXES = {
    "reporting_period",
    "entity_aggregation_role",
    "reporting_entity",
    "reporting_boundary",
    "measurement_method",
    "value_role",
    "aggregation_role",
    "denominator",
}


def location(
    frame, object_id, text, role, *, object_type=None, bbox=None, page_index=None
):
    return EvidenceLocation(
        object_id=object_id,
        object_type=object_type
        or (
            "table"
            if object_id == frame.object_id and frame.kind == "table"
            else "text_block"
        ),
        page_index=frame.page_index if page_index is None else page_index,
        excerpt=text,
        role=role,
        bbox=bbox,
    )


def context_from_claims(frame, claims, *, row=None, column_id=None, document=None, attempts=None):
    physical = [*frame.texts, *frame.title_sources]
    aliases = {}
    if column_id:
        aliases[column_id] = frame.column_sources.get(column_id, [])
        physical.extend(aliases[column_id])
    if row:
        aliases[row.row_id] = row.label_sources
        physical.extend([*row.label_sources, *row.unit_sources])
    sources = {}
    for item in physical:
        sources[item.span_id or item.object_id] = (
            item.text,
            item.object_type,
            item.bbox,
            item.page_index,
            item.object_id,
            item.char_start,
        )
    if row:
        for cell in row.cells:
            sources[cell.cell_id] = (
                cell.raw_text,
                cell.source_object_type,
                cell.bbox,
                cell.page_index if cell.page_index is not None else frame.page_index,
                cell.source_object_id or cell.cell_id,
                cell.char_start or 0,
            )
    fields = {}
    dimensions = {}
    flags = []
    for proposed in claims:
        try:
            claim = IdentityProposal.model_validate(proposed)
        except ValueError:
            flags.append("identity_metadata_requires_confirmation")
            continue
        if claim.state == "not_stated":
            value = ReportedField(state="not_stated")
        elif claim.state == "conflict":
            value = ReportedField(state="conflict")
            flags.append("identity_conflict:" + claim.axis)
        else:
            source = sources.get(claim.source_id)
            if source is None and claim.quote:
                candidates = [
                    item
                    for item in aliases.get(claim.source_id, [])
                    if claim.quote in item.text
                ]
                if len(candidates) == 1:
                    item = candidates[0]
                    source = (
                        item.text,
                        item.object_type,
                        item.bbox,
                        item.page_index,
                        item.object_id,
                        item.char_start,
                    )
            if (
                source is None
                or not claim.quote
                or claim.quote not in source[0]
                or claim.value is None
            ):
                flags.append("unbound_identity:" + claim.axis)
                continue
            evidence = EvidenceLocation(
                object_id=source[4],
                object_type=source[1],
                page_index=source[3],
                excerpt=claim.quote,
                role="row" if row and claim.source_id == row.row_id else "table",
                bbox=source[2],
                row_id=row.row_id if row else None,
            )
            value = ReportedField(
                state="stated",
                raw_text=claim.quote,
                value=claim.value,
                evidence=[evidence],
            )
        (fields if claim.axis in CONTEXT_AXES else dimensions)[claim.axis] = value
    period, period_flags = bind_period(frame, row=row, column_id=column_id, document=document)
    if period is not None:
        fields["reporting_period"] = period
    flags.extend(period_flags)
    if attempts is not None:
        attempts.extend(flags)
    # Diagnostics assess final bound fields, not superseded model proposals.
    final = {**fields, **dimensions}
    flags = [flag for flag in flags if not (
        flag.startswith(("unbound_identity:", "identity_conflict:"))
        and final.get(flag.split(":", 1)[1]) is not None
        and final[flag.split(":", 1)[1]].state in {"stated", "inherited"}
    )]
    return ReportedContext(**fields, dimensions=dimensions), list(dict.fromkeys(flags))


def _number(raw):
    if re.search(r"\d\.\d{3},\d{1,2}(?:\D|$)|^\s*\d+,\d{1,2}\s*$", raw):
        raise ValueError("Ambiguous decimal separators; retain printed value")
    parsed = parse_measure(raw)
    if parsed is None:
        # Combined quantities keep the printed unit; no hidden canonical conversion.
        match = re.fullmatch(
            r"\s*([<>≤≥≈~约約]?\s*[-+−]?\d[\d,，]*(?:\.\d+)?)\s*([^\d]+)\s*", raw
        )
        if not match:
            raise ValueError("Selected cell is not a supported quantitative literal")
        parsed = parse_measure(match.group(1))
        suffix = match.group(2).strip()
        if parsed is None or canonical_unit_id(suffix) is None:
            raise ValueError("Unbound quantitative unit suffix")
    else:
        suffix = None
    comparator = parsed["comparator"]
    return parsed, comparator, suffix


def numeric_fact(clause, document, frame, match, row, cell, interpretation_id):
    if match.selection != "measurement" or cell.candidate_kind not in {"measurement", "unresolved"}:
        return None, None, "not_a_reported_measurement"
    if re.fullmatch(r"\s*(?:[-—–－]+|N/?A|不适用|未披露|未提供|无数据|not\s+(?:disclosed|available))\s*", cell.raw_text, re.I):
        return None, None, "not_a_reported_measurement"
    raw = cell.raw_text.strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", raw) and re.search(r"\d", raw):
        return None, None, "identifier_is_not_a_measurement"
    if not re.search(r"[\d〇零一二三四五六七八九十百千万亿兩两壹贰叁肆伍陆柒捌玖拾佰仟]", raw) and not re.search(
        r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion|nil|none)\b", raw, re.I
    ):
        return None, None, "label_or_unit_is_not_a_measurement"
    try:
        parsed, comparator, suffix = _number(cell.raw_text)
        parse_status = "parsed"
    except ValueError:
        parsed = {"value": None, "lower": None, "upper": None}
        comparator = "unknown"
        suffix = None
        parse_status = "unparsed"
    column_unit = None
    for header in frame.columns.get(cell.column_id, []):
        for candidate in re.findall(r"[（(]([^（）()]{1,30})[）)]", header):
            if canonical_unit_id(candidate):
                column_unit = candidate
                break
    # The actual cell and column outrank a shared row-unit label.
    unit = suffix or column_unit or row.unit_raw
    if unit:
        for source in row.unit_sources:
            if normalize_unit_text(source.text) == normalize_unit_text(unit):
                unit = source.text
                break
    if not unit and frame.title:
        for candidate in re.findall(r"[（(]([^（）()]{1,30})[）)]", frame.title):
            if canonical_unit_id(candidate):
                unit = candidate
                break
    number = format(parsed["value"], "f") if parsed["value"] is not None else None
    value = QuantitativePayload(
        value_raw=cell.raw_text,
        parse_status=parse_status,
        comparator=comparator,
        value_numeric=number,
        value_lower=format(parsed["lower"], "f")
        if parsed["lower"] is not None
        else None,
        value_upper=format(parsed["upper"], "f")
        if parsed["upper"] is not None
        else None,
        unit_raw=unit,
    )
    attempts = []
    context, flags = context_from_claims(
        frame, [*match.identity, *match.column_identity.get(cell.column_id, [])], row=row, column_id=cell.column_id, document=document, attempts=attempts
    )
    evidence = [
        EvidenceLocation(
            object_id=cell.source_object_id or cell.cell_id,
            object_type=cell.source_object_type,
            page_index=cell.page_index
            if cell.page_index is not None
            else frame.page_index,
            excerpt=cell.raw_text,
            role="value",
            bbox=cell.bbox,
            char_start=cell.char_start,
            char_end=cell.char_end,
            column_id=cell.column_id,
            row_id=row.row_id,
        )
    ]
    for source in row.label_sources:
        evidence.append(
            EvidenceLocation(
                object_id=source.object_id,
                object_type=source.object_type,
                page_index=source.page_index,
                excerpt=source.text,
                role="row",
                bbox=source.bbox,
                row_id=row.row_id,
            )
        )
    if unit:
        sources = [
            *row.unit_sources,
            *frame.column_sources.get(cell.column_id, []),
            *frame.title_sources,
            *[t for t in frame.texts if t.object_type != "cell"],
        ]
        support = next((source for source in sources if unit in source.text), None)
        if support:
            evidence.append(
                EvidenceLocation(
                    object_id=support.object_id,
                    object_type=support.object_type,
                    page_index=support.page_index,
                    excerpt=unit,
                    role="unit",
                    bbox=support.bbox,
                    row_id=row.row_id if support in row.unit_sources else None,
                )
            )
        elif unit in cell.raw_text:
            evidence.append(
                evidence[0].model_copy(
                    update={
                        "excerpt": unit,
                        "role": "unit",
                        "char_start": None,
                        "char_end": None,
                    }
                )
            )
        else:
            flags.append("unit_source_unconfirmed")
    reading = check_reading(
        clause,
        row.label,
        row.section,
        unit,
        unit_dimension(unit),
        evidence=" ".join(
            [
                frame.title or "",
                *frame.columns.get(cell.column_id, []),
                *(t.text for t in frame.texts if t.object_type != "cell"),
            ]
        ),
    )
    if parse_status != "parsed":
        flags.append("value_requires_parsing")
    if not unit:
        flags.append("unit_not_stated")
    if document.limited_evidence:
        flags.append("document_evidence_limited")
    anchor = digest({"document": document_key(document), "cell": cell.cell_id})
    fid = digest(
        {
            "document_snapshot": document.model_dump(mode="json"),
            "anchor": anchor,
            "interpretation": interpretation_id,
            "value": value.model_dump(mode="json"),
            "identity": context.model_dump(mode="json"),
        }
    )
    fact = SourceFact(
        source_fact_id=fid,
        source_anchor_id=anchor,
        document_id=document.document_id,
        ir_run_id=document.ir_run_id,
        ir_revision=document.ir_revision,
        report_resource=document.report_resource,
        interpretation_id=interpretation_id,
        value=value,
        source_row_label=row.label,
        identity=context,
        evidence=evidence,
        quality="grounded",
        flags=flags,
        extensions={"identity_attempts": attempts} if attempts else {},
    )
    mapping = mapping_for(clause, document, fact, match.match, match.reason)
    mapping = apply_reading(mapping, reading)
    return fact, mapping, None


def qualitative_fact(clause, document, frame, match, interpretation_id):
    text = next(
        (t for t in frame.texts if (t.span_id or t.object_id) == match.span_id), None
    )
    if text is None:
        raise ValueError("Statement does not name a visible evidence span")
    raw = match.quote or text.text
    if raw not in text.text or text.text.count(raw) != 1:
        raise ValueError(
            "Statement quote is missing or ambiguous within its source span"
        )
    start = text.char_start + text.text.index(raw)
    end = start + len(raw)
    value = QualitativePayload(
        statement_raw=raw,
        extensions={"source_text_raw": text.text}
        if match.quote and raw != text.text
        else {},
    )
    reading = check_reading(
        clause,
        raw,
        frame.title,
        None,
        None,
        evidence=" ".join(t.text for t in frame.texts),
    )
    attempts = []
    context, flags = context_from_claims(frame, match.identity, document=document, attempts=attempts)
    if match.quote and raw != text.text:
        flags.append("statement_is_excerpt")
    if document.limited_evidence:
        flags.append("document_evidence_limited")
    anchor = digest(
        {
            "document": document_key(document),
            "object": text.object_id,
            "start": start,
            "end": end,
        }
    )
    fact = SourceFact(
        source_fact_id=digest(
            {
                "document_snapshot": document.model_dump(mode="json"),
                "anchor": anchor,
                "interpretation": interpretation_id,
            }
        ),
        source_anchor_id=anchor,
        document_id=document.document_id,
        ir_run_id=document.ir_run_id,
        ir_revision=document.ir_revision,
        report_resource=document.report_resource,
        interpretation_id=interpretation_id,
        value=value,
        identity=context,
        evidence=[
            EvidenceLocation(
                object_id=text.object_id,
                object_type=text.object_type,
                page_index=text.page_index,
                excerpt=raw,
                role="statement",
                bbox=text.bbox,
                char_start=start,
                char_end=end,
            )
        ],
        quality="grounded",
        flags=flags,
        extensions={"identity_attempts": attempts} if attempts else {},
    )
    mapping = mapping_for(clause, document, fact, match.match, match.reason)
    mapping = apply_reading(mapping, reading)
    return fact, mapping, None


def mapping_for(clause, document, fact, match, reason):
    covered = []
    flags = list(fact.flags)
    fields = {
        "value": [
            item.model_dump(mode="json")
            for item in fact.evidence
            if item.role == "value"
        ],
        "source_row_label": [
            item.model_dump(mode="json") for item in fact.evidence if item.role == "row"
        ],
        "unit": [
            item.model_dump(mode="json")
            for item in fact.evidence
            if item.role == "unit"
        ],
        "statement_context": [
            item.model_dump(mode="json")
            for item in fact.evidence
            if item.role == "statement"
        ],
    }
    for name in ("reporting_period", "reporting_boundary"):
        fields[name] = [
            item.model_dump(mode="json")
            for item in getattr(fact.identity, name).evidence
        ]
    flags.extend(check_evidence(clause, fields))
    flags.extend(
        clause.organization.confirmation_flags(fact.identity.model_dump(mode="json"))
    )
    axes = {
        "entity": fact.identity.entity_aggregation_role,
        "method": fact.identity.measurement_method,
        "value_role": fact.identity.value_role,
        "aggregation": fact.identity.aggregation_role,
    }
    discriminators = set(clause.purposes.target_scope.discriminating_axes)
    scope_conflicts = []
    for scope in clause.output_scopes:
        required = {
            key: value
            for key, value in {
                "entity": scope.entity,
                "value_role": scope.value_role,
                "aggregation": scope.aggregation,
            }.items()
            if value is not None
        }
        if scope.method:
            required["method"] = scope.method
        complete = True
        conflict = False
        known_conflicts = []
        for axis, wanted in required.items():
            field = axes[axis]
            if field.state not in {"stated", "inherited"}:
                complete = False
            elif field.value != wanted:
                conflict = True
                known_conflicts.append(axis)
        for axis, wanted in scope.axes.items():
            field = fact.identity.dimensions.get(axis)
            if not field or field.state not in {"stated", "inherited"}:
                complete = False
            elif field.value != wanted:
                conflict = True
                known_conflicts.append(axis)
        period_field = fact.identity.reporting_period
        period = normalize_period(period_field.value)
        if scope.period:
            if period_field.state not in {"stated", "inherited"} or period is None:
                complete = False
            elif scope.period in {"target_period", "baseline_period", "restated_period"}:
                wanted = {"target_period": "target", "baseline_period": "baseline", "restated_period": "restated"}[scope.period]
                if fact.identity.value_role.value != wanted:
                    complete = False
            elif scope.period == "annual":
                if period.kind not in {"annual", "fiscal_year"}:
                    complete = False
            elif scope.period != period.kind and not (scope.period in {"cumulative", "multi_year"} and period.kind == "range"):
                complete = False
        if complete and not conflict:
            covered.append(scope.scope_id)
        scope_conflicts.append(known_conflicts)
    if not covered:
        flags.append("scope_requires_confirmation")
    for axis in clause.purposes.comparison.required_axes:
        field = getattr(fact.identity, axis, None) or fact.identity.dimensions.get(axis)
        if field is None or field.state not in {"stated", "inherited"}:
            flags.append("comparison_axis_missing:" + axis)
    # Missing context affects comparison completeness, never raw admission or
    # an otherwise supported target relation. Explicit semantic conflict wins.
    different = bool(scope_conflicts) and all(set(conflicts) & discriminators for conflicts in scope_conflicts)
    ambiguous = any((axes.get(axis) or fact.identity.dimensions.get(axis)) is not None and
        (axes.get(axis) or fact.identity.dimensions.get(axis)).state == "conflict" for axis in discriminators)
    relation = "different" if different else "uncertain" if ambiguous else match
    return ClauseMapping(
        clause_key=clause.clause_key,
        clause_digest=clause.clause_digest,
        source_fact_id=fact.source_fact_id,
        match=relation,
        reason=reason,
        scope_ids=covered,
        flags=list(dict.fromkeys(flags)),
    )


def apply_reading(mapping, reading):
    flags = list(
        dict.fromkeys(
            [
                *mapping.flags,
                *reading.flags,
                *([reading.status] if reading.status else []),
            ]
        )
    )
    if not reading.ok:
        return mapping.model_copy(
            update={
                "match": "different",
                "scope_ids": [],
                "reason": "; ".join(reading.flags) or "reading_contract_rejected",
                "flags": flags,
            }
        )
    if (reading.status or reading.flags) and mapping.match != "different":
        return mapping.model_copy(update={"match": "uncertain", "flags": flags})
    return mapping


def document_key(document):
    return {
        name: getattr(document, name)
        for name in ("document_id", "ir_run_id", "ir_revision", "content_digest")
    }
