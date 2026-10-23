"""Standard-neutral report facts, mappings and scoped evidence gaps.

A source interpretation is immutable within its evidence and provider identity.
Package projections reference these facts; they never rewrite report meanings.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from esg_targeted.ids import stable_id
from esg_targeted.contracts import EvidenceGap
from esg_targeted.standards.runtime_snapshot import execution_catalog_dict, metric_binding_identity


# These are report reading axes, not a package's element/enum contract.
NEUTRAL_FIELD_DEFINITIONS = {
    "reporting_period": "Verbatim period identified by the complete column header or report text",
    "period_start": "Explicit start of the disclosed reporting period, if stated",
    "period_end": "Explicit end of the disclosed reporting period, if stated",
    "reporting_entity": "Verbatim entity represented by the row or column",
    "entity_aggregation_role": "Observed entity role, such as reporting entity, subsidiary or combined entities",
    "reporting_boundary": "Verbatim operational/geographical boundary, only if stated",
    "aggregation_role": "Verbatim total, subtotal or component label",
    "measurement_kind": "Observed absolute amount, share, intensity, capacity, production or change label",
    "scope": "Verbatim disclosed GHG scope",
    "method": "Verbatim accounting/measurement method",
    "amount_basis": "Explicit gross/net amount basis; a total label alone does not establish gross",
    "gwp_basis": "Explicit GWP assessment basis and horizon",
    "consolidation_approach": "Explicit operational control, financial control or equity share approach",
    "accounting_standard": "Explicit accounting standard/version applied to the measurement",
    "category": "Verbatim disclosed category or list member",
    "carrier": "Verbatim energy carrier/source",
    "flow": "Verbatim consumption/production or flow qualifier",
    "pollutant": "Verbatim pollutant/substance label",
    "medium": "Verbatim air/water/soil or other environmental medium",
    "site": "Verbatim site/facility",
    "business_segment": "Verbatim business/industry segment",
    "denominator": "Verbatim denominator of a reported intensity/ratio",
    "additional_breakdown": "Other explicitly disclosed subdivision",
}


class SourceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_fact_id: str
    source_anchor_id: str | None = None
    interpretation_digest: str | None = None
    source_key: str
    document_id: str
    ir_run_id: str
    ir_revision: int
    source_object_id: str
    source_row_id: str
    source_row_label: str | None = None
    kind: Literal["quantitative", "assertion"]
    value_raw: str | None = None
    statement_raw: str | None = None
    unit_raw: str | None = None
    column: str | None = None
    column_headers: list[str] = Field(default_factory=list)
    identity_raw: dict[str, Any] = Field(default_factory=dict)
    semantics: dict[str, Any] = Field(default_factory=dict)
    physical_sources: list[dict[str, Any]] = Field(default_factory=list)
    identity_evidence: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    unverified_identity_raw: dict[str, Any] = Field(default_factory=dict)
    quality_status: Literal["grounded", "needs_confirmation", "unbound"] = "needs_confirmation"
    quality_issues: list[dict[str, Any]] = Field(default_factory=list)
    interpretation_version: str
    visual_override: bool = False
    review_status: Literal["candidate", "needs_confirmation"] = "candidate"


class RequirementMapping(BaseModel):
    source_fact_id: str
    package_id: str
    package_version: str
    package_digest: str
    metric_id: str
    match: Literal["match", "different", "uncertain"]
    reason: str


def source_anchor_id(source_key: str, row: dict, cell: dict | None = None, ordinal: int = 0) -> str | None:
    """Identify an actual source position, independently of semantic row order.

    The interpretation digest retains semantic slots and their meaning. This
    anchor names the selected IR cell/occurrence, or real assertion passages;
    equal reported values alone never establish shared physical identity.
    """
    group_id = row.get("group_id")
    if not group_id:
        return None
    if row.get("kind") == "quantitative":
        cell = cell or {}
        if cell.get("cell_id"):
            position = {"cell_id": cell["cell_id"]}
        elif cell.get("span_id") and cell.get("char_start") is not None and cell.get("char_end") is not None:
            position = {key: cell[key] for key in ("span_id", "char_start", "char_end")}
        else:
            locations = _physical_anchor_locations(cell.get("physical_sources", []))
            if not locations:
                return None
            # Unlisted visual cells have no canonical cell ID. Only their real
            # column and source coordinates can distinguish their location.
            position = {"column": cell.get("column"), "locations": locations}
        return stable_id("source-anchor", source_key, group_id, position)
    locations = _physical_anchor_locations(row.get("physical_sources", []), include_excerpt=True)
    fragments = [{"span_id": fragment.get("ref") or fragment.get("span_id"), "excerpt": fragment.get("text")}
                 for fragment in row.get("statement_fragments", [])
                 if (fragment.get("ref") or fragment.get("span_id")) and fragment.get("text")]
    if not locations:
        return None
    passages = sorted({json.dumps(fragment, ensure_ascii=False, sort_keys=True) for fragment in fragments})
    return stable_id("source-anchor", source_key, group_id, {"locations": locations, "passages": passages})


def _physical_anchor_locations(locations, *, include_excerpt=False):
    keys = ("ir_object_type", "ir_object_id", "pdf_page_index", "bbox", "span_id", "char_start", "char_end")
    if include_excerpt:
        keys += ("evidence_excerpt",)
    physical = [{key: location[key] for key in keys if location.get(key) is not None}
                for location in locations if location.get("ir_object_id") or location.get("span_id")]
    return sorted({json.dumps(location, ensure_ascii=False, sort_keys=True) for location in physical})


def source_facts(source: dict, identity: dict, obj: dict) -> list[dict]:
    """Legacy row-to-fact expansion; new report semantics use models.source_facts."""
    context_rows = {r["group_id"]: r for r in obj["rows"]}
    output, positions = [], {}
    for row in source.get("rows", []):
        if row.get("kind") == "context":
            continue  # a consumed header/section/empty row is not a report fact
        context = context_rows.get(row["group_id"], {})
        common = dict(source_key=source["source_key"], document_id=identity["document_id"],
                      ir_run_id=identity["ir_run_id"], ir_revision=identity["ir_revision"],
                      source_object_id=source["object_id"], source_row_id=row["source_id"],
                      source_row_label=row.get("source_row_label"), kind=row["kind"],
                      semantics=row.get("semantics", {}), interpretation_version=source["version"])
        cells = row.get("cells", []) if row["kind"] == "quantitative" else [{}]
        for ordinal, cell in enumerate(cells):
            context_cell = next((c for c in context.get("cells", []) if c.get("cell_id") == cell.get("cell_id")), {})
            annotated = "quality_status" in row or "quality_status" in cell
            physical = (cell.get("physical_sources", []) if row["kind"] == "quantitative" else row.get("physical_sources", [])) if annotated else (context_cell.get("locators") or context.get("locators") or [])
            quality_issues = cell.get("quality_issues", row.get("quality_issues", []))
            quality_status = cell.get("quality_status", row.get("quality_status", "needs_confirmation"))
            anchor = source_anchor_id(source["source_key"], row, cell, ordinal)
            candidate_key = anchor or stable_id("source-candidate", source["source_key"], row["source_id"], ordinal)
            fact = SourceFact(
                source_fact_id=candidate_key, source_anchor_id=anchor,
                value_raw=str(cell["value"]) if cell.get("value") is not None else None,
                statement_raw=row.get("statement") if row["kind"] == "assertion" else None,
                unit_raw=cell.get("unit") if annotated and row["kind"] == "quantitative" else cell.get("unit") or row.get("unit"), column=cell.get("column"),
                column_headers=cell.get("column_headers", []),
                identity_raw=cell.get("identity_fields", {**row.get("fields", {}), **cell.get("fields", {})}),
                identity_evidence=cell.get("identity_evidence", row.get("identity_evidence", {})),
                unverified_identity_raw=cell.get("unverified_identity_raw", row.get("unverified_identity_raw", {})),
                quality_status=quality_status, quality_issues=quality_issues,
                review_status="needs_confirmation" if quality_status != "grounded" else "candidate",
                physical_sources=physical, visual_override=bool(cell.get("visual_override")),
                **{**common, "semantics": cell.get("source_semantics", common["semantics"]),
                   "source_row_label": cell.get("source_row_label", common["source_row_label"])},
            )
            digest = _interpretation_digest(fact)
            fact.interpretation_digest = digest
            fact.source_fact_id = stable_id("source-fact", candidate_key, digest)
            payload = fact.model_dump(mode="json")
            # This is a final defensive guard: equal values at different cells
            # remain different facts, but the same physical cell is one grain.
            if row["kind"] == "quantitative" and anchor and anchor in positions:
                prior = output[positions[anchor]]
                prior["semantics"].setdefault("source_interpretation", {}).setdefault("duplicate_candidates", []).append(
                    {"interpretation_digest": digest, "source_row_id": row["source_id"]})
                prior["quality_issues"].append({"code": "duplicate_physical_candidate",
                    "message": "Multiple interpretations name the same physical occurrence; retained once for review."})
                prior["quality_status"] = prior["review_status"] = "needs_confirmation"
                revised = SourceFact.model_validate(prior)
                revised.interpretation_digest = _interpretation_digest(revised)
                revised.source_fact_id = stable_id("source-fact", anchor, revised.interpretation_digest)
                output[positions[anchor]] = revised.model_dump(mode="json")
                continue
            if row["kind"] == "quantitative" and anchor:
                positions[anchor] = len(output)
            output.append(payload)
    return output


def _interpretation_digest(fact: SourceFact) -> str:
    """An interpretation revision, independent of mutable human review.

    Raw values alone cannot identify a fact: corrected periods/entities and
    their actual proof are part of the immutable interpretation payload too.
    """
    payload = fact.model_dump(mode="json", exclude={"source_fact_id", "source_anchor_id", "interpretation_digest", "review_status"})
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def adapt_direct_core_facts(records: dict, package, *, bound_core_ids: set[str], request,
                            model_executions=None, target_cells=None) -> tuple[list[dict], list[dict]]:
    """Adapt accepted mature direct results without another model call.

    Fixed standard classifications are excluded from the report identity. This
    adapter creates report facts only for real reported records with source
    locators; derived records keep their formula/dependency identity separately.
    """
    package_data = execution_catalog_dict(package) if hasattr(package, "model_dump") else package
    elements = {e["element_id"]: e for e in package_data.get("elements", [])}
    linked, evidence = {}, {}
    for collection in ("attribute_values", "dimension_values"):
        for row in records.get(collection, []):
            linked.setdefault(row.get("parent_record_id"), []).append(row)
    for row in records.get("evidence_references", []):
        evidence.setdefault(row.get("source_record_id"), []).append(row)
    facts, bindings = [], []
    aliases = {"ghg_scope": "scope", "scope_2_method": "method", "scope3_category": "category"}
    for collection, key, kind in (("quantitative_observations", "observation_id", "quantitative"),
                                 ("qualitative_assertions", "assertion_id", "assertion")):
        for record in records.get(collection, []):
            core_id = record[key]
            if core_id in bound_core_ids or record.get("value_origin") == "derived":
                continue
            citations = evidence.get(core_id, [])
            primary = next((e for e in citations if e.get("evidence_role") == "primary"), None)
            if not primary or not primary.get("document_id") or not primary.get("ir_run_id") or not primary.get("ir_object_id"):
                continue
            identity_raw = {name: record[field] for name, field in (("reporting_period", "reporting_period_raw"),
                ("reporting_boundary", "reporting_boundary"), ("period_start", "period_start"), ("period_end", "period_end"))
                if record.get(field) is not None}
            for item in linked.get(core_id, []):
                element = elements.get(item.get("element_id"), {})
                if not element or element.get("value_contract", {}).get("fixed_value") is not None:
                    continue
                value = next((item[field] for field in ("value_raw", "value_text", "member_label", "value_code", "value_numeric")
                              if item.get(field) is not None), None)
                if value is not None:
                    code = element["element_code"]
                    identity_raw[aliases.get(code, code)] = value
            target = (target_cells or {}).get(primary["ir_object_id"])
            if target and target.row_label:
                identity_raw.setdefault("source_row_label", target.row_label)
            locator = {"ir_object_type": primary["ir_object_type"], "ir_object_id": primary["ir_object_id"],
                       "pdf_page_index": primary["pdf_page_index"], "printed_page_label": primary.get("printed_page_label"),
                       "bbox": {axis: primary.get("bbox_" + axis) for axis in ("x0", "y0", "x1", "y1")}}
            execution = next((run for run in reversed(model_executions or []) if run.get("metric_id") == record["metric_id"]), {})
            role = request.roles.specialist if request.roles else None
            provider = execution.get("provider") or (role.provider if role else request.semantic_provider)
            model = execution.get("model_id") or (role.model if role else request.semantic_model) or "unknown"
            source_identity = {"document_id": primary["document_id"], "ir_run_id": primary["ir_run_id"],
                "ir_revision": primary.get("ir_revision", 1), "locator": locator, "kind": kind,
                "identity_raw": identity_raw, "value_raw": record.get("value_raw"), "statement_raw": record.get("statement_raw"),
                "unit_raw": record.get("unit_raw"), "column_headers": list(target.column_headers) if target else [], "provider": provider, "model": model,
                "interpretation_version": "direct-core-adapter-v1"}
            source_key = stable_id("direct-source", source_identity)
            fact = SourceFact(source_fact_id=stable_id("source-fact", source_key), source_key=source_key,
                document_id=primary["document_id"], ir_run_id=primary["ir_run_id"], ir_revision=primary.get("ir_revision", 1),
                source_object_id=primary["ir_object_id"], source_row_id=primary["ir_object_id"],
                source_row_label=identity_raw.get("source_row_label"), kind=kind,
                value_raw=record.get("value_raw") if kind == "quantitative" else None,
                statement_raw=record.get("statement_raw") if kind == "assertion" else None,
                unit_raw=record.get("unit_raw"), identity_raw=identity_raw, physical_sources=[locator],
                column_headers=list(target.column_headers) if target else [],
                interpretation_version="direct-core-adapter-v1").model_dump(mode="json")
            fact["source_anchor_id"] = stable_id("source-anchor", source_key)
            fact["interpretation_digest"] = _interpretation_digest(SourceFact.model_validate(fact))
            facts.append(fact)
            bindings.append({"core_record_id": core_id, "source_fact_id": fact["source_fact_id"], "metric_id": record["metric_id"],
                "match": "match", "record_type": "quantitative_observation" if kind == "quantitative" else "qualitative_assertion",
                "package_id": package.manifest.package_id, "package_version": package.manifest.package_version,
                "package_digest": package.compilation.source_digest, "route": "mature_direct_adapter",
                **metric_binding_identity(package, record['metric_id'])})
    return facts, bindings
