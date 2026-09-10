from __future__ import annotations

import json

from esg_targeted.contracts import EvidencePacket


class DirectFillTemplateCompiler:
    def compile(self, packet: EvidencePacket) -> dict:
        context = json.loads(packet.model_context)
        group_ids = list(packet.alias_map.get("groups", {}))
        target_cell_ids = list(packet.alias_map.get("target_cells", {}))
        fields = {}
        for element in context.get("elements", []):
            name = element.get("code") or element.get("id")
            if not name:
                continue
            fields[name] = [None, "exact visible scalar from this evidence object"]
        return {
            "task_id": packet.task_id,
            "status": ["found", "partial", "not_found", "ambiguous"],
            "rows": [
                {
                    "target_cell": target_cell_ids[0] if target_cell_ids else None,
                    # This template is an example shape for the local model, not
                    # a JSON Schema. A list here prompted some models to return a
                    # list even though the contract is a scalar enum.
                    "group": group_ids[0] if group_ids else "G-alias",
                    "fields": fields,
                    "metric_match": "match",
                    "interpretation_note": None,
                    "context_refs": [],
                }
            ],
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

    @staticmethod
    def instructions(feedback: str | None = None) -> str:
        lines = [
            "You directly fill the supplied standard-package fields from exactly one coherent ESG evidence object (table, figure or bounded text) and its linked explanatory context.",
            "Read the metric description and reporting requirement for meaning. fixed_scope is the REQUESTED measure, not a report fact. semantic_intent is recall vocabulary, not mandatory wording. Model judgment owns semantic applicability; do not use excluded_scope_terms as a word-presence checklist.",
            "Each row: metric_match=match if the meaning fits; uncertain if a grounded quantity lacks decisive method/scope context; different if its meaning differs. Retain useful nonmatching measurements for inspection, without relabelling them. Briefly explain uncertainty in interpretation_note and cite relevant L aliases in context_refs.",
            "Read linked_context with the main object; these sourced notes/methods are candidate explanations, not extra quantity targets. Scope 2 does not itself establish location-based or market-based. Use the actual method description. Unknown method is uncertain; explicit other method is different. Same values can serve both only if the report establishes both.",
            "ONE APPLICABLE NUMERIC VALUE CELL OR DATA POINT = ONE OUTPUT ROW. Preserve all applicable entities, periods, sites and carriers. A component is not a total; consumption is not production/capacity/intensity/change; Scope 1+2 is not Scope 1. Preserve conflicting original units and explain, rather than silently correcting or dropping them.",
            "The T worklist pairs visible_value, G group, direct row_label, stacked headers, unit and row_context. Return its scalar target_cell and matching scalar group. Skip irrelevant numbers; optionally explain in skipped_targets=[{target_cell,reason}]. The worklist is not a mandate to fill every number. Do not force missing context to make a row match.",
            "Use the complete crop when supplied to verify header/value pairing and recover OCR omissions within an existing G row (then target_cell=null). For header_alignment_uncertain, prefer the image; without one leave uncertain headers null. Never invent a visual value. Without a T worklist use target_cell=null and the source G.",
            "Each field is a DIRECT scalar or null, never nested source/confidence objects. Copy visible wording and precision; the local adapter binds provenance. Use only supplied fields. No unstated values, calculations, conversions or aggregation.",
            "Classify headers using typed fields first: entity/subsidiary/site -> reporting_entity, carrier/source/flow -> the corresponding field. Additional_breakdown is a fallback when no typed field exists. Entity columns (including totals) are NOT reporting_boundary; that requires an explicit accounting perimeter. A fixed aggregation_role describes the measure, not the entity column.",
            "Other objects run separately. Backend deterministic exact-duplicate merge preserves evidence and links alternative units; do not preemptively discard valid observations.",
            "Use found for supported matches, partial for retained grounded measurements with missing decisive context, not_found for no relevant quantity, ambiguous for conflicting evidence with no retainable row. Nonmatching grounded rows remain partial. missing_context is a short list of genuinely missing explanations. Return one compact JSON object only, copy task_id, obey max_output_rows; no prose.",
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
