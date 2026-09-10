from __future__ import annotations

from collections import defaultdict

from esg_targeted.contracts import (
    EvidenceInventory,
    EvidenceSufficiency,
    MetricQuery,
    RetrievalHit,
)
from esg_targeted.evidence.quality import is_retrieval_eligible
from esg_targeted.retrieval.fact_pattern import evaluate_fact_pattern


class EvidenceSufficiencyGate:
    """Conservatively decide whether a quantitative metric needs model inference."""

    def assess(
        self,
        query: MetricQuery,
        inventory: EvidenceInventory,
        hits: list[RetrievalHit],
        *,
        retrieval_complete: bool,
    ) -> EvidenceSufficiency:
        spans_by_group: dict[str, list] = defaultdict(list)
        candidates_by_group: dict[str, list] = defaultdict(list)
        spans_by_id = {span.span_id: span for span in inventory.spans}
        for span in inventory.spans:
            if not is_retrieval_eligible(span):
                continue
            spans_by_group[span.context_group_id].append(span)
        for candidate in inventory.candidates:
            candidates_by_group[candidate.context_group_id].append(candidate)

        summaries = []
        qualifying = []
        conflicts = []
        best_coverage = 0.0
        best_topic_coverage = 0.0
        best_role_coverage = 0.0
        uncertain_signal = False
        topic_value_signal = False
        structured_semantic_signal = False
        hit_groups = {
            spans_by_id[hit.span_id].context_group_id
            for hit in hits
            if hit.span_id in spans_by_id
        }
        for hit in hits:
            span = spans_by_id.get(hit.span_id)
            if span is None or span.span_type not in {"table_cell", "table_row", "figure_text"}:
                continue
            if not candidates_by_group.get(span.context_group_id):
                continue
            if (
                (hit.semantic_rank is not None and hit.semantic_rank <= 8)
                or (hit.lexical_rank is not None and hit.lexical_rank <= 8)
            ):
                structured_semantic_signal = True
        for group_id, spans in spans_by_group.items():
            match = evaluate_fact_pattern(query, spans, candidates_by_group[group_id])
            best_coverage = max(best_coverage, match.required_coverage)
            best_topic_coverage = max(best_topic_coverage, match.topic_coverage)
            best_role_coverage = max(best_role_coverage, match.role_coverage)
            if match.conflict_terms:
                conflicts.append(group_id)
            candidate_types = {item.candidate_type for item in candidates_by_group[group_id]}
            if match.full_pattern:
                qualifying.append(group_id)
            if match.topic_coverage > 0 and not match.conflict_terms:
                uncertain_signal = True
            if (
                match.topic_coverage > 0
                and match.compatible_candidate
                and not match.conflict_terms
            ):
                topic_value_signal = True
            if group_id in hit_groups or match.topic_coverage > 0 or match.role_coverage > 0:
                summaries.append(
                    {
                        "group_id": group_id,
                        "coverage": round(match.required_coverage, 4),
                        "topic_coverage": round(match.topic_coverage, 4),
                        "role_coverage": round(match.role_coverage, 4),
                        "matched_topic_terms": match.matched_topic_terms,
                        "matched_role_terms": match.matched_role_terms,
                        "conflict_terms": match.conflict_terms,
                        "candidate_types": sorted(candidate_types),
                        "has_expected_candidate": match.compatible_candidate,
                        "full_pattern": match.full_pattern,
                        "page_indexes": sorted({span.page_index for span in spans}),
                    }
                )

        can_auto_not_found = bool(
            retrieval_complete
            and query.data_class == "quantitative"
            and query.intent.topic_term_groups
        )
        if qualifying:
            status = "sufficient"
            reasons = ["complete_fact_pattern_with_typed_value"]
        elif can_auto_not_found and not uncertain_signal and not structured_semantic_signal:
            status = "insufficient"
            reasons = ["full_inventory_scan_without_topic_signal"]
        else:
            status = "uncertain"
            if topic_value_signal and query.intent.role_term_groups:
                reasons = ["topic_and_typed_value_found_but_role_is_incomplete"]
            elif uncertain_signal:
                reasons = ["topic_signal_requires_semantic_or_visual_decision"]
            elif structured_semantic_signal:
                reasons = ["ranked_structured_evidence_requires_semantic_review"]
            else:
                reasons = ["non_quantitative_or_uncompiled_topic_requires_semantic_decision"]
        return EvidenceSufficiency(
            status=status,
            retrieval_complete=retrieval_complete,
            auto_not_found_allowed=status == "insufficient" and can_auto_not_found,
            required_group_count=len(query.intent.must_term_groups),
            best_group_coverage=best_coverage,
            best_topic_coverage=best_topic_coverage,
            best_role_coverage=best_role_coverage,
            qualifying_group_ids=qualifying,
            conflict_group_ids=sorted(set(conflicts)),
            reason_codes=reasons,
            group_summaries=sorted(
                summaries,
                key=lambda item: (
                    -int(item["full_pattern"]),
                    -int(item["has_expected_candidate"]),
                    -item["topic_coverage"],
                    -item["role_coverage"],
                    item["group_id"],
                ),
            )[:20],
        )
