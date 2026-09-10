from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from esg_targeted.contracts import EvidencePacket


PRIMARY_LITERAL_TYPES = {"number", "quantity", "percentage"}
DECIMAL_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class TargetCellContract:
    """One canonical table value cell that the current model call may emit."""

    alias: str
    cell_id: str
    group_alias: str
    group_id: str
    span_id: str | None
    value_forms: frozenset[str]
    column_headers: tuple[str, ...] = ()
    row_headers: tuple[str, ...] = ()
    row_label: str | None = None
    unit_context: str | None = None
    header_alignment_uncertain: bool = False


def target_cell_contracts(packet: EvidencePacket) -> list[TargetCellContract]:
    """Resolve compact model aliases into deterministic target-cell contracts.

    A visible cell can contain a combined quantity such as ``125.6吨`` while the
    semantic model is required to return ``125.6`` and ``吨`` in separate fields.
    Candidate normalized values are therefore valid forms of the same cell; values
    from another cell or value column are not.
    """

    try:
        context = json.loads(packet.model_context)
    except (TypeError, json.JSONDecodeError):
        return []
    group_aliases = packet.alias_map.get("groups", {})
    span_aliases = packet.alias_map.get("spans", {})
    candidates_by_span: dict[str, list] = {}
    for candidate in packet.candidates:
        if candidate.candidate_type in PRIMARY_LITERAL_TYPES:
            candidates_by_span.setdefault(candidate.span_id, []).append(candidate)

    contracts = []
    for cell in (context.get("region") or {}).get("target_value_cells") or []:
        alias = str(cell.get("id") or "")
        group_alias = str(cell.get("group") or "")
        group_id = group_aliases.get(group_alias)
        if not group_id:
            continue
        span_id = span_aliases.get(str(cell.get("span") or ""))
        forms = {str(cell.get("visible_value") or "").strip()}
        if span_id:
            for candidate in candidates_by_span.get(span_id, []):
                forms.add(candidate.raw_value.strip())
                if candidate.normalized_value:
                    forms.add(candidate.normalized_value.strip())
        contracts.append(
            TargetCellContract(
                alias=alias,
                cell_id=str(cell.get("cell_id") or span_id or "unknown-cell"),
                group_alias=group_alias,
                group_id=group_id,
                span_id=span_id,
                value_forms=frozenset(value for value in forms if value),
                column_headers=tuple(
                    str(value).strip()
                    for value in cell.get("column_headers") or []
                    if str(value).strip()
                ),
                row_headers=tuple(
                    str(value).strip()
                    for value in cell.get("row_headers") or []
                    if str(value).strip()
                ),
                row_label=(str(cell.get("row_label")).strip() or None)
                if cell.get("row_label") is not None
                else None,
                unit_context=(str(cell.get("unit_context")).strip() or None)
                if cell.get("unit_context") is not None
                else None,
                header_alignment_uncertain=bool(cell.get("header_alignment_uncertain")),
            )
        )
    return contracts


def values_equivalent(value: str, expected_forms: frozenset[str] | set[str]) -> bool:
    actual = _compact(value)
    for expected in expected_forms:
        wanted = _compact(expected)
        if actual == wanted:
            return True
        actual_decimal = _decimal(actual)
        wanted_decimal = _decimal(wanted)
        if actual_decimal is not None and wanted_decimal is not None:
            if actual_decimal == wanted_decimal:
                return True
    return False


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).replace(",", "").replace("，", "")


def _decimal(value: str) -> Decimal | None:
    if not DECIMAL_RE.fullmatch(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None
