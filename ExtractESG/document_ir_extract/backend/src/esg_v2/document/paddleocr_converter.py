from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from esg_v2.document.artifact_loader import OcrPageArtifact, OcrRunArtifact
from esg_v2.document.contracts import (
    ArtifactRef,
    BlockIR,
    BoundingBox,
    CellIR,
    CoordinateSystem,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    LayoutObjectIR,
    PageIR,
    SectionIR,
    SourceTrace,
    TableIR,
)
from esg_v2.document.layout_extractor import PaddleLayoutExtractor
from esg_v2.document.geometry import bbox_area_ratio, bbox_containment, bbox_iou, image_bbox_from_path, image_bbox_to_canonical
from esg_v2.document.html_table_parser import parse_html_table
from esg_v2.document.page_renderer import RenderedDocument
from esg_v2.document.provenance import durable_remote_reference, sanitize_ir_payload
from esg_v2.utils.hashing import sha256_file


class PaddleOcrDocumentConverter:
    def __init__(self, layout_extractor: PaddleLayoutExtractor | None = None):
        self.layout_extractor = layout_extractor or PaddleLayoutExtractor()

    def convert(
        self,
        artifact: OcrRunArtifact,
        *,
        run_id: str,
        pdf_path: str | None = None,
        rendered: RenderedDocument | None = None,
        ir_revision: int = 1,
        parent_ir_run_id: str | None = None,
    ) -> DocumentIR:
        source_sha = sha256_file(pdf_path) if pdf_path and Path(pdf_path).exists() else None
        metadata = DocumentIRMetadata(
            run_id=run_id,
            ocr_run_id=artifact.run_id,
            ir_revision=ir_revision,
            parent_ir_run_id=parent_ir_run_id,
            source_pdf_path=pdf_path,
            source_pdf_sha256=source_sha,
            ocr_manifest_path=str(artifact.manifest_path),
            raw_jsonl_path=str(artifact.raw_jsonl_path),
            source_artifacts={
                "ocr_root_dir": str(artifact.root_dir),
                "artifact_ids": [
                    *(["artifact-source-pdf"] if source_sha else []),
                    "artifact-ocr-raw",
                ],
                "ocr_manifest": sanitize_ir_payload(artifact.manifest),
            },
        )
        rendered_by_page = {page.page_index: page for page in (rendered.pages if rendered else [])}
        pages: list[PageIR] = []
        blocks: list[BlockIR] = []
        tables: list[TableIR] = []
        figures: list[FigureIR] = []
        layout_objects: list[LayoutObjectIR] = []
        coordinate_systems: list[CoordinateSystem] = []
        artifacts: list[ArtifactRef] = []

        if pdf_path and Path(pdf_path).exists():
            artifacts.append(
                ArtifactRef(
                    artifact_id="artifact-source-pdf",
                    kind="source_pdf",
                    path=str(Path(pdf_path).resolve()),
                    media_type="application/pdf",
                    sha256=source_sha,
                    source="upstream",
                )
            )
        artifacts.append(
            ArtifactRef(
                artifact_id="artifact-ocr-raw",
                kind="ocr_raw",
                path=str(artifact.raw_jsonl_path.resolve()),
                media_type="application/x-ndjson",
                sha256=sha256_file(artifact.raw_jsonl_path),
                source="PaddleOCRVLApiAdapter",
            )
        )

        for page_artifact in artifact.pages:
            rendered_page = rendered_by_page.get(page_artifact.page_index)
            page, page_blocks, page_tables, page_figures, page_layout, page_systems, page_artifacts = self._convert_page(
                artifact, page_artifact, rendered_page
            )
            pages.append(page)
            blocks.extend(page_blocks)
            tables.extend(page_tables)
            figures.extend(page_figures)
            layout_objects.extend(page_layout)
            coordinate_systems.extend(page_systems)
            artifacts.extend(page_artifacts)

        sections = self._build_sections(blocks, len(pages))
        self._assign_sections(blocks, sections)
        return DocumentIR(
            metadata=metadata,
            artifacts=self._dedupe_artifacts(artifacts),
            coordinate_systems=coordinate_systems,
            pages=pages,
            sections=sections,
            blocks=blocks,
            layout_objects=layout_objects,
            tables=tables,
            figures=figures,
        )

    def _convert_page(self, artifact, page_artifact, rendered_page):
        page_id = f"page-{page_artifact.page_index + 1:04d}"
        source = self._source_trace(artifact, page_artifact)
        systems: list[CoordinateSystem] = []
        page_artifacts: list[ArtifactRef] = []
        canonical = None
        if rendered_page:
            canonical = rendered_page.canonical_coordinate_system
            systems.extend([canonical, rendered_page.image_coordinate_system])
            page_artifacts.append(rendered_page.artifact)

        layout_result = self.layout_extractor.extract(
            page_artifact,
            canonical_system=canonical,
            source_trace=source,
        )
        if layout_result.parser_coordinate_system:
            systems.append(layout_result.parser_coordinate_system)
            if canonical is None:
                canonical = layout_result.parser_coordinate_system

        blocks: list[BlockIR] = []
        tables: list[TableIR] = []
        figures: list[FigureIR] = []
        seen_text: list[str] = []
        bound_image_names: set[str] = set()
        embedded_table_images: set[str] = set()
        image_candidates = self._image_candidates(
            page_artifact,
            artifact,
            layout_result.parser_coordinate_system,
            canonical,
        )
        for layout in layout_result.objects:
            label = layout.label.lower().replace("-", "_").replace(" ", "_")
            normalized = self._normalize_text(layout.text)
            if normalized:
                seen_text.append(normalized)
            if self._is_figure_label(label):
                figure, bound_name = self._figure_from_layout(
                    layout,
                    len(figures),
                    source,
                    image_candidates=image_candidates,
                    already_bound=bound_image_names,
                    page_width=canonical.width if canonical else None,
                    page_height=canonical.height if canonical else None,
                )
                if bound_name:
                    bound_image_names.add(bound_name)
                layout.figure_id = figure.figure_id
                figures.append(figure)
                continue
            block = self._block_from_layout(layout, len(blocks), source)
            blocks.append(block)
            layout.block_id = block.block_id
            if block.block_type == "table_markdown":
                table = self._table_from_markup(
                    markdown=block.markdown or block.text,
                    page_index=page_artifact.page_index,
                    order=len(tables),
                    block_id=block.block_id,
                    source=block.source_trace.model_copy(deep=True),
                    bbox=block.bbox,
                )
                block.table_id = table.table_id
                layout.table_id = table.table_id
                tables.append(table)
                parsed = parse_html_table(block.markdown or block.text)
                if parsed:
                    embedded_table_images.update(Path(item).name for item in parsed.image_sources)

        for spec in self._markdown_blocks(page_artifact.markdown_text):
            normalized = self._normalize_text(str(spec["text"]))
            if normalized and self._text_already_seen(normalized, seen_text):
                continue
            block_id = f"blk-{page_artifact.page_index + 1:04d}-{len(blocks) + 1:04d}"
            block = BlockIR(
                block_id=block_id,
                page_index=page_artifact.page_index,
                order=len(blocks),
                block_type=spec["type"],
                text=str(spec["text"]),
                markdown=str(spec.get("markdown") or "") or None,
                heading_level=spec.get("heading_level"),
                quality_flags=["markdown_fallback_without_layout_geometry"],
                source_trace=source,
            )
            blocks.append(block)
            if normalized:
                seen_text.append(normalized)
            if block.block_type == "table_markdown":
                table = self._table_from_markup(
                    markdown=block.markdown or block.text,
                    page_index=page_artifact.page_index,
                    order=len(tables),
                    block_id=block.block_id,
                    source=block.source_trace.model_copy(deep=True),
                    bbox=None,
                )
                block.table_id = table.table_id
                tables.append(table)
                parsed = parse_html_table(block.markdown or block.text)
                if parsed:
                    embedded_table_images.update(Path(item).name for item in parsed.image_sources)

        existing_figure_images = {figure.image_path for figure in figures if figure.image_path}
        for image_path in sorted(page_artifact.markdown_images.keys()):
            if Path(image_path).name in embedded_table_images or image_path in bound_image_names:
                continue
            resolved = str(
                page_artifact.local_markdown_images.get(
                    image_path,
                    (artifact.root_dir / "images" / image_path).resolve(),
                )
            )
            if resolved in existing_figure_images:
                continue
            image_bbox = image_candidates.get(image_path, {}).get("canonical_bbox")
            area_ratio = bbox_area_ratio(
                image_bbox,
                page_width=canonical.width if canonical else None,
                page_height=canonical.height if canonical else None,
            )
            figures.append(
                FigureIR(
                    figure_id=f"fig-{page_artifact.page_index + 1:04d}-{len(figures) + 1:04d}",
                    page_index=page_artifact.page_index,
                    order=len(figures),
                    image_path=resolved,
                    bbox=image_bbox,
                    visual_type="icon" if 0 < area_ratio < 0.02 else "unknown",
                    quality_flags=["image_bbox_from_filename"] if image_bbox else ["markdown_image_without_layout_geometry"],
                    source_trace=source,
                )
            )

        self._classify_embedded_figures(
            figures,
            tables,
            page_width=canonical.width if canonical else None,
            page_height=canonical.height if canonical else None,
        )

        markdown_artifact_id = None
        if page_artifact.markdown_path:
            markdown_artifact_id = f"artifact-page-markdown-p{page_artifact.page_index + 1:04d}"
            page_artifacts.append(
                ArtifactRef(
                    artifact_id=markdown_artifact_id,
                    kind="page_markdown",
                    path=str(page_artifact.markdown_path.resolve()),
                    media_type="text/markdown",
                    page_index=page_artifact.page_index,
                    sha256=sha256_file(page_artifact.markdown_path),
                    source="PaddleOCRVLApiAdapter",
                )
            )
        page = PageIR(
            page_id=page_id,
            page_index=page_artifact.page_index,
            page_number=page_artifact.page_index + 1,
            width=canonical.width if canonical else None,
            height=canonical.height if canonical else None,
            markdown_path=str(page_artifact.markdown_path) if page_artifact.markdown_path else None,
            page_image_path=str(rendered_page.image_path) if rendered_page else None,
            text=page_artifact.markdown_text,
            text_length=len(page_artifact.markdown_text.strip()),
            coordinate_system_ids=[system.coordinate_system_id for system in systems],
            layout_object_ids=[item.layout_object_id for item in layout_result.objects],
            block_ids=[block.block_id for block in blocks],
            table_ids=[table.table_id for table in tables],
            figure_ids=[figure.figure_id for figure in figures],
            image_paths=[
                str(page_artifact.local_markdown_images.get(name, (artifact.root_dir / "images" / name).resolve()))
                for name in sorted(page_artifact.markdown_images)
            ],
            output_image_paths=[
                str(
                    page_artifact.local_output_images.get(
                        name,
                        (artifact.root_dir / "output_images" / f"{name}_{page_artifact.page_index:04d}.jpg").resolve(),
                    )
                )
                for name in sorted(page_artifact.output_images)
            ],
            remote_image_urls=list(
                dict.fromkeys(
                    reference
                    for value in [*page_artifact.output_images.values(), *page_artifact.markdown_images.values()]
                    if (reference := durable_remote_reference(value))
                )
            ),
            quality_flags=list(layout_result.quality_flags),
            source_trace=source.model_copy(
                update={
                    "artifact_ids": list(
                        dict.fromkeys([*source.artifact_ids, *(item.artifact_id for item in page_artifacts)])
                    )
                }
            ),
        )
        return page, blocks, tables, figures, layout_result.objects, systems, page_artifacts

    def _block_from_layout(self, layout: LayoutObjectIR, order: int, source: SourceTrace) -> BlockIR:
        block_type, heading_level = self._map_block_type(layout.label)
        text = layout.text
        quality_flags = list(layout.quality_flags)
        if block_type == "heading" and re.fullmatch(r"\d{1,3}", text.strip()):
            block_type = "paragraph"
            heading_level = None
            quality_flags.append("numeric_process_step_label")
        block_id = f"blk-{layout.page_index + 1:04d}-{order + 1:04d}"
        return BlockIR(
            block_id=block_id,
            page_index=layout.page_index,
            order=order,
            block_type=block_type,
            text=text,
            markdown=text if block_type == "table_markdown" else None,
            heading_level=heading_level,
            bbox=layout.bbox,
            polygon=layout.polygon,
            layout_object_id=layout.layout_object_id,
            quality_flags=quality_flags,
            source_trace=source.model_copy(update={"raw_object_path": layout.source_trace.raw_object_path}),
        )

    def _figure_from_layout(
        self,
        layout,
        order,
        source,
        *,
        image_candidates,
        already_bound,
        page_width,
        page_height,
    ):
        label = layout.label.lower().replace("-", "_").replace(" ", "_")
        raw_bbox = self._raw_bbox(layout.raw_payload)
        ranked: list[tuple[float, str, dict[str, object]]] = []
        for name, candidate in image_candidates.items():
            if name in already_bound:
                continue
            score = bbox_iou(raw_bbox, candidate.get("raw_bbox"))
            basename = Path(name).name.lower()
            if "header_image" in label and "header_image" in basename:
                score += 1.0
            elif label in {"image", "figure"} and "image_box" in basename:
                score += 0.05
            ranked.append((score, name, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = ranked[0] if ranked and ranked[0][0] >= 0.2 else None
        bound_name = selected[1] if selected else None
        image_path = str(selected[2]["resolved_path"]) if selected else None
        flags = list(layout.quality_flags)
        if selected:
            flags.append("image_bound_by_bbox_iou")
        elif image_candidates:
            flags.append("image_binding_unresolved")
        area_ratio = bbox_area_ratio(layout.bbox, page_width=page_width, page_height=page_height)
        visual_type = self._visual_type(label, area_ratio)
        return FigureIR(
            figure_id=f"fig-{layout.page_index + 1:04d}-{order + 1:04d}",
            page_index=layout.page_index,
            order=order,
            image_path=image_path,
            bbox=layout.bbox,
            visual_type=visual_type,
            quality_flags=flags,
            source_trace=source.model_copy(update={"raw_object_path": layout.source_trace.raw_object_path}),
        ), bound_name

    @staticmethod
    def _image_candidates(page_artifact, artifact, parser_system, canonical_system):
        candidates: dict[str, dict[str, object]] = {}
        for name in page_artifact.markdown_images:
            candidates[name] = {
                "resolved_path": str(
                    page_artifact.local_markdown_images.get(name, (artifact.root_dir / "images" / name).resolve())
                ),
                "raw_bbox": image_bbox_from_path(
                    name,
                    coordinate_system_id=parser_system.coordinate_system_id if parser_system else None,
                ),
                "canonical_bbox": image_bbox_to_canonical(name, parser_system, canonical_system),
            }
        return candidates

    @staticmethod
    def _raw_bbox(payload) -> BoundingBox | None:
        value = payload.get("block_bbox") or payload.get("blockBbox") or payload.get("bbox")
        if isinstance(value, list) and len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
            return BoundingBox(
                x0=float(value[0]),
                y0=float(value[1]),
                x1=float(value[2]),
                y1=float(value[3]),
                unit="pixels",
                origin="top_left",
            )
        return None

    @staticmethod
    def _visual_type(label: str, area_ratio: float) -> str:
        if "header" in label or "footer" in label:
            return "decoration"
        if "chart" in label:
            return "chart"
        if "diagram" in label:
            return "diagram"
        if "photo" in label:
            return "photo"
        if 0 < area_ratio < 0.02:
            return "icon"
        if area_ratio > 0.3:
            return "composite"
        return "unknown"

    @staticmethod
    def _classify_embedded_figures(figures, tables, *, page_width, page_height) -> None:
        for figure in figures:
            if figure.visual_type == "decoration":
                continue
            if any(bbox_containment(figure.bbox, table.bbox) >= 0.8 for table in tables):
                figure.visual_type = "icon"
                if "embedded_in_table" not in figure.quality_flags:
                    figure.quality_flags.append("embedded_in_table")
                continue
            area_ratio = bbox_area_ratio(figure.bbox, page_width=page_width, page_height=page_height)
            if 0 < area_ratio < 0.02 and figure.visual_type == "unknown":
                figure.visual_type = "icon"
                if "small_visual_region" not in figure.quality_flags:
                    figure.quality_flags.append("small_visual_region")

    @staticmethod
    def _source_trace(artifact: OcrRunArtifact, page_artifact: OcrPageArtifact) -> SourceTrace:
        artifact_ids = ["artifact-ocr-raw"]
        if page_artifact.markdown_path:
            artifact_ids.append(f"artifact-page-markdown-p{page_artifact.page_index + 1:04d}")
        return SourceTrace(
            parser="paddleocr-vl",
            parser_version=str(artifact.manifest.get("model") or ""),
            artifact_path=str(artifact.root_dir),
            artifact_ids=artifact_ids,
            raw_jsonl_path=str(artifact.raw_jsonl_path),
            raw_line_number=page_artifact.raw_line_number,
            raw_result_index=page_artifact.raw_result_index,
            page_markdown_path=str(page_artifact.markdown_path) if page_artifact.markdown_path else None,
        )

    @staticmethod
    def _map_block_type(label: str):
        normalized = label.lower().replace("-", "_").replace(" ", "_")
        if normalized in {"title", "heading", "section_title", "paragraph_title", "doc_title"}:
            level = 1 if normalized in {"title", "doc_title"} else 2
            return "heading", level
        if "table" in normalized:
            return "table_markdown", None
        if normalized in {"list", "list_item"}:
            return "list", None
        if normalized in {"caption", "figure_caption", "table_caption"}:
            return "caption", None
        if normalized in {"footnote", "note"}:
            return "footnote", None
        if normalized == "vision_footnote":
            return "paragraph", None
        if normalized in {"header", "page_header"}:
            return "header", None
        if normalized in {"footer", "page_footer", "page_number"}:
            return "footer", None
        if normalized in {"formula", "equation"}:
            return "formula", None
        if normalized == "seal":
            return "seal", None
        if normalized in {"text", "paragraph", "abstract", "content"}:
            return "paragraph", None
        return "unknown", None

    @staticmethod
    def _is_figure_label(label: str) -> bool:
        return any(token in label for token in ("figure", "image", "chart", "diagram", "photo")) and "caption" not in label

    def _markdown_blocks(self, markdown: str) -> Iterable[dict[str, object]]:
        lines = markdown.splitlines()
        buffer: list[str] = []
        i = 0

        def flush_paragraph():
            nonlocal buffer
            if not buffer:
                return None
            text = "\n".join(buffer).strip()
            buffer = []
            if not text or re.fullmatch(r"!\[[^]]*]\([^)]+\)", text):
                return None
            if re.fullmatch(r"<div\b[^>]*>\s*<img\b[^>]*>\s*</div>", text, re.IGNORECASE | re.DOTALL):
                return None
            block_type = "list" if self._looks_like_list(text) else "paragraph"
            return {"type": block_type, "text": text, "markdown": text}

        while i < len(lines):
            line = lines[i].rstrip()
            if not line.strip():
                item = flush_paragraph()
                if item:
                    yield item
                i += 1
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
            if heading:
                item = flush_paragraph()
                if item:
                    yield item
                yield {"type": "heading", "text": heading.group(2).strip(), "markdown": line.strip(), "heading_level": len(heading.group(1))}
                i += 1
                continue
            if re.search(r"<table\b", line, re.IGNORECASE):
                item = flush_paragraph()
                if item:
                    yield item
                table_lines = [line]
                i += 1
                while i < len(lines) and not re.search(r"</table\s*>", table_lines[-1], re.IGNORECASE):
                    table_lines.append(lines[i].rstrip())
                    i += 1
                html_table = "\n".join(table_lines).strip()
                yield {"type": "table_markdown", "text": html_table, "markdown": html_table}
                continue
            if self._is_table_line(line):
                item = flush_paragraph()
                if item:
                    yield item
                table_lines = [line]
                i += 1
                while i < len(lines) and self._is_table_line(lines[i]):
                    table_lines.append(lines[i].rstrip())
                    i += 1
                markdown_table = "\n".join(table_lines).strip()
                yield {"type": "table_markdown", "text": markdown_table, "markdown": markdown_table}
                continue
            buffer.append(line)
            i += 1
        item = flush_paragraph()
        if item:
            yield item

    def _table_from_markup(self, *, markdown, page_index, order, block_id, source, bbox: BoundingBox | None):
        parsed = parse_html_table(markdown)
        if not parsed:
            return self._table_from_markdown(
                markdown=markdown,
                page_index=page_index,
                order=order,
                block_id=block_id,
                source=source,
                bbox=bbox,
            )

        table_id = f"tbl-{page_index + 1:04d}-{order + 1:04d}"
        cells = []
        for parsed_cell in parsed.cells:
            flags = ["contains_embedded_image"] if parsed_cell.image_sources else []
            cells.append(
                CellIR(
                    cell_id=f"{table_id}-r{parsed_cell.row_index + 1:03d}-c{parsed_cell.col_index + 1:03d}",
                    table_id=table_id,
                    page_index=page_index,
                    row_index=parsed_cell.row_index,
                    col_index=parsed_cell.col_index,
                    text=parsed_cell.text,
                    row_span=parsed_cell.row_span,
                    col_span=parsed_cell.col_span,
                    is_header=parsed_cell.is_header,
                    quality_flags=flags,
                    source_trace=source,
                )
            )
        header_rows = sorted({cell.row_index for cell in cells if cell.is_header})
        flags = ["html_table_parsed"]
        if parsed.image_sources:
            flags.append("embedded_table_images_present")
        if bbox is None:
            flags.append("table_geometry_missing")
        return TableIR(
            table_id=table_id,
            page_index=page_index,
            page_indices=[page_index],
            order=order,
            block_id=block_id,
            markdown=markdown,
            row_count=parsed.row_count,
            column_count=parsed.column_count,
            cells=cells,
            header_row_indices=header_rows,
            bbox=bbox,
            quality_flags=flags,
            source_trace=source,
        )

    def _table_from_markdown(self, *, markdown, page_index, order, block_id, source, bbox: BoundingBox | None):
        table_id = f"tbl-{page_index + 1:04d}-{order + 1:04d}"
        rows = [self._split_table_row(line) for line in markdown.splitlines() if self._is_table_line(line)]
        rows = [row for row in rows if row and not self._is_separator_row(row)]
        column_count = max((len(row) for row in rows), default=0)
        cells: list[CellIR] = []
        header_row = rows[0] if rows else []
        for row_index, row in enumerate(rows):
            for col_index in range(column_count):
                text = row[col_index].strip() if col_index < len(row) else ""
                cells.append(
                    CellIR(
                        cell_id=f"{table_id}-r{row_index + 1:03d}-c{col_index + 1:03d}",
                        table_id=table_id,
                        page_index=page_index,
                        row_index=row_index,
                        col_index=col_index,
                        text=text,
                        is_header=row_index == 0,
                        column_header_path=[header_row[col_index].strip()] if row_index > 0 and col_index < len(header_row) and header_row[col_index].strip() else [],
                        row_header_path=[row[0].strip()] if col_index > 0 and row and row[0].strip() else [],
                        source_trace=source,
                    )
                )
        flags = []
        if not rows:
            flags.append("empty_markdown_table")
        if column_count <= 1:
            flags.append("single_column_table_candidate")
        if bbox is None:
            flags.append("table_geometry_missing")
        return TableIR(
            table_id=table_id,
            page_index=page_index,
            page_indices=[page_index],
            order=order,
            block_id=block_id,
            markdown=markdown,
            row_count=len(rows),
            column_count=column_count,
            cells=cells,
            header_row_indices=[0] if rows else [],
            bbox=bbox,
            quality_flags=flags,
            source_trace=source,
        )

    def _build_sections(self, blocks, page_count):
        sections: list[SectionIR] = []
        stack: list[SectionIR] = []
        for block in blocks:
            if block.block_type != "heading":
                continue
            level = block.heading_level or 1
            while stack and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1] if stack else None
            section = SectionIR(
                section_id=f"sec-{len(sections) + 1:04d}",
                title=block.text,
                level=level,
                start_page_index=block.page_index,
                heading_block_id=block.block_id,
                parent_section_id=parent.section_id if parent else None,
            )
            if parent:
                parent.child_section_ids.append(section.section_id)
            sections.append(section)
            stack.append(section)
        last_page = max(page_count - 1, 0)
        for index, section in enumerate(sections):
            end_page = last_page
            for following in sections[index + 1 :]:
                if following.level <= section.level:
                    end_page = following.start_page_index
                    break
            section.end_page_index = max(section.start_page_index, end_page)
        return sections

    @staticmethod
    def _assign_sections(blocks, sections):
        by_heading = {section.heading_block_id: section for section in sections}
        active = None
        for block in blocks:
            if block.block_id in by_heading:
                active = by_heading[block.block_id]
            if active:
                block.section_id = active.section_id
                active.block_ids.append(block.block_id)

    @staticmethod
    def _dedupe_artifacts(artifacts):
        return list({artifact.artifact_id: artifact for artifact in artifacts}.values())

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    @staticmethod
    def _text_already_seen(candidate: str, existing: list[str]) -> bool:
        for value in existing:
            if candidate == value:
                return True
            if min(len(candidate), len(value)) >= 40:
                shorter, longer = sorted((candidate, value), key=len)
                if shorter in longer and len(shorter) / len(longer) >= 0.72:
                    return True
                if SequenceMatcher(None, candidate, value).ratio() >= 0.94:
                    return True
        return False

    @staticmethod
    def _is_table_line(line: str) -> bool:
        stripped = line.strip()
        if re.search(r"<[^>]+>", stripped):
            return False
        if stripped.count("|") < 2:
            return False
        return stripped.startswith("|") or stripped.endswith("|") or bool(re.search(r"\s\|\s", stripped))

    @staticmethod
    def _split_table_row(line: str) -> list[str]:
        stripped = line.strip().strip("|")
        return [part.strip() for part in stripped.split("|")]

    @staticmethod
    def _is_separator_row(row: list[str]) -> bool:
        return bool(row) and all(re.fullmatch(r":?-{2,}:?", cell.strip() or "-") for cell in row)

    @staticmethod
    def _looks_like_list(text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        markers = sum(1 for line in lines if re.match(r"^([-*+]|\d+[.)])\s+", line))
        return bool(lines) and markers >= max(1, len(lines) // 2)
