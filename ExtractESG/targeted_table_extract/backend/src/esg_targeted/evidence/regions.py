from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from esg_targeted.contracts import EvidencePacket, EvidenceSpan
from esg_targeted.evidence.units import UNIT_ONLY_RE
from esg_targeted.ids import stable_id


PRIMARY_LITERAL_TYPES = {"number", "quantity", "percentage"}
TABLE_PLACEHOLDER_RE = re.compile(
    r"^(?:[/／\\|｜·•.。,:：;；_\-–—]+|n/?a|不适用|未披露)$",
    re.IGNORECASE,
)


class EvidenceRegionCompiler:
    """Turn complete evidence objects into bounded direct-fill model calls.

    Each table/figure/page stays an independent object. A selected table is one
    semantic call by default: all applicable rows, periods and business columns
    remain visible together and the original table crop is sent once. Only a
    genuinely oversized table is deterministically tiled to satisfy the hard
    row/context contract. This component never creates facts or assigns
    semantic roles.
    """

    def __init__(
        self,
        *,
        max_context_chars: int,
        max_groups_per_region: int,
        max_regions: int,
        max_output_rows: int,
        max_span_chars: int,
        max_images_per_region: int = 1,
    ) -> None:
        self.max_context_chars = max(4_000, max_context_chars)
        self.max_groups_per_region = max(1, max_groups_per_region)
        self.max_regions = max(1, max_regions)
        self.max_output_rows = max(1, max_output_rows)
        self.max_span_chars = max(240, max_span_chars)
        # One region is one visual object. More objects must become more calls.
        self.max_images_per_region = min(1, max(0, max_images_per_region))

    def split(self, packet: EvidencePacket) -> list[EvidencePacket]:
        spans_by_group: dict[str, list[EvidenceSpan]] = defaultdict(list)
        candidates_by_group: dict[str, list] = defaultdict(list)
        for span in packet.spans:
            spans_by_group[span.context_group_id].append(span)
        for candidate in packet.candidates:
            candidates_by_group[candidate.context_group_id].append(candidate)

        regions: dict[tuple[str, str, str], list[str]] = {}
        for group_id in self._ranked_groups(packet):
            key = self._region_key(spans_by_group.get(group_id, []))
            regions.setdefault(key, []).append(group_id)

        compiled: list[EvidencePacket] = []
        for (region_kind, region_id, region_visual_key), raw_group_ids in regions.items():
            group_ids = self._ordered_groups(raw_group_ids, spans_by_group)
            value_slices: list[tuple[int, ...] | None] = [None]
            if region_kind == "table":
                columns = self._table_value_columns(
                    group_ids,
                    spans_by_group,
                    candidates_by_group,
                )
                if columns:
                    value_slices = self._table_column_slices(
                        columns,
                        group_ids,
                        spans_by_group,
                        candidates_by_group,
                    )
            region_chunk_index = 0
            for value_slice_index, target_columns in enumerate(value_slices, 1):
                active_groups = self._groups_for_table_columns(
                    group_ids,
                    spans_by_group,
                    candidates_by_group,
                    target_columns,
                )
                chunks = self._partition_groups(
                    active_groups,
                    candidates_by_group,
                    spans_by_group=spans_by_group,
                    target_columns=target_columns,
                )
                for chunk in chunks:
                    region_chunk_index += 1
                    compiled.extend(
                        self._fit_region(
                            packet,
                            chunk,
                            region_kind=region_kind,
                            region_id=region_id,
                            region_visual_key=region_visual_key,
                            chunk_index=region_chunk_index,
                            value_slice_index=value_slice_index,
                            target_value_columns=target_columns,
                            span_chars=self.max_span_chars,
                        )
                    )

        if not compiled:
            return [self._fallback(packet)]
        if len(compiled) > self.max_regions:
            raise ValueError(
                "retrieved evidence requires "
                f"{len(compiled)} coherent model regions, exceeding safety limit "
                f"{self.max_regions}; narrow retrieval before semantic filling"
            )
        return compiled

    def _table_column_slices(
        self,
        columns: list[int],
        group_ids,
        spans_by_group,
        candidates_by_group,
    ) -> list[tuple[int, ...]]:
        """Keep a table whole unless its literal worklist exceeds a hard cap."""

        spans_by_id = {
            span.span_id: span
            for group_id in group_ids
            for span in spans_by_group.get(group_id, [])
        }
        counts = {column: 0 for column in columns}
        for group_id in group_ids:
            for candidate in candidates_by_group.get(group_id, []):
                if candidate.candidate_type not in PRIMARY_LITERAL_TYPES:
                    continue
                span = spans_by_id.get(candidate.span_id)
                column = self._span_column(span)
                if span is not None and self._is_table_value_span(span) and column in counts:
                    counts[column] += 1
        if sum(counts.values()) <= self.max_output_rows:
            return [tuple(columns)]

        # Oversized fallback: preserve adjacent columns and use as few tiles as
        # possible. A single exceptionally dense column remains one tile; its
        # max_output_rows is raised to the actual deterministic worklist below.
        slices: list[tuple[int, ...]] = []
        current: list[int] = []
        current_count = 0
        for column in columns:
            column_count = max(1, counts[column])
            if current and current_count + column_count > self.max_output_rows:
                slices.append(tuple(current))
                current = []
                current_count = 0
            current.append(column)
            current_count += column_count
        if current:
            slices.append(tuple(current))
        return slices

    def _partition_groups(
        self,
        group_ids,
        candidates_by_group,
        *,
        spans_by_group=None,
        target_columns: tuple[int, ...] | None = None,
    ) -> list[list[str]]:
        spans_by_id = {
            span.span_id: span
            for spans in (spans_by_group or {}).values()
            for span in spans
        }
        chunks: list[list[str]] = []
        current: list[str] = []
        output_rows = 0
        for group_id in group_ids:
            primary_span_ids = {
                item.span_id
                for item in candidates_by_group.get(group_id, [])
                if item.candidate_type in PRIMARY_LITERAL_TYPES
                and (
                    target_columns is None
                    or self._span_column(spans_by_id.get(item.span_id))
                    in target_columns
                )
            }
            group_rows = max(1, len(primary_span_ids))
            if current and (
                len(current) >= self.max_groups_per_region
                or output_rows + group_rows > self.max_output_rows
            ):
                chunks.append(current)
                current = []
                output_rows = 0
            current.append(group_id)
            output_rows += group_rows
        if current:
            chunks.append(current)
        return chunks

    @classmethod
    def _table_value_columns(
        cls,
        group_ids,
        spans_by_group,
        candidates_by_group,
    ) -> list[int]:
        spans_by_id = {
            span.span_id: span
            for group_id in group_ids
            for span in spans_by_group.get(group_id, [])
        }
        return sorted(
            {
                column
                for group_id in group_ids
                for candidate in candidates_by_group.get(group_id, [])
                if candidate.candidate_type in PRIMARY_LITERAL_TYPES
                and (
                    span := spans_by_id.get(candidate.span_id)
                ) is not None
                and cls._is_table_value_span(span)
                and (column := cls._span_column(span)) is not None
            }
        )

    @classmethod
    def _groups_for_table_columns(
        cls,
        group_ids,
        spans_by_group,
        candidates_by_group,
        target_columns,
    ) -> list[str]:
        if target_columns is None:
            return list(group_ids)
        result = []
        for group_id in group_ids:
            spans_by_id = {
                span.span_id: span for span in spans_by_group.get(group_id, [])
            }
            if any(
                candidate.candidate_type in PRIMARY_LITERAL_TYPES
                and (span := spans_by_id.get(candidate.span_id)) is not None
                and cls._is_table_value_span(span)
                and cls._span_column(span) in target_columns
                for candidate in candidates_by_group.get(group_id, [])
            ):
                result.append(group_id)
        return result

    def _fit_region(
        self,
        packet: EvidencePacket,
        group_ids: list[str],
        *,
        region_kind: str,
        region_id: str,
        region_visual_key: str,
        chunk_index: int,
        value_slice_index: int,
        target_value_columns: tuple[int, ...] | None,
        span_chars: int,
    ) -> list[EvidencePacket]:
        built = self._build(
            packet,
            group_ids,
            region_kind=region_kind,
            region_id=region_id,
            region_visual_key=region_visual_key,
            chunk_index=chunk_index,
            value_slice_index=value_slice_index,
            target_value_columns=target_value_columns,
            span_chars=span_chars,
        )
        if len(built.model_context) <= self.max_context_chars:
            return [built]
        if len(group_ids) > 1:
            midpoint = max(1, len(group_ids) // 2)
            return [
                *self._fit_region(
                    packet,
                    group_ids[:midpoint],
                    region_kind=region_kind,
                    region_id=region_id,
                    region_visual_key=region_visual_key,
                    chunk_index=chunk_index * 2 - 1,
                    value_slice_index=value_slice_index,
                    target_value_columns=target_value_columns,
                    span_chars=span_chars,
                ),
                *self._fit_region(
                    packet,
                    group_ids[midpoint:],
                    region_kind=region_kind,
                    region_id=region_id,
                    region_visual_key=region_visual_key,
                    chunk_index=chunk_index * 2,
                    value_slice_index=value_slice_index,
                    target_value_columns=target_value_columns,
                    span_chars=span_chars,
                ),
            ]
        if span_chars > 240:
            return self._fit_region(
                packet,
                group_ids,
                region_kind=region_kind,
                region_id=region_id,
                region_visual_key=region_visual_key,
                chunk_index=chunk_index,
                value_slice_index=value_slice_index,
                target_value_columns=target_value_columns,
                span_chars=max(240, span_chars // 2),
            )
        raise ValueError(
            f"single evidence group cannot fit context budget: "
            f"{len(built.model_context)} > {self.max_context_chars} chars"
        )

    def _build(
        self,
        packet: EvidencePacket,
        group_ids: list[str],
        *,
        region_kind: str,
        region_id: str,
        region_visual_key: str,
        chunk_index: int,
        value_slice_index: int,
        target_value_columns: tuple[int, ...] | None,
        span_chars: int,
    ) -> EvidencePacket:
        selected_groups = set(group_ids)
        spans = [
            item for item in packet.spans if item.context_group_id in selected_groups
        ]
        candidates = [
            item
            for item in packet.candidates
            if item.context_group_id in selected_groups
        ]
        spans, candidates = self._compact_region_sources(
            spans,
            candidates,
            region_kind=region_kind,
        )
        visual_focus = self._visual_focus_geometry(
            spans,
            candidates,
            region_kind=region_kind,
            target_columns=target_value_columns,
        )
        spans, candidates = self._filter_table_value_slice(
            spans,
            candidates,
            region_kind=region_kind,
            target_columns=target_value_columns,
        )
        span_ids = {item.span_id for item in spans}

        span_aliases = self._subset_aliases(
            packet.alias_map.get("spans", {}), span_ids
        )
        candidate_ids = {item.candidate_id for item in candidates}
        candidate_aliases = self._subset_aliases(
            packet.alias_map.get("candidates", {}), candidate_ids
        )
        group_aliases = self._subset_aliases(
            packet.alias_map.get("groups", {}), selected_groups
        )
        reverse_spans = {value: key for key, value in span_aliases.items()}
        reverse_candidates = {value: key for key, value in candidate_aliases.items()}
        reverse_groups = {value: key for key, value in group_aliases.items()}
        images = self._images_for_spans(packet, spans, region_visual_key)
        visual_aliases, visual_sources = self._visual_sources(
            spans,
            images,
            reverse_spans,
            reverse_groups,
        )

        evidence = []
        clipped_count = 0
        for span in spans:
            span_alias = reverse_spans.get(span.span_id)
            group_alias = reverse_groups.get(span.context_group_id)
            if not span_alias or not group_alias:
                continue
            text, clipped = self._clip(span.text, span_chars)
            if region_kind == "table":
                # Canonical table cells already carry coordinates and header paths.
                # Repeating the flattened full row in every cell multiplies input
                # size without adding evidence.
                context, context_clipped = None, False
            else:
                context, context_clipped = self._clip(
                    " ".join(span.context_text.split()), span_chars
                )
            clipped_count += int(clipped or context_clipped)
            evidence.append(
                {
                    "id": span_alias,
                    "group": group_alias,
                    "page": span.page_index,
                    "type": span.span_type,
                    "text": text,
                    "context": context if context and context != text else None,
                    "structure": self._compact_structure(span.structural_context),
                    "visual": bool(span.visual_evidence_paths),
                    "clipped": bool(clipped or context_clipped),
                }
            )

        literals = []
        for item in candidates:
            alias = reverse_candidates.get(item.candidate_id)
            span_alias = reverse_spans.get(item.span_id)
            group_alias = reverse_groups.get(item.context_group_id)
            if not alias or not span_alias or not group_alias:
                continue
            literal: dict[str, Any] = {
                "id": alias,
                "span": span_alias,
                "group": group_alias,
                "type": item.candidate_type,
                "raw": item.raw_value,
            }
            if item.normalized_value not in {None, item.raw_value}:
                literal["normalized"] = item.normalized_value
            if item.unit_raw:
                literal["unit"] = item.unit_raw
            literals.append(literal)

        parent_contract = json.loads(packet.model_context)
        context_spans = [s for s in packet.spans if s.span_id in packet.context_only_span_ids]
        context_aliases = packet.alias_map.get("context", {})
        context_by_id = {s.span_id: s for s in context_spans}
        source_groups = [
            self._source_group(
                group_id,
                [item for item in spans if item.context_group_id == group_id],
                reverse_groups,
            )
            for group_id in group_ids
        ]
        target_value_cells = self._target_value_cells(
            spans,
            candidates,
            target_value_columns,
            reverse_spans,
            reverse_groups,
        )
        target_cell_aliases = {
            item["id"]: str(item.get("cell_id") or item.get("span"))
            for item in target_value_cells
        }
        primary_candidates = sum(
            item.candidate_type in PRIMARY_LITERAL_TYPES for item in candidates
        )
        if target_value_cells:
            max_rows = max(self.max_output_rows, len(target_value_cells))
        elif images and region_kind in {"table", "figure"}:
            # A crop may contain values which OCR/literal harvesting missed. Do not
            # derive the visual output ceiling from local candidate count.
            max_rows = self.max_output_rows
        else:
            max_rows = min(
                self.max_output_rows,
                max(1, primary_candidates, len(group_ids) * 4),
            )
        context = {
            "task_id": packet.task_id,
            "metric": parent_contract.get("metric", packet.metric),
            "elements": self._model_elements(parent_contract.get("elements", [])),
            "region": {
                "id": region_id,
                "kind": region_kind,
                "object_kind": region_kind,
                "object_id": region_id,
                "visual_key": region_visual_key or None,
                "chunk": chunk_index,
                "value_slice": value_slice_index,
                "target_value_columns": list(target_value_columns or []),
                "target_value_cells": target_value_cells,
                "visual_focus": visual_focus,
                "visual_policy": (
                    "full_object" if region_kind == "table" else "bounded_object"
                ),
                "source_groups": source_groups,
                "image_count": len(images),
                "scope_rule": (
                    "Inspect the complete table once. Emit one row for each applicable "
                    "listed target_value_cell and identify it by its T id. Listed cells "
                    "that conflict with the metric scope may be skipped."
                    if target_value_cells
                    else
                    "Only emit facts whose primary value belongs to a listed G group. "
                    "The crop may show additional rows outside this chunk."
                ),
            },
            "evidence": evidence,
            "candidates": literals,
            "visual_sources": visual_sources,
            "linked_context": [
                {"id": alias, "page": context_by_id[sid].page_index,
                 "text": context_by_id[sid].text,
                 "use": "Context candidate: judge its applicable entity, period and measure; not a primary-value worklist."}
                for alias, sid in context_aliases.items() if sid in context_by_id
            ],
            "decision_scope": {
                "mode": "direct_multi_row_semantic_fill",
                "one_quantity_per_row": True,
                "max_output_rows": max_rows,
            },
        }
        model_context = json.dumps(
            context, ensure_ascii=False, separators=(",", ":")
        )
        packet_id = stable_id(
            "evidence-region",
            packet.packet_id,
            region_kind,
            region_id,
            region_visual_key,
            chunk_index,
            target_value_columns,
            group_ids,
        )
        return packet.model_copy(
            update={
                "packet_id": packet_id,
                "spans": [*spans, *context_spans],
                "candidates": candidates,
                "retrieval_hits": [
                    item for item in packet.retrieval_hits if item.span_id in span_ids
                ],
                "allowed_group_ids": group_ids,
                "context_only_span_ids": [s.span_id for s in context_spans],
                "page_image_paths": images,
                "model_context": model_context,
                "alias_map": {
                    "spans": span_aliases,
                    "candidates": candidate_aliases,
                    "elements": packet.alias_map.get("elements", {}),
                    "groups": group_aliases,
                    "visual": visual_aliases,
                    "target_cells": target_cell_aliases,
                    "context": context_aliases,
                },
                "budget": {
                    **packet.budget,
                    "stage": "direct_semantic_fill_region",
                    "semantic_mode": "direct_multi_row",
                    "parent_packet_id": packet.packet_id,
                    "region": context["region"],
                    "max_output_rows": max_rows,
                    "actual_context_chars": len(model_context),
                    "region_group_count": len(group_ids),
                    "region_span_count": len(spans),
                    "region_candidate_count": len(candidates),
                    "region_visual_source_count": len(visual_sources),
                    "region_image_count": len(images),
                    "target_value_columns": list(target_value_columns or []),
                    "target_value_cell_count": len(target_value_cells),
                    "clipped_span_count": clipped_count,
                },
            },
            deep=True,
        )

    def _fallback(self, packet: EvidencePacket) -> EvidencePacket:
        return packet.model_copy(
            update={
                "page_image_paths": packet.page_image_paths[: self.max_images_per_region],
                "budget": {
                    **packet.budget,
                    "stage": "direct_semantic_fill_region",
                    "semantic_mode": "direct_multi_row",
                    "max_output_rows": self.max_output_rows,
                    "region_image_count": min(
                        len(packet.page_image_paths), self.max_images_per_region
                    ),
                }
            },
            deep=True,
        )

    @staticmethod
    def _compact_region_sources(spans, candidates, *, region_kind):
        """Remove duplicate table representations before prompt compilation.

        Document IR intentionally keeps row text, canonical cells and repeated
        header-context spans for auditability. A model region needs one canonical
        representation only. For a structured table we keep real cells; if a row
        has no real cells we retain its row span as a lossless fallback.
        """

        if region_kind != "table":
            return spans, candidates
        spans_by_group = defaultdict(list)
        for span in spans:
            spans_by_group[span.context_group_id].append(span)
        retained = []
        for group_spans in spans_by_group.values():
            cells = [
                item
                for item in group_spans
                if item.span_type == "table_cell"
                and (item.structural_context or {}).get("object_type") == "table_cell"
            ]
            if cells:
                retained.extend(cells)
                continue
            rows = [item for item in group_spans if item.span_type == "table_row"]
            retained.extend(rows or group_spans[:1])
        retained_ids = {item.span_id for item in retained}
        compact_candidates = []
        seen_candidates = set()
        for candidate in candidates:
            if candidate.span_id not in retained_ids:
                continue
            fingerprint = (
                candidate.context_group_id,
                candidate.span_id,
                candidate.candidate_type,
                candidate.raw_value.strip(),
                candidate.unit_raw or "",
            )
            if fingerprint in seen_candidates:
                continue
            seen_candidates.add(fingerprint)
            compact_candidates.append(candidate)
        return retained, compact_candidates

    @classmethod
    def _filter_table_value_slice(
        cls,
        spans,
        candidates,
        *,
        region_kind,
        target_columns,
    ):
        if region_kind != "table" or target_columns is None:
            return spans, candidates
        spans_by_id = {item.span_id: item for item in spans}
        all_value_span_ids = {
            item.span_id
            for item in candidates
            if item.candidate_type in PRIMARY_LITERAL_TYPES
            and (span := spans_by_id.get(item.span_id)) is not None
            and cls._is_table_value_span(span)
        }
        target_span_ids = {
            span_id
            for span_id in all_value_span_ids
            if cls._span_column(spans_by_id.get(span_id)) in target_columns
        }
        retained = [
            item
            for item in spans
            if (
                item.span_id not in all_value_span_ids
                or item.span_id in target_span_ids
            )
            and not cls._irrelevant_table_placeholder(item, target_columns)
        ]
        retained_ids = {item.span_id for item in retained}
        return retained, [
            item for item in candidates if item.span_id in retained_ids
        ]

    @classmethod
    def _irrelevant_table_placeholder(cls, span, target_columns) -> bool:
        if not cls._is_table_value_span(span):
            return False
        column = cls._span_column(span)
        if column in target_columns:
            return False
        return bool(TABLE_PLACEHOLDER_RE.fullmatch(span.text.strip()))

    @classmethod
    def _target_value_cells(
        cls,
        spans,
        candidates,
        target_columns,
        reverse_spans,
        reverse_groups,
    ) -> list[dict[str, Any]]:
        if target_columns is None:
            return []
        spans_by_id = {item.span_id: item for item in spans}
        spans_by_group: dict[str, list] = defaultdict(list)
        for item in spans:
            spans_by_group[item.context_group_id].append(item)
        candidate_span_ids = {
            item.span_id
            for item in candidates
            if item.candidate_type in PRIMARY_LITERAL_TYPES
        }
        records = []
        for span_id in sorted(candidate_span_ids):
            span = spans_by_id.get(span_id)
            if (
                span is None
                or not cls._is_table_value_span(span)
                or cls._span_column(span) not in target_columns
            ):
                continue
            structure = span.structural_context or {}
            row_context = cls._target_row_context(
                span,
                spans_by_group.get(span.context_group_id, []),
            )
            records.append(
                {
                    "span": reverse_spans.get(span.span_id, span.span_id),
                    "group": reverse_groups.get(
                        span.context_group_id,
                        span.context_group_id,
                    ),
                    "cell_id": structure.get("cell_id"),
                    "col_index": structure.get("col_index"),
                    "column_headers": structure.get("column_header_path") or [],
                    **({"header_alignment_uncertain": True} if structure.get("header_alignment_uncertain") else {}),
                    "row_headers": structure.get("row_header_path") or [],
                    "row_label": cls._target_row_label(row_context),
                    "unit_context": cls._target_unit_context(row_context),
                    "row_context": row_context,
                    "visible_value": span.text,
                    "row_index": structure.get("row_index"),
                }
            )
        records.sort(
            key=lambda item: (
                item.get("row_index")
                if isinstance(item.get("row_index"), int)
                else 10_000,
                item.get("col_index")
                if isinstance(item.get("col_index"), int)
                else 10_000,
                str(item.get("cell_id") or ""),
            )
        )
        for index, record in enumerate(records, 1):
            record["id"] = f"T{index}"
        return records

    @classmethod
    def _target_row_context(cls, target_span, group_spans) -> list[dict[str, Any]]:
        target_column = cls._span_column(target_span)
        seen: set[tuple[int | None, str]] = set()
        context = []
        for span in sorted(
            group_spans,
            key=lambda item: (
                cls._span_column(item)
                if cls._span_column(item) is not None
                else 10_000,
                item.span_id,
            ),
        ):
            structure = span.structural_context or {}
            if (
                span.span_id == target_span.span_id
                or span.span_type != "table_cell"
                or structure.get("object_type") != "table_cell"
                or cls._span_column(span) == target_column
            ):
                continue
            text = " ".join(span.text.split())
            if not text or TABLE_PLACEHOLDER_RE.fullmatch(text):
                continue
            column = cls._span_column(span)
            fingerprint = (column, text)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            headers = structure.get("column_header_path") or []
            if cls._context_role(text, headers) == "neighbor_value":
                # Neighbor numbers already have their own T records. Repeating
                # all of them under every cell both wastes tokens and disguises
                # numeric values as row subjects.
                continue
            context.append(
                {
                    "col_index": column,
                    "column_headers": headers,
                    "text": text,
                    "role": cls._context_role(text, headers),
                }
            )
        return context

    @staticmethod
    def _context_role(text: str, headers: list[str]) -> str:
        header_text = " ".join(str(item) for item in headers).casefold()
        compact = re.sub(r"\s+", "", text)
        if re.fullmatch(r"[+\-]?\d[\d,，]*(?:\.\d+)?(?:%|％)?", compact):
            return "neighbor_value"
        if UNIT_ONLY_RE.fullmatch(text.strip()) or any(
            marker in header_text for marker in ("单位", "單位", "unit")
        ):
            return "unit"
        if any(
            marker in header_text
            for marker in ("code", "代码", "代碼", "编码", "編碼")
        ) or re.fullmatch(r"[A-Z]{1,8}(?:-[A-Z0-9.]+)+", compact, re.IGNORECASE):
            return "code"
        return "row_label"

    @staticmethod
    def _target_row_label(context: list[dict[str, Any]]) -> str | None:
        return next(
            (
                str(item["text"])
                for item in context
                if item.get("role") == "row_label"
            ),
            None,
        )

    @staticmethod
    def _target_unit_context(context: list[dict[str, Any]]) -> str | None:
        return next(
            (
                str(item["text"])
                for item in context
                if item.get("role") == "unit"
            ),
            None,
        )

    @classmethod
    def _visual_focus_geometry(
        cls,
        spans,
        candidates,
        *,
        region_kind,
        target_columns,
    ) -> dict[str, Any] | None:
        if region_kind != "table" or target_columns is None:
            return None
        cell_spans = [
            item
            for item in spans
            if item.span_type == "table_cell"
            and (item.structural_context or {}).get("object_type") == "table_cell"
            and cls._span_x_range(item) is not None
        ]
        if not cell_spans:
            return None
        primary_span_ids = {
            item.span_id
            for item in candidates
            if item.candidate_type in PRIMARY_LITERAL_TYPES
        }
        target_spans = [
            item
            for item in cell_spans
            if item.span_id in primary_span_ids
            and cls._is_table_value_span(item)
            and cls._span_column(item) in target_columns
        ]
        if not target_spans:
            return None
        minimum_col = min(
            (cls._span_column(item) for item in cell_spans),
            default=None,
        )
        context_spans = [
            item
            for item in cell_spans
            if cls._span_column(item) == minimum_col
        ]
        if not context_spans:
            return None
        table_range = cls._table_x_range(cell_spans) or cls._x_range(cell_spans)
        context_range = cls._x_range(context_spans)
        target_range = cls._x_range(target_spans)
        if not table_range or not context_range or not target_range:
            return None
        return {
            "mode": "context_plus_target_column",
            "coordinate_unit": "points",
            "table_x_range": list(table_range),
            "context_x_range": list(context_range),
            "target_x_range": list(target_range),
            "target_columns": list(target_columns),
            "target_cell_ids": [
                str((item.structural_context or {}).get("cell_id"))
                for item in target_spans
            ],
        }

    @staticmethod
    def _table_x_range(spans) -> tuple[float, float] | None:
        for span in spans:
            bbox = (span.structural_context or {}).get("table_bbox")
            if not isinstance(bbox, dict):
                continue
            try:
                x0 = float(bbox["x0"])
                x1 = float(bbox["x1"])
            except (KeyError, TypeError, ValueError):
                continue
            if x1 > x0:
                return x0, x1
        return None

    @staticmethod
    def _span_bbox(span) -> dict[str, Any] | None:
        for locator in span.locators:
            bbox = locator.bbox
            if bbox and all(bbox.get(key) is not None for key in ("x0", "x1")):
                return bbox
        return None

    @classmethod
    def _x_range(cls, spans) -> tuple[float, float] | None:
        ranges = [cls._span_x_range(item) for item in spans]
        ranges = [item for item in ranges if item]
        if not ranges:
            return None
        return (
            min(item[0] for item in ranges),
            max(item[1] for item in ranges),
        )

    @classmethod
    def _span_x_range(cls, span) -> tuple[float, float] | None:
        bbox = cls._span_bbox(span)
        if bbox:
            return float(bbox["x0"]), float(bbox["x1"])
        column_bbox = (span.structural_context or {}).get("column_bbox")
        if not isinstance(column_bbox, dict):
            return None
        try:
            x0 = float(column_bbox["x0"])
            x1 = float(column_bbox["x1"])
        except (KeyError, TypeError, ValueError):
            return None
        return (x0, x1) if x1 > x0 else None

    @staticmethod
    def _span_column(span) -> int | None:
        if span is None:
            return None
        value = (span.structural_context or {}).get("col_index")
        return value if isinstance(value, int) else None

    @staticmethod
    def _is_table_value_span(span) -> bool:
        structure = span.structural_context or {}
        return bool(
            span.span_type == "table_cell"
            and structure.get("object_type") == "table_cell"
            and isinstance(structure.get("col_index"), int)
            and structure.get("row_header_path")
        )

    def _images_for_spans(
        self,
        packet: EvidencePacket,
        spans: list[EvidenceSpan],
        region_visual_key: str,
    ) -> list[str]:
        available = set(packet.page_image_paths)
        result: list[str] = []
        for span in spans:
            for path in span.visual_evidence_paths:
                if path in available and path not in result:
                    result.append(path)
        if not result:
            page_images = packet.budget.get("selected_page_images", {})
            for page_index in sorted({item.page_index for item in spans}):
                path = page_images.get(str(page_index))
                if path in available and path not in result:
                    result.append(path)
        if not result and region_visual_key in available:
            result.append(region_visual_key)
        return result[: self.max_images_per_region]

    @staticmethod
    def _visual_sources(spans, images, reverse_spans, reverse_groups):
        aliases: dict[str, str] = {}
        records = []
        for span in spans:
            span_alias = reverse_spans.get(span.span_id)
            group_alias = reverse_groups.get(span.context_group_id)
            if not span_alias or not group_alias:
                continue
            related = [
                index
                for index, path in enumerate(images, 1)
                if path in span.visual_evidence_paths
            ]
            if not related and images:
                related = [1]
            for image_index in related[:1]:
                if any(
                    item["group"] == group_alias
                    and item["image_index"] == image_index
                    for item in records
                ):
                    continue
                alias = f"V{len(records) + 1}"
                aliases[alias] = span.span_id
                records.append(
                    {
                        "id": alias,
                        "group": group_alias,
                        "image_index": image_index,
                        "anchor": span_alias,
                    }
                )
        return aliases, records

    @staticmethod
    def _source_group(group_id, spans, reverse_groups):
        return {
            "id": reverse_groups.get(group_id, group_id),
            "kind": EvidenceRegionCompiler._group_kind(spans),
            "pages": sorted({item.page_index for item in spans}),
            "object_ids": sorted(
                {
                    str(object_id)
                    for item in spans
                    for object_id in item.object_ids
                    if str(object_id).startswith(("table-", "figure-", "cell-"))
                }
            )[:64],
        }

    @staticmethod
    def _compact_structure(value: dict[str, Any]) -> dict[str, Any] | None:
        if not value:
            return None
        allowed = {
            "object_type",
            "table_id",
            "figure_id",
            "caption",
            "cell_id",
            "row_index",
            "col_index",
            "row_span",
            "col_span",
            "column_header_path",
            "row_header_path",
            "relation",
            "anchor_cell_id",
            "chart_spec",
        }
        return {key: value[key] for key in allowed if key in value}

    @staticmethod
    def _model_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep semantic fill instructions, omit storage-only schema metadata."""

        allowed = {
            "code",
            "label",
            "description",
            "semantic_role",
            "type",
            "required",
            "nullable",
            "code_set_id",
            "allowed_values",
            "open_vocabulary",
            "cardinality",
        }
        return [
            {key: item[key] for key in allowed if key in item}
            for item in elements
        ]

    @staticmethod
    def _subset_aliases(aliases: dict[str, str], targets: set[str]):
        return {key: value for key, value in aliases.items() if value in targets}

    @staticmethod
    def _clip(value: str, limit: int) -> tuple[str, bool]:
        if len(value) <= limit:
            return value, False
        return value[:limit] + "…", True

    @staticmethod
    def _region_key(spans: list[EvidenceSpan]) -> tuple[str, str, str]:
        visual_key = next(
            (
                path
                for span in spans
                for path in span.visual_evidence_paths
            ),
            "",
        )
        for span in spans:
            table_id = span.structural_context.get("table_id")
            if table_id:
                return "table", str(table_id), visual_key
            for object_id in span.object_ids:
                if str(object_id).startswith("table-"):
                    return "table", str(object_id), visual_key
        for span in spans:
            figure_id = span.structural_context.get("figure_id")
            if figure_id:
                return "figure", str(figure_id), visual_key
            for object_id in span.object_ids:
                if str(object_id).startswith("figure-"):
                    return "figure", str(object_id), visual_key
        page = min((span.page_index for span in spans), default=0)
        return "page", f"page-{page:04d}", visual_key

    @staticmethod
    def _group_kind(spans: list[EvidenceSpan]) -> str:
        if any(item.has_table_structure for item in spans):
            return "table_row"
        if any(item.span_type == "figure_text" for item in spans):
            return "figure"
        return "text"

    @staticmethod
    def _ordered_groups(group_ids, spans_by_group):
        def key(group_id):
            spans = spans_by_group.get(group_id, [])
            row_indexes = [
                item.structural_context.get("row_index")
                for item in spans
                if item.structural_context.get("row_index") is not None
            ]
            return (
                min((item.page_index for item in spans), default=0),
                min(row_indexes, default=10_000),
                group_id,
            )

        return sorted(group_ids, key=key)

    @staticmethod
    def _ranked_groups(packet: EvidencePacket) -> list[str]:
        spans = {item.span_id: item for item in packet.spans}
        scores: dict[str, float] = defaultdict(float)
        first: dict[str, int] = {}
        for index, hit in enumerate(packet.retrieval_hits):
            span = spans.get(hit.span_id)
            if span is None:
                continue
            scores[span.context_group_id] = max(
                scores[span.context_group_id], hit.final_score
            )
            first.setdefault(span.context_group_id, index)
        return sorted(
            packet.allowed_group_ids,
            key=lambda group_id: (
                -scores.get(group_id, 0.0),
                first.get(group_id, 10_000),
                group_id,
            ),
        )
