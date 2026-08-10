from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from esg_v2.document.contracts import BlockIR, DocumentIR, LogicalTableIR, TableIR
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.evidence.contracts import EvidenceAtom, EvidenceBuildRequest, EvidenceBuildResult, EvidenceLocation
from esg_v2.evidence.package import EvidencePackageWriter
from esg_v2.storage.package_layout import package_dir


class EvidenceAdmissionError(ValueError):
    pass


class EvidenceInventoryBuilder:
    """Builds a lossless, retrieval-oriented inventory without ESG interpretation."""

    def __init__(self, document_ir_root: Path, evidence_output_root: Path):
        self.document_ir_root = document_ir_root
        self.evidence_output_root = evidence_output_root

    def build(self, request: EvidenceBuildRequest, *, log=lambda _message: None) -> EvidenceBuildResult:
        ir_root = package_dir(self.document_ir_root, request.ir_run_id)
        reader = DocumentIrPackageReader(ir_root)
        integrity = reader.validate_integrity()
        if not integrity.valid:
            raise EvidenceAdmissionError("Document IR package integrity failed: " + "; ".join(integrity.errors))
        validation = reader.read_json("validation_report", "validation_report.json")
        can_build = bool((validation.get("checks") or {}).get("can_build_evidence"))
        if not can_build or not bool(reader.manifest.get("can_build_evidence")):
            raise EvidenceAdmissionError(
                f"Document IR {request.ir_run_id} is not admitted: can_build_evidence=false"
            )
        document = reader.load_document()
        log("Document IR admission passed; building deterministic evidence atoms")
        atoms = self._atoms(document)
        run_id = request.run_id or self._new_run_id()
        output_dir = package_dir(self.evidence_output_root, run_id)
        paths = EvidencePackageWriter(output_dir).write(
            run_id=run_id,
            source_manifest=reader.manifest,
            atoms=atoms,
        )
        counts: dict[str, int] = defaultdict(int)
        for atom in atoms:
            counts[atom.atom_type] += 1
        log(f"Evidence inventory complete: {len(atoms)} atoms")
        return EvidenceBuildResult(
            run_id=run_id,
            ir_run_id=request.ir_run_id,
            ir_revision=int(reader.manifest.get("ir_revision") or 1),
            output_dir=output_dir,
            manifest_path=paths["manifest"],
            atom_count=len(atoms),
            counts_by_type=dict(sorted(counts.items())),
        )

    def find_existing(self, ir_run_id: str) -> Path | None:
        if not self.evidence_output_root.exists():
            return None
        candidates: list[Path] = []
        for manifest_path in self.evidence_output_root.glob("*/manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("source_ir_run_id") == ir_run_id and payload.get("can_run_targeted_recall") is True:
                candidates.append(manifest_path.parent)
        return sorted(candidates)[-1] if candidates else None

    def _atoms(self, document: DocumentIR) -> list[EvidenceAtom]:
        page_map = {page.page_index: page for page in document.pages}
        section_map = {section.section_id: section for section in document.sections}
        section_paths = {
            section_id: self._section_path(section_id, section_map)
            for section_id in section_map
        }
        block_map = {block.block_id: block for block in document.blocks}
        atoms: list[EvidenceAtom] = []

        for block in document.blocks:
            if not block.text.strip():
                continue
            atom_type = {
                "heading": "heading_atom",
                "paragraph": "paragraph_atom",
                "list": "list_atom",
                "caption": "caption_atom",
                "footnote": "footnote_atom",
            }.get(block.block_type, "other_atom")
            atoms.append(
                self._atom(
                    atom_type=atom_type,
                    source_key=block.block_id,
                    source_text=block.text,
                    context=self._block_context(block, section_paths),
                    page_indices=[block.page_index],
                    page_map=page_map,
                    section_id=block.section_id,
                    section_paths=section_paths,
                    source_node_ids=[block.block_id],
                    source_block_ids=[block.block_id],
                    source_table_ids=[block.table_id] if block.table_id else [],
                    source_figure_ids=[block.figure_id] if block.figure_id else [],
                    bbox=block.bbox.model_dump(mode="json") if block.bbox else None,
                    quality_flags=block.quality_flags,
                    source_trace=block.source_trace.model_dump(mode="json"),
                )
            )
            if block.block_type == "list":
                items = [self._clean_list_item(line) for line in block.text.splitlines()]
                for index, item in enumerate(value for value in items if value):
                    atoms.append(
                        self._atom(
                            atom_type="list_item_atom",
                            source_key=f"{block.block_id}:item:{index}",
                            source_text=item,
                            context=self._block_context(block, section_paths),
                            page_indices=[block.page_index],
                            page_map=page_map,
                            section_id=block.section_id,
                            section_paths=section_paths,
                            source_node_ids=[block.block_id],
                            source_block_ids=[block.block_id],
                            bbox=block.bbox.model_dump(mode="json") if block.bbox else None,
                            quality_flags=block.quality_flags,
                            source_trace=block.source_trace.model_dump(mode="json"),
                        )
                    )

        for table in document.tables:
            atoms.extend(self._physical_table_atoms(table, page_map, block_map, section_paths))
        for table in document.logical_tables:
            atoms.extend(self._logical_table_atoms(table, page_map, document.tables, block_map, section_paths))
        for figure in document.figures:
            text_parts = [figure.caption or "", *figure.legend_text]
            if figure.chart_spec:
                text_parts.extend([figure.chart_spec.title or "", *figure.chart_spec.categories])
                for series in figure.chart_spec.series:
                    text_parts.append(series.name)
                    text_parts.extend(
                        f"{point.category}: {point.display_value or point.value}"
                        for point in series.points
                    )
            source_text = "\n".join(part for part in text_parts if str(part).strip()).strip()
            if not source_text:
                continue
            related_blocks = [block_map[item] for item in figure.element_block_ids if item in block_map]
            section_id = next((block.section_id for block in related_blocks if block.section_id), None)
            atoms.append(
                self._atom(
                    atom_type="figure_atom",
                    source_key=figure.figure_id,
                    source_text=source_text,
                    context=" | ".join(section_paths.get(section_id, [])),
                    page_indices=[figure.page_index],
                    page_map=page_map,
                    section_id=section_id,
                    section_paths=section_paths,
                    source_node_ids=[figure.figure_id, *figure.element_block_ids],
                    source_block_ids=figure.element_block_ids,
                    source_figure_ids=[figure.figure_id],
                    bbox=figure.bbox.model_dump(mode="json") if figure.bbox else None,
                    quality_flags=figure.quality_flags,
                    source_trace=figure.source_trace.model_dump(mode="json"),
                )
            )

        atoms.extend(self._index_atoms(document, page_map, section_paths))
        return self._deduplicate(atoms)

    def _physical_table_atoms(
        self,
        table: TableIR,
        page_map: dict[int, Any],
        block_map: dict[str, BlockIR],
        section_paths: dict[str, list[str]],
    ) -> list[EvidenceAtom]:
        atoms: list[EvidenceAtom] = []
        section_id = block_map.get(table.block_id).section_id if table.block_id in block_map else None
        context = " | ".join([*section_paths.get(section_id, []), table.caption or ""])
        page_indices = table.page_indices or [table.page_index]
        cells_by_row: dict[int, list[Any]] = defaultdict(list)
        for cell in table.cells:
            cells_by_row[cell.row_index].append(cell)
            if not cell.text.strip():
                continue
            header_paths = [*cell.column_header_path, *cell.row_header_path]
            atoms.append(
                self._atom(
                    atom_type="table_cell_atom",
                    source_key=cell.cell_id,
                    source_text=cell.text,
                    context=" | ".join([context, *header_paths]),
                    page_indices=[cell.page_index],
                    page_map=page_map,
                    section_id=section_id,
                    section_paths=section_paths,
                    source_node_ids=[table.table_id, cell.cell_id],
                    source_table_ids=[table.table_id],
                    source_cell_ids=[cell.cell_id],
                    bbox=cell.bbox.model_dump(mode="json") if cell.bbox else None,
                    row_index=cell.row_index,
                    column_index=cell.col_index,
                    header_paths=header_paths,
                    unit_hint=cell.unit_hint,
                    quality_flags=[*table.quality_flags, *cell.quality_flags],
                    source_trace=cell.source_trace.model_dump(mode="json"),
                )
            )
        for row_index, cells in sorted(cells_by_row.items()):
            ordered = sorted(cells, key=lambda item: item.col_index)
            row_text = " | ".join(cell.text for cell in ordered if cell.text.strip())
            if not row_text:
                continue
            headers = self._unique(
                header for cell in ordered for header in [*cell.column_header_path, *cell.row_header_path]
            )
            atoms.append(
                self._atom(
                    atom_type="table_row_atom",
                    source_key=f"{table.table_id}:row:{row_index}",
                    source_text=row_text,
                    context=" | ".join([context, *headers]),
                    page_indices=self._unique_int(cell.page_index for cell in ordered),
                    page_map=page_map,
                    section_id=section_id,
                    section_paths=section_paths,
                    source_node_ids=[table.table_id, *(cell.cell_id for cell in ordered)],
                    source_table_ids=[table.table_id],
                    source_cell_ids=[cell.cell_id for cell in ordered],
                    row_index=row_index,
                    header_paths=headers,
                    quality_flags=self._unique([*table.quality_flags, *(flag for cell in ordered for flag in cell.quality_flags)]),
                    source_trace=table.source_trace.model_dump(mode="json"),
                )
            )
        region_text = table.markdown.strip() or "\n".join(
            " | ".join(cell.text for cell in sorted(cells, key=lambda item: item.col_index) if cell.text.strip())
            for _, cells in sorted(cells_by_row.items())
        )
        if region_text.strip():
            atoms.append(
                self._atom(
                    atom_type="table_region_atom",
                    source_key=table.table_id,
                    source_text=region_text,
                    context=context,
                    page_indices=page_indices,
                    page_map=page_map,
                    section_id=section_id,
                    section_paths=section_paths,
                    source_node_ids=[table.table_id, *(cell.cell_id for cell in table.cells)],
                    source_table_ids=[table.table_id],
                    source_cell_ids=[cell.cell_id for cell in table.cells],
                    bbox=table.bbox.model_dump(mode="json") if table.bbox else None,
                    quality_flags=table.quality_flags,
                    source_trace=table.source_trace.model_dump(mode="json"),
                )
            )
        return atoms

    def _logical_table_atoms(
        self,
        table: LogicalTableIR,
        page_map: dict[int, Any],
        physical_tables: list[TableIR],
        block_map: dict[str, BlockIR],
        section_paths: dict[str, list[str]],
    ) -> list[EvidenceAtom]:
        physical = {item.table_id: item for item in physical_tables}
        section_id = next(
            (
                block_map[item.block_id].section_id
                for table_id in table.source_table_ids
                if (item := physical.get(table_id)) and item.block_id in block_map
            ),
            None,
        )
        context = " | ".join(section_paths.get(section_id, []))
        cells_by_row: dict[int, list[Any]] = defaultdict(list)
        for cell in table.cells:
            cells_by_row[cell.row_index].append(cell)
        atoms: list[EvidenceAtom] = []
        for row_index, cells in sorted(cells_by_row.items()):
            ordered = sorted(cells, key=lambda item: item.col_index)
            row_text = " | ".join(cell.text for cell in ordered if cell.text.strip())
            if not row_text:
                continue
            source_cell_ids = self._unique(cell_id for cell in ordered for cell_id in cell.source_cell_ids)
            atoms.append(
                self._atom(
                    atom_type="logical_table_row_atom",
                    source_key=f"{table.logical_table_id}:row:{row_index}",
                    source_text=row_text,
                    context=context,
                    page_indices=table.page_indices,
                    page_map=page_map,
                    section_id=section_id,
                    section_paths=section_paths,
                    source_node_ids=[table.logical_table_id, *table.source_table_ids, *source_cell_ids],
                    source_table_ids=table.source_table_ids,
                    source_cell_ids=source_cell_ids,
                    logical_table_id=table.logical_table_id,
                    row_index=row_index,
                    quality_flags=table.quality_flags,
                    source_trace=table.source_trace.model_dump(mode="json"),
                )
            )
        region_text = "\n".join(atom.source_text for atom in atoms)
        if region_text:
            atoms.append(
                self._atom(
                    atom_type="logical_table_region_atom",
                    source_key=table.logical_table_id,
                    source_text=region_text,
                    context=context,
                    page_indices=table.page_indices,
                    page_map=page_map,
                    section_id=section_id,
                    section_paths=section_paths,
                    source_node_ids=[table.logical_table_id, *table.source_table_ids],
                    source_table_ids=table.source_table_ids,
                    logical_table_id=table.logical_table_id,
                    quality_flags=table.quality_flags,
                    source_trace=table.source_trace.model_dump(mode="json"),
                )
            )
        return atoms

    def _index_atoms(
        self,
        document: DocumentIR,
        page_map: dict[int, Any],
        section_paths: dict[str, list[str]],
    ) -> list[EvidenceAtom]:
        markers = ("contents", "index", "目录", "目錄", "索引", "附录", "附錄")
        blocks_by_page: dict[int, list[BlockIR]] = defaultdict(list)
        for block in document.blocks:
            blocks_by_page[block.page_index].append(block)
        atoms = []
        for page_index, blocks in blocks_by_page.items():
            text = "\n".join(block.text for block in sorted(blocks, key=lambda item: item.order) if block.text.strip())
            if not text or not any(marker in text.lower() for marker in markers):
                continue
            section_id = next((block.section_id for block in blocks if block.section_id), None)
            atoms.append(
                self._atom(
                    atom_type="index_atom",
                    source_key=f"page-{page_index + 1:04d}:index",
                    source_text=text,
                    context=" | ".join(section_paths.get(section_id, [])),
                    page_indices=[page_index],
                    page_map=page_map,
                    section_id=section_id,
                    section_paths=section_paths,
                    source_node_ids=[block.block_id for block in blocks],
                    source_block_ids=[block.block_id for block in blocks],
                    quality_flags=self._unique(flag for block in blocks for flag in block.quality_flags),
                    source_trace={"parser": "evidence-index-detector", "source_block_ids": [block.block_id for block in blocks]},
                )
            )
        return atoms

    def _atom(
        self,
        *,
        atom_type: str,
        source_key: str,
        source_text: str,
        context: str,
        page_indices: Iterable[int],
        page_map: dict[int, Any],
        section_id: str | None,
        section_paths: dict[str, list[str]],
        source_node_ids: list[str],
        source_block_ids: list[str] | None = None,
        source_table_ids: list[str] | None = None,
        source_cell_ids: list[str] | None = None,
        source_figure_ids: list[str] | None = None,
        logical_table_id: str | None = None,
        bbox: dict[str, Any] | None = None,
        row_index: int | None = None,
        column_index: int | None = None,
        header_paths: list[str] | None = None,
        unit_hint: str | None = None,
        quality_flags: list[str] | None = None,
        source_trace: dict[str, Any] | None = None,
    ) -> EvidenceAtom:
        pages = self._unique_int(page_indices)
        normalized_source = self._normalize_text(source_text)
        search_text = self._normalize_text("\n".join(part for part in [context, source_text] if part))
        digest = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
        atom_key = hashlib.sha256(f"{atom_type}|{source_key}|{digest}".encode("utf-8")).hexdigest()[:16]
        printed = [
            str(page_map[page].printed_page_label)
            for page in pages
            if page in page_map and page_map[page].printed_page_label
        ]
        return EvidenceAtom(
            atom_id=f"atom-{atom_type.removesuffix('_atom').replace('_', '-')}-{atom_key}",
            atom_type=atom_type,
            source_text=source_text.strip(),
            search_text=search_text,
            location=EvidenceLocation(
                page_indices=pages,
                page_numbers=[page_map[page].page_number for page in pages if page in page_map],
                printed_page_labels=printed,
                section_id=section_id,
                section_path=section_paths.get(section_id, []),
                bbox=bbox,
            ),
            source_node_ids=self._unique(source_node_ids),
            source_block_ids=self._unique(source_block_ids or []),
            source_table_ids=self._unique(source_table_ids or []),
            source_cell_ids=self._unique(source_cell_ids or []),
            source_figure_ids=self._unique(source_figure_ids or []),
            logical_table_id=logical_table_id,
            row_index=row_index,
            column_index=column_index,
            header_paths=self._unique(header_paths or []),
            unit_hint=unit_hint,
            quality_flags=self._unique(quality_flags or []),
            source_trace=source_trace or {},
            content_sha256=digest,
        )

    @staticmethod
    def _block_context(block: BlockIR, section_paths: dict[str, list[str]]) -> str:
        return " | ".join(section_paths.get(block.section_id, []))

    @staticmethod
    def _section_path(section_id: str, section_map: dict[str, Any]) -> list[str]:
        path: list[str] = []
        seen: set[str] = set()
        current = section_map.get(section_id)
        while current and current.section_id not in seen:
            seen.add(current.section_id)
            if current.title.strip():
                path.append(current.title.strip())
            current = section_map.get(current.parent_section_id) if current.parent_section_id else None
        return list(reversed(path))

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_list_item(value: str) -> str:
        return re.sub(r"^\s*(?:[-*•·]|\d+[.)、])\s*", "", value).strip()

    @staticmethod
    def _unique(values: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _unique_int(values: Iterable[int]) -> list[int]:
        return list(dict.fromkeys(int(value) for value in values))

    @staticmethod
    def _deduplicate(atoms: list[EvidenceAtom]) -> list[EvidenceAtom]:
        unique: dict[str, EvidenceAtom] = {}
        for atom in atoms:
            unique.setdefault(atom.atom_id, atom)
        return list(unique.values())

    @staticmethod
    def _new_run_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"evd-{stamp}-{uuid.uuid4().hex[:12]}"
