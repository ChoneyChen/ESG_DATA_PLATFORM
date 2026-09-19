"""Backend-owned, lossless presentation of the six immutable fact collections.

No semantic veto, inferred labels or calculated facts. Unit conversion is used
only to link compatible reported presentations, never to produce an observation.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re
import unicodedata


ROLE_ORDER = ["subject", "category", "period", "dimension", "scope", "method", "value", "unit", "context"]
IDENTITY_CODE_ORDER = {
    "reporting_entity": 20,
    "entity_aggregation_role": 30,
    "aggregation_role": 31,
    "ghg_scope": 40,
    "scope_2_method": 41,
    "scope3_category": 42,
}


def element_order(element):
    role = element.get("semantic_role", "context")
    fixed = element.get("value_contract", {}).get("fixed_value") is not None
    code = element.get("element_code", "")
    return (
        fixed,
        code == "additional_breakdown",
        IDENTITY_CODE_ORDER.get(
            code,
            ROLE_ORDER.index(role) if role in ROLE_ORDER else 99,
        ),
        code,
    )


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def fact_id(fact):
    return fact.get("observation_id") or fact.get("assertion_id")


class FactOrganizer:
    def organize(self, records, package):
        package = package.model_dump(mode="json") if hasattr(package, "model_dump") else package
        elements = {e["element_id"]: e for e in package.get("elements", [])}
        units = {u["unit_id"]: u for u in package.get("core", {}).get("common_units", [])}
        linked, evidence = defaultdict(list), defaultdict(list)
        for key in ("attribute_values", "dimension_values"):
            for row in records.get(key, []):
                linked[row.get("parent_record_id")].append(row)
        for row in records.get("evidence_references", []):
            evidence[row.get("source_record_id")].append(row)

        def identity_dimensions(fact):
            result = []
            for row in linked[fact_id(fact)]:
                element = elements.get(row.get("element_id"), {})
                if element.get("semantic_role") in {"value", "unit", "period"}:
                    continue
                value = next((row[k] for k in ("value_raw", "value_text", "value_code", "value_numeric", "member_label", "member_code") if row.get(k) is not None), None)
                if value is not None:
                    result.append({
                        "element_id": row.get("element_id"),
                        "element_code": element.get("element_code", row.get("element_id", "")),
                        "semantic_role": element.get("semantic_role", "context"),
                        "fixed": element.get("value_contract", {}).get("fixed_value") is not None,
                        "value": str(value),
                        "normalized_value": normalized(value),
                    })
            return sorted(
                result,
                key=lambda item: (
                    IDENTITY_CODE_ORDER.get(
                        item["element_code"],
                        ROLE_ORDER.index(item["semantic_role"])
                        if item["semantic_role"] in ROLE_ORDER else 99,
                    ),
                    item["element_code"],
                    item["normalized_value"],
                ),
            )

        def dimensions(fact):
            return tuple(
                (item["element_code"], item["normalized_value"])
                for item in identity_dimensions(fact)
            )

        def period(fact):
            raw = normalized(fact.get("reporting_period_raw"))
            match = re.fullmatch(r"((?:19|20)\d{2})(?:年|年度)?", raw)
            return match[1] if match else raw or str(fact.get("reporting_year") or "")

        def order(fact):
            years = re.findall(r"(?:19|20)\d{2}", period(fact))
            primary = next(
                (item for item in evidence[fact_id(fact)] if item.get("evidence_role") == "primary"),
                {},
            )
            source_order = (
                primary.get("pdf_page_index", 10_000),
                primary.get("ir_object_id", ""),
            )
            return (
                fact.get("metric_id", ""),
                dimensions(fact),
                -max(map(int, years), default=0),
                period(fact),
                normalized(fact.get("reporting_boundary")),
                source_order,
                str(fact_id(fact)),
            )

        facts = sorted([*records.get("quantitative_observations", []), *records.get("qualitative_assertions", [])], key=order)
        buckets, groups, annotations = defaultdict(list), [], {}
        for fact in facts:
            fid = fact_id(fact)
            typed_identity = identity_dimensions(fact)
            dims, per = dimensions(fact), period(fact)
            primary = next((e for e in evidence[fid] if e.get("evidence_role") == "primary"), {})
            source_coordinates = {
                key: primary.get(key)
                for key in ("pdf_page_index", "ir_object_type", "ir_object_id", "bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")
                if primary.get(key) is not None
            }
            identity = (fact.get("metric_id"), dims, per, normalized(fact.get("reporting_boundary")), fact.get("comparator"), fact.get("period_start"), fact.get("period_end"))
            # Missing identity must not collapse unrelated equal numbers.
            if not per or not dims:
                identity += (primary.get("ir_object_id", fid),)
            match, relation = None, "single"
            for group in buckets[identity]:
                relation = self._relation(group["_fact"], fact, units)
                if relation:
                    match = group
                    break
            if match is None:
                match = {"logical_measurement_id": f"measurement:{fid}", "metric_id": fact.get("metric_id"), "fact_ids": [], "evidence_ids": [], "relations": [], "_fact": fact}
                groups.append(match)
                buckets[identity].append(match)
                relation = "single"
            match["fact_ids"].append(fid)
            match["evidence_ids"] = list(dict.fromkeys([*match["evidence_ids"], *(e.get("evidence_id") for e in evidence[fid] if e.get("evidence_id"))]))
            match["relations"].append(relation)
            annotations[fid] = {
                "logical_measurement_id": match["logical_measurement_id"],
                "relation": relation,
                "semantic_identity": {
                    "period": per or None,
                    "reporting_boundary": fact.get("reporting_boundary"),
                    "dimensions": typed_identity,
                },
                "source_coordinates": source_coordinates,
                "identity_complete": bool(per) and (
                    len(facts) == 1
                    or bool(fact.get("reporting_boundary"))
                    or any(not item["fixed"] for item in typed_identity)
                    or len({period(item) for item in facts}) == len(facts)
                ),
            }
        ordered_ids = [fid for g in groups for fid in g["fact_ids"]]
        rank = {fid: i for i, fid in enumerate(ordered_ids)}
        for g in groups:
            g.pop("_fact")
            for fid in g["fact_ids"]:
                annotations[fid]["presentation_count"] = len(g["fact_ids"])
        ordered = dict(records)
        for key in ("quantitative_observations", "qualitative_assertions"):
            ordered[key] = sorted(records.get(key, []), key=lambda r: rank[fact_id(r)])
        ordered["reporting_tasks"] = sorted(records.get("reporting_tasks", []), key=lambda r: r.get("metric_id", ""))
        for key in ("attribute_values", "dimension_values", "evidence_references"):
            ordered[key] = sorted(records.get(key, []), key=lambda r: (rank.get(r.get("parent_record_id") or r.get("source_record_id"), -1), r.get("evidence_role") != "primary", r.get("element_id", "")))
        return ordered, {"schema_version": "fact-organization-v2", "logical_measurement_count": len(groups), "raw_fact_count": len(facts), "ordered_fact_ids": ordered_ids, "facts": annotations, "groups": groups}

    @staticmethod
    def _relation(left, right, units):
        left_value = normalized(left.get("value_raw") or left.get("statement_raw"))
        right_value = normalized(right.get("value_raw") or right.get("statement_raw"))
        if left_value == right_value and normalized(left.get("unit_raw")) == normalized(right.get("unit_raw")):
            return "repeated_disclosure"
        a, b = units.get(left.get("unit_id"), {}), units.get(right.get("unit_id"), {})
        if not a or not b or a.get("dimension") != b.get("dimension") or a == b:
            return None
        try:
            x = Decimal(str(left.get("value_numeric") or left_value).replace(",", ""))
            y = Decimal(str(right.get("value_numeric") or right_value).replace(",", ""))
            sx, sy = Decimal(a["scale_to_si"]), Decimal(b["scale_to_si"])
            if not all(v.is_finite() for v in (x, y, sx, sy)):
                return None
            # Overlap of the precision intervals printed by the report.
            tolerance = (Decimal(10) ** x.as_tuple().exponent * sx + Decimal(10) ** y.as_tuple().exponent * sy) / 2
            if abs(x * sx - y * sy) <= tolerance:
                return "alternative_unit_presentation"
        except (InvalidOperation, KeyError, TypeError):
            pass
        return None
