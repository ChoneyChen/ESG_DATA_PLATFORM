from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

from esg_targeted.contracts import (
    EvidenceInventory,
    EvidenceSpan,
    MetricQuery,
    RetrievalHit,
)
from esg_targeted.retrieval.fact_pattern import contains_term, evaluate_fact_pattern


PLACEHOLDER_RE = re.compile(r"^(?:[/\\\-—–]|n/?a|不适用|未披露)?$", re.IGNORECASE)
NAVIGATION_TERMS = (
    "披露回应",
    "指标索引",
    "内容索引",
    "报告索引",
    "content index",
    "disclosure index",
    "gri index",
    "披露章节/公开位置",
    "对应本报告页码",
)
PRIMARY_LITERAL_TYPES = {"number", "quantity", "percentage"}


def evidence_object_key(span: EvidenceSpan) -> tuple[str, str]:
    structure = span.structural_context or {}
    table_id = structure.get("table_id")
    if table_id:
        return "table", str(table_id)
    figure_id = structure.get("figure_id")
    if figure_id:
        return "figure", str(figure_id)
    for object_id in span.object_ids:
        value = str(object_id)
        if value.startswith("table-"):
            return "table", value
        if value.startswith("figure-"):
            return "figure", value
        if value.startswith("block-"):
            return "block", value
    return "group", str(span.context_group_id)


@dataclass(frozen=True)
class RankedEvidenceObject:
    kind: str
    object_id: str
    score: float
    eligible: bool
    group_ids: tuple[str, ...]
    hit_span_ids: tuple[str, ...]
    page_indices: tuple[int, ...]
    hit_count: int
    compatible_group_count: int
    fillable_group_count: int
    primary_literal_count: int
    topic_coverage: float
    role_coverage: float
    placeholder_ratio: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["group_ids"] = list(self.group_ids)
        payload["hit_span_ids"] = list(self.hit_span_ids)
        payload["page_indices"] = list(self.page_indices)
        payload["reasons"] = list(self.reasons)
        payload["score"] = round(self.score, 6)
        payload["topic_coverage"] = round(self.topic_coverage, 4)
        payload["role_coverage"] = round(self.role_coverage, 4)
        payload["placeholder_ratio"] = round(self.placeholder_ratio, 4)
        return payload


