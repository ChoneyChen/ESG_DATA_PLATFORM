from __future__ import annotations

import hashlib
import re

from esg_v2.standards.contracts import RequirementProfile, RequirementSlot
from esg_v2.targeted.catalog import matched_aliases, normalized_match_text
from esg_v2.targeted.contracts import (
    CandidateEvidence,
    CandidateVerification,
    DisclosureGroup,
    FactInstance,
    SlotCoverage,
)
from esg_v2.targeted.features import DisclosureFeatureExtractor


class RequirementVerifier:
    """Deterministic semantic boundary between retrieval relevance and a fillable answer."""

    def verify(
        self,
        profile: RequirementProfile,
        candidate: CandidateEvidence,
        group: DisclosureGroup,
    ) -> CandidateVerification:
        coverage: list[SlotCoverage] = []
        dimensions: dict[str, list[str]] = {}
        for slot in profile.required_slots:
            result = self._slot(profile, slot, group)
            coverage.append(result)
            if slot.role == "dimension" and result.satisfied:
                dimensions[slot.slot_id.removeprefix("dimension:")] = result.matched_values

        if profile.requires_dimension_intersection:
            self._apply_intersection_guard(profile, group, coverage)
            dimensions = {
                item.slot_id.removeprefix("dimension:"): item.matched_values
                for item in coverage
                if item.slot_id.startswith("dimension:") and item.satisfied
            }

        required = [slot for slot in coverage if slot.required]
        satisfied = [slot for slot in required if slot.satisfied]
        ratio = len(satisfied) / len(required) if required else 1.0
        negative = matched_aliases(group.source_text, profile.negative_aliases)
        reasons: list[str] = []
        if group.features.index_like:
            reasons.append("index_or_standard_reference_cannot_be_final_evidence")
        if negative:
            reasons.append("negative_concept_signals:" + ",".join(negative[:5]))
        missing = [slot.slot_id for slot in required if not slot.satisfied]
        if missing:
            reasons.append("missing_required_slots:" + ",".join(missing))

        concept = next((slot for slot in coverage if slot.slot_id == "concept"), None)
        value = next((slot for slot in coverage if slot.slot_id == "value"), None)
        blocked = group.features.index_like or bool(negative)
        if ratio == 1 and not blocked:
            decision = "complete"
        elif concept and concept.satisfied and (value is None or value.satisfied):
            decision = "partial"
        else:
            decision = "irrelevant"
        facts = self._facts(profile, group, coverage) if decision in {"complete", "partial"} else []
        score = candidate.fused_score + 0.2 * ratio - 0.3 * int(group.features.index_like) - 0.12 * len(negative)
        return CandidateVerification(
            requirement_id=profile.requirement_id,
            group_id=group.group_id,
            decision=decision,
            coverage_ratio=round(ratio, 4),
            slot_coverage=coverage,
            facts=facts,
            reasons=reasons,
            verifier_score=round(score, 6),
        )

    def _slot(
        self,
        profile: RequirementProfile,
        slot: RequirementSlot,
        group: DisclosureGroup,
    ) -> SlotCoverage:
        if slot.role == "concept":
            matches = matched_aliases(group.source_text, slot.aliases)
            return self._coverage(slot, bool(matches), matches, group, "measure aliases in source evidence")
        if slot.role == "value":
            explicit_zero = profile.allow_explicit_zero_statement and group.features.explicit_zero
            matches = [*group.features.numeric_values[:20], *( ["explicit_zero"] if explicit_zero else [])]
            return self._coverage(slot, bool(matches), matches, group, "reported numeric value or explicit zero statement")
        if slot.role == "unit":
            matches = sorted(set(slot.value_types) & set(group.features.unit_families))
            return self._coverage(slot, bool(matches), matches, group, "compatible unit family")
        if slot.role == "period":
            matches = [str(year) for year in group.features.years]
            return self._coverage(slot, bool(matches), matches, group, "identifiable reporting year")
        if slot.role == "dimension":
            name = slot.slot_id.removeprefix("dimension:")
            matches = self._dimension_values(name, group.source_text, slot.aliases)
            passed = len(matches) >= slot.minimum_distinct
            reason = f"{len(matches)}/{slot.minimum_distinct} distinct {name} values"
            return self._coverage(slot, passed, matches, group, reason)
        if slot.role == "statement":
            statement_type = slot.slot_id.removeprefix("statement:")
            passed = statement_type in group.features.statement_types or "absence" in group.features.statement_types
            matches = [value for value in group.features.statement_types if value in {statement_type, "absence"}]
            return self._coverage(slot, passed, matches, group, "topic-specific control statement")
        return self._coverage(slot, False, [], group, "unsupported slot role")

    @staticmethod
    def _coverage(
        slot: RequirementSlot,
        satisfied: bool,
        matches: list[str],
        group: DisclosureGroup,
        reason: str,
    ) -> SlotCoverage:
        return SlotCoverage(
            slot_id=slot.slot_id,
            required=slot.required,
            satisfied=satisfied,
            matched_values=list(dict.fromkeys(matches)),
            evidence_atom_ids=[group.representative_atom_id] if satisfied else [],
            reason=reason,
        )

    def _apply_intersection_guard(
        self,
        profile: RequirementProfile,
        group: DisclosureGroup,
        coverage: list[SlotCoverage],
    ) -> None:
        slots = [slot for slot in profile.required_slots if slot.role == "dimension" and slot.relation == "cross_tab"]
        if len(slots) < 2:
            return
        intersection_rows = []
        for row in group.row_texts:
            if all(self._dimension_values(slot.slot_id.removeprefix("dimension:"), row, slot.aliases) for slot in slots):
                intersection_rows.append(row)
        if intersection_rows:
            return
        guarded_ids = {slot.slot_id for slot in slots}
        for item in coverage:
            if item.slot_id in guarded_ids:
                item.satisfied = False
                item.evidence_atom_ids = []
                item.reason += "; dimensions coexist only as separate axes/rows, not a cross-tab intersection"

    @staticmethod
    def _dimension_values(name: str, text: str, aliases: list[str]) -> list[str]:
        normalized_with_spaces = normalized_match_text(text)
        normalized = normalized_with_spaces.replace(" ", "")
        if name == "gender":
            values = []
            if re.search(r"\bmale\b", normalized_with_spaces) or "男性" in normalized:
                values.append("male")
            if re.search(r"\bfemale\b", normalized_with_spaces) or "女性" in normalized:
                values.append("female")
            return values
        if name == "scope":
            return [scope for scope in ("scope1", "scope2", "scope3") if scope in normalized]
        if name == "contract_type":
            values = []
            if any(value in normalized for value in ("fulltime", "全職", "全职")):
                values.append("full_time")
            if any(value in normalized for value in ("parttime", "兼職", "兼职")):
                values.append("part_time")
            return values
        matches = matched_aliases(text, aliases)
        return list(dict.fromkeys(normalized_match_text(value) for value in matches))

    def _facts(
        self,
        profile: RequirementProfile,
        group: DisclosureGroup,
        coverage: list[SlotCoverage],
    ) -> list[FactInstance]:
        dimensions = {
            item.slot_id.removeprefix("dimension:"): item.matched_values
            for item in coverage
            if item.slot_id.startswith("dimension:") and item.matched_values
        }
        period = max(group.features.years) if group.features.years else None
        unit = next((family for family in profile.unit_families if family in group.features.unit_families), None)
        dimension_slots = [slot for slot in profile.required_slots if slot.role == "dimension"]
        facts: list[FactInstance] = []
        if group.features.explicit_zero and profile.allow_explicit_zero_statement:
            return [
                FactInstance(
                    fact_id=self._fact_id(profile.concept_id, group.group_id, "0", dimensions),
                    concept_id=profile.concept_id,
                    value="0",
                    numeric_values=["0"],
                    raw_text=group.source_text,
                    unit_family=unit or "count_incident",
                    period=period,
                    dimensions=dimensions,
                    source_group_id=group.group_id,
                    evidence_atom_ids=[group.representative_atom_id],
                )
            ]
        for row in group.row_texts:
            row_features = DisclosureFeatureExtractor.extract(
                row,
                section_path=group.section_path,
                group_type="table",
            )
            values = row_features.numeric_values
            if not values:
                continue
            row_dimensions = {
                slot.slot_id.removeprefix("dimension:"): self._dimension_values(
                    slot.slot_id.removeprefix("dimension:"), row, slot.aliases
                )
                for slot in dimension_slots
            }
            row_dimensions = {name: values for name, values in row_dimensions.items() if values}
            if dimension_slots and not row_dimensions:
                continue
            if profile.requires_dimension_intersection and len(row_dimensions) < len(dimension_slots):
                continue
            if not dimension_slots:
                row_concepts = matched_aliases(row, profile.measure_aliases)
                row_units = set(profile.unit_families) & set(row_features.unit_families)
                if not row_concepts and not row_units:
                    continue
            facts.append(
                FactInstance(
                    fact_id=self._fact_id(profile.concept_id, group.group_id, row, row_dimensions or dimensions),
                    concept_id=profile.concept_id,
                    value=row,
                    numeric_values=values,
                    raw_text=row,
                    unit_family=unit,
                    period=period,
                    dimensions=row_dimensions or dimensions,
                    source_group_id=group.group_id,
                    evidence_atom_ids=[group.representative_atom_id],
                )
            )
        if facts:
            return facts
        return [
            FactInstance(
                fact_id=self._fact_id(profile.concept_id, group.group_id, group.source_text[:3000], dimensions),
                concept_id=profile.concept_id,
                value=group.source_text[:3000],
                numeric_values=group.features.numeric_values,
                raw_text=group.source_text[:3000],
                unit_family=unit,
                period=period,
                dimensions=dimensions,
                source_group_id=group.group_id,
                evidence_atom_ids=[group.representative_atom_id],
            )
        ]

    @staticmethod
    def _fact_id(concept_id: str, group_id: str, value: str, dimensions: dict[str, list[str]]) -> str:
        payload = f"{concept_id}|{group_id}|{value}|{sorted(dimensions.items())}"
        return "fact-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
