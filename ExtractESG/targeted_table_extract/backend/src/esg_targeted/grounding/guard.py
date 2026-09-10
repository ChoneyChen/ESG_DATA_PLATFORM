from __future__ import annotations

import json
import re
from collections import Counter

from esg_targeted.contracts import (
    EvidencePacket,
    GuardIssue,
    GuardResult,
    SemanticDecision,
)
from esg_targeted.evidence.target_cells import (
    target_cell_contracts,
    values_equivalent,
)


NUMERIC_TYPES = {"decimal", "decimal_range", "integer"}
NUMERIC_CANDIDATES = {"number", "quantity", "percentage"}
DATE_TYPES = {"year", "date", "date_range"}
DATE_CANDIDATES = {
    "year": {"year"},
    "date": {"date", "year"},
    "date_range": {"date_range", "date", "year"},
}
NUMBER_RE = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")


class GroundingGuard:
    """Reject only deterministic contract/provenance defects.

    Relevance, semantic categorisation and visual table reading belong to the
    model. The guard does not second-guess those judgements or require a visual
    value to have been found by a local regex first.
    """

    def validate(self, packet: EvidencePacket, decision: SemanticDecision) -> GuardResult:
        issues: list[GuardIssue] = []
        spans = {item.span_id: item for item in packet.spans}
        candidates = {item.candidate_id: item for item in packet.candidates}
        elements = {item["element_id"]: item for item in packet.elements}
        allowed_fact_group_ids = set(packet.allowed_group_ids) | {
            item.cell_id for item in target_cell_contracts(packet)
        }

        if decision.task_id != packet.task_id:
            issues.append(
                self._error(
                    "task_id_mismatch",
                    f"Expected {packet.task_id}, got {decision.task_id}",
                    recommended_action="Copy task_id exactly.",
                )
            )
        if decision.status in {"found", "partial"} and not decision.fact_groups:
            issues.append(
                self._error(
                    "positive_status_without_rows",
                    "found/partial requires at least one grounded output row",
                )
            )
        if decision.status in {"not_found", "ambiguous"} and decision.fact_groups:
            issues.append(
                self._error(
                    "empty_status_with_rows",
                    "not_found/ambiguous cannot contain output rows",
                )
            )
        if decision.status == "not_found" and not packet.retrieval_complete:
            issues.append(
                self._error(
                    "incomplete_search",
                    "not_found requires a complete retrieval pass",
                )
            )

        for span_id in set(decision.selected_evidence_span_ids):
            if span_id not in spans:
                issues.append(
                    self._error(
                        "unknown_selected_span",
                        f"Unknown selected evidence span: {span_id}",
                        source_ref_id=span_id,
                    )
                )

        assignment_count = 0
        row_signatures: set[tuple] = set()
        for row_index, group in enumerate(decision.fact_groups, 1):
            if group.group_ref_id not in allowed_fact_group_ids:
                issues.append(
                    self._error(
                        "unknown_group",
                        f"Unknown row group: {group.group_ref_id}",
                        group_ref_id=group.group_ref_id,
                    )
                )
            counts = Counter(item.element_id for item in group.assignments)
            for element_id, count in counts.items():
                maximum = elements.get(element_id, {}).get("cardinality", {}).get(
                    "maximum"
                )
                if maximum is not None and count > maximum:
                    issues.append(
                        self._error(
                            "element_cardinality_exceeded",
                            f"Field appears {count} times but maximum is {maximum}",
                            group_ref_id=group.group_ref_id,
                            element_id=element_id,
                        )
                    )

            for assignment in group.assignments:
                assignment_count += 1
                element = elements.get(assignment.element_id)
                if element is None:
                    issues.append(
                        self._error(
                            "unknown_element",
                            f"Field is not defined by the selected standard package: "
                            f"{assignment.element_id}",
                            group_ref_id=group.group_ref_id,
                            element_id=assignment.element_id,
                        )
                    )
                    continue
                if element.get("value_contract", {}).get("fixed_value") is not None:
                    issues.append(
                        self._error(
                            "fixed_element_returned_by_model",
                            "Fixed fields are materialized from the standard package, not model output",
                            group_ref_id=group.group_ref_id,
                            element_id=assignment.element_id,
                        )
                    )
                    continue
                if not assignment.value_raw or not assignment.value_raw.strip():
                    issues.append(
                        self._error(
                            "model_value_missing",
                            "Every non-null filled field requires a visible value",
                            group_ref_id=group.group_ref_id,
                            element_id=assignment.element_id,
                            source_ref_id=assignment.source_ref_id,
                        )
                    )
                unknown_evidence = set(assignment.evidence_span_ids) - spans.keys()
                for span_id in sorted(unknown_evidence):
                    issues.append(
                        self._error(
                            "unknown_assignment_span",
                            f"Unknown evidence span: {span_id}",
                            group_ref_id=group.group_ref_id,
                            element_id=assignment.element_id,
                            source_ref_id=span_id,
                        )
                    )
                if not assignment.evidence_span_ids:
                    issues.append(
                        self._error(
                            "missing_assignment_evidence",
                            "Every filled field requires evidence",
                            group_ref_id=group.group_ref_id,
                            element_id=assignment.element_id,
                            source_ref_id=assignment.source_ref_id,
                        )
                    )
                issues.extend(
                    self._source_issues(
                        packet,
                        group.group_ref_id,
                        assignment,
                        element,
                        spans,
                        candidates,
                    )
                )

            issues.extend(
                self._row_shape_issues(group, decision.status, elements)
            )
            issues.extend(
                self._table_coordinate_issues(group, elements, spans, candidates)
            )
            for element_id, element in elements.items():
                if element.get("value_contract", {}).get("fixed_value") is not None:
                    continue
                if int(element.get("cardinality", {}).get("minimum", 0)) <= 0:
                    continue
                if element_id in counts:
                    continue
                issues.append(
                    GuardIssue(
                        code="required_element_missing",
                        severity="warning",
                        message=f"Required standard field is absent: {element_id}",
                        group_ref_id=group.group_ref_id,
                        element_id=element_id,
                        recommended_action=(
                            "Retain the grounded row and leave this field empty when the "
                            "report does not explicitly disclose it."
                        ),
                    )
                )

            signature = (
                group.group_ref_id,
                tuple(
                    sorted(
                        (
                            item.element_id,
                            (item.value_raw or "").strip(),
                            item.source_mode,
                            item.source_ref_id,
                        )
                        for item in group.assignments
                    )
                ),
            )
            if signature in row_signatures:
                issues.append(
                    self._error(
                        "duplicate_output_row",
                        f"Output row {row_index} duplicates an earlier fact",
                        group_ref_id=group.group_ref_id,
                    )
                )
            row_signatures.add(signature)

        issues.extend(self._target_cell_coverage_issues(packet, decision, elements))

        errors = [item for item in issues if item.severity == "error"]
        return GuardResult(
            task_id=packet.task_id,
            accepted=not errors,
            recoverable=bool(errors),
            issues=issues,
            accepted_group_count=len(decision.fact_groups) if not errors else 0,
            accepted_assignment_count=assignment_count if not errors else 0,
            feedback=self._feedback(issues),
        )

    def _target_cell_coverage_issues(self, packet, decision, elements):
        if decision.status not in {"found", "partial"}:
            return []
        target_cells = target_cell_contracts(packet)
        if not target_cells:
            return []
        primary_element_ids = {
            element_id
            for element_id, element in elements.items()
            if element.get("binding", {}).get("target") == "value_raw"
        }
        emitted = [
            (group.group_ref_id, assignment)
            for group in decision.fact_groups
            for assignment in group.assignments
            if assignment.element_id in primary_element_ids and assignment.value_raw
        ]
        issues = []
        for cell in target_cells:
            covered = any(
                group_id in {cell.group_id, cell.cell_id}
                and (
                    (cell.span_id and cell.span_id in assignment.evidence_span_ids)
                    or values_equivalent(
                        assignment.value_raw or "", cell.value_forms
                    )
                )
                for group_id, assignment in emitted
            )
            if covered:
                continue
            issues.append(
                self._warning(
                    "target_value_cell_not_emitted",
                    f"Listed target value cell {cell.cell_id} "
                    f"({', '.join(sorted(cell.value_forms))}) was not "
                    "emitted; it may be out of metric scope or may require review",
                    group_ref_id=cell.group_id,
                    recommended_action=(
                        "If the cell is applicable, emit it with its T target_cell id; "
                        "otherwise leave it omitted."
                    ),
                )
            )
        return issues

    @staticmethod
    def _source_group_matches_fact_group(
        packet, group_id, source_group, source_span
    ) -> bool:
        if source_group == group_id:
            return True
        target = next(
            (
                item
                for item in target_cell_contracts(packet)
                if item.cell_id == group_id
            ),
            None,
        )
        if target is not None and source_group == target.group_id:
            return True
        structure = source_span.structural_context or {} if source_span else {}
        return structure.get("cell_id") == group_id

    def _source_issues(
        self,
        packet,
        group_id,
        assignment,
        element,
        spans,
        candidates,
    ):
        issues = []
        value = (assignment.value_raw or "").strip()
        candidate = candidates.get(assignment.source_ref_id)
        span = spans.get(assignment.source_ref_id)

        if assignment.source_mode == "candidate":
            if candidate is None:
                return [
                    self._error(
                        "candidate_source_missing",
                        "candidate source_mode requires a supplied literal candidate",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                    )
                ]
            source_span = spans.get(candidate.span_id)
            source_group = candidate.context_group_id
            candidate_forms = {candidate.raw_value.strip()}
            if candidate.normalized_value:
                candidate_forms.add(candidate.normalized_value.strip())
            losslessly_equal = bool(
                candidate.candidate_type in NUMERIC_CANDIDATES
                and values_equivalent(value, candidate_forms)
            )
            if value != candidate.raw_value.strip() and not losslessly_equal:
                issues.append(
                    self._error(
                        "candidate_value_mismatch",
                        f"Value {value!r} does not match selected literal "
                        f"{candidate.raw_value!r}",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                        recommended_action="Copy the selected C value exactly.",
                    )
                )
            if candidate.span_id not in assignment.evidence_span_ids:
                issues.append(
                    self._error(
                        "candidate_evidence_mismatch",
                        "Candidate's source span is not cited",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                    )
                )
            if not self._candidate_type_compatible(element, candidate.candidate_type):
                issues.append(
                    self._error(
                        "candidate_type_mismatch",
                        f"Candidate type {candidate.candidate_type} is incompatible with "
                        f"{element['value_contract']['primary_type']}",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                    )
                )
        elif assignment.source_mode == "span":
            if span is None:
                return [
                    self._error(
                        "span_source_missing",
                        "span source_mode requires a supplied Document IR span",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                    )
                ]
            source_span = span
            source_group = span.context_group_id
            if not self._verbatim_in_span(value, span):
                issues.append(
                    self._error(
                        "span_value_mismatch",
                        f"Value {value!r} is not visible in the selected Document IR span",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                        recommended_action="Copy an exact visible substring or use a V source.",
                    )
                )
        elif assignment.source_mode == "visual":
            if span is None:
                return [
                    self._error(
                        "visual_anchor_missing",
                        "visual source_mode requires a supplied image-bound span anchor",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                    )
                ]
            source_span = span
            source_group = span.context_group_id
            visual_anchor_ids = set(packet.alias_map.get("visual", {}).values())
            if (
                assignment.source_ref_id not in visual_anchor_ids
                or not packet.page_image_paths
            ):
                issues.append(
                    self._error(
                        "visual_evidence_unbound",
                        "Visual value is not bound to a V source and image/crop in this "
                        "model call",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                    )
                )
            elif not self._value_shape_compatible(element, value):
                issues.append(
                    self._warning(
                        "visual_value_type_uncertain",
                        "The visual value has an unusual shape for this field; model "
                        "semantics are retained for review instead of being rejected",
                        group_ref_id=group_id,
                        element_id=assignment.element_id,
                        source_ref_id=assignment.source_ref_id,
                    )
                )
        else:
            return [
                self._error(
                    "unknown_source_mode",
                    f"Unknown source mode: {assignment.source_mode}",
                    group_ref_id=group_id,
                    element_id=assignment.element_id,
                    source_ref_id=assignment.source_ref_id,
                )
            ]

        contextual_field = (
            source_span is not None
            and source_span.span_id in packet.context_only_span_ids
            and element.get("semantic_role") in {"scope", "method", "context"}
        )
        if not contextual_field and not self._source_group_matches_fact_group(
            packet, group_id, source_group, source_span
        ):
            issues.append(
                self._error(
                    "cross_group_source",
                    "A filled field uses evidence from another row group",
                    group_ref_id=group_id,
                    element_id=assignment.element_id,
                    source_ref_id=assignment.source_ref_id,
                )
            )
        if source_span is not None and source_span.span_id not in assignment.evidence_span_ids:
            issues.append(
                self._error(
                    "source_evidence_mismatch",
                    "The selected source span is not present in evidence_span_ids",
                    group_ref_id=group_id,
                    element_id=assignment.element_id,
                    source_ref_id=assignment.source_ref_id,
                )
            )
        return issues

    def _row_shape_issues(self, group, status, elements):
        quantitative_fields = {
            element_id
            for element_id, item in elements.items()
            if item.get("binding", {}).get("record_type") == "quantitative_observation"
            and item.get("binding", {}).get("target") == "value_raw"
        }
        if not quantitative_fields:
            return []
        primary = [
            item for item in group.assignments if item.element_id in quantitative_fields
        ]
        if len(primary) == 1:
            return []
        return [
            self._error(
                "primary_value_cardinality",
                "Each quantitative output row must contain exactly one primary value",
                group_ref_id=group.group_ref_id,
                recommended_action="Split independent quantities into separate rows.",
            )
        ]

    def _table_coordinate_issues(self, group, elements, spans, candidates):
        primary = next(
            (
                item
                for item in group.assignments
                if elements.get(item.element_id, {}).get("binding", {}).get("target")
                == "value_raw"
            ),
            None,
        )
        if primary is None or primary.source_mode == "visual":
            return []
        source = candidates.get(primary.source_ref_id) or spans.get(primary.source_ref_id)
        primary_span = spans.get(source.span_id) if hasattr(source, "span_id") else source
        if primary_span is None:
            return []
        structure = primary_span.structural_context or {}
        table_id = structure.get("table_id")
        value_cell_id = structure.get("cell_id")
        if not table_id:
            return []
        issues = []
        for item in group.assignments:
            if item.source_mode == "visual":
                continue
            source = candidates.get(item.source_ref_id) or spans.get(item.source_ref_id)
            source_span = spans.get(source.span_id) if hasattr(source, "span_id") else source
            if source_span is None:
                continue
            other = source_span.structural_context or {}
            if other.get("table_id") not in {None, table_id}:
                issues.append(
                    self._error(
                        "table_coordinate_mismatch",
                        "One output row mixes different tables",
                        group_ref_id=group.group_ref_id,
                        element_id=item.element_id,
                        source_ref_id=item.source_ref_id,
                    )
                )
            anchor = other.get("anchor_cell_id")
            if anchor and value_cell_id and anchor != value_cell_id:
                issues.append(
                    self._error(
                        "table_header_value_mismatch",
                        "A stacked header belongs to a different value cell",
                        group_ref_id=group.group_ref_id,
                        element_id=item.element_id,
                        source_ref_id=item.source_ref_id,
                    )
                )
        return issues

    @staticmethod
    def _candidate_type_compatible(element, candidate_type: str) -> bool:
        value_type = element.get("value_contract", {}).get("primary_type")
        target = element.get("binding", {}).get("target")
        code = str(element.get("element_code") or "")
        if target == "unit_raw" or code.endswith("unit"):
            return candidate_type == "unit"
        if value_type in NUMERIC_TYPES:
            return candidate_type in NUMERIC_CANDIDATES
        if value_type in DATE_TYPES:
            return candidate_type in DATE_CANDIDATES[value_type]
        if value_type == "boolean":
            return candidate_type == "boolean"
        return True

    @staticmethod
    def _value_shape_compatible(element, value: str) -> bool:
        value_type = element.get("value_contract", {}).get("primary_type")
        if value_type in NUMERIC_TYPES:
            return bool(NUMBER_RE.search(value))
        if value_type == "year":
            return bool(YEAR_RE.search(value))
        return True

    @staticmethod
    def _verbatim_in_span(value: str, span) -> bool:
        needle = " ".join(value.split()).casefold()
        if not needle:
            return False
        haystacks = [span.text, span.context_text]
        structure = span.structural_context or {}
        for key in ("column_header_path", "row_header_path"):
            haystacks.extend(str(item) for item in structure.get(key, []))
        return any(
            needle in " ".join(str(item).split()).casefold() for item in haystacks
        )

    @staticmethod
    def _error(code: str, message: str, **kwargs) -> GuardIssue:
        return GuardIssue(code=code, severity="error", message=message, **kwargs)

    @staticmethod
    def _warning(code: str, message: str, **kwargs) -> GuardIssue:
        return GuardIssue(code=code, severity="warning", message=message, **kwargs)

    @staticmethod
    def _feedback(issues: list[GuardIssue]) -> str:
        grouped: Counter[tuple[str, str | None, str | None]] = Counter(
            (item.code, item.element_id, item.recommended_action) for item in issues
        )
        lines = []
        for (code, element_id, action), count in grouped.most_common(24):
            target = f" element={element_id}" if element_id else ""
            recommendation = f" Action: {action}" if action else ""
            lines.append(f"- {code}{target}: {count} occurrence(s).{recommendation}")
        return "\n".join(lines)