class EvidenceObjectRanker:
    """Aggregate span retrieval into fillability-aware evidence-object ranking.

    Retrieval still discovers candidates at span granularity. Selection, however,
    must rank the independent object which will become one model-call stream. This
    avoids admitting an index table merely because one of its cells has the best
    lexical score.
    """

    def rank(
        self,
        *,
        inventory: EvidenceInventory,
        query: MetricQuery,
        hits: list[RetrievalHit],
        priority_span_ids: list[str] | None = None,
    ) -> list[RankedEvidenceObject]:
        spans_by_id = {item.span_id: item for item in inventory.spans}
        groups_by_object: dict[tuple[str, str], set[str]] = defaultdict(set)
        spans_by_object: dict[tuple[str, str], list[EvidenceSpan]] = defaultdict(list)
        spans_by_group: dict[str, list[EvidenceSpan]] = defaultdict(list)
        candidates_by_group: dict[str, list] = defaultdict(list)
        for span in inventory.spans:
            key = evidence_object_key(span)
            groups_by_object[key].add(span.context_group_id)
            spans_by_object[key].append(span)
            spans_by_group[span.context_group_id].append(span)
        for candidate in inventory.candidates:
            candidates_by_group[candidate.context_group_id].append(candidate)

        hits_by_object: dict[tuple[str, str], list[RetrievalHit]] = defaultdict(list)
        for hit in hits:
            span = spans_by_id.get(hit.span_id)
            if span is not None:
                hits_by_object[evidence_object_key(span)].append(hit)
        priority_objects = {
            evidence_object_key(span)
            for span_id in priority_span_ids or []
            if (span := spans_by_id.get(span_id)) is not None
        }

        ranked = [
            self._score_object(
                key=key,
                spans=spans_by_object[key],
                group_ids=groups_by_object[key],
                hits=object_hits,
                query=query,
                spans_by_group=spans_by_group,
                candidates_by_group=candidates_by_group,
                priority=key in priority_objects,
            )
            for key, object_hits in hits_by_object.items()
        ]
        ranked.sort(
            key=lambda item: (
                not item.eligible,
                -item.score,
                min(item.page_indices, default=10_000),
                item.kind,
                item.object_id,
            )
        )
        return ranked

    @staticmethod
    def _score_object(
        *,
        key,
        spans,
        group_ids,
        hits,
        query,
        spans_by_group,
        candidates_by_group,
        priority,
    ) -> RankedEvidenceObject:
        ordered_hits = sorted(hits, key=lambda item: -item.final_score)
        hit_signal = (ordered_hits[0].final_score if ordered_hits else 0.0) + sum(
            item.final_score * 0.25 for item in ordered_hits[1:5]
        )
        compatible_groups = 0
        fillable_groups = 0
        primary_literals = 0
        for group_id in group_ids:
            group_candidates = candidates_by_group.get(group_id, [])
            match = evaluate_fact_pattern(
                query,
                spans_by_group.get(group_id, []),
                group_candidates,
            )
            compatible_groups += int(match.compatible_candidate)
            primary_count = sum(
                item.candidate_type in PRIMARY_LITERAL_TYPES
                for item in group_candidates
            )
            primary_literals += primary_count
            fillable_groups += int(
                match.compatible_candidate
                and not match.conflict_terms
                and (match.topic_coverage > 0 or match.role_coverage > 0)
            )

        object_candidates = [
            item for group_id in group_ids for item in candidates_by_group.get(group_id, [])
        ]
        object_match = evaluate_fact_pattern(query, spans, object_candidates)
        cell_texts = [
            item.text.strip()
            for item in spans
            if item.span_type == "table_cell"
            and (item.structural_context or {}).get("object_type") == "table_cell"
        ]
        placeholder_ratio = (
            sum(bool(PLACEHOLDER_RE.fullmatch(item)) for item in cell_texts)
            / len(cell_texts)
            if cell_texts
            else 0.0
        )
        object_text = "\n".join(item.context_text for item in spans)
        navigation_like = any(contains_term(object_text, term) for term in NAVIGATION_TERMS)
        quantitative = query.data_class in {"quantitative", "mixed"}
        qualitative = query.data_class in {"qualitative", "narrative", "structure"}

        reasons = ["span_scores_aggregated"]
        score = hit_signal
        if priority:
            score += 0.2
            reasons.append("explicit_priority")
        if fillable_groups:
            score += min(0.10, 0.025 * fillable_groups)
            reasons.append("fillable_groups")
        if compatible_groups:
            score += min(0.03, 0.006 * compatible_groups)
            reasons.append("compatible_literals")
        score += object_match.topic_coverage * 0.018
        score += object_match.role_coverage * 0.008
        if key[0] in {"table", "figure"}:
            score += 0.006
            reasons.append("structured_object")
        if qualitative and key[0] in {"block", "group"}:
            score += 0.025
            reasons.append("bounded_text_preferred_for_qualitative")
        if qualitative and key[0] == "table" and primary_literals >= 20:
            score -= 0.035
            reasons.append("numeric_table_deprioritized_for_qualitative")
        if quantitative and fillable_groups == 0:
            score -= 0.065
            reasons.append("no_fillable_quantity")
        if navigation_like:
            score -= 0.05
            reasons.append("navigation_or_disclosure_index")
        if placeholder_ratio >= 0.25:
            score -= min(0.04, placeholder_ratio * 0.04)
            reasons.append("placeholder_dense")

        eligible = bool(
            priority
            or (
                not navigation_like
                and (
                    not quantitative
                    or fillable_groups > 0
                    or (
                        key[0] == "figure"
                        and object_match.topic_coverage > 0
                        and any(item.visual_evidence_paths for item in spans)
                    )
                )
            )
        )
        return RankedEvidenceObject(
            kind=key[0],
            object_id=key[1],
            score=score,
            eligible=eligible,
            group_ids=tuple(sorted(group_ids)),
            hit_span_ids=tuple(item.span_id for item in ordered_hits),
            page_indices=tuple(sorted({item.page_index for item in spans})),
            hit_count=len(ordered_hits),
            compatible_group_count=compatible_groups,
            fillable_group_count=fillable_groups,
            primary_literal_count=primary_literals,
            topic_coverage=object_match.topic_coverage,
            role_coverage=object_match.role_coverage,
            placeholder_ratio=placeholder_ratio,
            reasons=tuple(reasons),
        )
