from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from esg_targeted.contracts import EvidenceInventory, EvidenceSpan, SourceLocator
from esg_targeted.evidence.harvester import LiteralHarvester
from esg_targeted.evidence.quality import is_navigation_span, is_retrieval_eligible
from esg_targeted.evidence.units import UNIT_PATTERN
from esg_targeted.ids import stable_id
from esg_targeted.ir.loader import LoadedDocumentIr, iter_page_records


HEADER_LITERAL_RE = re.compile(
    rf"(?<!\d)(?:19|20)\d{{2}}(?!\d)|{UNIT_PATTERN}",
    re.IGNORECASE,
)
NUMERIC_CELL_RE = re.compile(
    r"^[（(]?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|％)?[）)]?"
    rf"(?:\s*(?:{UNIT_PATTERN}))?$",
    re.IGNORECASE,
)
YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}$")
UNIT_ONLY_RE = re.compile(
    rf"^(?:{UNIT_PATTERN})$",
    re.IGNORECASE,
)
LABEL_PLACEHOLDER_RE = re.compile(r"^[\s/／\\|｜·•.。,:：;；_\-–—]+$")
AGGREGATE_LABEL_RE = re.compile(
    r"^(?:合计|總計|总计|汇总|匯總|小计|小計|总量|總量|总额|總額|"
    r"total|subtotal|aggregate|aggregated)$",
    re.IGNORECASE,
)


