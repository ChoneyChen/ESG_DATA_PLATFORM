from __future__ import annotations

import json

from esg_targeted.contracts import EvidencePacket


class DirectFillTemplateCompiler:
    CELL_FIELD_CODES = {
        "reporting_period",
        "reporting_entity",
        "entity_aggregation_role",
        "additional_breakdown",
    }

    @classmethod
    def field_partitions(cls, context: dict) -> tuple[dict, dict]:
        """Return disjoint row-shared and value-cell field templates."""

        shared: dict[str, list] = {}
        cell: dict[str, list] = {}
        for element in context.get("elements", []):
            name = element.get("code") or element.get("id")
            if not name:
                continue
            scope = element.get("field_scope")
            if scope not in {"row", "cell"}:
                scope = (
                    "cell"
                    if element.get("semantic_role") == "value"
                    or name in cls.CELL_FIELD_CODES
                    else "row"
                )
            target = cell if scope == "cell" else shared
            target[name] = [None, "exact visible scalar from this evidence object"]
        return shared, cell

    @staticmethod
    def output_profile(context: dict) -> str:
        record_types = {
            item.get("record_type") for item in context.get("elements", [])
        }
        data_class = str((context.get("metric") or {}).get("data_class") or "")
        if "qualitative_assertion" in record_types or data_class in {
            "qualitative",
            "structure",
            "narrative",
        }:
            return "qualitative_assertions"
        return "quantitative_row_cells"

    def compile(self, packet: EvidencePacket) -> dict:
        context = json.loads(packet.model_context)
        group_ids = list(packet.alias_map.get("groups", {}))
        target_cell_ids = list(packet.alias_map.get("target_cells", {}))
        shared_fields, cell_fields = self.field_partitions(context)
        base = {
            "task_id": packet.task_id,
            "status": ["found", "partial", "not_found", "ambiguous"],
            "uncertainty_code": [
                "none",
                "insufficient_evidence",
                "conflicting_values",
                "scope_ambiguous",
                "period_ambiguous",
                "model_output_invalid",
            ],
            "skipped_targets": [],
            "missing_context": [],
        }
        if self.output_profile(context) == "qualitative_assertions":
            base["assertions"] = [
                {
                    "group": group_ids[0] if group_ids else "G-alias",
                    "metric_match": "match",
                    "interpretation_note": None,
                    "context_refs": [],
                    "fields": {**shared_fields, **cell_fields},
                }
            ]
            return base
        base["row_groups"] = [
            {
                "group": group_ids[0] if group_ids else "G-alias",
                "metric_match": "match",
                "interpretation_note": None,
                "context_refs": [],
                "shared_fields": shared_fields,
                "values": [
                    {
                        "target_cell": target_cell_ids[0] if target_cell_ids else None,
                        "fields": cell_fields,
                    }
                ],
            }
        ]
        return base

    @staticmethod
    def instructions(feedback: str | None = None) -> str:
        lines = [
            "You directly fill the supplied standard-package fields from exactly one coherent ESG evidence object (table, figure or bounded text) and its linked explanatory context.",
            "Read the metric description and reporting requirement for meaning. fixed_scope is the REQUESTED measure, not a report fact. semantic_intent is recall vocabulary, not mandatory wording. Model judgment owns semantic applicability; do not use excluded_scope_terms as a word-presence checklist.",
            "Use metric.measurement_contract as authoritative meaning, including definition/guidance/distinctions/exclusions, value_role and unit_dimension. identity_axes and required_dimension_ids describe needed identity, not facts to assume. Water pollutant mass is neither wastewater volume nor concentration/percentage. Receiving medium needs report evidence; a chemical name alone proves neither medium nor exclusion.",
            "Preserve disclosed value/unit. Contradiction with measurement_contract means metric_match=uncertain plus an explanation. canonical_unit_id is a requested target, not proof of source units. Never convert volume/percentage into mass, repair a conflicting unit, or assume catalogue membership; normalization follows review.",
            "First classify each PHYSICAL SOURCE ROW semantically: metric_match=match if its meaning fits; uncertain if grounded evidence lacks decisive method/scope context; different if its meaning differs. Only after that decision, expand matched quantitative rows into their applicable entity/period cells. Retain useful nonmatching rows for inspection without relabelling them. Briefly explain uncertainty in interpretation_note and cite relevant L aliases in context_refs.",
            "Read linked_context with the main object; these sourced notes/methods are candidate explanations, not extra quantity targets. Scope 2 does not itself establish location-based or market-based. Use the actual method description. Unknown method is uncertain; explicit other method is different. Same values can serve both only if the report establishes both.",
            "Use metric.family_adjudication before assigning a row: compare the current metric with every listed confusable sibling and its fixed scope. Do not reuse one physical cell across incompatible siblings merely because the subject is broadly related.",
            "ONE APPLICABLE NUMERIC VALUE CELL OR DATA POINT = ONE values ITEM inside its physical row_group. Preserve all applicable entities, periods, sites and carriers. A component is not a total; consumption is not production/capacity/intensity/change; Scope 1+2 is not Scope 1. Preserve conflicting original units and explain, rather than silently correcting or dropping them.",
            "The T worklist pairs visible_value, G group, direct row_label, stacked headers, unit and row_context. Return its scalar target_cell and matching scalar group. Skip irrelevant numbers; optionally explain in skipped_targets=[{target_cell,reason}]. The worklist is not a mandate to fill every number. Do not force missing context to make a row match.",
            "Use the complete crop when supplied to verify header/value pairing and recover OCR omissions within an existing G row (then target_cell=null). For header_alignment_uncertain, prefer the image; without one leave uncertain headers null. Never invent a visual value. Without a T worklist use target_cell=null and the source G.",
            "For quantitative output, use each field in its supplied location only: row semantics/unit/method belong in shared_fields; value/period/entity fields belong in values.fields. Never duplicate a cell field into shared_fields. Each field is a DIRECT scalar or null, never a source/confidence object.",
            "For qualitative/list output use assertions, one independently evidenced claim or list member per item. statement must be actual readable source wording or a visible row/list member; never substitute a number, unit, period or whole unrelated evidence block. Copy visible wording and precision; the local adapter binds provenance. Use only supplied fields. No unstated values, calculations, conversions or aggregation.",
            "Classify headers using typed fields first: entity/subsidiary/site -> reporting_entity, carrier/source/flow -> the corresponding field. Additional_breakdown is a fallback when no typed field exists. Entity columns (including totals) are NOT reporting_boundary; that requires an explicit accounting perimeter. A fixed aggregation_role describes the measure, not the entity column.",
            "Other objects run separately. Backend deterministic exact-duplicate merge preserves evidence and links alternative units; do not preemptively discard valid observations.",
            "Use found for supported matches, partial for retained grounded measurements with missing decisive context, not_found for no relevant fact, ambiguous for conflicting evidence with no retainable row. Nonmatching grounded rows remain partial. missing_context is a short list of genuinely missing explanations. Return one compact JSON object only, copy task_id, obey max_output_rows; no prose.",
        ]
        if feedback:
            lines.extend(
                [
                    "Follow-up for this evidence object; retain prior semantic decisions and respond only to the specific issue below:",
                    feedback,
                ]
            )
        return "\n".join(lines)


# Backward import compatibility is intentionally limited to the class name used by
# model transports. The old behavior and old visual-transcription contract are gone.
NuExtractDecisionTemplateCompiler = DirectFillTemplateCompiler
