from __future__ import annotations

from pathlib import Path
from typing import Sequence

from esg_document.document.contracts import (
    BoundingBox,
    LocalPageForensics,
    LocalPdfForensics,
    TableCellObservation,
    LocalTableCandidate,
    LocalTextBlock,
)
from esg_document.document.page_renderer import RenderedDocument
from esg_document.document.geometry import normalize_bbox_to_page


class LocalPdfProcessor:
    """Deterministic PDF cross-check. It never replaces the primary parser."""

    def analyze(
        self,
        pdf_path: str | None,
        rendered: RenderedDocument | None = None,
        *,
        page_indices: Sequence[int] | None = None,
    ) -> LocalPdfForensics:
        if not pdf_path:
            return LocalPdfForensics(
                pdf_path=None, exists=False, errors=["pdf_path_not_provided"]
            )

        path = Path(pdf_path)
        if not path.exists():
            return LocalPdfForensics(
                pdf_path=str(path), exists=False, errors=["pdf_path_not_found"]
            )

        try:
            import pdfplumber  # type: ignore
        except Exception as exc:
            return LocalPdfForensics(
                pdf_path=str(path),
                exists=True,
                errors=[f"pdfplumber_unavailable: {exc}"],
            )

        rendered_by_page = {
            page.page_index: page for page in (rendered.pages if rendered else [])
        }
        pages: list[LocalPageForensics] = []
        errors: list[str] = list(rendered.errors if rendered else [])
        try:
            with pdfplumber.open(path) as pdf:
                # page_count stays the source PDF's; a targeted IR only inspects its covered pages.
                page_count = len(pdf.pages)
                selected = (
                    [index for index in page_indices if 0 <= index < page_count]
                    if page_indices is not None
                    else range(page_count)
                )
                for page_index in selected:
                    page = pdf.pages[page_index]
                    rendered_page = rendered_by_page.get(page_index)
                    coordinate_system_id = f"page-{page_index + 1:04d}-pdf-points"
                    try:
                        text = page.extract_text() or ""
                        flags: list[str] = []
                        if len(text.strip()) < 20:
                            flags.append("low_native_text")
                        words = page.extract_words(
                            x_tolerance=2,
                            y_tolerance=2,
                            keep_blank_chars=False,
                            use_text_flow=True,
                        )
                        # Only glyphs inside the visible page box belong to this
                        # page. A PDF that lays a spread out in one content
                        # stream (both facing pages present in one page's
                        # literal space) otherwise contributes the neighbour's
                        # text too, and the page's own OCR then looks far
                        # shorter than "native" although nothing is missing.
                        visible = [w for w in words if self._visible(w, page)]
                        if len(visible) < len(words):
                            flags.append("native_text_clipped_to_page_box")
                            text = (
                                "\n".join(w.get("text", "") for w in visible).strip()
                                or ""
                            )
                            words = visible
                        native_blocks = []
                        for word in words:
                            if not word.get("text"):
                                continue
                            raw_bbox = BoundingBox(
                                x0=float(word["x0"]),
                                y0=float(word["top"]),
                                x1=float(word["x1"]),
                                y1=float(word["bottom"]),
                                unit="points",
                                origin="top_left",
                                coordinate_system_id=coordinate_system_id,
                            )
                            bbox, _ = normalize_bbox_to_page(
                                raw_bbox,
                                page_width=float(page.width),
                                page_height=float(page.height),
                            )
                            if bbox is not None:
                                native_blocks.append(
                                    LocalTextBlock(
                                        text=str(word.get("text") or ""), bbox=bbox
                                    )
                                )
                        table_candidates = self._table_candidates(
                            page,
                            coordinate_system_id,
                            errors,
                            page_index,
                            page_width=float(page.width),
                            page_height=float(page.height),
                        )
                        if table_candidates:
                            flags.append("local_table_candidates_present")
                        pages.append(
                            LocalPageForensics(
                                page_index=page_index,
                                width_points=float(page.width),
                                height_points=float(page.height),
                                native_text_length=len(text.strip()),
                                native_text=text.strip() or None,
                                native_text_preview=text.strip()[:500] or None,
                                native_text_blocks=native_blocks,
                                table_candidates=table_candidates,
                                page_image_path=str(rendered_page.image_path)
                                if rendered_page
                                else None,
                                quality_flags=flags,
                            )
                        )
                    except Exception as page_exc:
                        pages.append(
                            LocalPageForensics(
                                page_index=page_index,
                                width_points=float(page.width),
                                height_points=float(page.height),
                                page_image_path=str(rendered_page.image_path)
                                if rendered_page
                                else None,
                                native_text_length=None,
                                quality_flags=["native_text_extract_failed"],
                            )
                        )
                        errors.append(
                            f"page_{page_index + 1}_native_text_extract_failed: {page_exc}"
                        )
        except Exception as exc:
            return LocalPdfForensics(
                pdf_path=str(path),
                exists=True,
                errors=[*errors, f"pdf_open_failed: {exc}"],
            )

        return LocalPdfForensics(
            pdf_path=str(path.resolve()),
            exists=True,
            page_count=page_count,
            pages=pages,
            errors=errors,
        )

    @staticmethod
    def _visible(word: dict, page) -> bool:
        """A glyph whose centre lies inside the page's visible box.

        Pages of a spread laid out in one content stream carry the neighbour's
        glyphs at coordinates outside the page box; they are not this page's
        text and must not count against its OCR coverage.
        """
        try:
            x0, top, x1, bottom = (float(v) for v in page.bbox)
            cx = (float(word["x0"]) + float(word["x1"])) / 2
            cy = (float(word["top"]) + float(word["bottom"])) / 2
        except (KeyError, TypeError, ValueError):
            return True
        return x0 - 1 <= cx <= x1 + 1 and top - 1 <= cy <= bottom + 1

    @staticmethod
    def _table_candidates(
        page,
        coordinate_system_id: str,
        errors: list[str],
        page_index: int,
        *,
        page_width: float,
        page_height: float,
    ) -> list[LocalTableCandidate]:
        candidates: list[LocalTableCandidate] = []
        try:
            for candidate_index, table in enumerate(page.find_tables() or []):
                x0, top, x1, bottom = table.bbox
                candidate_bbox, bbox_status = normalize_bbox_to_page(
                    BoundingBox(
                        x0=float(x0),
                        y0=float(top),
                        x1=float(x1),
                        y1=float(bottom),
                        unit="points",
                        origin="top_left",
                        coordinate_system_id=coordinate_system_id,
                    ),
                    page_width=page_width,
                    page_height=page_height,
                )
                if candidate_bbox is None:
                    errors.append(
                        f"page_{page_index + 1}_table_candidate_{candidate_index + 1}_{bbox_status}"
                    )
                    continue
                extracted = table.extract() or []
                rows = getattr(table, "rows", []) or []
                cells: list[TableCellObservation] = []
                quality_flags = []
                if bbox_status == "clipped_to_page":
                    quality_flags.append("local_table_bbox_clipped_to_page")
                for row_index, row in enumerate(extracted):
                    raw_cells = (
                        getattr(rows[row_index], "cells", [])
                        if row_index < len(rows)
                        else []
                    )
                    for col_index, text in enumerate(row or []):
                        raw_bbox = (
                            raw_cells[col_index] if col_index < len(raw_cells) else None
                        )
                        bbox = None
                        if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
                            bbox, cell_status = normalize_bbox_to_page(
                                BoundingBox(
                                    x0=float(raw_bbox[0]),
                                    y0=float(raw_bbox[1]),
                                    x1=float(raw_bbox[2]),
                                    y1=float(raw_bbox[3]),
                                    unit="points",
                                    origin="top_left",
                                    coordinate_system_id=coordinate_system_id,
                                ),
                                page_width=page_width,
                                page_height=page_height,
                            )
                            if cell_status != "unchanged":
                                quality_flags.append(
                                    f"local_table_cell_bbox_{cell_status}"
                                )
                        cells.append(
                            TableCellObservation(
                                row_index=row_index,
                                col_index=col_index,
                                text=str(text or "").strip(),
                                bbox=bbox,
                            )
                        )
                candidates.append(
                    LocalTableCandidate(
                        candidate_id=f"local-table-p{page_index + 1:04d}-{candidate_index + 1:04d}",
                        bbox=candidate_bbox,
                        row_count=len(extracted),
                        column_count=max((len(row) for row in extracted), default=0),
                        cells=cells,
                        quality_flags=list(dict.fromkeys(quality_flags)),
                    )
                )
        except Exception as exc:
            errors.append(f"page_{page_index + 1}_table_detection_failed: {exc}")
        return candidates
