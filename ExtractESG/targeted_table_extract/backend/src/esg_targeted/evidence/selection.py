from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from esg_standard_packages.contracts import ElementDefinition, MetricDefinition

from esg_targeted.contracts import (
    EvidenceInventory,
    EvidencePacket,
    MetricQuery,
    RetrievalHit,
)
from esg_targeted.evidence.object_scope import EvidenceObjectScope
from esg_targeted.ids import stable_id
from esg_targeted.retrieval.fact_pattern import candidate_allowed_in_packet
from esg_targeted.retrieval.object_ranker import EvidenceObjectRanker


class EvidenceSelectionCompiler:
    """Compile ranked evidence objects into metric-compatible source groups.

    Top N always means N independent tables, figures or text objects. A selected
    multi-topic table is scoped to relevant row groups before the downstream
    region compiler performs bounded per-object model calls.
    """

    def __init__(
        self,
        *,
        max_groups: int = 256,
        max_spans: int = 2400,
        max_candidates: int = 4800,
        max_visual_artifacts: int = 32,
        max_retrieved_objects: int = 3,
    ) -> None:
        self.max_groups = max(1, max_groups)
        self.max_spans = max(1, max_spans)
        self.max_candidates = max(1, max_candidates)
        self.max_visual_artifacts = max(0, max_visual_artifacts)
        self.max_retrieved_objects = max(1, max_retrieved_objects)
        self.object_ranker = EvidenceObjectRanker()
        self.object_scope = EvidenceObjectScope()

    def compile(
        self,
        *,
        task_id: str,
        metric: MetricDefinition,
        elements: list[ElementDefinition],
        query: MetricQuery,
        inventory: EvidenceInventory,
        hits: list[RetrievalHit],
        retrieval_complete: bool,
        priority_span_ids: list[str] | None = None,
    ) -> EvidencePacket:
        spans_by_group: dict[str, list] = defaultdict(list)
        for span in inventory.spans:
            spans_by_group[span.context_group_id].append(span)

        object_rankings = self.object_ranker.rank(
            inventory=inventory,
            query=query,
            hits=hits,
            priority_span_ids=priority_span_ids or [],
        )
        # For energy/GHG measures, lexical compatibility ranks evidence; it
        # cannot exclude a strongly retrieved numeric object from model review.
        # The established mass/pollution selection stays unchanged.
        model_led = metric.value_family in {"quantitative_energy", "quantitative_ghg", "energy_intensity", "ghg_intensity"}
        eligible_rankings = [item for item in object_rankings if item.eligible or (model_led and self._semantic_review_candidate(item, hits))]
        scoped_rankings = []
        for ranked_object in eligible_rankings:
            scope = self.object_scope.select(
                ranked_object=ranked_object,
                inventory=inventory,
                query=query,
                metric=metric,
                elements=elements,
                hits=hits,
                semantic_review=model_led,
            )
            if scope.group_ids:
                scoped_rankings.append((ranked_object, scope))
        semantic_review_used = model_led
        if not scoped_rankings:
            # A literal FactPattern is a useful ranking signal, not a semantic
            # admission gate. If it misses a differently worded disclosure but
            # hybrid retrieval strongly identifies a quantitative table/figure,
            # retain that object for one bounded model judgement.
            for ranked_object in object_rankings:
                if not self._semantic_review_candidate(ranked_object, hits):
                    continue
                scope = self.object_scope.select(
                    ranked_object=ranked_object,
                    inventory=inventory,
                    query=query,
                    metric=metric,
                    elements=elements,
                    hits=hits,
                    semantic_review=True,
                )
                if scope.group_ids:
                    scoped_rankings.append((ranked_object, scope))
                    semantic_review_used = True
                    break
        selected_scopes = scoped_rankings[: self.max_retrieved_objects]
        selected_object_keys = [
            (ranked.kind, ranked.object_id) for ranked, _ in selected_scopes
        ]
        group_order = list(
            dict.fromkeys(
                group_id
                for _, scope in selected_scopes
                for group_id in scope.group_ids
            )
        )
        object_limit_reached = len(scoped_rankings) > len(selected_scopes)
        source_group_count = len(group_order)
        selected_group_ids = group_order[: self.max_groups]
        selected = []
        for group_id in selected_group_ids:
            selected.extend(
                sorted(
                    spans_by_group.get(group_id, []),
                    key=lambda item: (
                        self._span_rank(item.span_type),
                        item.page_index,
                        item.span_id,
                    ),
                )
            )
        source_span_count = len(selected)
        selected = selected[: self.max_spans]
        selected_ids = {item.span_id for item in selected}
        selected_group_ids = list(
            dict.fromkeys(item.context_group_id for item in selected)
        )

        candidates = [
            item
            for item in inventory.candidates
            if item.span_id in selected_ids
            and candidate_allowed_in_packet(item.candidate_type, query)
        ]
        candidates.sort(
            key=lambda item: (
                selected_group_ids.index(item.context_group_id),
                item.span_id,
                item.char_start,
                item.candidate_id,
            )
        )
        source_candidate_count = len(candidates)
        candidates = candidates[: self.max_candidates]

        span_aliases = {
            f"S{index}": item.span_id for index, item in enumerate(selected, 1)
        }
        candidate_aliases = {
            f"C{index}": item.candidate_id for index, item in enumerate(candidates, 1)
        }
        element_aliases = {
            f"E{index}": item.element_id
            for index, item in enumerate(
                [item for item in elements if item.value_contract.fixed_value is None],
                1,
            )
        }
        group_aliases = {
            f"G{index}": group_id
            for index, group_id in enumerate(selected_group_ids, 1)
        }

        images = self._visual_artifacts(inventory, selected)
        selected_hits = [item for item in hits if item.span_id in selected_ids]
        metric_contract = self._metric_contract(metric)
        metric_contract["fixed_scope"] = [
            {
                "element_code": item.element_code,
                "label": item.labels.get("zh") or item.labels.get("en"),
                "value": item.value_contract.fixed_value,
            }
            for item in elements
            if item.value_contract.fixed_value is not None
        ]
        metric_contract["excluded_scope_terms"] = query.intent.must_not_terms
        metric_contract["semantic_intent"] = {
            "subject_term_groups": query.intent.topic_term_groups,
            "fixed_role_term_groups": query.intent.role_term_groups,
            "expected_candidate_types": query.intent.expected_candidate_types,
            "purpose": (
                "retrieval vocabulary for semantic interpretation; terms are not "
                "a checklist and need not appear verbatim in the report"
            ),
        }
        compact_contract = {
            "task_id": task_id,
            "metric": metric_contract,
            "elements": [
                self._element_contract(item, element_aliases)
                for item in elements
                if item.value_contract.fixed_value is None
            ],
            "selection": {
                "group_count": len(selected_group_ids),
                "span_count": len(selected),
                "candidate_count": len(candidates),
                "visual_artifact_count": len(images),
                "retrieval_object_limit": self.max_retrieved_objects,
                "selected_retrieval_object_count": len(selected_object_keys),
                "selection_unit": "independent_evidence_object",
                "semantic_model_review_fallback": semantic_review_used,
            },
        }
        model_context = json.dumps(
            compact_contract, ensure_ascii=False, separators=(",", ":")
        )
        truncated = bool(
            source_group_count > len(selected_group_ids)
            or source_span_count > len(selected)
            or source_candidate_count > len(candidates)
            or object_limit_reached
        )
        return EvidencePacket(
            packet_id=stable_id(
                "evidence-selection",
                task_id,
                query.metric_id,
                selected_group_ids,
            ),
            task_id=task_id,
            metric=metric.model_dump(mode="json"),
            elements=[item.model_dump(mode="json") for item in elements],
            query=query,
            spans=selected,
            candidates=candidates,
            retrieval_hits=selected_hits,
            allowed_group_ids=selected_group_ids,
            page_image_paths=images,
            retrieval_complete=retrieval_complete,
            truncated=truncated,
            model_context=model_context,
            alias_map={
                "spans": span_aliases,
                "candidates": candidate_aliases,
                "elements": element_aliases,
                "groups": group_aliases,
                "visual": {},
            },
            budget={
                "stage": "retrieved_evidence_selection",
                "source_group_count": source_group_count,
                "selected_group_count": len(selected_group_ids),
                "source_span_count": source_span_count,
                "selected_span_count": len(selected),
                "source_candidate_count": source_candidate_count,
                "selected_candidate_count": len(candidates),
                "visual_artifact_count": len(images),
                "selection_truncated": truncated,
                "retrieval_object_limit": self.max_retrieved_objects,
                "selected_retrieval_object_count": len(selected_object_keys),
                "semantic_model_review_fallback": semantic_review_used,
                "retrieval_object_limit_reached": object_limit_reached,
                "selected_retrieval_objects": [
                    {
                        "kind": ranked.kind,
                        "id": ranked.object_id,
                        "score": round(ranked.score, 6),
                        "selected_group_ids": list(scope.group_ids),
                        "excluded_group_count": scope.excluded_group_count,
                        "scope_reasons": list(scope.reasons),
                    }
                    for ranked, scope in selected_scopes
                ],
                "evidence_object_ranking": [
                    item.as_dict() for item in object_rankings
                ],
                "selected_page_images": {
                    str(page_index): path
                    for page_index, path in inventory.page_images.items()
                    if page_index in {item.page_index for item in selected}
                },
            },
        )

    def _visual_artifacts(self, inventory: EvidenceInventory, spans: list) -> list[str]:
        result: list[str] = []
        selected_pages = {item.page_index for item in spans}
        for span in spans:
            for path in span.visual_evidence_paths:
                if path not in result:
                    result.append(path)
        for page_index in sorted(selected_pages):
            path = inventory.page_images.get(page_index)
            if path and path not in result:
                result.append(path)
        return result[: self.max_visual_artifacts]

    @staticmethod
    def _span_rank(span_type: str) -> int:
        return {
            "table_row": 0,
            "table_cell": 1,
            "figure_text": 2,
            "sentence": 3,
            "block": 4,
            "page_text": 5,
        }.get(span_type, 9)

    @staticmethod
    def _metric_contract(metric: MetricDefinition) -> dict[str, Any]:
        return {
            "id": metric.metric_id,
            "source_datapoint_id": metric.source_datapoint_id,
            "label": metric.labels.get("zh") or metric.labels.get("en"),
            "description": metric.description,
            "reporting_requirement": metric.reporting_requirement,
            "data_class": metric.data_class.value,
            "value_family": metric.value_family,
            "cardinality": metric.cardinality.model_dump(mode="json"),
        }

    @staticmethod
    def _semantic_review_candidate(ranked_object, hits: list[RetrievalHit]) -> bool:
        if (
            ranked_object.kind not in {"table", "figure"}
            or ranked_object.primary_literal_count == 0
            or ranked_object.hit_count == 0
            or "navigation_or_disclosure_index" in ranked_object.reasons
        ):
            return False
        object_span_ids = set(ranked_object.hit_span_ids)
        object_hits = [item for item in hits if item.span_id in object_span_ids]
        strong_hybrid_rank = any(
            (item.semantic_rank is not None and item.semantic_rank <= 8)
            or (item.lexical_rank is not None and item.lexical_rank <= 8)
            for item in object_hits
        )
        return bool(
            strong_hybrid_rank
            or ranked_object.topic_coverage > 0
            or ranked_object.role_coverage > 0
        )

    @staticmethod
    def _element_contract(
        element: ElementDefinition,
        aliases: dict[str, str],
    ) -> dict[str, Any]:
        alias = next(
            key for key, value in aliases.items() if value == element.element_id
        )
        return {
            "id": alias,
            "element_id": element.element_id,
            "code": element.element_code,
            "label": element.labels.get("zh") or element.labels.get("en"),
            "description": element.description,
            "semantic_role": element.semantic_role,
            "type": element.value_contract.primary_type.value,
            "required": element.cardinality.minimum > 0,
            "nullable": element.null_allowed,
            "cardinality": element.cardinality.model_dump(mode="json"),
            "binding": element.binding.model_dump(mode="json"),
            "code_set_id": element.value_contract.code_set_id,
        }
