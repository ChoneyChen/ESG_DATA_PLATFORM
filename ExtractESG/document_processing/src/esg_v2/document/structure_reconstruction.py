from __future__ import annotations

import re
from collections import Counter

from esg_v2.document.contracts import DocumentIR, SectionIR, StructureEdge


class StructureReconstructor:
    """Builds document relationships without ESG-domain interpretation."""

    def reconstruct(self, document: DocumentIR) -> DocumentIR:
        document.sections = self._rebuild_sections(document)
        edges: list[StructureEdge] = [
            edge.model_copy(deep=True)
            for edge in document.structure_edges
            if edge.relation.startswith("visual_")
        ]
        blocks_by_page: dict[int, list] = {}
        for block in document.blocks:
            blocks_by_page.setdefault(block.page_index, []).append(block)
        tables_by_page = self._by_page(document.tables)
        figures_by_page = self._by_page(document.figures)
        local_by_page = {
            page.page_index: page
            for page in (document.metadata.local_forensics.pages if document.metadata.local_forensics else [])
        }

        for page in document.pages:
            page_blocks = sorted(blocks_by_page.get(page.page_index, []), key=self._reading_key)
            for order, block in enumerate(page_blocks):
                block.order = order
            page.block_ids = [block.block_id for block in page_blocks]
            for item in page_blocks:
                edges.append(self._edge(page.page_id, item.block_id, "contains"))
            for item in tables_by_page.get(page.page_index, []):
                edges.append(self._edge(page.page_id, item.table_id, "contains"))
            for item in figures_by_page.get(page.page_index, []):
                edges.append(self._edge(page.page_id, item.figure_id, "contains"))
            for current, following in zip(page_blocks, page_blocks[1:]):
                edges.append(self._edge(current.block_id, following.block_id, "reading_next"))
            self._attach_captions_and_footnotes(page_blocks, tables_by_page.get(page.page_index, []), figures_by_page.get(page.page_index, []), edges)
            self._infer_printed_page_label(page, page_blocks, local_by_page.get(page.page_index))

        for section in document.sections:
            for block_id in section.block_ids:
                edges.append(self._edge(section.section_id, block_id, "contains"))
        self._link_cross_page_paragraphs(blocks_by_page, edges)
        document.structure_edges = self._dedupe(edges)
        return document

    def _attach_captions_and_footnotes(self, blocks, tables, figures, edges):
        targets = [*tables, *figures]
        for block in blocks:
            if block.block_type not in {"caption", "footnote"} or not targets:
                continue
            target = self._nearest_target(block, targets)
            if not target:
                continue
            if block.block_type == "caption":
                edges.append(self._edge(block.block_id, target.table_id if hasattr(target, "table_id") else target.figure_id, "caption_of", 0.9))
                if hasattr(target, "caption") and not target.caption:
                    target.caption = block.text
            else:
                target_id = target.table_id if hasattr(target, "table_id") else target.figure_id
                edges.append(self._edge(block.block_id, target_id, "footnote_of", 0.75))
                if hasattr(target, "footnote_block_ids") and block.block_id not in target.footnote_block_ids:
                    target.footnote_block_ids.append(block.block_id)

    @staticmethod
    def _nearest_target(block, targets):
        if block.bbox:
            located = [item for item in targets if item.bbox]
            if located:
                return min(located, key=lambda item: abs(item.bbox.y1 - block.bbox.y0))
        return targets[-1]

    @staticmethod
    def _infer_printed_page_label(page, blocks, local_page=None):
        if page.printed_page_label:
            return
        footer_texts = [block.text.strip() for block in blocks if block.block_type == "footer"]
        for text in footer_texts:
            match = re.fullmatch(r"(?:page\s*)?([ivxlcdm]+|\d+)(?:\s*of\s*\d+)?", text, re.IGNORECASE)
            if match:
                page.printed_page_label = match.group(1)
                return
        if not local_page:
            return
        page_height = local_page.height_points or page.height or 0
        page_width = local_page.width_points or page.width or 0
        candidates = []
        for block in local_page.native_text_blocks:
            text = block.text.strip()
            if not re.fullmatch(r"[ivxlcdm]+|\d{1,4}", text, re.IGNORECASE):
                continue
            if page_height and block.bbox.y0 < page_height * 0.88:
                continue
            edge_distance = min(block.bbox.x0, max(0.0, page_width - block.bbox.x1)) if page_width else 0.0
            candidates.append((edge_distance, -block.bbox.y0, text))
        if candidates:
            page.printed_page_label = sorted(candidates)[0][2]
            if "artifact-source-pdf" not in page.source_trace.artifact_ids:
                page.source_trace.artifact_ids.append("artifact-source-pdf")
            if "printed_page_label_from_native_pdf" not in page.quality_flags:
                page.quality_flags.append("printed_page_label_from_native_pdf")

    def _link_cross_page_paragraphs(self, blocks_by_page, edges):
        page_indices = sorted(blocks_by_page)
        for current_index, next_index in zip(page_indices, page_indices[1:]):
            if next_index != current_index + 1:
                continue
            current = [item for item in blocks_by_page[current_index] if item.block_type == "paragraph"]
            following = [item for item in blocks_by_page[next_index] if item.block_type == "paragraph"]
            if not current or not following:
                continue
            last = sorted(current, key=lambda item: item.order)[-1]
            first = sorted(following, key=lambda item: item.order)[0]
            if last.text and first.text and last.text[-1] not in ".!?。！？：:" and first.text[0].islower():
                edges.append(self._edge(last.block_id, first.block_id, "continues", 0.55))
                last.quality_flags.append("cross_page_continuation_candidate")
                first.quality_flags.append("cross_page_continuation_candidate")

    @staticmethod
    def _by_page(items):
        result = {}
        for item in items:
            result.setdefault(item.page_index, []).append(item)
        return result

    def _rebuild_sections(self, document: DocumentIR) -> list[SectionIR]:
        for block in document.blocks:
            block.section_id = None
        ordered_blocks = sorted(document.blocks, key=lambda item: (item.page_index, *self._reading_key(item)))
        chapter_headers = [
            block
            for block in ordered_blocks
            if block.block_type == "header" and re.match(r"^第[一二三四五六七八九十百\d]+章", block.text.strip())
        ]
        first_heading_by_figure: dict[str, str] = {}
        for block in ordered_blocks:
            if block.block_type == "heading" and block.figure_id and block.figure_id not in first_heading_by_figure:
                first_heading_by_figure[block.figure_id] = block.block_id
        first_chapter_header_by_title = {}
        for block in chapter_headers:
            first_chapter_header_by_title.setdefault(self._normalized_heading(block.text), block)
        heading_candidates = [*first_chapter_header_by_title.values()]
        heading_candidates.extend(
            block
            for block in ordered_blocks
            if block.block_type == "heading"
            and block.text.strip()
            and not re.fullmatch(r"(?:具體|具体)內容[:：]?", block.text.strip())
            and (not block.figure_id or first_heading_by_figure.get(block.figure_id) == block.block_id)
        )
        heading_candidates = list({block.block_id: block for block in heading_candidates}.values())
        title_counts = Counter(self._normalized_heading(block.text) for block in heading_candidates)
        repeat_threshold = max(5, int(max(1, len(document.pages)) * 0.02))
        headings = []
        repeated_chapter_titles_seen: set[str] = set()
        for block in heading_candidates:
            normalized_title = self._normalized_heading(block.text)
            repeated_chapter = (
                bool(re.match(r"^第[一二三四五六七八九十百\d]+章", block.text.strip()))
                and title_counts[normalized_title] >= repeat_threshold
            )
            if repeated_chapter and normalized_title in repeated_chapter_titles_seen:
                self._add_flag(block.quality_flags, "section_heading_rejected_repeated_running_header")
                continue
            reason = self._heading_rejection_reason(
                block.text,
                repeated_count=title_counts[normalized_title],
                repeat_threshold=repeat_threshold,
            )
            if reason:
                self._add_flag(block.quality_flags, f"section_heading_rejected_{reason}")
                continue
            headings.append(block)
            if repeated_chapter:
                repeated_chapter_titles_seen.add(normalized_title)
        headings.sort(key=lambda item: (item.page_index, *self._reading_key(item)))
        sections: list[SectionIR] = []
        stack: list[SectionIR] = []
        for heading in headings:
            level = self._heading_level(heading.text, heading.heading_level)
            if level == 3 and any(
                ancestor.level == 3
                and re.search(r"(?:識別|识别)$", ancestor.title)
                and re.search(r"(?:風險|风险)$", heading.text.strip())
                for ancestor in stack
            ):
                level = 4
            heading.heading_level = level
            while stack and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1] if stack else None
            section = SectionIR(
                section_id=f"section-{len(sections) + 1:04d}",
                title=heading.text.strip(),
                level=level,
                start_page_index=heading.page_index,
                heading_block_id=heading.block_id,
                parent_section_id=parent.section_id if parent else None,
            )
            if parent:
                parent.child_section_ids.append(section.section_id)
            sections.append(section)
            stack.append(section)

        active: SectionIR | None = None
        by_heading = {section.heading_block_id: section for section in sections}
        for block in sorted(document.blocks, key=lambda item: (item.page_index, *self._reading_key(item))):
            if block.block_id in by_heading:
                active = by_heading[block.block_id]
            if active:
                block.section_id = active.section_id
                active.block_ids.append(block.block_id)
        last_page = max((page.page_index for page in document.pages), default=0)
        self._close_section_ranges(sections, last_page)
        document.metadata.source_artifacts["section_reconstruction"] = {
            "candidate_heading_count": len(heading_candidates),
            "accepted_heading_count": len(headings),
            "rejected_heading_count": len(heading_candidates) - len(headings),
            "repeat_threshold": repeat_threshold,
        }
        return sections

    @classmethod
    def _heading_rejection_reason(cls, text: str, *, repeated_count: int, repeat_threshold: int) -> str | None:
        value = re.sub(r"\s+", " ", text or "").strip()
        if cls.is_suspicious_title(value):
            return "semantic_noise"
        numbered = bool(re.match(r"^(?:第[一二三四五六七八九十百\d]+章|\d+(?:\.\d+)+)", value))
        if not numbered and len(value) <= 24 and repeated_count >= repeat_threshold:
            return "repeated_label"
        return None

    @staticmethod
    def is_suspicious_title(text: str) -> bool:
        value = re.sub(r"\s+", " ", text or "").strip()
        if not value or len(value) > 100:
            return True
        if not re.search(r"[A-Za-z0-9\u3400-\u9fff]", value):
            return True
        if re.fullmatch(r"[\d\W_]+", value, re.UNICODE):
            return True
        sentence_marks = len(re.findall(r"[。！？!?；;]", value))
        if len(value) > 40 and (sentence_marks >= 2 or value.endswith(("。", ".", "！", "!", "？", "?"))):
            return True
        if value.count("\n") >= 3:
            return True
        return False

    @staticmethod
    def _normalized_heading(text: str) -> str:
        return re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE).lower()

    @staticmethod
    def _close_section_ranges(sections: list[SectionIR], last_page: int) -> None:
        for index, section in enumerate(sections):
            end_page = last_page
            for following in sections[index + 1 :]:
                if following.level <= section.level:
                    end_page = following.start_page_index
                    break
            section.end_page_index = max(section.start_page_index, end_page)

    @staticmethod
    def _heading_level(text: str, fallback: int | None) -> int:
        chapter = re.match(r"^第[一二三四五六七八九十百\d]+章", text.strip())
        if chapter:
            return 1
        numbered = re.match(r"^(\d+(?:\.\d+)+)", text.strip())
        if numbered:
            if re.search(r"(?:℃|°\s*C)", text, re.IGNORECASE):
                return 4
            return 2
        if re.search(r"(?:℃|°\s*C)", text, re.IGNORECASE):
            return 4
        return 3

    @staticmethod
    def _reading_key(item):
        if item.bbox:
            return item.order, round(item.bbox.y0, 2), round(item.bbox.x0, 2), item.block_id
        return item.order, float("inf"), float("inf"), item.block_id

    @staticmethod
    def _edge(source_id, target_id, relation, confidence=1.0):
        safe = f"{source_id}-{relation}-{target_id}"
        return StructureEdge(
            edge_id=f"edge-{safe}",
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            confidence=confidence,
        )

    @staticmethod
    def _dedupe(edges):
        return list({edge.edge_id: edge for edge in edges}.values())

    @staticmethod
    def _add_flag(flags, value):
        if value not in flags:
            flags.append(value)
