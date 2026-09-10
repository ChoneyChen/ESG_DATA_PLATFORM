from __future__ import annotations

import json
import re
from typing import Any

from esg_targeted.contracts import EvidencePacket, SemanticDecision
from esg_targeted.evidence.target_cells import (
    target_cell_contracts,
    values_equivalent,
)


SPECIAL_TOKEN_RE = re.compile(r"^(?:\s*<\|[^|>]+\|>\s*)+$")
TRAILING_SPECIAL_TOKEN_RE = re.compile(r"(?:\s*<\|[^|>]+\|>\s*)+$")
PERIOD_HEADER_RE = re.compile(
    r"^(?:FY\s*)?(?:19|20)\d{2}(?:\s*(?:年|年度|财年|財年|合计|合計|total))?$",
    re.IGNORECASE,
)
GENERIC_VALUE_HEADER_RE = re.compile(
    r"^(?:排放(?:量|总量|總量)?|数量|數量|数值|數值|值|单位|單位|unit|"
    r"amount|value|total|合计值|總計值)$",
    re.IGNORECASE,
)


class ModelResponseError(ValueError):
    pass


class DirectFillResponseAdapter:
    """Bind simple semantic-fill values to deterministic IR provenance.

    The model decides what each visible value means and returns direct scalar
    fields. This adapter performs the mechanical part: within the selected G
    group it binds each value to a literal, an IR span, or—only when an image was
    actually sent—the group's visual anchor.
    """

    def parse(
        self,
        raw_output: str,
        *,
        packet: EvidencePacket | None = None,
        visual_used: bool = False,
    ) -> tuple[SemanticDecision, str]:
        decision, normalized, _ = self.parse_with_diagnostics(
            raw_output,
            packet=packet,
            visual_used=visual_used,
        )
        return decision, normalized

    def parse_with_diagnostics(
        self,
        raw_output: str,
        *,
        packet: EvidencePacket | None = None,
        visual_used: bool = False,
    ) -> tuple[SemanticDecision, str, tuple[str, ...]]:
        if packet is None:
            raise ModelResponseError("direct-fill output requires an evidence region")
        try:
            payload, decode_actions = decode_first_json_object_with_actions(raw_output)
        except ModelResponseError:
            payload = _salvage_complete_direct_fill_rows(raw_output)
            if payload is None:
                raise
            decode_actions = ("salvage_complete_rows_from_truncated_output",)
        actions = list(decode_actions)
        decision_payload = self._expand_rows(
            payload,
            packet,
            visual_used=visual_used,
            actions=actions,
        )
        try:
            decision = SemanticDecision.model_validate(decision_payload)
        except Exception as exc:
            raise ModelResponseError(
                f"model response violates direct-fill decision contract: {exc}"
            ) from exc
        normalized = json.dumps(
            decision.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )
        return decision, normalized, tuple(actions)

    @classmethod
    def _expand_rows(
        cls,
        payload: dict[str, Any],
        packet: EvidencePacket,
        *,
        visual_used: bool,
        actions: list[str],
    ) -> dict[str, Any]:
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ModelResponseError("rows must be an array")

        elements = {
            item.get("element_code"): item
            for item in packet.elements
            if item.get("element_code")
            and item.get("element_id")
            and item.get("value_contract", {}).get("fixed_value") is None
        }
        group_aliases = packet.alias_map.get("groups", {})
        spans = {item.span_id: item for item in packet.spans}
        candidates = {item.candidate_id: item for item in packet.candidates}
        visual_anchor_ids = set(packet.alias_map.get("visual", {}).values())
        target_contract_list = target_cell_contracts(packet)
        target_values_by_group: dict[str, set[str]] = {}
        for contract in target_contract_list:
            target_values_by_group.setdefault(contract.group_id, set()).update(
                contract.value_forms
            )
        primary_fields = {
            field_name
            for field_name, element in elements.items()
            if element.get("binding", {}).get("target") == "value_raw"
        }

        fact_groups = []
        selected_span_ids: list[str] = []
        for row_index, raw_row in enumerate(rows, 1):
            if not isinstance(raw_row, dict):
                raise ModelResponseError(f"row {row_index} must be an object")
            raw_row = dict(raw_row)
            raw_group = raw_row.get("group")
            if (
                isinstance(raw_group, list)
                and len(raw_group) == 1
                and isinstance(raw_group[0], str)
            ):
                # This is lossless contract repair: no alias is selected or
                # inferred. Multi-value arrays remain invalid and visible.
                raw_row["group"] = raw_group[0]
                actions.append("unwrap_single_group_alias")
            fields = raw_row.get("fields")
            if not isinstance(fields, dict):
                raise ModelResponseError(f"row {row_index} requires a fields object")
            fields = dict(fields)
            unknown_fields = sorted(set(fields) - set(elements))
            if unknown_fields:
                raise ModelResponseError(
                    f"row {row_index} contains unknown standard field: "
                    f"{', '.join(unknown_fields)}"
                )
            target_contract = cls._resolve_target_cell(
                raw_row,
                fields,
                target_contract_list,
                primary_fields,
                row_index=row_index,
                allow_unlisted_visual=visual_used,
            )
            group_alias = raw_row.get("group")
            if target_contract is not None:
                if group_alias is None:
                    group_alias = target_contract.group_alias
                    actions.append("derive_group_from_target_cell")
                elif group_alias != target_contract.group_alias:
                    raise ModelResponseError(
                        f"row {row_index} target_cell {target_contract.alias} belongs "
                        f"to {target_contract.group_alias}, not {group_alias}"
                    )
                cls._fill_structural_fields(
                    fields,
                    target_contract,
                    elements,
                    actions,
                )
            if not isinstance(group_alias, str) or group_alias not in group_aliases:
                raise ModelResponseError(
                    f"row {row_index} requires one supplied G group alias; "
                    f"received {group_alias!r}; allowed: {', '.join(group_aliases)}"
                )

            assignments = []
            for field_name, field_value in fields.items():
                if field_name not in elements:
                    raise ModelResponseError(
                        f"row {row_index} contains unknown standard field: {field_name}"
                    )
                if field_value is None:
                    continue
                if isinstance(field_value, (dict, list)):
                    raise ModelResponseError(
                        f"row {row_index} field {field_name} must be null or a direct scalar"
                    )
                value = cls._scalar_text(field_value)
                if not value:
                    raise ModelResponseError(
                        f"row {row_index} field {field_name} requires a visible value"
                    )
                source_mode, source_id, evidence_span_id = cls._bind_source(
                    value=value,
                    group_id=group_aliases[group_alias],
                    element=elements[field_name],
                    spans=spans,
                    candidates=candidates,
                    visual_anchor_ids=visual_anchor_ids,
                    visual_used=visual_used,
                    allowed_primary_values=(
                        (
                            set(target_contract.value_forms)
                            if target_contract is not None
                            else (
                                None
                                if visual_used
                                else target_values_by_group.get(group_aliases[group_alias])
                            )
                        )
                        if elements[field_name].get("binding", {}).get("target")
                        == "value_raw"
                        else None
                    ),
                    target_span_id=(
                        target_contract.span_id
                        if target_contract is not None
                        and elements[field_name].get("binding", {}).get("target")
                        == "value_raw"
                        else None
                    ),
                    prefer_visual=bool(
                        visual_used and target_contract is not None
                        and target_contract.header_alignment_uncertain
                        and field_name not in primary_fields
                    ),
                    context_span_ids=set(packet.context_only_span_ids),
                )
                assignments.append(
                    {
                        "element_id": elements[field_name]["element_id"],
                        "source_ref_id": source_id,
                        "evidence_span_ids": [evidence_span_id],
                        "value_raw": value,
                        "source_mode": source_mode,
                        "confidence": None,
                    }
                )
                selected_span_ids.append(evidence_span_id)

            fact_groups.append(
                {
                    # A table row can legitimately yield several period/entity
                    # facts. Bind the semantic row to the physical target cell so
                    # identical numeric values in sibling columns remain distinct.
                    # The target-cell id is a real immutable IR cell id, not a
                    # generated semantic fact.
                    "group_ref_id": (
                        target_contract.cell_id
                        if target_contract is not None
                        else group_aliases[group_alias]
                    ),
                    "assignments": assignments,
                    "metric_match": (
                        raw_row.get("metric_match")
                        if raw_row.get("metric_match") in {"match", "uncertain", "different"}
                        else "match"
                    ),
                    "interpretation_note": str(raw_row.get("interpretation_note") or "") or None,
                    "context_span_ids": list(dict.fromkeys(
                        packet.alias_map.get("context", {}).get(ref)
                        for ref in (raw_row.get("context_refs") or [])
                        if isinstance(ref, str) and ref in packet.alias_map.get("context", {})
                    )),
                }
            )

        status = payload.get("status")
        if (
            not rows
            and status not in {"found", "partial", "not_found", "ambiguous"}
            and packet.retrieval_complete
        ):
            # Some JSON-capable providers return a semantically lossless empty-row
            # object while omitting status. A complete retrieval packet makes the
            # only valid interpretation deterministic.
            status = "not_found"
        if status not in {"found", "partial", "not_found", "ambiguous"}:
            raise ModelResponseError("status is not a permitted direct-fill status")
        uncertainty = payload.get("uncertainty_code")
        allowed_uncertainty = {
            "none",
            "insufficient_evidence",
            "conflicting_values",
            "scope_ambiguous",
            "period_ambiguous",
            "model_output_invalid",
        }
        if uncertainty not in allowed_uncertainty:
            uncertainty = (
                "none" if status in {"found", "not_found"} else "insufficient_evidence"
            )
        return {
            "task_id": payload.get("task_id") or packet.task_id,
            "status": status,
            "fact_groups": fact_groups,
            "selected_evidence_span_ids": list(dict.fromkeys(selected_span_ids)),
            "uncertainty_code": uncertainty,
            "skipped_targets": {
                str(item["target_cell"]): str(item.get("reason") or "Model judged this cell out of scope")
                for item in (payload.get("skipped_targets") or [])
                if isinstance(item, dict)
                and item.get("target_cell") in packet.alias_map.get("target_cells", {})
            },
            "missing_context": [str(item) for item in (payload.get("missing_context") or []) if isinstance(item, str)],
        }

    @classmethod
    def _resolve_target_cell(
        cls,
        row: dict[str, Any],
        fields: dict[str, Any],
        contracts,
        primary_fields: set[str],
        *,
        row_index: int,
        allow_unlisted_visual: bool,
    ):
        if not contracts:
            return None
        by_alias = {item.alias: item for item in contracts if item.alias}
        supplied = row.get("target_cell")
        if supplied is not None:
            if not isinstance(supplied, str) or supplied not in by_alias:
                raise ModelResponseError(
                    f"row {row_index} contains unknown target_cell: {supplied!r}"
                )
            return by_alias[supplied]

        group_alias = row.get("group")
        primary_values = [
            cls._scalar_text(fields.get(name))
            for name in primary_fields
            if fields.get(name) is not None
        ]
        matches = [
            item
            for item in contracts
            if (group_alias is None or item.group_alias == group_alias)
            and any(values_equivalent(value, item.value_forms) for value in primary_values)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches and allow_unlisted_visual and supplied is None:
            # The complete crop may contain a value OCR/literal harvesting did
            # not recover. Vision may emit it with target_cell=null; provenance
            # still has to bind to a supplied visual group anchor below.
            return None
        reason = "no unique value/group match" if matches else "no matching value cell"
        raise ModelResponseError(
            "model response violates target-cell contract: "
            f"row {row_index} requires a supplied target_cell ({reason})"
        )

    @classmethod
    def _fill_structural_fields(
        cls,
        fields: dict[str, Any],
        contract,
        elements: dict[str, dict[str, Any]],
        actions: list[str],
    ) -> None:
        """Fill only mechanically explicit stacked-header values.

        Relevance and row-subject interpretation remain model-owned. A literal
        year header, leaf business/site header, or explicit row unit is already
        structurally bound to the selected target cell and can be copied without
        another semantic judgement.
        """

        headers = [value for value in contract.column_headers if value]
        period = next((value for value in headers if PERIOD_HEADER_RE.fullmatch(value)), None)
        period_index = headers.index(period) if period in headers else None
        breakdown_candidates = [
            value
            for index, value in enumerate(headers)
            if value != period
            # Only a leaf nested below an explicit period is mechanically a
            # per-period breakdown. A broad header preceding the year describes
            # the measure and must not be relabelled as a business dimension.
            and period_index is not None
            and index > period_index
            and not PERIOD_HEADER_RE.fullmatch(value)
            and not GENERIC_VALUE_HEADER_RE.fullmatch(value)
        ]
        def first_field(
            *,
            roles: set[str],
            targets: set[str] | None = None,
            target_token: str | None = None,
        ):
            targets = targets or set()
            for field_name, element in elements.items():
                role = str(element.get("semantic_role") or "")
                target = str(element.get("binding", {}).get("target") or "")
                if role in roles or target in targets or (target_token and target_token in target):
                    return field_name
            return None

        period_field = first_field(
            roles={"period"},
            targets={"reporting_period_raw", "reporting_year", "period_start", "period_end"},
        )
        breakdown_field = first_field(
            roles={"breakdown"},
            target_token="breakdown",
        )
        # A typed entity field means the model can distinguish entities from
        # carriers, geography and other header labels. Do not silently duplicate
        # its semantic choice into the legacy catch-all breakdown. Packages
        # without typed entities (including the E2-4 baseline) retain their
        # existing structural-header repair behavior.
        has_typed_entity = any(
            element.get("element_code") == "reporting_entity"
            or str(element.get("binding", {}).get("target", "")).endswith(
                ".reporting-entity"
            )
            for element in elements.values()
        )
        if has_typed_entity:
            breakdown_field = None
        if getattr(contract, "header_alignment_uncertain", False):
            # Do not overwrite a model's visual reading with known-uncertain
            # OCR header geometry, nor invent a missing year from that geometry.
            period_field = None
            breakdown_field = None
        unit_field = first_field(roles={"unit"}, targets={"unit_raw", "unit_id"})
        deterministic = {
            period_field: period,
            breakdown_field: breakdown_candidates[-1] if breakdown_candidates else None,
            unit_field: contract.unit_context,
        }
        for field_name, value in deterministic.items():
            if field_name is None or field_name not in elements or value is None:
                continue
            existing = fields.get(field_name)
            if existing is None:
                fields[field_name] = value
                actions.append(f"fill_{field_name}_from_target_cell_structure")
                continue
            if cls._scalar_text(existing).casefold() != str(value).casefold():
                # The selected T cell is an explicit structural coordinate. Its
                # own stacked header/unit wins over a model value copied from a
                # neighbouring column. This changes no source fact: it restores
                # the literal label already attached to that target cell.
                fields[field_name] = value
                actions.append(f"correct_{field_name}_from_target_cell_structure")

    @staticmethod
    def _scalar_text(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (str, int, float)):
            return str(value).strip()
        return ""

    @classmethod
    def _bind_source(
        cls,
        *,
        value: str,
        group_id: str,
        element: dict[str, Any],
        spans: dict[str, Any],
        candidates: dict[str, Any],
        visual_anchor_ids: set[str],
        visual_used: bool,
        allowed_primary_values: set[str] | None,
        target_span_id: str | None,
        prefer_visual: bool = False,
        context_span_ids: set[str] | None = None,
    ) -> tuple[str, str, str]:
        if allowed_primary_values is not None and not values_equivalent(
            value, allowed_primary_values
        ):
            raise ModelResponseError(
                "model response violates target-cell contract: primary value "
                f"{value!r} is outside this region's target values "
                f"{sorted(allowed_primary_values)!r} for group {group_id}"
            )
        if prefer_visual and visual_used:
            anchors = sorted(
                (spans[item] for item in visual_anchor_ids
                 if item in spans and spans[item].context_group_id == group_id),
                key=lambda item: item.span_id,
            )
            if anchors:
                anchor = anchors[0]
                return "visual", anchor.span_id, anchor.span_id
        group_candidates = [
            item
            for item in candidates.values()
            if item.context_group_id == group_id
            and cls._candidate_compatible(element, item.candidate_type)
            and (target_span_id is None or item.span_id == target_span_id)
        ]
        exact_candidates = [
            item
            for item in group_candidates
            if item.raw_value.strip() == value
            or (
                item.normalized_value is not None
                and item.normalized_value.strip() == value
            )
        ]
        if exact_candidates:
            candidate = sorted(
                exact_candidates,
                key=lambda item: (len(item.raw_value), item.candidate_id),
            )[0]
            return "candidate", candidate.candidate_id, candidate.span_id

        group_spans = [
            item
            for item in spans.values()
            if item.context_group_id == group_id
            and (target_span_id is None or item.span_id == target_span_id)
        ]
        compatible_span_ids = {item.span_id for item in group_candidates}
        visible_spans = [
            item
            for item in group_spans
            if cls._visible_in_span(value, item.text, item.context_text)
        ]
        if compatible_span_ids:
            compatible_visible = [
                item for item in visible_spans if item.span_id in compatible_span_ids
            ]
            if compatible_visible:
                visible_spans = compatible_visible
        if visible_spans:
            span = sorted(
                visible_spans,
                key=lambda item: (
                    item.text.strip() != value,
                    item.span_type != "table_cell",
                    len(item.text),
                    item.span_id,
                ),
            )[0]
            return "span", span.span_id, span.span_id

        if element.get("semantic_role") in {"scope", "method", "context"}:
            contextual = [s for sid, s in spans.items() if sid in (context_span_ids or set()) and value in s.text]
            if contextual:
                source = min(contextual, key=lambda s: (len(s.text), s.span_id))
                return "span", source.span_id, source.span_id

        if visual_used:
            anchors = [
                spans[span_id]
                for span_id in visual_anchor_ids
                if span_id in spans and spans[span_id].context_group_id == group_id
            ]
            if anchors:
                anchor = sorted(anchors, key=lambda item: item.span_id)[0]
                return "visual", anchor.span_id, anchor.span_id

        raise ModelResponseError(
            "model response violates direct-fill provenance contract: "
            f"value {value!r} for {element.get('element_code')} is not visible "
            f"in selected group {group_id}"
        )

    @staticmethod
    def _visible_in_span(value: str, text: str, context_text: str) -> bool:
        return value in text or value in context_text

    @staticmethod
    def _candidate_compatible(element: dict[str, Any], candidate_type: str) -> bool:
        value_contract = element.get("value_contract", {})
        primary_type = str(value_contract.get("primary_type") or "")
        binding_target = str(element.get("binding", {}).get("target") or "")
        element_code = str(element.get("element_code") or "")
        if binding_target == "unit_raw" or element_code.endswith("unit"):
            return candidate_type == "unit"
        allowed = {
            "decimal": {"number", "quantity", "percentage"},
            "decimal_range": {"number", "quantity", "percentage"},
            "integer": {"number", "year"},
            "year": {"year"},
            "date": {"date", "year"},
            "date_range": {"date_range", "date", "year"},
            "boolean": {"boolean"},
        }.get(primary_type)
        return allowed is None or candidate_type in allowed


SemanticResponseAdapter = DirectFillResponseAdapter


def decode_first_json_object(raw_output: str) -> dict[str, Any]:
    payload, _ = decode_first_json_object_with_actions(raw_output)
    return payload


def decode_first_json_object_with_actions(
    raw_output: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    cleaned = _strip_fence(raw_output.strip())
    actions: list[str] = []
    without_tokens = TRAILING_SPECIAL_TOKEN_RE.sub("", cleaned).rstrip()
    if without_tokens != cleaned:
        cleaned = without_tokens
        actions.append("strip_trailing_special_tokens")
    object_start = cleaned.find("{")
    array_start = cleaned.find("[")
    starts = [item for item in (object_start, array_start) if item >= 0]
    if not starts:
        raise ModelResponseError("model output does not contain a JSON object")
    start = min(starts)
    decoder = json.JSONDecoder(parse_float=str, parse_int=str)
    try:
        payload, end = decoder.raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        repaired = _close_unbalanced_json_containers(cleaned[start:])
        if repaired is None or repaired == cleaned[start:]:
            raise ModelResponseError(f"invalid model JSON: {exc}") from exc
        try:
            payload, end = decoder.raw_decode(repaired)
        except json.JSONDecodeError:
            raise ModelResponseError(f"invalid model JSON: {exc}") from exc
        cleaned = cleaned[:start] + repaired
        actions.append("close_unbalanced_json_containers")
    consumed_end = start + end
    trailing = cleaned[consumed_end:].strip()
    if trailing and not SPECIAL_TOKEN_RE.fullmatch(trailing):
        raise ModelResponseError(
            f"unexpected content after model JSON: {trailing[:120]}"
        )
    # Some OpenAI-compatible gateways acknowledge a strict top-level object
    # schema but still wrap the one valid object in a singleton JSON array. This
    # is lossless transport normalization, not semantic repair. Multi-item arrays
    # remain invalid because there is no deterministic merge rule.
    if isinstance(payload, list):
        if len(payload) != 1 or not isinstance(payload[0], dict):
            raise ModelResponseError(
                "model response array must contain exactly one JSON object"
            )
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ModelResponseError("model response must be a JSON object")
    return payload, tuple(actions)


def _strip_fence(value: str) -> str:
    if not value.startswith("```"):
        return value
    lines = value.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _close_unbalanced_json_containers(value: str) -> str | None:
    """Close only structurally unmatched JSON containers outside strings.

    NuExtract occasionally emits a complete object field and then closes the
    surrounding array without first closing the row object. Inserting the one
    mechanically required ``}`` is lossless. Quotes, commas, keys and values are
    never invented; any other syntax error still fails normally.
    """

    closing = {"{": "}", "[": "]"}
    opener_for = {"}": "{", "]": "["}
    stack: list[str] = []
    result: list[str] = []
    in_string = False
    escaped = False
    changed = False
    for char in value:
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            result.append(char)
            continue
        if char in closing:
            stack.append(char)
            result.append(char)
            continue
        if char in opener_for:
            wanted = opener_for[char]
            while stack and stack[-1] != wanted:
                result.append(closing[stack.pop()])
                changed = True
            if not stack:
                return None
            stack.pop()
            result.append(char)
            continue
        result.append(char)
    if in_string:
        return None
    while stack:
        result.append(closing[stack.pop()])
        changed = True
    return "".join(result) if changed else value


def _salvage_complete_direct_fill_rows(raw_output: str) -> dict[str, Any] | None:
    """Recover only fully closed row objects from a truncated direct-fill JSON.

    No partial row, key, scalar or quote is repaired. The returned status is
    deliberately ``partial`` so workflow continuation requests the remaining T
    cells. This is safe for output-budget truncation and intentionally does not
    claim that an apparently missing row was absent from the source table.
    """

    cleaned = _strip_fence(raw_output.strip())
    cleaned = TRAILING_SPECIAL_TOKEN_RE.sub("", cleaned).rstrip()
    match = re.search(r'"rows"\s*:\s*\[', cleaned)
    if match is None:
        return None
    cursor = match.end()
    rows: list[dict[str, Any]] = []
    decoder = json.JSONDecoder(parse_float=str, parse_int=str)
    while cursor < len(cleaned):
        while cursor < len(cleaned) and cleaned[cursor] in " \t\r\n,":
            cursor += 1
        if cursor >= len(cleaned) or cleaned[cursor] == "]":
            break
        if cleaned[cursor] != "{":
            break
        end = _balanced_object_end(cleaned, cursor)
        if end is None:
            break
        try:
            row, consumed = decoder.raw_decode(cleaned[cursor:end])
        except json.JSONDecodeError:
            break
        if consumed != end - cursor or not isinstance(row, dict):
            break
        rows.append(row)
        cursor = end
    if not rows:
        return None
    return {
        "task_id": _extract_json_string_field(cleaned, "task_id"),
        "status": "partial",
        "rows": rows,
        "uncertainty_code": "insufficient_evidence",
    }


def _balanced_object_end(value: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(value)):
        char = value[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                return None
    return None


def _extract_json_string_field(value: str, field: str) -> str | None:
    pattern = re.compile(
        rf'"{re.escape(field)}"\s*:\s*("(?:\\.|[^"\\])*")'
    )
    match = pattern.search(value)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, str) else None
