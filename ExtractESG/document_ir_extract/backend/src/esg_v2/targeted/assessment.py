from __future__ import annotations

import re
from collections import Counter

from esg_v2.evidence.contracts import EvidenceAtom
from esg_v2.standards.contracts import RequirementProfile
from esg_v2.targeted.catalog import DisclosureCatalog, matched_aliases
from esg_v2.targeted.contracts import (
    CandidateEvidence,
    CandidateVerification,
    SlotCoverage,
    TargetedAnswer,
    TargetedValidationReport,
)
from esg_v2.targeted.verification import RequirementVerifier


class TargetedAssessor:
    def __init__(self, atoms: list[EvidenceAtom], catalog: DisclosureCatalog | None = None):
        self.atoms = {atom.atom_id: atom for atom in atoms}
        self.catalog = catalog or DisclosureCatalog.build(atoms)
        self.groups = self.catalog.by_id()
        self.verifier = RequirementVerifier()
        self.verifications: list[CandidateVerification] = []

    def assess(
        self,
        profile: RequirementProfile,
        candidates: list[CandidateEvidence],
        *,
        search_truncated: bool = False,
    ) -> TargetedAnswer:
        if profile.requirement_kind == "conditional_absence":
            return self._conditional(profile, candidates, search_truncated=search_truncated)
        return self._quantitative_or_narrative(profile, candidates, search_truncated=search_truncated)

    def _quantitative_or_narrative(
        self,
        profile: RequirementProfile,
        candidates: list[CandidateEvidence],
        *,
        search_truncated: bool,
    ) -> TargetedAnswer:
        verified = [
            self.verifier.verify(profile, candidate, self.groups[candidate.group_id])
            for candidate in candidates
            if candidate.group_id in self.groups
        ]
        self.verifications.extend(verified)
        complete = sorted(
            (item for item in verified if item.decision == "complete"),
            key=lambda item: (-item.verifier_score, item.group_id),
        )
        if complete:
            selected = self._select_complete(profile, complete)
            margin = complete[0].verifier_score - (complete[1].verifier_score if len(complete) > 1 else 0)
            return self._answer_from_verified_groups(
                profile,
                candidates,
                selected,
                reasoning=(
                    "必要概念、量值、单位、期间和维度已由结构化证据组覆盖，并通过独立本地 Verifier；"
                    f"按照 {profile.multiplicity} 策略保留 {len(selected)} 个互补披露组。"
                ),
                confidence=min(0.97, 0.86 + max(0, min(0.08, margin))),
                routes=[
                    "disclosure_group_rerank",
                    "slot_coverage_complete",
                    f"multiplicity:{profile.multiplicity}",
                    "independent_local_verifier",
                ],
                easy=(len(selected) > 1 or margin >= 0.04)
                and all(not self.groups[item.group_id].quality_flags for item in selected),
                rejected=self._rejection_summary(verified, {item.group_id for item in selected}),
            )

        partial = sorted(
            (item for item in verified if item.decision == "partial"),
            key=lambda item: (-item.coverage_ratio, -item.verifier_score, item.group_id),
        )
        if partial:
            selected = partial[0]
            candidate = self._candidate(candidates, selected.group_id)
            group = self.groups[selected.group_id]
            missing = [item.slot_id for item in selected.slot_coverage if item.required and not item.satisfied]
            return self._answer_from_group(
                profile,
                candidate,
                group,
                status="uncertain",
                internal_decision="partial",
                value="不确定：发现相关披露，但缺少 " + "、".join(missing) + "。",
                reasoning=(
                    "候选召回达到本次上限，且最佳候选仍只覆盖部分必填槽位；系统保留证据但拒绝把主题相似当作完整答案。"
                    if search_truncated
                    else "候选只覆盖部分必填槽位；系统保留证据但拒绝把主题相似当作完整答案。"
                ),
                confidence=min(0.69, 0.3 + 0.4 * selected.coverage_ratio),
                routes=[
                    "disclosure_group_rerank",
                    "slot_coverage_partial",
                    *(["candidate_limit_reached"] if search_truncated else []),
                    "deterministic_abstain",
                ],
                easy=False,
                verification=selected,
                rejected=self._rejection_summary(verified, {selected.group_id}),
            )
        if search_truncated:
            return self._empty_answer(
                profile,
                status="uncertain",
                reasoning="候选召回达到本次上限，已核验候选均不完整，但仍不能据此断言整份报告未披露。",
                rejected=self._rejection_summary(verified),
            )
        return self._empty_answer(
            profile,
            status="not_found",
            reasoning="报告级本地多路检索完成且候选池未被上限截断；没有候选同时具备所需概念和事实形态，索引引用不计作披露事实。",
            rejected=self._rejection_summary(verified),
        )

    def _conditional(
        self,
        profile: RequirementProfile,
        candidates: list[CandidateEvidence],
        *,
        search_truncated: bool,
    ) -> TargetedAnswer:
        evaluations: list[CandidateVerification] = []
        contexts: dict[str, tuple[CandidateEvidence, object, list[str]]] = {}
        for candidate in candidates:
            group = self.groups.get(candidate.group_id)
            if not group:
                continue
            topic_clauses = self._topic_control_clauses(profile, group)
            contexts[group.group_id] = (candidate, group, topic_clauses)
            if group.features.index_like:
                evaluations.append(
                    CandidateVerification(
                        requirement_id=profile.requirement_id,
                        group_id=group.group_id,
                        decision="irrelevant",
                        reasons=["index_or_standard_reference_cannot_be_final_evidence"],
                        verifier_score=round(candidate.fused_score - 0.3, 6),
                    )
                )
                continue
            if not topic_clauses:
                evaluations.append(
                    CandidateVerification(
                        requirement_id=profile.requirement_id,
                        group_id=group.group_id,
                        decision="irrelevant",
                        reasons=["topic_and_control_not_in_same_clause"],
                        verifier_score=round(candidate.fused_score - 0.12, 6),
                    )
                )
                continue
            absence = next(
                (clause for clause in topic_clauses if self._matches(profile.absence_patterns, clause)),
                None,
            )
            positive = next(
                (clause for clause in topic_clauses if self._positive_control(profile, clause, group)),
                None,
            )
            if absence:
                decision = "complete"
                coverage = self._conditional_coverage(profile, group, "absence")
                reasons = ["explicit_absence_in_topic_clause"]
            elif positive:
                decision = "not_applicable"
                coverage = self._conditional_coverage(profile, group, profile.disclosure_type)
                reasons = ["positive_control_in_topic_clause"]
            else:
                decision = "ambiguous"
                coverage = self._conditional_coverage(
                    profile,
                    group,
                    "ambiguous",
                    statement_satisfied=False,
                )
                reasons = ["topic_found_but_control_state_ambiguous"]
            ratio = sum(item.satisfied for item in coverage if item.required) / max(
                1, sum(item.required for item in coverage)
            )
            evaluations.append(
                CandidateVerification(
                    requirement_id=profile.requirement_id,
                    group_id=group.group_id,
                    decision=decision,
                    coverage_ratio=ratio,
                    slot_coverage=coverage,
                    reasons=reasons,
                    verifier_score=round(candidate.fused_score + 0.2 * ratio, 6),
                )
            )
        self.verifications.extend(evaluations)

        absence_matches = sorted(
            (item for item in evaluations if item.decision == "complete"),
            key=lambda item: (-item.verifier_score, item.group_id),
        )
        if absence_matches:
            selected = absence_matches[0]
            candidate, group, _clauses = contexts[selected.group_id]
            return self._answer_from_group(
                profile,
                candidate,
                group,
                status="found",
                internal_decision="complete",
                value="报告明确披露该主题未采用相应政策、行动或目标；条件式 Data Point 适用。",
                reasoning="同一主题语句中识别到明确缺失表述，且证据不是目录或标准索引。",
                confidence=0.93,
                routes=["topic_clause_guard", "explicit_absence_pattern", "independent_local_verifier"],
                easy=not group.quality_flags,
                slot_coverage=selected.slot_coverage,
                rejected=self._rejection_summary(evaluations, {selected.group_id}),
            )

        positive_matches = sorted(
            (item for item in evaluations if item.decision == "not_applicable"),
            key=lambda item: (-item.verifier_score, item.group_id),
        )
        if positive_matches:
            selected = positive_matches[0]
            candidate, group, _clauses = contexts[selected.group_id]
            return self._answer_from_group(
                profile,
                candidate,
                group,
                status="not_applicable",
                internal_decision="not_applicable",
                value="报告在同一主题语句中提供了相应政策、行动或目标的正向披露，因此该条件式 Data Point 不适用。",
                reasoning="条件要求只在缺少相应控制时适用；主题与控制语句通过同句/同表格单元约束。",
                confidence=0.9,
                routes=["topic_clause_guard", "deterministic_applicability", "independent_local_verifier"],
                easy=not group.quality_flags,
                slot_coverage=selected.slot_coverage,
                rejected=self._rejection_summary(evaluations, {selected.group_id}),
            )

        ambiguous = sorted(
            (item for item in evaluations if item.decision == "ambiguous"),
            key=lambda item: (-item.verifier_score, item.group_id),
        )
        if ambiguous:
            selected = ambiguous[0]
            candidate, group, _clauses = contexts[selected.group_id]
            return self._answer_from_group(
                profile,
                candidate,
                group,
                status="uncertain",
                internal_decision="ambiguous",
                value="不确定：检索到主题相关内容，但无法证明明确缺失或明确采用。",
                reasoning="条件式判断需要主题与政策、行动或目标在同一语义单元内成立；报告沉默不能证明缺失。",
                confidence=0.42,
                routes=["topic_clause_guard", "deterministic_abstain"],
                easy=False,
                slot_coverage=selected.slot_coverage,
                rejected=self._rejection_summary(evaluations, {selected.group_id}),
            )
        return self._empty_answer(
            profile,
            status="uncertain",
            reasoning=(
                "候选召回达到上限，且已检查范围内没有足以判断条件适用性的主题内证据。"
                if search_truncated
                else "报告级搜索未取得足以判断该条件是否适用的主题内证据；缺失不能由沉默推断。"
            ),
            rejected=self._rejection_summary(evaluations),
        )

    def _answer_from_group(
        self,
        profile: RequirementProfile,
        candidate: CandidateEvidence,
        group,
        *,
        status: str,
        internal_decision: str,
        value: str,
        reasoning: str,
        confidence: float,
        routes: list[str],
        easy: bool,
        verification: CandidateVerification | None = None,
        slot_coverage: list[SlotCoverage] | None = None,
        rejected: list[str] | None = None,
    ) -> TargetedAnswer:
        atom = self.atoms[group.representative_atom_id]
        return TargetedAnswer(
            requirement_id=profile.requirement_id,
            sequence=profile.sequence,
            source_sheet=profile.source_sheet,
            source_row=profile.source_row,
            data_point_id=profile.data_point_id,
            status=status,
            internal_decision=internal_decision,
            location=self._location(atom),
            quote=self._excerpt(atom.source_text, [*profile.measure_aliases, *candidate.matched_terms]),
            value=value,
            easy_to_judge=easy,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            selected_atom_ids=[atom.atom_id],
            selected_group_ids=[group.group_id],
            selected_source_node_ids=atom.source_node_ids,
            matched_dimensions=candidate.matched_dimensions,
            decision_routes=list(dict.fromkeys([*routes, *candidate.lane_ranks])),
            quality_flags=group.quality_flags,
            slot_coverage=verification.slot_coverage if verification else (slot_coverage or []),
            facts=verification.facts if verification else [],
            rejected_candidate_reasons=rejected or [],
        )

    def _answer_from_verified_groups(
        self,
        profile: RequirementProfile,
        candidates: list[CandidateEvidence],
        selected: list[CandidateVerification],
        *,
        reasoning: str,
        confidence: float,
        routes: list[str],
        easy: bool,
        rejected: list[str],
    ) -> TargetedAnswer:
        selected_candidates = [self._candidate(candidates, item.group_id) for item in selected]
        groups = [self.groups[item.group_id] for item in selected]
        atoms = [self.atoms[group.representative_atom_id] for group in groups]
        facts = self._unique_facts([fact for item in selected for fact in item.facts])
        matched_dimensions: dict[str, list[str]] = {}
        for candidate in selected_candidates:
            for name, values in candidate.matched_dimensions.items():
                matched_dimensions.setdefault(name, [])
                matched_dimensions[name].extend(value for value in values if value not in matched_dimensions[name])
        return TargetedAnswer(
            requirement_id=profile.requirement_id,
            sequence=profile.sequence,
            source_sheet=profile.source_sheet,
            source_row=profile.source_row,
            data_point_id=profile.data_point_id,
            status="found",
            internal_decision="complete",
            location=self._combined_location(atoms),
            quote=self._combined_quote(profile, selected_candidates, atoms),
            value=self._render_aggregated_value(selected, groups),
            easy_to_judge=easy,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            selected_atom_ids=[atom.atom_id for atom in atoms],
            selected_group_ids=[group.group_id for group in groups],
            selected_source_node_ids=list(
                dict.fromkeys(node_id for atom in atoms for node_id in atom.source_node_ids)
            ),
            matched_dimensions=matched_dimensions,
            decision_routes=list(
                dict.fromkeys([*routes, *(lane for candidate in selected_candidates for lane in candidate.lane_ranks)])
            ),
            quality_flags=list(dict.fromkeys(flag for group in groups for flag in group.quality_flags)),
            slot_coverage=self._merge_slot_coverage(selected),
            facts=facts,
            rejected_candidate_reasons=rejected,
        )

    @staticmethod
    def _select_complete(
        profile: RequirementProfile,
        complete: list[CandidateVerification],
    ) -> list[CandidateVerification]:
        if profile.multiplicity in {"single_best", "table_bundle"}:
            return complete[:1]
        if profile.multiplicity == "all_instances":
            return complete
        selected: list[CandidateVerification] = []
        seen: set[tuple] = set()
        for item in complete:
            signatures = {TargetedAssessor._fact_signature(fact) for fact in item.facts}
            if not selected or not signatures or signatures - seen:
                selected.append(item)
                seen.update(signatures)
        return selected

    @staticmethod
    def _fact_signature(fact) -> tuple:
        dimensions = tuple(sorted((name, tuple(values)) for name, values in fact.dimensions.items()))
        values = tuple(fact.numeric_values) if fact.numeric_values else (re.sub(r"\s+", " ", fact.value).strip(),)
        return (fact.concept_id, values, fact.unit_family, fact.period, dimensions)

    @staticmethod
    def _unique_facts(facts: list) -> list:
        output = []
        seen = set()
        for fact in facts:
            key = fact.fact_id or TargetedAssessor._fact_signature(fact)
            if key in seen:
                continue
            seen.add(key)
            output.append(fact)
        return output

    @staticmethod
    def _merge_slot_coverage(verifications: list[CandidateVerification]) -> list[SlotCoverage]:
        merged: dict[str, SlotCoverage] = {}
        for verification in verifications:
            for item in verification.slot_coverage:
                current = merged.get(item.slot_id)
                if current is None:
                    merged[item.slot_id] = item.model_copy(deep=True)
                    continue
                current.satisfied = current.satisfied or item.satisfied
                current.matched_values = list(dict.fromkeys([*current.matched_values, *item.matched_values]))
                current.evidence_atom_ids = list(
                    dict.fromkeys([*current.evidence_atom_ids, *item.evidence_atom_ids])
                )
        return list(merged.values())

    def _combined_quote(
        self,
        profile: RequirementProfile,
        candidates: list[CandidateEvidence],
        atoms: list[EvidenceAtom],
    ) -> str:
        excerpts = [
            self._excerpt(atom.source_text, [*profile.measure_aliases, *candidate.matched_terms])
            for candidate, atom in zip(candidates, atoms, strict=True)
        ]
        return "\n\n--- evidence ---\n\n".join(excerpts)

    @staticmethod
    def _combined_location(atoms: list[EvidenceAtom]) -> str:
        pages = sorted({number for atom in atoms for number in atom.location.page_numbers})
        sections = list(
            dict.fromkeys(" > ".join(atom.location.section_path) for atom in atoms if atom.location.section_path)
        )
        page_text = "、".join(str(number) for number in pages)
        if page_text and sections:
            return f"PDF第{page_text}页；" + "；".join(sections)
        return f"PDF第{page_text}页" if page_text else "；".join(sections)

    @staticmethod
    def _render_aggregated_value(
        verifications: list[CandidateVerification],
        groups: list,
    ) -> str:
        if len(verifications) == 1:
            return TargetedAssessor._render_value(verifications[0], groups[0])
        lines = []
        for verification, group in zip(verifications, groups, strict=True):
            if verification.facts:
                lines.extend(f"[{group.group_id}] {fact.value}" for fact in verification.facts)
            else:
                lines.append(f"[{group.group_id}] {group.source_text}")
        return "\n".join(lines)[:30000]

    @staticmethod
    def _empty_answer(
        profile: RequirementProfile,
        *,
        status: str,
        reasoning: str,
        rejected: list[str] | None = None,
    ) -> TargetedAnswer:
        return TargetedAnswer(
            requirement_id=profile.requirement_id,
            sequence=profile.sequence,
            source_sheet=profile.source_sheet,
            source_row=profile.source_row,
            data_point_id=profile.data_point_id,
            status=status,
            internal_decision="irrelevant" if status == "not_found" else "ambiguous",
            value="否：未找到满足要求的证据。" if status == "not_found" else "不确定。",
            easy_to_judge=False,
            confidence=0.55 if status == "not_found" else 0.2,
            reasoning=reasoning,
            decision_routes=["report_search_coverage_ledger", "deterministic_abstain"],
            rejected_candidate_reasons=rejected or [],
        )

    def _topic_control_clauses(self, profile: RequirementProfile, group) -> list[str]:
        anchors = profile.topic_anchor_terms or profile.topic_terms
        units = group.row_texts or [group.source_text]
        clauses: list[str] = []
        for unit in units:
            parts = [
                value.strip()
                for value in re.split(r"[。！？!?\n]|\s+\|\s+|</t[dh]>|</tr>", unit, flags=re.IGNORECASE)
                if value.strip()
            ]
            for part in parts:
                clauses.extend(self._topic_windows(part, anchors))
        return list(dict.fromkeys(clauses))

    @staticmethod
    def _topic_windows(text: str, anchors: list[str], radius: int = 80) -> list[str]:
        lowered = text.casefold()
        windows = []
        for anchor in anchors:
            needle = anchor.casefold().strip()
            if not needle:
                continue
            start = 0
            while True:
                position = lowered.find(needle, start)
                if position < 0:
                    break
                windows.append(text[max(0, position - radius) : position + len(anchor) + radius])
                start = position + max(1, len(needle))
        return windows

    def _positive_control(self, profile: RequirementProfile, clause: str, group) -> bool:
        if not self._matches(profile.positive_patterns, clause):
            return False
        if profile.disclosure_type == "target":
            has_measure = bool(re.search(r"\d|%|年|減少|降低|increase|reduce", clause, flags=re.IGNORECASE))
            return has_measure
        return profile.disclosure_type in group.features.statement_types

    @staticmethod
    def _conditional_coverage(
        profile: RequirementProfile,
        group,
        statement: str,
        *,
        statement_satisfied: bool = True,
    ) -> list[SlotCoverage]:
        return [
            SlotCoverage(
                slot_id="concept",
                satisfied=True,
                matched_values=matched_aliases(group.source_text, profile.topic_terms),
                evidence_atom_ids=[group.representative_atom_id],
                reason="topic appears in the same clause as the control statement",
            ),
            SlotCoverage(
                slot_id=f"statement:{profile.disclosure_type}",
                satisfied=statement_satisfied,
                matched_values=[statement] if statement_satisfied else [],
                evidence_atom_ids=[group.representative_atom_id] if statement_satisfied else [],
                reason="explicit absence or positive control statement",
            ),
        ]

    @staticmethod
    def _candidate(candidates: list[CandidateEvidence], group_id: str) -> CandidateEvidence:
        return next(item for item in candidates if item.group_id == group_id)

    @staticmethod
    def _render_value(verification: CandidateVerification, group) -> str:
        if len(verification.facts) == 1 and verification.facts[0].value == "0":
            return "0"
        return group.source_text[:12000]

    @staticmethod
    def _rejection_summary(
        verifications: list[CandidateVerification],
        selected_group_ids: set[str] | None = None,
    ) -> list[str]:
        rows = []
        for item in verifications:
            if selected_group_ids and item.group_id in selected_group_ids:
                continue
            if item.reasons:
                rows.append(f"{item.group_id}: " + "; ".join(item.reasons))
            if len(rows) >= 12:
                break
        return rows

    @staticmethod
    def _matches(patterns: list[str], text: str) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)

    @staticmethod
    def _location(atom: EvidenceAtom) -> str:
        pages = "、".join(str(number) for number in atom.location.page_numbers)
        section = " > ".join(atom.location.section_path)
        if pages and section:
            return f"PDF第{pages}页；{section}"
        return f"PDF第{pages}页" if pages else section

    @staticmethod
    def _excerpt(text: str, terms: list[str], limit: int = 6000) -> str:
        if len(text) <= limit:
            return text
        lowered = text.casefold()
        positions = [lowered.find(term.casefold()) for term in terms if term and lowered.find(term.casefold()) >= 0]
        center = min(positions) if positions else 0
        start = max(0, center - limit // 4)
        return text[start : start + limit]


class TargetedGuard:
    def validate(
        self,
        profiles: list[RequirementProfile],
        answers: list[TargetedAnswer],
        atoms: list[EvidenceAtom],
        *,
        cloud_calls: list[dict[str, object]],
        catalog: DisclosureCatalog | None = None,
    ) -> TargetedValidationReport:
        atom_map = {atom.atom_id: atom for atom in atoms}
        catalog = catalog or DisclosureCatalog.build(atoms)
        groups = catalog.by_id()
        profile_map = {profile.requirement_id: profile for profile in profiles}

        def required_slots_complete(answer: TargetedAnswer) -> bool:
            if answer.status != "found":
                return True
            profile = profile_map[answer.requirement_id]
            if profile.requirement_kind == "conditional_absence":
                return all(item.satisfied for item in answer.slot_coverage if item.required)
            covered = {item.slot_id for item in answer.slot_coverage if item.satisfied}
            required = {item.slot_id for item in profile.required_slots if item.required}
            return required <= covered and answer.internal_decision == "complete"

        checks = {
            "one_answer_per_requirement": len(answers) == len(profiles) == len({item.requirement_id for item in answers}),
            "terminal_status_for_every_answer": all(answer.status in {"found", "not_found", "not_applicable", "uncertain", "system_failed"} for answer in answers),
            "grounded_positive_answers": all(
                answer.selected_atom_ids and answer.selected_group_ids and answer.quote
                for answer in answers
                if answer.status in {"found", "not_applicable"}
            ),
            "quotes_are_exact": all(
                all(
                    any(excerpt in atom_map[atom_id].source_text for atom_id in answer.selected_atom_ids if atom_id in atom_map)
                    for excerpt in answer.quote.split("\n\n--- evidence ---\n\n")
                    if excerpt
                )
                for answer in answers
                if answer.quote
            ),
            "selected_atoms_exist": all(atom_id in atom_map for answer in answers for atom_id in answer.selected_atom_ids),
            "selected_groups_exist": all(group_id in groups for answer in answers for group_id in answer.selected_group_ids),
            "index_not_used_as_final_evidence": all(
                not groups[group_id].features.index_like
                for answer in answers
                if answer.status in {"found", "not_applicable"}
                for group_id in answer.selected_group_ids
            ),
            "required_slots_complete_for_found": all(required_slots_complete(answer) for answer in answers),
            "applicability_slots_complete": all(
                all(item.satisfied for item in answer.slot_coverage if item.required)
                for answer in answers
                if answer.status == "not_applicable"
            ),
            "quantitative_found_has_facts": all(
                answer.facts
                for answer in answers
                if answer.status == "found" and profile_map[answer.requirement_id].disclosure_type.startswith("quantitative")
            ),
            "cloud_calls_zero": len(cloud_calls) == 0,
            "system_failures_not_mapped_to_not_found": all(
                not (answer.status == "system_failed" and answer.value.startswith("否")) for answer in answers
            ),
        }
        issues = [
            {"code": name, "severity": "error", "message": f"Targeted Guard failed: {name}"}
            for name, passed in checks.items()
            if not passed
        ]
        counts = Counter(answer.status for answer in answers)
        return TargetedValidationReport(
            valid=all(checks.values()),
            can_export=all(checks.values()),
            checks=checks,
            status_counts=dict(sorted(counts.items())),
            issues=issues,
        )
