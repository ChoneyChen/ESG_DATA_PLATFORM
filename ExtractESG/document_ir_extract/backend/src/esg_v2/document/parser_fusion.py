from __future__ import annotations

import html
import re

from esg_v2.document.contracts import (
    CellIR,
    ConflictGroup,
    DocumentIR,
    LocalPdfForensics,
    SourceTrace,
    TableIR,
    TableObservationIR,
)
from esg_v2.document.geometry import bbox_iou


class ParserFusion:
    """Combines observations and review patches; it never hides disagreement."""

    def fuse(self, candidate: DocumentIR, *, local_forensics: LocalPdfForensics | None = None) -> DocumentIR:
        candidate.metadata.local_forensics = local_forensics
        if not local_forensics:
            return candidate
        local_by_page = {page.page_index: page for page in local_forensics.pages}
        conflicts = list(candidate.conflict_groups)
        for page in candidate.pages:
            local = local_by_page.get(page.page_index)
            if not local:
                page.quality_flags.append("local_page_forensics_missing")
                continue
            page.width = page.width or local.width_points
            page.height = page.height or local.height_points
            page.page_image_path = page.page_image_path or local.page_image_path
            ocr_visible_length = len(self._visible_text(page.text))
            if local.native_text_length is not None and ocr_visible_length >= 80 and local.native_text_length >= 80:
                ratio = abs(ocr_visible_length - local.native_text_length) / max(ocr_visible_length, local.native_text_length)
                if ratio > 0.65:
                    page.quality_flags.append("native_ocr_text_length_divergence")
                    conflicts.append(
                        ConflictGroup(
                            conflict_id=f"conflict-{page.page_id}-native-ocr",
                            target_id=page.page_id,
                            conflict_type="native_ocr_text_length_divergence",
                            observation_refs=["paddleocr-vl", "pdfplumber"],
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
            for candidate_index, local_table in enumerate(local_page.table_candidates):
                ranked = sorted(
                    ((bbox_iou(local_table.bbox, table.bbox), table) for table in page_tables if table.bbox),
                    key=lambda item: item[0],
                    reverse=True,
                )
                match_iou, matched = ranked[0] if ranked else (0.0, None)
                observation = TableObservationIR(
                    observation_id=local_table.candidate_id or f"pdfplumber-table-{local_page.page_index + 1:04d}-{candidate_index + 1:03d}",
                    source="pdfplumber",
                    row_count=local_table.row_count or 0,
                    column_count=local_table.column_count or 0,
                    bbox=local_table.bbox,
                    cells=local_table.cells,
                    match_iou=round(match_iou, 4) if matched else None,
                )
                if matched is not None and match_iou >= 0.35:
                    matched.observations.append(observation)
                    self._add_artifact_id(matched.source_trace, "artifact-source-pdf")
                    self._reconcile_local_table_observation(matched, observation, conflicts)
                    continue
                if not self._credible_local_table(observation):
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
                    status="open",
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
    def _credible_local_table(observation: TableObservationIR) -> bool:
        nonempty = sum(1 for cell in observation.cells if cell.text.strip())
        return observation.row_count >= 2 and observation.column_count >= 2 and nonempty >= 4

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
        text = re.sub(r"<img\b[^>]*>", " ", value, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"!\[[^]]*]\([^)]+\)", " ", text)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

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
