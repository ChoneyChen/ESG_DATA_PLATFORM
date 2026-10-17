from __future__ import annotations

import re
from collections import defaultdict

import numpy as np

from esg_targeted.contracts import EvidenceInventory, MetricQuery, RetrievalHit
from esg_targeted.evidence.quality import is_retrieval_eligible
from esg_targeted.retrieval.bm25 import Bm25Index
from esg_targeted.retrieval.embeddings import EmbeddingProvider
from esg_targeted.retrieval.fact_pattern import (
    FactPatternMatch,
    contains_term,
    evaluate_fact_pattern,
)


class HybridRetriever:
    def __init__(
        self,
        inventory: EvidenceInventory,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_matrix: np.ndarray | None = None,
    ) -> None:
        self.inventory = inventory
        self.searchable = [is_retrieval_eligible(span) for span in inventory.spans]
        self.bm25 = Bm25Index(
            [span.context_text if self.searchable[index] else "" for index, span in enumerate(inventory.spans)]
        )
        self.embedding_provider = embedding_provider
        self.embedding_matrix = embedding_matrix
        self.semantic_searchable = (
            np.any(embedding_matrix != 0, axis=1)
            if embedding_matrix is not None
            else None
        )
        self.spans_by_group: dict[str, list] = defaultdict(list)
        self.candidates_by_group: dict[str, list] = defaultdict(list)
        self.candidates_by_span: dict[str, list] = defaultdict(list)
        for index, span in enumerate(inventory.spans):
            if self.searchable[index]:
                self.spans_by_group[span.context_group_id].append(span)
        for candidate in inventory.candidates:
            self.candidates_by_span[candidate.span_id].append(candidate)
            self.candidates_by_group[candidate.context_group_id].append(candidate)

    def search(
        self,
        query: MetricQuery,
        top_k: int,
        *,
        query_vector: np.ndarray | None = None,
    ) -> list[RetrievalHit]:
        ranked = self.rank_all(query, query_vector=query_vector)
        selected: list[RetrievalHit] = []
        group_counts: dict[str, int] = {}
        text_fingerprints: set[str] = set()
        spans_by_id = {span.span_id: span for span in self.inventory.spans}
        for hit in ranked:
            span = spans_by_id[hit.span_id]
            fingerprint = re.sub(r"\s+", "", span.text).lower()[:500]
            if fingerprint and fingerprint in text_fingerprints:
                continue
            if group_counts.get(span.context_group_id, 0) >= 2:
                continue
            selected.append(hit)
            group_counts[span.context_group_id] = group_counts.get(span.context_group_id, 0) + 1
            if fingerprint:
                text_fingerprints.add(fingerprint)
            if len(selected) >= top_k:
                break
        return selected

    def rank_all(
        self, query: MetricQuery, *, query_vector: np.ndarray | None = None
    ) -> list[RetrievalHit]:
        lexical_scores = self.bm25.scores(query.lexical_text)
        lexical_order = sorted(
            range(len(lexical_scores)), key=lambda index: lexical_scores[index], reverse=True
        )
        lexical_rank = {
            index: rank for rank, index in enumerate(lexical_order, 1) if lexical_scores[index] > 0
        }

        semantic_scores: list[float] | None = None
        semantic_rank: dict[int, int] = {}
        if self.embedding_matrix is not None and (
            query_vector is not None or self.embedding_provider is not None
        ):
            vector = query_vector
            if vector is None:
                vector = self.embedding_provider.embed_query(query.semantic_text)
            semantic_scores = (self.embedding_matrix @ vector).astype(float).tolist()
            semantic_indexes = (
                np.flatnonzero(self.semantic_searchable).tolist()
                if self.semantic_searchable is not None
                else list(range(len(semantic_scores)))
            )
            semantic_order = sorted(
                semantic_indexes,
                key=lambda index: semantic_scores[index],
                reverse=True,
            )
            semantic_rank = {index: rank for rank, index in enumerate(semantic_order, 1)}

        hits: list[RetrievalHit] = []
        rrf_k = 60
        group_matches = {
            group_id: evaluate_fact_pattern(
                query, spans, self.candidates_by_group.get(group_id, [])
            )
            for group_id, spans in self.spans_by_group.items()
        }
        for index, span in enumerate(self.inventory.spans):
            if not self.searchable[index]:
                continue
            lex_rank = lexical_rank.get(index)
            sem_rank = semantic_rank.get(index)
            rrf_score = 0.0
            reasons: list[str] = []
            if lex_rank is not None:
                rrf_score += 1.15 / (rrf_k + lex_rank)
                if lex_rank <= 20:
                    reasons.append("lexical_top_20")
            if sem_rank is not None:
                rrf_score += 1.0 / (rrf_k + sem_rank)
                if sem_rank <= 20:
                    reasons.append("semantic_top_20")

            group_match = group_matches[span.context_group_id]
            direct_match = evaluate_fact_pattern(
                query, [span], self.candidates_by_span.get(span.span_id, [])
            )
            coverage = group_match.required_coverage
            intent_boost = coverage * 0.012
            if coverage == 1.0 and query.intent.must_term_groups:
                reasons.append("intent_complete")
            elif coverage > 0:
                reasons.append("intent_partial")

            conflict = bool(group_match.conflict_terms)
            conflict_penalty = -0.05 if conflict else 0.0
            if conflict:
                reasons.append("must_not_conflict")

            structural_boost = 0.0
            structure_allowed = not query.intent.topic_term_groups or group_match.topic_coverage > 0
            if structure_allowed and span.span_type in {"table_row", "table_cell"}:
                structural_boost += 0.0015
                reasons.append("table_structure")
            if (
                structure_allowed
                and query.data_class in {"quantitative", "mixed"}
                and group_match.compatible_candidate
            ):
                structural_boost += 0.002
                reasons.append("compatible_typed_value")
            if span.span_type == "page_text":
                structural_boost -= 0.002

            combination_boost = self._combination_boost(
                query, group_match, direct_match, self.spans_by_group[span.context_group_id]
            )
            if combination_boost:
                reasons.append("topic_typed_value_combination")
                if query.intent.role_term_groups and group_match.role_complete:
                    reasons.append("role_complete")
            final_score = (
                rrf_score
                + intent_boost
                + combination_boost
                + conflict_penalty
                + structural_boost
            )
            if final_score <= 0:
                continue
            hits.append(
                RetrievalHit(
                    span_id=span.span_id,
                    lexical_rank=lex_rank,
                    lexical_score=lexical_scores[index] if lex_rank is not None else None,
                    semantic_rank=sem_rank,
                    semantic_score=(
                        semantic_scores[index]
                        if semantic_scores is not None and sem_rank is not None
                        else None
                    ),
                    rrf_score=rrf_score,
                    structural_boost=structural_boost,
                    intent_boost=intent_boost,
                    conflict_penalty=conflict_penalty,
                    intent_coverage=coverage,
                    topic_coverage=group_match.topic_coverage,
                    role_coverage=group_match.role_coverage,
                    compatible_candidate=group_match.compatible_candidate,
                    combination_boost=combination_boost,
                    final_score=final_score,
                    reasons=reasons,
                )
            )
        hits.sort(key=lambda item: (-item.final_score, item.span_id))
        return hits

    @staticmethod
    def _combination_boost(
        query: MetricQuery,
        group_match: FactPatternMatch,
        direct_match: FactPatternMatch,
        group_spans: list,
    ) -> float:
        if (
            not query.intent.expected_candidate_types
            or not group_match.topic_complete
            or not group_match.compatible_candidate
            or group_match.conflict_terms
        ):
            return 0.0
        base = 0.010
        if query.intent.role_term_groups and group_match.role_complete:
            base += 0.006
        if (
            direct_match.topic_complete
            and direct_match.compatible_candidate
            and not direct_match.conflict_terms
        ):
            cohesion = 1.0
        elif any(span.span_type in {"table_row", "table_cell"} for span in group_spans):
            cohesion = 0.9
        elif any(span.span_type == "figure_text" for span in group_spans):
            cohesion = 0.8
        else:
            cohesion = 0.6
        return base * cohesion
