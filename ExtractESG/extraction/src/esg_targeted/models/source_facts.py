"""Model-owned source semantics, projected into the existing Core contract.

This layer does not infer metric matches from numbers, units or keywords. The
model maps a physical source row once; the adapter only binds its declared facts.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from esg_targeted.contracts import SemanticAssignment, SemanticDecision, SemanticFactGroup
from esg_targeted.models.response_adapter import DirectFillResponseAdapter, decode_first_json_object
from esg_targeted.evidence.target_cells import target_cell_contracts


class MetricMapping(BaseModel):
    metric_id: str
    match: Literal["match", "different", "uncertain"]
    reason: str


class SourceCell(BaseModel):
    source_fact_id: str | None = None
    visual_override: bool = False
    target_cell: str | None = None
    value: str | float | int | None = None
    unit: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    metric_mappings: list[MetricMapping] = Field(default_factory=list)
    field_evidence_span_ids: dict[str, list[str]] = Field(default_factory=dict)
    quality_status: Literal["grounded", "needs_confirmation", "unbound"] = "grounded"


class SourceRow(BaseModel):
    source_fact_id: str | None = None
    source_id: str
    kind: Literal["quantitative", "assertion"]
    group: str
    source_row_label: str | None = None
    semantics: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)
    unit: str | None = None
    cells: list[SourceCell] = Field(default_factory=list)
    statement: str | None = None
    metric_mappings: list[MetricMapping]
    context_refs: list[str] = Field(default_factory=list)
    statement_span_ids: list[str] = Field(default_factory=list)
    statement_fragments: list[dict[str, str]] = Field(default_factory=list)
    field_evidence_span_ids: dict[str, list[str]] = Field(default_factory=dict)
    quality_status: Literal["grounded", "needs_confirmation", "unbound"] = "grounded"


class MetricCoverage(BaseModel):
    metric_id: str
    status: Literal["reviewed", "uncertain"]
    reason: str


class SourceInterpretation(BaseModel):
    source_rows: list[SourceRow]
    coverage: list[MetricCoverage]


def parse_source_output(raw: str, metrics: list[dict]) -> SourceInterpretation:
    result = SourceInterpretation.model_validate(decode_first_json_object(raw))
    allowed = {m["metric_id"] for m in metrics}
    source_ids = [row.source_id for row in result.source_rows]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source_rows reuse a source_id")
    for row in result.source_rows:
        for mappings in [row.metric_mappings, *(cell.metric_mappings for cell in row.cells)]:
            mapped = [m.metric_id for m in mappings]
            if len(mapped) != len(set(mapped)) or not set(mapped) <= allowed:
                raise ValueError(f"invalid metric mapping on {row.source_id}")
    return result


def project_source_facts(result: SourceInterpretation, packet, *, visual_used: bool):
    """Isolate binding errors to their source row; leave semantics to the model."""
    metric_id = packet.metric["metric_id"]
    elements = {e["element_code"]: e for e in packet.elements if e["value_contract"].get("fixed_value") is None}
    targets = {e["binding"]["target"]: code for code, e in elements.items()}
    compiler = SourceProjectionCompiler(packet, visual_used=visual_used)
    groups, spans, errors, mappings, bindings = [], [], [], [], []
    uncertain = False
    coverage = next((c for c in result.coverage if c.metric_id == metric_id), None)
    uncertain = coverage is None or coverage.status == "uncertain"
    for source in result.source_rows:
        mapping = next((m for m in source.metric_mappings if m.metric_id == metric_id), None)
        if mapping is not None:
            mappings.append({"source_id": source.source_id, "source_fact_id": source.source_fact_id, "mapping_level": "row", **mapping.model_dump()})
        fields = compiler.standard_fields(source.fields)
        if "source_row_label" in elements and source.source_row_label:
            fields["source_row_label"] = source.source_row_label
        common = {"group": source.group, "metric_match": "match", "context_refs": source.context_refs}
        entries = []
        if source.kind == "assertion":
            if mapping is None or mapping.match != "match":
                uncertain |= bool(mapping and mapping.match == "uncertain")
                continue
            if source.quality_status == "unbound" or not source.statement or "statement_raw" not in targets:
                errors.append({"source_id": source.source_id, "error": "assertion requires a real statement and assertion target"})
                continue
            entries.append(("assertions", {**common, "interpretation_note": mapping.reason,
                                            "fields": {**fields, targets["statement_raw"]: source.statement}, "_source_fact_id": source.source_fact_id,
                                            "_statement_span_ids": source.statement_span_ids, "_statement_fragments": source.statement_fragments,
                                            "_field_evidence_span_ids": compiler.standard_fields(source.field_evidence_span_ids)}))
        else:
            applicable = []
            for cell in source.cells:
                if cell.quality_status == "unbound":
                    errors.append({"source_id": source.source_id, "target_cell": cell.target_cell, "error": "quantitative candidate has no bound physical source"})
                    continue
                cell_mapping = next((m for m in cell.metric_mappings if m.metric_id == metric_id), None)
                effective_mapping = cell_mapping or mapping
                if cell_mapping:
                    mappings.append({"source_id": source.source_id, "source_fact_id": cell.source_fact_id, "mapping_level": "cell",
                                     "target_cell": cell.target_cell, **cell_mapping.model_dump()})
                if effective_mapping is None:
                    continue
                uncertain |= effective_mapping.match == "uncertain"
                if effective_mapping.match == "match":
                    applicable.append((cell, effective_mapping))
            if not applicable:
                if not source.cells and mapping and mapping.match == "match":
                    errors.append({"source_id": source.source_id, "error": "quantitative row has no cells"})
                continue
            if "value_raw" not in targets:
                errors.append({"source_id": source.source_id, "error": "quantitative row has no cells or quantitative target"})
                continue
            for cell, effective_mapping in applicable:
                if cell.value is None:
                    errors.append({"source_id": source.source_id, "target_cell": cell.target_cell, "error": "quantitative cell has no value"})
                    continue
                values = {**fields, **compiler.standard_fields(cell.fields), targets["value_raw"]: cell.value}
                evidence_fields = compiler.standard_fields({**source.field_evidence_span_ids, **cell.field_evidence_span_ids})
                # A positive semantic mapping confirms this row's role in the
                # selected measurement. Only then may its literal, same-row
                # label supply a typed pollutant identity. Unselected amounts,
                # unknown mappings and guessed labels never activate the bridge.
                if values.get("pollutant") in (None, ""):
                    label, label_refs = compiler.confirmed_row_label(source, "pollutant", cell.target_cell)
                    if label and label_refs:
                        values["pollutant"] = label
                        evidence_fields["pollutant"] = label_refs
                if "unit_raw" in targets:
                    values[targets["unit_raw"]] = cell.unit or source.unit
                entries.append(("rows", {**common, "interpretation_note": effective_mapping.reason,
                                         "target_cell": cell.target_cell, "fields": values, "_source_fact_id": cell.source_fact_id,
                                         "_field_evidence_span_ids": evidence_fields,
                                         "_visual_value_override": bool(cell.visual_override and visual_used)}))
        for kind, row in entries:
            try:
                group = compiler.bind(row, assertion=kind == "assertions")
                groups.append(group)
                if row.get("_source_fact_id"):
                    bindings.append({"source_fact_id": row["_source_fact_id"], "group": group.model_dump(mode="json"),
                                     "record_type": "qualitative_assertion" if kind == "assertions" else "quantitative_observation"})
                spans.extend(sid for assignment in group.assignments for sid in assignment.evidence_span_ids)
            except ValueError as exc:
                errors.append({"source_id": source.source_id, "target_cell": row.get("target_cell"), "error": str(exc)})
    incomplete = uncertain or bool(errors)
    decision = SemanticDecision(
        task_id=packet.task_id,
        status=("partial" if incomplete else "found") if groups else ("ambiguous" if incomplete else "not_found"),
        fact_groups=groups, selected_evidence_span_ids=list(dict.fromkeys(spans)),
        uncertainty_code="insufficient_evidence" if incomplete else "none",
        missing_context=([coverage.reason] if coverage and coverage.status == "uncertain" else []) + [e["error"] for e in errors],
    )
    return decision, {"metric_mappings": mappings, "projection_errors": errors, "coverage": coverage.model_dump() if coverage else None, "projection_bindings": bindings}


class SourceProjectionCompiler:
    """The single mechanical source-to-Core assignment compiler.

    Shared binding primitives also protect the mature direct adapter. Agent
    projection does not serialize a second model-output protocol or infer ESG
    semantics. Field aliases only translate names of the same report axis.
    """
    FIELD_ALIASES = {
        "scope": ("scope", "ghg_scope"), "method": ("method", "scope_2_method"),
        "category": ("category", "scope3_category"), "carrier": ("carrier", "energy_carrier", "energy_source"),
        "site": ("site", "facility", "location"),
    }

    def __init__(self, packet, *, visual_used):
        self.packet, self.visual_used = packet, visual_used
        self.elements = {e["element_code"]: e for e in packet.elements
                         if e["value_contract"].get("fixed_value") is None}
        self.targets = {t.alias: t for t in target_cell_contracts(packet)}
        self.spans = {s.span_id: s for s in packet.spans}
        self.candidates = {c.candidate_id: c for c in packet.candidates}

    def standard_fields(self, fields):
        result = {}
        for name, value in fields.items():
            for code in self.FIELD_ALIASES.get(name, (name,)):
                if code in self.elements:
                    result[code] = value
        return result

    def confirmed_row_label(self, source, axis, target_alias):
        element = self.elements.get(axis)
        label = source.source_row_label
        interpretation = source.semantics.get("source_interpretation") or {}
        group_id = self.packet.alias_map.get("groups", {}).get(source.group)
        if (axis != "pollutant" or not element or not label or not group_id or
            source.kind != "quantitative" or interpretation.get("label_conflict") or
            element.get("semantic_role") not in {"dimension", "scope"}):
            return None, []
        literal = "".join(label.split())
        target = self.targets.get(target_alias)
        target_id = target.cell_id if target else None
        value_cell_ids = {item.cell_id for item in self.targets.values() if item.group_id == group_id}
        value_span_ids = {item.span_id for item in self.targets.values() if item.group_id == group_id}
        supported = [span for span in self.packet.spans if span.context_group_id == group_id and
                     span.span_type in {"table_cell", "table_row"} and span.locators and
                     not (span.span_type == "table_cell" and (span.span_id in value_span_ids or any(
                         loc.ir_object_type == "cell" and loc.ir_object_id in value_cell_ids for loc in span.locators))) and
                     span.structural_context.get("anchor_cell_id") in {None, target_id} and
                     literal in "".join(span.text.split())]
        # Prefer the real row-label cell to an annotated whole-row view.
        exact = [span for span in supported if literal == "".join(span.text.split())]
        unanchored = [span for span in exact if not span.structural_context.get("anchor_cell_id")]
        row_views = [span for span in supported if span.span_type == "table_row" and any(
            literal == "".join(str(header).split())
            for cell in span.structural_context.get("cells", []) for header in cell.get("row_header_path", []))]
        refs = [span.span_id for span in (unanchored or exact or row_views)]
        return (label, refs) if refs else (None, [])

    def bind(self, row, *, assertion=False):
        group_alias = row["group"]
        group_id = self.packet.alias_map.get("groups", {}).get(group_alias)
        if not group_id:
            raise ValueError(f"Unknown source group {group_alias}")
        target = self.targets.get(row.get("target_cell")) if not assertion else None
        if row.get("target_cell") and not target:
            raise ValueError(f"Unknown target cell {row['target_cell']}")
        if target and target.group_id != group_id:
            raise ValueError("Target cell belongs to another source group")
        if not assertion and not target and not self.visual_used:
            raise ValueError("An unlisted cell requires supplied visual evidence")
        assignments = []
        for code, raw in row["fields"].items():
            if raw is None:
                continue
            if isinstance(raw, (dict, list)):
                raise ValueError(f"Source field {code} must be a scalar")
            value = str(raw).strip()
            if not value:
                continue
            element = self.elements[code]
            explicit = row.get("_statement_span_ids", []) if element["binding"]["target"] == "statement_raw" else row.get("_field_evidence_span_ids", {}).get(code, [])
            if explicit:
                supplied = [self.spans[sid] for sid in explicit if sid in self.spans]
                if len(supplied) != len(set(explicit)):
                    raise ValueError(f"Source field {code} references a span outside this packet")
                if element["binding"]["target"] == "statement_raw":
                    fragments = row.get("_statement_fragments", [])
                    if not fragments or any(fragment.get("ref") not in explicit or not any(
                        self._verbatim(fragment.get("text", ""), span.text) for span in supplied if span.span_id == fragment.get("ref")) for fragment in fragments):
                        raise ValueError("Assertion fragments do not match their declared source spans")
                elif not any(self._verbatim(value, span.text) or any(self._verbatim(value, header) for header in span.structural_context.get("column_header_path", [])) for span in supplied):
                    raise ValueError(f"Source field {code} is absent from its declared source spans")
                assignments.append(SemanticAssignment(element_id=element["element_id"], source_ref_id=supplied[0].span_id,
                    evidence_span_ids=list(dict.fromkeys(explicit)), value_raw=value, source_mode="span",
                    support_fragments=[{"span_id": fragment["ref"], "text": fragment["text"]} for fragment in fragments]
                        if element["binding"]["target"] == "statement_raw" else [{"span_id": span.span_id, "text": value} for span in supplied],
                    support_axis=None if element["binding"]["target"] == "statement_raw" else code))
                continue
            primary = element["binding"]["target"] == "value_raw"
            override = primary and row.get("_visual_value_override") and self.visual_used
            mode, ref, span = DirectFillResponseAdapter._bind_source(
                value=value, group_id=group_id, element=element, spans=self.spans,
                candidates=self.candidates, visual_anchor_ids=set(self.packet.alias_map.get("visual", {}).values()),
                visual_used=self.visual_used, allowed_primary_values=set(target.value_forms) if primary and target and not override else None,
                target_span_id=target.span_id if primary and target and not override else None,
                prefer_visual=bool(override or (target and target.header_alignment_uncertain and not primary and self.visual_used)),
                context_span_ids=set(self.packet.context_only_span_ids),
            )
            assignments.append(SemanticAssignment(element_id=element["element_id"], source_ref_id=ref,
                                                  evidence_span_ids=[span], value_raw=value, source_mode=mode))
        return SemanticFactGroup(group_ref_id=target.cell_id if target else group_id,
                                 assignments=assignments, metric_match="match",
                                 interpretation_note=row.get("interpretation_note"),
                                 context_span_ids=list(dict.fromkeys([
                                     *(self.packet.alias_map.get("context", {}).get(ref) for ref in row.get("context_refs", [])
                                       if ref in self.packet.alias_map.get("context", {})),
                                     *(sid for ids in row.get("_field_evidence_span_ids", {}).values() for sid in ids
                                       if sid in self.packet.context_only_span_ids)])))

    @staticmethod
    def _verbatim(value, text):
        return bool(str(value).strip()) and "".join(str(value).split()).casefold() in "".join(str(text).split()).casefold()
