from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from esg_v2.document.contracts import (
    CellIR,
    ConflictGroup,
    DocumentIR,
    LocalPdfForensics,
    SourceTrace,
    TableIR,
    TableObservationIR,
)
from esg_v2.document.geometry import bbox_containment, bbox_iou
from esg_v2.document.native_text_comparison import NativeTextComparisonPreprocessor
from esg_v2.document.text_normalization import canonical_page_text, source_character_recall, visible_text


class ParserFusion:
    """Combines observations and review patches; it never hides disagreement."""

    def fuse(self, candidate: DocumentIR, *, local_forensics: LocalPdfForensics | None = None) -> DocumentIR:
        candidate.metadata.local_forensics = local_forensics
        if not local_forensics:
            return candidate
        NativeTextComparisonPreprocessor().prepare(candidate, local_forensics)
        local_by_page = {page.page_index: page for page in local_forensics.pages}
        conflicts = [
            conflict
            for conflict in candidate.conflict_groups
            if conflict.conflict_type != "native_ocr_text_length_divergence"
        ]
        for page in candidate.pages:
            page.quality_flags = [
                flag
                for flag in page.quality_flags
                if flag != "native_ocr_text_length_divergence"
            ]
            local = local_by_page.get(page.page_index)
            if not local:
                page.quality_flags.append("local_page_forensics_missing")
                continue
            page.width = page.width or local.width_points
            page.height = page.height or local.height_points
            page.page_image_path = page.page_image_path or local.page_image_path
            canonical_text = canonical_page_text(candidate, page)
            ocr_visible_length = len(visible_text(canonical_text))
            native_text = (
                local.native_text_for_comparison
                if local.native_text_for_comparison is not None
                else local.native_text
            )
            native_text_length = (
                local.native_text_for_comparison_length
                if local.native_text_for_comparison_length is not None
                else local.native_text_length
            )
            if (
                native_text_length is not None
                and ocr_visible_length >= 80
                and native_text_length >= 80
                and ocr_visible_length < native_text_length * 0.60
                and source_character_recall(native_text or "", canonical_text) < 0.75
            ):
                self._add_flag(page.quality_flags, "native_ocr_text_length_divergence")
                conflicts.append(
                    ConflictGroup(
                        conflict_id=f"conflict-{page.page_id}-native-ocr",
                        target_id=page.page_id,
                        conflict_type="native_ocr_text_length_divergence",
                        observation_refs=["paddleocr-vl", "pdfplumber"],
                        routing_disposition="blocking_task",
                        status="open",
                    )
                )
        self._fuse_local_tables(candidate, local_forensics, conflicts)
        candidate.conflict_groups = self._dedupe(conflicts)
        return candidate

    def _fuse_local_tables(self, document: DocumentIR, local_forensics: LocalPdfForensics, conflicts) -> None:
        tables_by_page: dict[int, list[TableIR]] = {}
        pages_by_index = {page.page_index: page for page in document.pages}
        for table in document.tables:
            tables_by_page.setdefault(table.page_index, []).append(table)

        for local_page in local_forensics.pages:
            page_tables = tables_by_page.setdefault(local_page.page_index, [])
            matched_table_ids: set[str] = set()
            for candidate_index, local_table in enumerate(local_page.table_candidates):
                match_iou, match_score, matched = self._match_local_table(
                    local_table,
                    page_tables,
                    candidate_index=candidate_index,
                    matched_table_ids=matched_table_ids,
                )
                observation = TableObservationIR(
                    observation_id=local_table.candidate_id or f"pdfplumber-table-{local_page.page_index + 1:04d}-{candidate_index + 1:03d}",
                    source="pdfplumber",
                    row_count=local_table.row_count or 0,
                    column_count=local_table.column_count or 0,
                    bbox=local_table.bbox,
                    cells=local_table.cells,
                    match_iou=round(match_iou, 4) if matched else None,
                    match_score=round(match_score, 4) if matched else None,
                    quality_flags=list(local_table.quality_flags),
                )
                if matched is not None:
                    matched_table_ids.add(matched.table_id)
                    matched.observations.append(observation)
                    self._add_artifact_id(matched.source_trace, "artifact-source-pdf")
                    if matched.bbox is None:
                        matched.bbox = observation.bbox
                        self._add_flag(matched.quality_flags, "table_geometry_recovered_from_pdfplumber")
                        matched.quality_flags = [
                            flag
                            for flag in matched.quality_flags
                            if flag not in {"table_geometry_missing", "visual_crop_unavailable"}
                        ]
                        wrapper = next(
                            (block for block in document.blocks if block.block_id == matched.block_id),
                            None,
                        )
                        if wrapper is not None and wrapper.bbox is None:
                            wrapper.bbox = observation.bbox
                            self._add_flag(wrapper.quality_flags, "geometry_recovered_from_pdfplumber")
                    self._reconcile_local_table_observation(matched, observation, conflicts)
                    continue
                subsuming = self._find_subsuming_table(local_table, page_tables)
                if subsuming is not None:
                    observation.match_iou = round(bbox_iou(local_table.bbox, subsuming.bbox), 4)
                    observation.match_score = round(
                        self._text_coverage(
                            self._observation_text(local_table.cells),
                            self._table_text(subsuming),
                        ),
                        4,
                    )
                    self._add_flag(observation.quality_flags, "subsumed_by_canonical_table")
                    subsuming.observations.append(observation)
                    self._add_artifact_id(subsuming.source_trace, "artifact-source-pdf")
                    self._add_flag(subsuming.quality_flags, "local_candidate_subsumed")
                    continue
                if not self._credible_local_table(observation, local_page):
                    self._add_flag(
                        local_table.quality_flags,
                        "rejected_noncredible_local_table_candidate",
                    )
                    self._add_flag(
                        local_page.quality_flags,
                        "noncredible_local_table_candidate_filtered",
                    )
                    continue
                table = self._table_from_local_observation(
                    observation,
                    page_index=local_page.page_index,
                    order=len(page_tables),
                )
                document.tables.append(table)
                page_tables.append(table)
                page = pages_by_index.get(local_page.page_index)
                if page and table.table_id not in page.table_ids:
                    page.table_ids.append(table.table_id)

    def _match_local_table(self, local_table, page_tables, *, candidate_index: int, matched_table_ids: set[str]):
        candidates = [table for table in page_tables if table.table_id not in matched_table_ids]
        geometric = sorted(
            ((bbox_iou(local_table.bbox, table.bbox), table) for table in candidates if table.bbox),
            key=lambda item: item[0],
            reverse=True,
        )
        if geometric and geometric[0][0] >= 0.35:
            return geometric[0][0], geometric[0][0], geometric[0][1]

        local_rows = int(local_table.row_count or 0)
        local_columns = int(local_table.column_count or 0)
        local_text = self._observation_text(local_table.cells)
        ranked = []
        for table in candidates:
            shape_score = self._shape_similarity(
                local_rows,
                local_columns,
                table.row_count,
                table.column_count,
            )
            text_score = SequenceMatcher(None, local_text, self._table_text(table)).ratio() if local_text else 0.0
            order_score = 1.0 / (1.0 + abs(int(table.order) - candidate_index))
            score = 0.5 * text_score + 0.35 * shape_score + 0.15 * order_score
            ranked.append((score, bbox_iou(local_table.bbox, table.bbox), table))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < 0.62:
            return 0.0, 0.0, None
        return ranked[0][1], ranked[0][0], ranked[0][2]

    def _find_subsuming_table(self, local_table, page_tables: list[TableIR]) -> TableIR | None:
        local_text = self._observation_text(local_table.cells)
        if not local_text:
            return None
        ranked = []
        for table in page_tables:
            if "local_only_table_candidate" in table.quality_flags:
                continue
            containment = bbox_containment(local_table.bbox, table.bbox)
            if containment < 0.92:
                continue
            text_coverage = self._text_coverage(local_text, self._table_text(table))
            canonical_capacity = max(len(table.cells), table.row_count * table.column_count)
            local_capacity = max(len(local_table.cells), int(local_table.row_count or 0) * int(local_table.column_count or 0))
            canonical_text = self._table_text(table)
            shared_header_signal = self._shared_header_signal(local_text, canonical_text)
            local_area = self._bbox_area(local_table.bbox)
            canonical_area = self._bbox_area(table.bbox)
            strong_fragment_geometry = (
                containment >= 0.98
                and canonical_area > 0
                and local_area / canonical_area <= 0.45
                and shared_header_signal
            )
            if (
                not strong_fragment_geometry
                and (text_coverage < 0.72 or canonical_capacity < max(1, local_capacity // 2))
            ):
                continue
            ranked.append((0.55 * text_coverage + 0.35 * containment + 0.10 * float(shared_header_signal), table))
        return max(ranked, key=lambda item: item[0])[1] if ranked else None

    def _reconcile_local_table_observation(self, table: TableIR, observation: TableObservationIR, conflicts) -> None:
        same_shape = (
            table.row_count == observation.row_count
            and table.column_count == observation.column_count
            and table.row_count > 0
            and table.column_count > 0
        )
        if same_shape:
            self._add_flag(table.quality_flags, "local_table_dimensions_confirmed")
            local_by_position = {(cell.row_index, cell.col_index): cell for cell in observation.cells}
            for cell in table.cells:
                local = local_by_position.get((cell.row_index, cell.col_index))
                if local and local.bbox and not cell.bbox:
                    cell.bbox = local.bbox
                    self._add_artifact_id(cell.source_trace, "artifact-source-pdf")
                    self._add_flag(cell.quality_flags, "cell_geometry_from_pdfplumber")
            return
        if table.cells and observation.cells:
            self._add_flag(table.quality_flags, "local_table_dimension_conflict")
            conflicts.append(
                ConflictGroup(
                    conflict_id=f"conflict-{table.table_id}-local-shape",
                    target_id=table.table_id,
                    conflict_type="ocr_local_table_dimension_mismatch",
                    observation_refs=[table.table_id, observation.observation_id],
                    blocking=False,
                    status="resolved",
                    routing_disposition="accepted_nonmaterial_difference",
                    resolution=(
                        "The secondary parser dimension differs from the canonical OCR table. "
                        "Both observations remain auditable; this non-material difference does "
                        "not itself request a review task."
                    ),
                )
            )
            return
        if not table.cells and observation.cells:
            table.cells = self._cells_from_observation(table.table_id, table.page_index, observation)
            table.row_count = observation.row_count
            table.column_count = observation.column_count
            table.header_row_indices = [0] if table.cells else []
            self._add_flag(table.quality_flags, "table_structure_recovered_from_pdfplumber")

    @staticmethod
    def _credible_local_table(observation: TableObservationIR, local_page) -> bool:
        nonempty = sum(1 for cell in observation.cells if cell.text.strip())
        total = max(1, observation.row_count * observation.column_count)
        visible_values = [str(cell.text or "").strip() for cell in observation.cells if str(cell.text or "").strip()]
        alphanumeric = "".join(character for value in visible_values for character in value if character.isalnum())
        area_ratio = 0.0
        if local_page.width_points and local_page.height_points and observation.bbox:
            area_ratio = (
                max(0.0, observation.bbox.x1 - observation.bbox.x0)
                * max(0.0, observation.bbox.y1 - observation.bbox.y0)
                / (local_page.width_points * local_page.height_points)
            )
        decoration_like = (
            1.0 - nonempty / total >= 0.70
            and not any(character.isdigit() for character in alphanumeric)
            and len(alphanumeric) <= 12
            and area_ratio <= 0.025
        )
        return (
            observation.row_count >= 2
            and observation.column_count >= 2
            and nonempty >= max(4, int(total * 0.25))
            and area_ratio <= 0.95
            and not decoration_like
        )

    @staticmethod
    def _shape_similarity(first_rows: int, first_columns: int, second_rows: int, second_columns: int) -> float:
        if min(first_rows, first_columns, second_rows, second_columns) <= 0:
            return 0.0
        row_score = min(first_rows, second_rows) / max(first_rows, second_rows)
        column_score = min(first_columns, second_columns) / max(first_columns, second_columns)
        return (row_score + column_score) / 2

    @staticmethod
    def _observation_text(cells) -> str:
        return re.sub(
            r"\s+",
            "",
            "|".join(str(cell.text or "").strip().lower() for cell in cells if str(cell.text or "").strip()),
        )

    @classmethod
    def _table_text(cls, table) -> str:
        values = [cell.text for cell in sorted(table.cells, key=lambda item: (item.row_index, item.col_index))]
        if not any(str(value or "").strip() for value in values):
            values = [table.markdown]
        return re.sub(r"\s+", "", "|".join(str(value or "").strip().lower() for value in values))

    @staticmethod
    def _text_coverage(source: str, candidate: str) -> float:
        source_chars = Counter(character for character in source if character.isalnum())
        candidate_chars = Counter(character for character in candidate if character.isalnum())
        if not source_chars:
            return 0.0
        retained = sum((source_chars & candidate_chars).values())
        return retained / sum(source_chars.values())

    @staticmethod
    def _text_recall(native_text: str, ocr_text: str) -> float:
        return source_character_recall(native_text, ocr_text)

    @staticmethod
    def _shared_header_signal(first: str, second: str) -> bool:
        markers = ("sdg", "年度", "指標", "指标", "行動", "行动", "計劃", "计划", "類別", "类别")
        first_lower = first.casefold()
        second_lower = second.casefold()
        return any(marker.casefold() in first_lower and marker.casefold() in second_lower for marker in markers)

    @staticmethod
    def _bbox_area(bbox) -> float:
        if bbox is None:
            return 0.0
        return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)

    def _table_from_local_observation(self, observation: TableObservationIR, *, page_index: int, order: int) -> TableIR:
        table_id = f"tbl-{page_index + 1:04d}-local-{order + 1:04d}"
        trace = SourceTrace(
            parser="pdfplumber",
            artifact_ids=["artifact-source-pdf"],
            notes=[observation.observation_id],
        )
        return TableIR(
            table_id=table_id,
            page_index=page_index,
            page_indices=[page_index],
            order=order,
            row_count=observation.row_count,
            column_count=observation.column_count,
            cells=self._cells_from_observation(table_id, page_index, observation),
            observations=[observation],
            header_row_indices=[0],
            bbox=observation.bbox,
            quality_flags=["local_only_table_candidate", "table_structure_recovered_from_pdfplumber"],
            source_trace=trace,
        )

    @staticmethod
    def _cells_from_observation(table_id: str, page_index: int, observation: TableObservationIR) -> list[CellIR]:
        trace = SourceTrace(
            parser="pdfplumber",
            artifact_ids=["artifact-source-pdf"],
            notes=[observation.observation_id],
        )
        return [
            CellIR(
                cell_id=f"{table_id}-r{cell.row_index + 1:03d}-c{cell.col_index + 1:03d}",
                table_id=table_id,
                page_index=page_index,
                row_index=cell.row_index,
                col_index=cell.col_index,
                text=cell.text,
                is_header=cell.row_index == 0,
                bbox=cell.bbox,
                quality_flags=["cell_from_pdfplumber"],
                source_trace=trace,
            )
            for cell in observation.cells
        ]

    @staticmethod
    def _visible_text(value: str) -> str:
        return visible_text(value)

    @staticmethod
    def _add_flag(flags: list[str], value: str) -> None:
        if value not in flags:
            flags.append(value)

    @staticmethod
    def _add_artifact_id(trace: SourceTrace, artifact_id: str) -> None:
        if artifact_id not in trace.artifact_ids:
            trace.artifact_ids.append(artifact_id)

    def reconcile_reviews(self, document: DocumentIR) -> DocumentIR:
        targets = {
            **{item.page_id: item for item in document.pages},
            **{item.block_id: item for item in document.blocks},
            **{item.table_id: item for item in document.tables},
            **{item.figure_id: item for item in document.figures},
            **{item.spread_id: item for item in document.spreads},
        }
        for patch in document.correction_patches:
            target = targets.get(patch.target_id)
            if not target:
                continue
            if patch.status == "accepted" and patch.operation == "confirm":
                if "vlm_review_confirmed" not in target.quality_flags:
                    target.quality_flags.append("vlm_review_confirmed")
            elif patch.status == "human_required":
                if "review_correction_requires_human" not in target.quality_flags:
                    target.quality_flags.append("review_correction_requires_human")
        document.conflict_groups = self._dedupe(document.conflict_groups)
        return document

    @staticmethod
    def _dedupe(items):
        return list({item.conflict_id: item for item in items}.values())
