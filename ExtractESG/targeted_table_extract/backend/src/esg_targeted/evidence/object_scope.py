from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

from esg_standard_packages.contracts import ElementDefinition, MetricDefinition

from esg_targeted.contracts import EvidenceInventory, MetricQuery, RetrievalHit
from esg_targeted.evidence.units import UNIT_DIMENSION_TERMS, GHG_FORMULA_RE, normalize_unit_text
from esg_targeted.retrieval.fact_pattern import evaluate_fact_pattern
from esg_targeted.retrieval.object_ranker import RankedEvidenceObject


DERIVED_VALUE_RE = re.compile(
    r"(?:排放强度|所占百分比|占比|浓度|密度|除以|每(?:人|万元|百万元|亿元|单位)|/\s*(?:人|万元|百万元|亿元|营收|收入|产量)|\bper\b)",
    re.IGNORECASE,
)
@dataclass(frozen=True)
class ScopedEvidenceObject:
    kind: str
    object_id: str
    group_ids: tuple[str, ...]
    excluded_group_count: int
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["group_ids"] = list(self.group_ids)
        payload["reasons"] = list(self.reasons)
        return payload


class EvidenceObjectScope:
    """Keep only fillable row groups inside a ranked evidence object.

    This is structural relevance filtering, not semantic filling. It prevents a
    multi-topic KPI table from sending unrelated business sections to the model,
    while retaining all rows that can independently satisfy the metric contract.
    """

    def select(
        self,
        *,
        ranked_object: RankedEvidenceObject,
        inventory: EvidenceInventory,
        query: MetricQuery,
        metric: MetricDefinition,
        elements: list[ElementDefinition],
        hits: list[RetrievalHit],
        semantic_review: bool = False,
    ) -> ScopedEvidenceObject:
        spans_by_group = defaultdict(list)
        candidates_by_group = defaultdict(list)
        for span in inventory.spans:
            if span.context_group_id in ranked_object.group_ids:
                spans_by_group[span.context_group_id].append(span)
        for candidate in inventory.candidates:
            if candidate.context_group_id in ranked_object.group_ids:
                candidates_by_group[candidate.context_group_id].append(candidate)
        hit_span_ids = {item.span_id for item in hits}
        hit_groups = {
            span.context_group_id
            for spans in spans_by_group.values()
            for span in spans
            if span.span_id in hit_span_ids
        }
        expected_dimensions = {
            str(item.value_contract.unit_dimension).rsplit(".", 1)[-1]
            for item in elements
            if item.value_contract.unit_dimension
        }
        value_family = str(metric.value_family).lower()
        quantitative = query.data_class in {"quantitative", "mixed"}

        group_profiles = {}
        strong_group_count = 0
        structural_scope_anchor = False
        for group_id in ranked_object.group_ids:
            spans = spans_by_group.get(group_id, [])
            candidates = candidates_by_group.get(group_id, [])
            match = evaluate_fact_pattern(query, spans, candidates)
            text = "\n".join(item.context_text for item in spans)
            dimension_compatible, expected_dimension_visible = (
                self._unit_dimension_compatibility(
                    text,
                    expected_dimensions,
                    candidates,
                )
            )
            direct_measurement_text = self._direct_measurement_text(spans, candidates)
            derived_incompatible = self._derived_value_incompatible(
                direct_measurement_text, value_family
            )
            group_profiles[group_id] = (
                spans,
                candidates,
                match,
                text,
                dimension_compatible,
                expected_dimension_visible,
                derived_incompatible,
            )
            strong_group_count += int(
                match.compatible_candidate
                and not match.conflict_terms
                and dimension_compatible
                and expected_dimension_visible
                and (match.topic_coverage > 0 or match.role_coverage > 0)
                and not derived_incompatible
            )
            structural_scope_anchor = bool(
                structural_scope_anchor
                or (
                    not match.compatible_candidate
                    and not match.conflict_terms
                    and (match.topic_coverage > 0 or match.role_coverage > 0)
                )
            )
        propagate_table_scope = bool(
            ranked_object.kind == "table"
            and (
                strong_group_count >= 2
                or (strong_group_count >= 1 and structural_scope_anchor)
            )
        )

        selected = []
        rejected_derived = 0
        rejected_conflict = 0
        rejected_unit_dimension = 0
        retained_visual_unit_conflict = False
        for group_id in ranked_object.group_ids:
            (
                spans,
                candidates,
                match,
                text,
                dimension_compatible,
                expected_dimension_visible,
                derived_incompatible,
            ) = group_profiles[group_id]
            # A tonne CO2e is not a pollutant mass. The new formula recognizer
            # must not make GHG quantities look like ordinary tonnes to legacy
            # mass metrics. This preserves unit isolation before and during the
            # semantic fallback; it is not an ESG applicability Guard.
            if expected_dimensions == {"mass"} and self._has_explicit_ghg_unit(candidates):
                rejected_unit_dimension += 1
                continue
            if semantic_review:
                # If deterministic row qualification found nothing but retrieval
                # still identified a strong structured object, send its numeric
                # rows to the semantic model. The model receives row labels,
                # stacked headers and exclusions and owns the applicability call.
                model_led = value_family in {"quantitative_energy", "quantitative_ghg", "energy_intensity", "ghg_intensity"}
                if match.conflict_terms and not model_led:
                    rejected_conflict += 1
                    continue
                numeric = any(c.candidate_type in {"quantity", "number", "percentage"} for c in candidates)
                if (match.compatible_candidate or (model_led and numeric)) and (
                    group_id in hit_groups
                    or ranked_object.kind in {"table", "figure"}
                ):
                    selected.append(group_id)
                continue
            if match.conflict_terms:
                rejected_conflict += 1
                continue
            if derived_incompatible:
                rejected_derived += 1
                continue
            if quantitative:
                visual_scope_override = bool(
                    ranked_object.kind in {"table", "figure"}
                    and any(item.visual_evidence_paths for item in spans)
                    and (match.topic_coverage > 0 or match.role_coverage > 0)
                    and not dimension_compatible
                )
                keep = bool(
                    match.compatible_candidate
                    and (dimension_compatible or visual_scope_override)
                    and (
                        expected_dimension_visible
                        or not expected_dimensions.intersection(UNIT_DIMENSION_TERMS)
                        or ranked_object.kind == "figure"
                        or visual_scope_override
                    )
                    and (
                        match.topic_coverage > 0
                        or match.role_coverage > 0
                        or group_id in hit_groups
                        or propagate_table_scope
                    )
                )
                retained_visual_unit_conflict = bool(
                    retained_visual_unit_conflict or (visual_scope_override and keep)
                )
                if (
                    ranked_object.kind == "figure"
                    and not keep
                    and group_id in hit_groups
                    and any(item.visual_evidence_paths for item in spans)
                    and (match.topic_coverage > 0 or match.role_coverage > 0)
                ):
                    keep = True
            else:
                keep = bool(group_id in hit_groups or match.required_coverage > 0)
            if keep:
                selected.append(group_id)

        reasons = [
            "semantic_model_review_fallback"
            if semantic_review
            else "metric_compatible_groups_only"
        ]
        if rejected_conflict:
            reasons.append("excluded_conflicting_scope")
        if rejected_derived:
            reasons.append("excluded_derived_value_rows")
        if rejected_unit_dimension:
            reasons.append("excluded_distinct_unit_dimension")
        if propagate_table_scope:
            reasons.append("propagated_structured_table_scope")
        if retained_visual_unit_conflict:
            reasons.append("retained_visual_unit_conflict")
        selected = self._ordered(selected, spans_by_group)
        return ScopedEvidenceObject(
            kind=ranked_object.kind,
            object_id=ranked_object.object_id,
            group_ids=tuple(selected),
            excluded_group_count=max(0, len(ranked_object.group_ids) - len(selected)),
            reasons=tuple(reasons),
        )

    @staticmethod
    def _has_explicit_ghg_unit(candidates) -> bool:
        units = [
            item.unit_raw or (item.raw_value if item.candidate_type == "unit" else "")
            for item in candidates
        ]
        return any(
            GHG_FORMULA_RE.search(unit)
            or "二氧化碳当量" in normalize_unit_text(unit)
            for unit in units
        )

    @staticmethod
    def _derived_value_incompatible(text: str, value_family: str) -> bool:
        if any(marker in value_family for marker in ("intensity", "ratio", "rate")):
            return False
        return bool(DERIVED_VALUE_RE.search(text))

    @staticmethod
    def _direct_measurement_text(spans, candidates) -> str:
        """Return row-subject and unit text, excluding broad table headings.

        A table headed "emissions and intensity" can contain a perfectly valid
        absolute-emissions row. Applying a derived-value regex to the complete
        table-row rendering suppresses that row before the model can see it.
        """

        direct: list[str] = []
        for span in spans:
            structure = span.structural_context or {}
            row_headers = structure.get("row_header_path") or []
            direct.extend(str(item) for item in row_headers if str(item).strip())
            if (
                structure.get("object_type") == "table_cell"
                and (
                    structure.get("col_index") == 0
                    or any(
                        str(header).strip().casefold() in {"单位", "單位", "unit", "units"}
                        for header in structure.get("column_header_path") or []
                    )
                )
            ):
                direct.append(span.text)
        direct.extend(
            str(item.unit_raw)
            for item in candidates
            if item.unit_raw
        )
        return "\n".join(dict.fromkeys(direct)) or "\n".join(
            item.context_text for item in spans
        )

    @staticmethod
    def _unit_dimension_compatibility(text, expected_dimensions, candidates) -> tuple[bool, bool]:
        if not expected_dimensions:
            return True, False
        lowered = normalize_unit_text(text)
        candidate_units = " ".join(
            str(item.unit_raw or "") for item in candidates if item.unit_raw
        )
        combined = normalize_unit_text(f"{lowered}\n{candidate_units}")
        recognised = {
            dimension
            for dimension, terms in UNIT_DIMENSION_TERMS.items()
            if any(normalize_unit_text(term) in combined for term in terms)
        }
        known_expected = expected_dimensions.intersection(UNIT_DIMENSION_TERMS)
        if not known_expected:
            return True, False
        if not recognised:
            return True, False
        matches = bool(known_expected.intersection(recognised))
        return matches, matches

    @staticmethod
    def _ordered(group_ids, spans_by_group):
        def key(group_id):
            spans = spans_by_group.get(group_id, [])
            rows = [
                item.structural_context.get("row_index")
                for item in spans
                if item.structural_context.get("row_index") is not None
            ]
            return (
                min((item.page_index for item in spans), default=0),
                min(rows, default=10_000),
                group_id,
            )

        return sorted(group_ids, key=key)
