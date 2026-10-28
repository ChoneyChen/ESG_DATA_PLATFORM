"""Neutral source observations independent of the selected clause projection."""
from __future__ import annotations

import hashlib
import json

from esg_targeted.foundation.guards import pollutant_key
from esg_targeted.foundation.kernel import infer_unit, scope2_method_from_text, unit_dimension
from esg_targeted.foundation.tables import boundary_label, number_text, parse_measure, period_years
from esg_targeted.ids import stable_id
from esg_targeted.report_facts import SourceFact


def _period(header: str, fallback: int | None) -> tuple[str | None, int | None, str | None, str | None]:
    years = period_years(header)
    if years:
        year = years[-1]
        return header, year, f"{year}-01-01", f"{year}-12-31"
    if fallback is not None:
        return str(fallback), fallback, None, None
    return header or (str(fallback) if fallback else None), years[-1] if years else fallback, None, None



def energy_carrier(label: str) -> str | None:
    folded = label.casefold()
    for code, terms in {
        "solar": ("太阳能", "光伏", "solar", "photovoltaic"),
        "wind": ("风能", "风电", "wind"),
        "hydro": ("水能", "水电", "hydro"),
        "biomass": ("生物质", "biomass"),
        "coal": ("煤炭", "原煤", "coal"),
        "natural_gas": ("天然气", "natural gas"),
        "diesel": ("柴油", "diesel"),
        "gasoline": ("汽油", "gasoline"),
        "electricity": ("电力", "electricity"),
        "steam": ("蒸汽", "steam"),
    }.items():
        if any(term in folded for term in terms):
            return code
    return None


def table_source_facts(task, reporting_year: int | None, *, document: dict | None = None) -> list[dict]:
    """One SourceFact per physical cell, including unmapped and historical cells.

    Clause mapping is deliberately absent. A historical cell is not promoted to
    the current-period observation merely because the table has a selected column.
    """
    view = task.view
    document = document or {}
    facts = []
    for line in task.lines:
        for col in view.value_cols:
            cell = line.values.get(col)
            if cell is None:
                continue
            raw = number_text(cell.text)
            if parse_measure(raw) is None:
                continue
            if cell.cell_id in view.corrected_cells:
                raw = view.corrected_cells[cell.cell_id]
            header = " › ".join(view.column_paths.get(col, []))
            period, period_year, start, end = _period(header, reporting_year if len(view.value_cols) == 1 else None)
            unit, unit_source = infer_unit(line, view, col)
            boundary = boundary_label(view, line.label, line.section)
            entity_basis = "subset" if boundary else "unknown"
            identity = {
                "source_row_label": line.label,
                "reporting_period": period,
                "period_start": start,
                "period_end": end,
                "entity_basis": entity_basis,
                "column_header": header,
                "unit_source": unit_source,
                "value_qualifier": parse_measure(raw)["comparator"],
            }
            if boundary:
                identity["reporting_boundary"] = boundary
            member = pollutant_key(line.label) if set(task.groups) & {"air", "water_pollutants"} else None
            if member:
                identity["pollutant"] = member
            carrier = energy_carrier(line.label) if "energy" in task.groups else None
            if carrier:
                identity["energy_carrier"] = carrier
            if "ghg" in task.groups:
                method = scope2_method_from_text(line.label, line.section)
                if method:
                    identity["method"] = {"E1-6_09": "location_based", "E1-6_10": "market_based"}[method]
            if unit_dimension(unit):
                identity["reported_unit_dimension"] = unit_dimension(unit)
            physical = {"ir_object_type": "cell", "ir_object_id": cell.cell_id,
                        "pdf_page_index": view.page_number - 1, "printed_page_label": None,
                        "bbox": {"x0": None, "y0": None, "x1": None, "y1": None}}
            source_identity = {"document_id": document["document_id"],
                               "ir_run_id": document["ir_run_id"],
                               "ir_revision": document["ir_revision"],
                               "line_id": line.rid, "column": col, "table_id": view.table_id}
            source_key = stable_id("foundation-source", source_identity)
            evidence = {
                "value": [{"ir_object_id": cell.cell_id, "pdf_page_index": view.page_number - 1,
                           "evidence_role": "cell", "raw_text": raw}],
                "source_row_label": [{"ir_object_id": cell.cell_id, "pdf_page_index": view.page_number - 1,
                                      "evidence_role": "row", "raw_text": line.label}],
                "reporting_period": [{"ir_object_id": cell.cell_id, "pdf_page_index": view.page_number - 1,
                                      "evidence_role": "column_header", "raw_text": header}],
                "reporting_boundary": ([{"ir_object_id": cell.cell_id, "pdf_page_index": view.page_number - 1,
                                         "evidence_role": "row_context", "raw_text": boundary}] if boundary else []),
            }
            if member:
                evidence["pollutant"] = [{"ir_object_id": cell.cell_id, "pdf_page_index": view.page_number - 1,
                                          "evidence_role": "row", "raw_text": line.label}]
            if carrier:
                evidence["energy_carrier"] = [{"ir_object_id": cell.cell_id, "pdf_page_index": view.page_number - 1,
                                                "evidence_role": "row", "raw_text": line.label}]
            if "ghg" in task.groups and identity.get("method"):
                evidence["method"] = [{"ir_object_id": cell.cell_id, "pdf_page_index": view.page_number - 1,
                                       "evidence_role": "row", "raw_text": line.label}]
            fact = SourceFact(
                source_fact_id=stable_id("source-fact", source_key), source_key=source_key,
                source_anchor_id=stable_id("source-anchor", source_key),
                document_id=document["document_id"], ir_run_id=document["ir_run_id"], ir_revision=document["ir_revision"],
                source_object_id=view.table_id, source_row_id=line.rid, source_row_label=line.label,
                kind="quantitative", value_raw=raw, unit_raw=unit, column=f"c{col}",
                column_headers=view.column_paths.get(col, []), identity_raw=identity,
                identity_evidence=evidence, physical_sources=[physical], quality_status="grounded",
                interpretation_version="foundation-neutral-cell-v1",
            ).model_dump(mode="json")
            fact["interpretation_digest"] = hashlib.sha256(json.dumps(
                {key: value for key, value in fact.items() if key not in {"source_fact_id", "interpretation_digest", "review_status"}},
                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            facts.append(fact)
    return facts


def neutral_facts(tasks, reporting_year: int | None, *, document: dict | None = None) -> list[dict]:
    facts = [fact for task in tasks for fact in table_source_facts(task, reporting_year, document=document)]
    identities = {}
    for fact in facts:
        previous = identities.get(fact["source_fact_id"])
        if previous and previous != fact:
            raise ValueError(f"Conflicting neutral source identity: {fact['source_fact_id']}")
        identities[fact["source_fact_id"]] = fact
    return list(identities.values())