class EvidenceInventoryBuilder:
    def __init__(self, harvester: LiteralHarvester | None = None) -> None:
        self.harvester = harvester or LiteralHarvester()

    def build(self, ir: LoadedDocumentIr) -> EvidenceInventory:
        spans: list[EvidenceSpan] = []
        page_images: dict[int, str] = {}
        visual_artifacts: dict[str, str] = {}
        visual_artifact_pages: dict[str, int] = {}
        page_lookup: dict[int, dict[str, Any]] = {}
        page_text_fallbacks: list[tuple[dict[str, Any], str]] = []

        for artifact in ir.artifacts:
            artifact_id = str(artifact.get("artifact_id", ""))
            relative = artifact.get("path")
            if artifact.get("kind") != "region_crop" or not relative:
                continue
            resolved = (ir.root / relative).resolve()
            if ir.root not in resolved.parents or not resolved.is_file():
                continue
            object_id = artifact_id.removeprefix("artifact-crop-")
            if object_id and object_id != artifact_id:
                visual_artifacts[object_id] = str(resolved)
                if artifact.get("page_index") is not None:
                    visual_artifact_pages[object_id] = int(artifact["page_index"])

        for page, blocks in iter_page_records(ir):
            page_index = int(page["page_index"])
            page_lookup[page_index] = page
            image_path = page.get("page_image_path")
            if image_path:
                resolved = (ir.root / image_path).resolve()
                if ir.root in resolved.parents and resolved.is_file():
                    page_images[page_index] = str(resolved)
            page_visual_paths = (
                [page_images[page_index]] if page_index in page_images else []
            )

            for block in blocks:
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                section_id = block.get("section_id")
                section = ir.sections_by_id.get(section_id or "", {})
                block_id = block["block_id"]
                spans.append(
                    EvidenceSpan(
                        span_id=stable_id(
                            "span", ir.document_id, ir.ir_revision, "block", block_id, text
                        ),
                        document_id=ir.document_id,
                        ir_run_id=ir.run_id,
                        ir_revision=ir.ir_revision,
                        span_type="block",
                        text=text,
                        context_text=self._context_text(section.get("title"), text),
                        section_id=section_id,
                        section_title=section.get("title"),
                        page_index=page_index,
                        printed_page_label=page.get("printed_page_label"),
                        object_ids=[block_id],
                        locators=[
                            self._locator(
                                "block",
                                block_id,
                                page_index,
                                page.get("printed_page_label"),
                                block.get("bbox"),
                            )
                        ],
                        context_group_id=f"block:{block_id}",
                        quality_flags=sorted(set(block.get("quality_flags", []))),
                        visual_evidence_paths=page_visual_paths,
                        has_table_structure=bool(block.get("table_id")),
                    )
                )
                spans.extend(
                    self._sentence_spans(
                        ir=ir,
                        block=block,
                        text=text,
                        page=page,
                        section=section,
                        visual_evidence_paths=page_visual_paths,
                    )
                )

            page_text = (page.get("text") or "").strip()
            if page_text:
                page_text_fallbacks.append((page, page_text))

        spans.extend(self._table_row_spans(ir, page_lookup, visual_artifacts))
        spans.extend(self._figure_spans(ir, page_lookup, visual_artifacts))
        structured_pages = {
            span.page_index for span in spans if span.span_type != "figure_text"
        }
        for page, page_text in page_text_fallbacks:
            page_index = int(page["page_index"])
            if page_index in structured_pages:
                continue
            page_id = page["page_id"]
            spans.append(
                EvidenceSpan(
                    span_id=stable_id(
                        "span", ir.document_id, ir.ir_revision, "page", page_id, page_text
                    ),
                    document_id=ir.document_id,
                    ir_run_id=ir.run_id,
                    ir_revision=ir.ir_revision,
                    span_type="page_text",
                    text=page_text,
                    context_text=page_text,
                    page_index=page_index,
                    printed_page_label=page.get("printed_page_label"),
                    object_ids=[page_id],
                    locators=[
                        self._locator(
                            "page",
                            page_id,
                            page_index,
                            page.get("printed_page_label"),
                            None,
                        )
                    ],
                    context_group_id=f"page:{page_id}",
                    quality_flags=["aggregate_page_text", "fallback_page_text"],
                    visual_evidence_paths=(
                        [page_images[page_index]] if page_index in page_images else []
                    ),
                )
            )
        spans.sort(key=lambda item: (item.page_index, self._span_order(item), item.span_id))
        candidates, harvest_report = self.harvester.harvest_with_report(spans)
        inventory_id = stable_id(
            "inventory",
            ir.document_id,
            ir.ir_revision,
            [(span.span_id, len(span.text)) for span in spans],
        )
        stats = defaultdict(int)
        for span in spans:
            stats[f"span_{span.span_type}"] += 1
            if is_retrieval_eligible(span):
                stats["retrieval_eligible_span_count"] += 1
            else:
                stats["retrieval_quarantined_span_count"] += 1
            if is_navigation_span(span):
                stats["navigation_span_count"] += 1
        for candidate in candidates:
            stats[f"candidate_{candidate.candidate_type}"] += 1
        stats["span_total"] = len(spans)
        stats["candidate_total"] = len(candidates)
        stats.update(harvest_report.stats())
        return EvidenceInventory(
            inventory_id=inventory_id,
            document_id=ir.document_id,
            ir_run_id=ir.run_id,
            ir_revision=ir.ir_revision,
            spans=spans,
            candidates=candidates,
            page_images=page_images,
            visual_artifacts=visual_artifacts,
            visual_artifact_pages=visual_artifact_pages,
            stats=dict(sorted(stats.items())),
        )

    def _figure_spans(
        self,
        ir: LoadedDocumentIr,
        page_lookup: dict[int, dict[str, Any]],
        visual_artifacts: dict[str, str],
    ) -> list[EvidenceSpan]:
        results = []
        for figure in ir.figures:
            figure_id = figure["figure_id"]
            page_index = int(figure.get("page_index", 0))
            page = page_lookup.get(page_index, {})
            parts = []
            caption = str(figure.get("caption") or "").strip()
            if caption:
                parts.append(f"caption: {caption}")
            legend = [str(item).strip() for item in figure.get("legend_text", []) if str(item).strip()]
            if legend:
                parts.append("legend: " + " | ".join(legend))
            chart_spec = figure.get("chart_spec")
            if chart_spec:
                parts.append(
                    "chart_spec: "
                    + json.dumps(chart_spec, ensure_ascii=False, separators=(",", ":"))
                )
            text = "\n".join(parts)
            if not text:
                continue
            flags = set(figure.get("quality_flags", []))
            if figure.get("visual_status") != "verified":
                flags.add("needs_visual_review")
            results.append(
                EvidenceSpan(
                    span_id=stable_id(
                        "span", ir.document_id, ir.ir_revision, "figure", figure_id, text
                    ),
                    document_id=ir.document_id,
                    ir_run_id=ir.run_id,
                    ir_revision=ir.ir_revision,
                    span_type="figure_text",
                    text=text,
                    context_text=text,
                    page_index=page_index,
                    printed_page_label=page.get("printed_page_label"),
                    object_ids=[figure_id],
                    locators=[
                        self._locator(
                            "figure",
                            figure_id,
                            page_index,
                            page.get("printed_page_label"),
                            figure.get("bbox"),
                        )
                    ],
                    context_group_id=f"figure:{figure_id}",
                    quality_flags=sorted(flags),
                    visual_evidence_paths=(
                        [visual_artifacts[figure_id]] if figure_id in visual_artifacts else []
                    ),
                    structural_context={
                        "object_type": "figure",
                        "figure_id": figure_id,
                        "caption": caption or None,
                        "legend": legend,
                        "chart_spec": chart_spec,
                    },
                )
            )
        return results

    def _table_row_spans(
        self,
        ir: LoadedDocumentIr,
        page_lookup: dict[int, dict[str, Any]],
        visual_artifacts: dict[str, str],
    ) -> list[EvidenceSpan]:
        results: list[EvidenceSpan] = []
        for table in ir.tables:
            table_id = table["table_id"]
            # Document IR is immutable at this stage.  Build an inventory-local
            # view with complete stacked column headers so a value cell keeps
            # both a spanning year header and its leaf business/site header.
            # Some OCR tables only mark the first physical row as a header even
            # though the next non-numeric row is also part of the header band.
            table_cells = self._with_effective_column_headers(
                table.get("cells", []),
                table.get("header_row_indices", []),
            )
            column_x_ranges = self._column_x_ranges(table_cells)
            table_visual_paths = (
                [visual_artifacts[table_id]] if table_id in visual_artifacts else []
            )
            rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for cell in table_cells:
                rows[int(cell.get("row_index", 0))].append(cell)
            for row_index, cells in sorted(rows.items()):
                ordered = sorted(cells, key=lambda item: int(item.get("col_index", 0)))
                pieces: list[str] = []
                for cell in ordered:
                    headers = [
                        *self._clean_labels(cell.get("column_header_path")),
                        *self._clean_labels(cell.get("row_header_path")),
                    ]
                    prefix = " / ".join(item for item in headers if item)
                    text = (cell.get("text") or "").strip()
                    if not text:
                        continue
                    pieces.append(f"{prefix}: {text}" if prefix else text)
                row_text = " | ".join(pieces).strip()
                if not row_text:
                    continue
                page_index = int(ordered[0].get("page_index", table.get("page_index", 0)))
                page = page_lookup.get(page_index, {})
                group_id = f"table-row:{table_id}:{row_index}"
                locators = [
                    self._locator(
                        "cell",
                        cell["cell_id"],
                        int(cell.get("page_index", page_index)),
                        page.get("printed_page_label"),
                        cell.get("bbox"),
                    )
                    for cell in ordered
                ]
                results.append(
                    EvidenceSpan(
                        span_id=stable_id(
                            "span",
                            ir.document_id,
                            ir.ir_revision,
                            "table-row",
                            table_id,
                            row_index,
                            row_text,
                        ),
                        document_id=ir.document_id,
                        ir_run_id=ir.run_id,
                        ir_revision=ir.ir_revision,
                        span_type="table_row",
                        text=row_text,
                        context_text=self._context_text(table.get("caption"), row_text),
                        page_index=page_index,
                        printed_page_label=page.get("printed_page_label"),
                        object_ids=[table_id, *[cell["cell_id"] for cell in ordered]],
                        locators=locators,
                        context_group_id=group_id,
                        quality_flags=sorted(set(table.get("quality_flags", []))),
                        visual_evidence_paths=table_visual_paths,
                        has_table_structure=True,
                        structural_context={
                            "object_type": "table_row",
                            "table_id": table_id,
                            "row_index": row_index,
                            "caption": table.get("caption"),
                            "cells": [self._raw_cell_structure(cell) for cell in ordered],
                        },
                    )
                )
                results.extend(
                    self._raw_table_cell_spans(
                        ir=ir,
                        table=table,
                        row_index=row_index,
                        cells=ordered,
                        column_x_ranges=column_x_ranges,
                        page_lookup=page_lookup,
                        visual_paths=table_visual_paths,
                    )
                )
        return results

    def _raw_table_cell_spans(
        self,
        *,
        ir: LoadedDocumentIr,
        table: dict[str, Any],
        row_index: int,
        cells: list[dict[str, Any]],
        column_x_ranges: dict[int, dict[str, float]],
        page_lookup: dict[int, dict[str, Any]],
        visual_paths: list[str],
    ) -> list[EvidenceSpan]:
        results: list[EvidenceSpan] = []
        group_id = f"table-row:{table['table_id']}:{row_index}"
        row_text = " | ".join(
            f"c{int(cell.get('col_index', 0))}:{str(cell.get('text') or '').strip()}"
            for cell in cells
            if str(cell.get("text") or "").strip()
        )
        for cell in cells:
            text = str(cell.get("text") or "").strip()
            if not text:
                continue
            page_index = int(cell.get("page_index", table.get("page_index", 0)))
            page = page_lookup.get(page_index, {})
            structure = self._raw_cell_structure(cell)
            context_text = self._context_text(
                table.get("caption"),
                " | ".join(
                    part
                    for part in (
                        f"row:{row_text}" if row_text else "",
                        "column_headers:" + " / ".join(structure["column_header_path"])
                        if structure["column_header_path"] else "",
                        "row_headers:" + " / ".join(structure["row_header_path"])
                        if structure["row_header_path"] else "",
                    )
                    if part
                ),
            )
            results.append(
                EvidenceSpan(
                    span_id=stable_id(
                        "span",
                        ir.document_id,
                        ir.ir_revision,
                        "table-cell",
                        table["table_id"],
                        cell["cell_id"],
                        text,
                    ),
                    document_id=ir.document_id,
                    ir_run_id=ir.run_id,
                    ir_revision=ir.ir_revision,
                    span_type="table_cell",
                    text=text,
                    context_text=context_text,
                    page_index=page_index,
                    printed_page_label=page.get("printed_page_label"),
                    object_ids=[table["table_id"], cell["cell_id"]],
                    locators=[
                        self._locator(
                            "cell",
                            cell["cell_id"],
                            page_index,
                            page.get("printed_page_label"),
                            cell.get("bbox"),
                        )
                    ],
                    context_group_id=group_id,
                    quality_flags=sorted(set(table.get("quality_flags", []))),
                    visual_evidence_paths=visual_paths,
                    has_table_structure=True,
                    structural_context={
                        "object_type": "table_cell",
                        "table_id": table["table_id"],
                        "table_bbox": table.get("bbox"),
                        "column_bbox": column_x_ranges.get(
                            int(cell.get("col_index", 0))
                        ),
                        "caption": table.get("caption"),
                        **structure,
                    },
                )
            )
            for relation, labels in (
                ("column_header", structure["column_header_path"]),
                ("row_header", structure["row_header_path"]),
            ):
                for sequence, label in enumerate(labels, 1):
                    results.append(
                        EvidenceSpan(
                            span_id=stable_id(
                                "span",
                                ir.document_id,
                                ir.ir_revision,
                                "table-header-path",
                                cell["cell_id"],
                                relation,
                                sequence,
                                label,
                            ),
                            document_id=ir.document_id,
                            ir_run_id=ir.run_id,
                            ir_revision=ir.ir_revision,
                            span_type="table_cell",
                            text=label,
                            context_text=context_text,
                            page_index=page_index,
                            printed_page_label=page.get("printed_page_label"),
                            object_ids=[table["table_id"], cell["cell_id"]],
                            locators=[
                                self._locator(
                                    "cell",
                                    cell["cell_id"],
                                    page_index,
                                    page.get("printed_page_label"),
                                    cell.get("bbox"),
                                )
                            ],
                            context_group_id=group_id,
                            quality_flags=["derived_table_header_context"],
                            visual_evidence_paths=visual_paths,
                            has_table_structure=True,
                            structural_context={
                                "object_type": "table_header_context",
                                "table_id": table["table_id"],
                                "table_bbox": table.get("bbox"),
                                "column_bbox": column_x_ranges.get(
                                    int(cell.get("col_index", 0))
                                ),
                                "caption": table.get("caption"),
                                "relation": relation,
                                "anchor_cell_id": str(cell["cell_id"]),
                                "row_index": int(cell.get("row_index", row_index)),
                                "col_index": int(cell.get("col_index", 0)),
                            },
                        )
                    )
        return results

    @staticmethod
    def _column_x_ranges(cells: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
        """Derive stable logical-column geometry without changing Document IR.

        OCR may omit a data cell bbox even when another cell or stacked header in
        the same logical column has valid geometry. Single-column cells are the
        authoritative source. A spanning header is used only for columns which
        have no direct geometry, dividing its horizontal range evenly.
        """

        direct: dict[int, list[tuple[float, float]]] = defaultdict(list)
        inferred: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for cell in cells:
            bbox = cell.get("bbox")
            if not isinstance(bbox, dict):
                continue
            try:
                x0 = float(bbox["x0"])
                x1 = float(bbox["x1"])
                col = int(cell.get("col_index", 0))
                span = max(1, int(cell.get("col_span", 1) or 1))
            except (KeyError, TypeError, ValueError):
                continue
            if x1 <= x0:
                continue
            if span == 1:
                direct[col].append((x0, x1))
                continue
            width = (x1 - x0) / span
            for offset in range(span):
                inferred[col + offset].append(
                    (x0 + width * offset, x0 + width * (offset + 1))
                )

        result = {}
        for col in sorted(set(direct) | set(inferred)):
            ranges = direct.get(col) or inferred.get(col) or []
            if not ranges:
                continue
            # Median endpoints resist occasional oversized merged-cell geometry.
            ordered_x0 = sorted(item[0] for item in ranges)
            ordered_x1 = sorted(item[1] for item in ranges)
            middle = len(ranges) // 2
            if len(ranges) % 2:
                x0, x1 = ordered_x0[middle], ordered_x1[middle]
            else:
                x0 = (ordered_x0[middle - 1] + ordered_x0[middle]) / 2
                x1 = (ordered_x1[middle - 1] + ordered_x1[middle]) / 2
            if x1 > x0:
                result[col] = {"x0": x0, "x1": x1}
        return result

    @classmethod
    def _raw_cell_structure(cls, cell: dict[str, Any]) -> dict[str, Any]:
        return {
            "cell_id": str(cell["cell_id"]),
            "row_index": int(cell.get("row_index", 0)),
            "col_index": int(cell.get("col_index", 0)),
            "row_span": max(1, int(cell.get("row_span", 1) or 1)),
            "col_span": max(1, int(cell.get("col_span", 1) or 1)),
            "column_header_path": cls._clean_labels(
                cell.get("effective_column_header_path")
                or cell.get("column_header_path")
            ),
            "row_header_path": cls._clean_labels(cell.get("row_header_path")),
            **({"header_alignment_uncertain": True} if cell.get("header_alignment_uncertain") else {}),
        }

    @classmethod
    def _with_effective_column_headers(
        cls,
        cells: list[dict[str, Any]],
        declared_header_rows,
    ) -> list[dict[str, Any]]:
        """Return copied cells with a losslessly derived stacked header path.

        The derivation is purely geometric/structural: every header cell whose
        column span covers the value column contributes its visible text.  It
        does not infer business meaning and therefore belongs in Evidence
        Inventory rather than in the semantic model or Document IR repair.
        """

        copied = [dict(cell) for cell in cells]
        declared: set[int] = set()
        for value in declared_header_rows or []:
            try:
                declared.add(int(value))
            except (TypeError, ValueError):
                continue
        header_rows = declared | cls._leading_header_rows(copied)
        if not header_rows:
            return copied

        header_cells = sorted(
            (
                cell
                for cell in copied
                if int(cell.get("row_index", 0)) in header_rows
            ),
            key=lambda item: (
                int(item.get("row_index", 0)),
                int(item.get("col_index", 0)),
            ),
        )
        # A non-unit leaf header located under a unit-column header is a
        # structural contradiction (seen in shifted multi-level OCR grids).
        # Preserve every original label, but stop presenting this derived
        # alignment as certain. The model can resolve it from the full crop.
        unit_headers = [
            item for item in header_cells
            if str(item.get("text") or "").strip().casefold() in {"单位", "單位", "unit", "units"}
        ]
        alignment_uncertain = any(
            int(leaf.get("row_index", 0)) > int(parent.get("row_index", 0))
            and int(parent.get("col_index", 0)) <= int(leaf.get("col_index", 0))
            < int(parent.get("col_index", 0)) + max(1, int(parent.get("col_span", 1) or 1))
            and bool(label := str(leaf.get("text") or "").strip())
            and not cls._is_placeholder_label(label)
            and not UNIT_ONLY_RE.fullmatch(label)
            and label.casefold() not in {"单位", "單位", "unit", "units"}
            for parent in unit_headers for leaf in header_cells
        )
        for cell in copied:
            if alignment_uncertain:
                cell["header_alignment_uncertain"] = True
            row_index = int(cell.get("row_index", 0))
            column_index = int(cell.get("col_index", 0))
            existing = cls._clean_labels(cell.get("column_header_path"))
            if row_index in header_rows:
                cell["effective_column_header_path"] = existing
                continue
            derived: list[str] = []
            for header in header_cells:
                header_row = int(header.get("row_index", 0))
                if header_row >= row_index:
                    continue
                start = int(header.get("col_index", 0))
                span = max(1, int(header.get("col_span", 1) or 1))
                if not start <= column_index < start + span:
                    continue
                label = str(header.get("text") or "").strip()
                if (
                    not label
                    or cls._is_placeholder_label(label)
                    or label == str(cell.get("text") or "").strip()
                    or label in derived
                ):
                    continue
                derived.append(label)
            cell["effective_column_header_path"] = list(
                dict.fromkeys([*derived, *existing])
            )
        return copied

    @classmethod
    def _leading_header_rows(cls, cells: list[dict[str, Any]]) -> set[int]:
        rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for cell in cells:
            rows[int(cell.get("row_index", 0))].append(cell)
        result: set[int] = set()
        for row_index in sorted(rows):
            row = rows[row_index]
            has_data_value = any(
                cls._is_value_like(str(cell.get("text") or "").strip())
                and not YEAR_ONLY_RE.fullmatch(str(cell.get("text") or "").strip())
                for cell in row
            )
            if has_data_value:
                break
            result.add(row_index)
        return result

    @staticmethod
    def _clean_labels(values) -> list[str]:
        labels = []
        for value in values or []:
            label = str(value).strip()
            if (
                label
                and not EvidenceInventoryBuilder._is_placeholder_label(label)
                and label not in labels
            ):
                labels.append(label)
        return labels

    @staticmethod
    def _is_placeholder_label(text: str) -> bool:
        return bool(LABEL_PLACEHOLDER_RE.fullmatch(text.strip()))

    @staticmethod
    def _is_aggregate_label(text: str) -> bool:
        return bool(AGGREGATE_LABEL_RE.fullmatch(text.strip()))

    @staticmethod
    def _is_value_like(text: str) -> bool:
        return bool(NUMERIC_CELL_RE.fullmatch(text.strip()))

    def _sentence_spans(
        self,
        *,
        ir: LoadedDocumentIr,
        block: dict[str, Any],
        text: str,
        page: dict[str, Any],
        section: dict[str, Any],
        visual_evidence_paths: list[str],
    ) -> list[EvidenceSpan]:
        pieces = [
            item.strip()
            for item in re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
            if item.strip()
        ]
        if len(pieces) <= 1:
            return []
        block_id = block["block_id"]
        page_index = int(page["page_index"])
        results = []
        for sequence, piece in enumerate(pieces, 1):
            if len(piece) < 4:
                continue
            results.append(
                EvidenceSpan(
                    span_id=stable_id(
                        "span",
                        ir.document_id,
                        ir.ir_revision,
                        "sentence",
                        block_id,
                        sequence,
                        piece,
                    ),
                    document_id=ir.document_id,
                    ir_run_id=ir.run_id,
                    ir_revision=ir.ir_revision,
                    span_type="sentence",
                    text=piece,
                    context_text=self._context_text(section.get("title"), piece),
                    section_id=block.get("section_id"),
                    section_title=section.get("title"),
                    page_index=page_index,
                    printed_page_label=page.get("printed_page_label"),
                    object_ids=[block_id],
                    locators=[
                        self._locator(
                            "block",
                            block_id,
                            page_index,
                            page.get("printed_page_label"),
                            block.get("bbox"),
                        )
                    ],
                    context_group_id=f"block:{block_id}",
                    quality_flags=sorted(set(block.get("quality_flags", []))),
                    visual_evidence_paths=visual_evidence_paths,
                    has_table_structure=bool(block.get("table_id")),
                )
            )
        return results

    @staticmethod
    def _context_text(title: str | None, text: str) -> str:
        return f"{title}\n{text}" if title and title.strip() else text

    @staticmethod
    def _locator(
        object_type: str,
        object_id: str,
        page_index: int,
        printed_page_label: str | None,
        bbox: dict[str, Any] | None,
    ) -> SourceLocator:
        return SourceLocator(
            ir_object_type=object_type,
            ir_object_id=object_id,
            pdf_page_index=page_index,
            printed_page_label=printed_page_label,
            bbox=bbox,
        )

    @staticmethod
    def _span_order(span: EvidenceSpan) -> int:
        return {
            "block": 0,
            "sentence": 1,
            "table_row": 2,
            "table_cell": 3,
            "figure_text": 4,
            "page_text": 5,
        }[span.span_type]
