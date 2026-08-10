from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from esg_v2.evidence.contracts import EvidenceAtom
from esg_v2.evidence.local_index import LocalEvidenceIndex
from esg_v2.standards.contracts import RequirementProfile
from esg_v2.targeted.catalog import DisclosureCatalog, matched_aliases
from esg_v2.targeted.contracts import CandidateEvidence, LocalInferenceRecord, RequirementSearchTrace
from esg_v2.targeted.semantic import EmbeddingBackend, semantic_rankings


@dataclass
class RetrievalOutput:
    candidates: dict[str, list[CandidateEvidence]]
    traces: list[RequirementSearchTrace]
    local_inferences: list[LocalInferenceRecord]


class HybridLocalRetriever:
    """High-recall atom search followed by disclosure-group structural reranking."""

    def __init__(
        self,
        index: LocalEvidenceIndex,
        atoms: list[EvidenceAtom],
        *,
        catalog: DisclosureCatalog | None = None,
        semantic_backend: EmbeddingBackend | None = None,
        semantic_status: str = "not_requested",
    ):
        self.index = index
        self.atoms = {atom.atom_id: atom for atom in atoms}
        self.catalog = catalog or DisclosureCatalog.build(atoms)
        self.groups = self.catalog.by_id()
        self.semantic_backend = semantic_backend
        self.semantic_status = semantic_status

    def retrieve(
        self,
        profiles: list[RequirementProfile],
        *,
        mode_requested: str,
        top_k: int,
    ) -> RetrievalOutput:
        pool_k = min(200, max(60, top_k * 4))
        lexical: dict[str, list[tuple[str, object]]] = defaultdict(list)
        for profile in profiles:
            lanes = {
                "concept": self._concept_terms(profile),
                "dimension": self._dimension_terms(profile),
                "topic": profile.topic_terms,
                "combined": self._query_terms(profile),
            }
            for role, terms in lanes.items():
                if not terms:
                    continue
                lexical[profile.requirement_id].extend(
                    (role, hit) for hit in self.index.search(terms, top_k=pool_k)
                )

        semantic: dict[str, list[tuple[str, float]]] = {}
        inference_records: list[LocalInferenceRecord] = []
        semantic_status = self.semantic_status
        if self.semantic_backend:
            result = semantic_rankings(
                self.semantic_backend,
                atom_texts={atom_id: atom.search_text for atom_id, atom in self.atoms.items()},
                queries={profile.requirement_id: profile.query_text for profile in profiles},
                top_k=pool_k,
            )
            semantic = result.rankings
            inference_records.append(result.record)
            semantic_status = "succeeded"

        output: dict[str, list[CandidateEvidence]] = {}
        traces: list[RequirementSearchTrace] = []
        for profile in profiles:
            group_ranks: dict[str, dict[str, int]] = defaultdict(dict)
            group_terms: dict[str, set[str]] = defaultdict(set)
            group_atoms: dict[str, set[str]] = defaultdict(set)
            lane_counts: dict[str, int] = defaultdict(int)
            for role, hit in lexical[profile.requirement_id]:
                group_id = self.catalog.atom_to_group.get(hit.atom_id)
                if not group_id:
                    continue
                lane = f"{role}_{hit.lane}"
                current = group_ranks[group_id].get(lane)
                group_ranks[group_id][lane] = min(current, hit.rank) if current else hit.rank
                group_terms[group_id].update(hit.matched_terms)
                group_atoms[group_id].add(hit.atom_id)
                lane_counts[lane] += 1
            for rank, (atom_id, _score) in enumerate(semantic.get(profile.requirement_id, []), start=1):
                group_id = self.catalog.atom_to_group.get(atom_id)
                if not group_id:
                    continue
                current = group_ranks[group_id].get("local_embedding")
                group_ranks[group_id]["local_embedding"] = min(current, rank) if current else rank
                group_atoms[group_id].add(atom_id)
                lane_counts["local_embedding"] += 1

            candidates = [
                self._candidate(profile, group_id, ranks, group_terms[group_id], group_atoms[group_id])
                for group_id, ranks in group_ranks.items()
                if group_id in self.groups
            ]
            candidates.sort(key=lambda item: (-item.fused_score, item.group_id))
            returned = candidates[:top_k]
            output[profile.requirement_id] = returned
            effective_mode = "local_semantic" if semantic_status == "succeeded" else "local_strict"
            traces.append(
                RequirementSearchTrace(
                    requirement_id=profile.requirement_id,
                    query_terms=self._query_terms(profile),
                    mode_requested=mode_requested,
                    mode_effective=effective_mode,
                    lane_counts=dict(sorted(lane_counts.items())),
                    semantic_status=semantic_status,
                    candidate_count=len(candidates),
                    returned_candidate_count=len(returned),
                    candidate_limit=top_k,
                    candidate_limit_reached=len(candidates) > len(returned),
                    top_candidate_ids=[item.group_id for item in candidates[:10]],
                )
            )
        return RetrievalOutput(candidates=output, traces=traces, local_inferences=inference_records)

    def _candidate(
        self,
        profile: RequirementProfile,
        group_id: str,
        ranks: dict[str, int],
        lexical_terms: set[str],
        atom_ids: set[str],
    ) -> CandidateEvidence:
        group = self.groups[group_id]
        components: dict[str, float] = {}
        components["rrf"] = sum(1 / (60 + rank) for rank in ranks.values())

        concept_matches = matched_aliases(group.source_text, profile.measure_aliases)
        topic_matches = matched_aliases(group.search_text, profile.topic_terms)
        negative_matches = matched_aliases(group.source_text, profile.negative_aliases)
        dimension_matches = {
            name: matched_aliases(group.source_text, aliases)
            for name, aliases in profile.required_dimensions.items()
        }
        dimension_matches = {name: values for name, values in dimension_matches.items() if values}
        matched_slots: dict[str, list[str]] = {}
        if concept_matches:
            matched_slots["concept"] = concept_matches
            components["concept"] = min(0.24, 0.12 + 0.025 * len(concept_matches))
        if topic_matches:
            matched_slots["topic"] = topic_matches
            components["topic"] = min(0.04, 0.01 * len(topic_matches))
        if dimension_matches:
            components["dimensions"] = min(0.12, 0.035 * len(dimension_matches))
            matched_slots.update({f"dimension:{name}": values for name, values in dimension_matches.items()})
        if group.features.numeric_values or (profile.allow_explicit_zero_statement and group.features.explicit_zero):
            matched_slots["value"] = group.features.numeric_values[:10] or ["explicit_zero"]
            components["value"] = 0.08
        unit_matches = sorted(set(profile.unit_families) & set(group.features.unit_families))
        if unit_matches:
            matched_slots["unit"] = unit_matches
            components["unit"] = 0.08
        if group.features.years:
            matched_slots["period"] = [str(year) for year in group.features.years]
            components["period"] = 0.035
        if profile.requirement_kind == "table_bundle" and group.features.table_like:
            components["evidence_shape"] = 0.06
        elif profile.requirement_kind != "table_bundle" and group.group_type == "paragraph":
            components["evidence_shape"] = 0.035

        penalties: list[str] = []
        if group.features.index_like:
            components["index_penalty"] = -0.45
            penalties.append("index_like")
        if negative_matches:
            components["negative_concept_penalty"] = -min(0.3, 0.1 * len(negative_matches))
            penalties.extend(f"negative:{value}" for value in negative_matches[:5])
        if profile.requirement_kind == "table_bundle" and not group.features.table_like:
            components["wrong_shape_penalty"] = -0.12
            penalties.append("non_table_for_table_requirement")

        required_slot_ids = {slot.slot_id for slot in profile.required_slots if slot.required}
        rough_satisfied = required_slot_ids & set(matched_slots)
        coverage_ratio = len(rough_satisfied) / len(required_slot_ids) if required_slot_ids else 1.0
        score = sum(components.values())
        representative = self.atoms[group.representative_atom_id]
        return CandidateEvidence(
            requirement_id=profile.requirement_id,
            atom_id=group.representative_atom_id,
            atom_type=representative.atom_type,
            group_id=group.group_id,
            representative_atom_ids=[group.representative_atom_id, *sorted(atom_ids - {group.representative_atom_id})[:4]],
            fused_score=round(score, 8),
            lane_ranks=dict(sorted(ranks.items())),
            matched_terms=sorted(lexical_terms),
            matched_dimensions=dimension_matches,
            matched_slots=matched_slots,
            score_components={name: round(value, 6) for name, value in sorted(components.items())},
            penalties=penalties,
            index_like=group.features.index_like,
            coverage_ratio=round(coverage_ratio, 4),
            page_indices=group.page_indices,
            source_node_ids=representative.source_node_ids,
            quality_flags=group.quality_flags,
        )

    @staticmethod
    def _concept_terms(profile: RequirementProfile) -> list[str]:
        return list(dict.fromkeys([*profile.measure_aliases, *profile.alias_terms, *profile.exact_terms[:8]]))

    @staticmethod
    def _dimension_terms(profile: RequirementProfile) -> list[str]:
        return list(dict.fromkeys(alias for aliases in profile.required_dimensions.values() for alias in aliases))

    @classmethod
    def _query_terms(cls, profile: RequirementProfile) -> list[str]:
        return list(dict.fromkeys([*cls._concept_terms(profile), *profile.topic_terms, *cls._dimension_terms(profile)]))
