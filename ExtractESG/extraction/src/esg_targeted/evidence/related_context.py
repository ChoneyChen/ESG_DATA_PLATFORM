"""Expose explicitly linked explanatory blocks as independent source evidence.

A standard relation selects the related measurement packets in the caller.
This compiler follows only their selected objects' IR footnote links. It does
not infer a method match, search adjacent pages, or import measurement values.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from esg_targeted.contracts import EvidenceInventory, EvidencePacket, EvidenceSpan


RELATED_CONTEXT_VERSION = "linked-explanatory-block-v1"


def _check_source(packet: EvidencePacket, inventory: EvidenceInventory) -> None:
    expected = (inventory.document_id, inventory.ir_run_id, inventory.ir_revision)
    declared = (packet.source_document_id, packet.source_ir_run_id, packet.source_ir_revision)
    if any(value is not None and value != expected[index] for index, value in enumerate(declared)):
        raise ValueError("linked explanatory evidence must use the same document and fixed IR revision")
    if any((span.document_id, span.ir_run_id, span.ir_revision) != expected for span in packet.spans):
        raise ValueError("linked explanatory packet contains evidence from another fixed IR revision")


def _block_identity(span: EvidenceSpan) -> str | None:
    # Object ID membership alone cannot turn an arbitrary span into a block.
    # The real IR locator supplies both the object type and exact identity.
    identities = {loc.ir_object_id for loc in span.locators if loc.ir_object_type == "block"}
    return next(iter(identities)) if len(identities) == 1 else None


def _append_aliases(aliases: dict[str, str], values: Iterable[str], prefix: str) -> dict[str, str]:
    result = dict(aliases)
    known = set(result.values())
    index = 1
    for value in values:
        if value in known:
            continue
        while f"{prefix}{index}" in result:
            index += 1
        result[f"{prefix}{index}"] = value
        known.add(value)
        index += 1
    return result


def linked_explanatory_packet(
    packet: EvidencePacket,
    related_packets: Iterable[EvidencePacket],
    inventory: EvidenceInventory | None,
) -> EvidencePacket:
    """Attach selected measurements' real footnotes as readable method objects.

    The returned packet preserves existing references and raw source text.
    Footnote blocks (and their sentence spans) become allowed evidence groups,
    rather than background-only context. Their explicit block reading identity
    prevents a downstream reader from widening them to a complete page.
    """
    if inventory is None:
        return packet
    _check_source(packet, inventory)
    inventory_spans = {span.span_id: span for span in inventory.spans}
    links: dict[str, set[str]] = defaultdict(set)
    for related in related_packets:
        _check_source(related, inventory)
        allowed = set(related.allowed_group_ids)
        metric_id = str(related.metric.get("metric_id") or related.query.metric_id)
        for span in related.spans:
            if span.context_group_id not in allowed or span.span_id in related.context_only_span_ids:
                continue
            original = inventory_spans.get(span.span_id)
            if original is None or original.context_group_id != span.context_group_id:
                continue
            # Only the inventory's fixed IR link can activate another object.
            # Packet metadata may be enriched for prompting; it is not authority
            # to invent a new cross-object relationship.
            for block_id in original.structural_context.get("footnote_block_ids", []):
                if isinstance(block_id, str) and block_id:
                    links[block_id].add(metric_id)
    if not links:
        return packet

    # Resolve links through complete real block spans first. A table cell, page
    # aggregate, or orphan sentence sharing an ID is not a substitute.
    blocks = {}
    for span in inventory.spans:
        block_id = _block_identity(span)
        if (span.span_type == "block" and block_id in links and
                (span.document_id, span.ir_run_id, span.ir_revision) ==
                (inventory.document_id, inventory.ir_run_id, inventory.ir_revision)):
            blocks[block_id] = span
    selected = []
    for span in inventory.spans:
        block_id = _block_identity(span)
        block = blocks.get(block_id)
        if (block is None or span.span_type not in {"block", "sentence"} or
                span.context_group_id != block.context_group_id or
                (span.document_id, span.ir_run_id, span.ir_revision) !=
                (inventory.document_id, inventory.ir_run_id, inventory.ir_revision)):
            continue
        selected.append(span.model_copy(update={"structural_context": {
            **span.structural_context,
            "reading_object_type": "block",
            "reading_object_id": block_id,
            "linked_explanatory_metrics": sorted(links[block_id]),
        }}, deep=True))
    selected.sort(key=lambda span: (span.page_index, _block_identity(span) or "",
                                    span.span_type != "block", span.span_id))
    selected_ids = {span.span_id for span in selected}
    selected_groups = list(dict.fromkeys(span.context_group_id for span in selected))
    spans = {span.span_id: span for span in packet.spans}
    spans.update({span.span_id: span for span in selected})
    candidates = {candidate.candidate_id: candidate for candidate in packet.candidates}
    candidates.update({candidate.candidate_id: candidate for candidate in inventory.candidates
                       if candidate.span_id in selected_ids and candidate.context_group_id in selected_groups})
    context_only = [span_id for span_id in packet.context_only_span_ids if span_id not in selected_ids]
    alias_map = {
        **packet.alias_map,
        "spans": _append_aliases(packet.alias_map.get("spans", {}), spans, "S"),
        "groups": _append_aliases(packet.alias_map.get("groups", {}), selected_groups, "G"),
        "candidates": _append_aliases(packet.alias_map.get("candidates", {}), candidates, "C"),
        "context": {alias: span_id for alias, span_id in packet.alias_map.get("context", {}).items()
                    if span_id not in selected_ids},
    }
    return packet.model_copy(update={
        "spans": list(spans.values()),
        "candidates": list(candidates.values()),
        "allowed_group_ids": list(dict.fromkeys([*packet.allowed_group_ids, *selected_groups])),
        "context_only_span_ids": context_only,
        "alias_map": alias_map,
        "budget": {**packet.budget, "linked_explanatory_evidence": {
            "version": RELATED_CONTEXT_VERSION,
            "block_ids": sorted(blocks),
            "span_ids": [span.span_id for span in selected],
            "unresolved_block_ids": sorted(set(links) - set(blocks)),
            "related_metric_ids": sorted({metric for metrics in links.values() for metric in metrics}),
        }},
    }, deep=True)
