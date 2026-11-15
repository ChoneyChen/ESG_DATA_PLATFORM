"""Preserve searchable PDF text printed inside physical figure regions."""

from __future__ import annotations

from collections import Counter
import re

from esg_document.document.contracts import BlockIR, DocumentIR, RetiredEntityIR, SourceTrace
from esg_document.document.geometry import bbox_containment
from esg_document.document.text_normalization import comparison_key
from esg_document.ocr.text_layer import CID_RE, Word, build_lines, text_layer_quality
from esg_document.storage.package_layout import page_object_id


class NativeFigureTextBuilder:
    """Keep labels, printed values and legends together without interpreting them.

    The original figure and word observations remain available. Reading order
    follows word baselines and horizontal positions, so the reader can select
    source literals without a program assigning colors or inventing table cells.
    """

    def build(self, document: DocumentIR) -> DocumentIR:
        forensics = document.metadata.local_forensics
        if forensics is None:
            return document
        local_pages = {page.page_index: page for page in forensics.pages}
        local_positions = {page.page_index: index for index, page in enumerate(forensics.pages)}
        replacements = {}
        created = 0
        for figure in document.figures:
            local = local_pages.get(figure.page_index)
            if local is None or figure.bbox is None or figure.visual_type == "decoration":
                continue
            excluded = {index for exclusion in local.native_text_exclusions
                for index in exclusion.native_block_indices}
            native = [(index, item) for index, item in enumerate(local.native_text_blocks)
                if index not in excluded and item.text.strip()
                and self._inside(item.bbox, figure.bbox)
                and figure is min((candidate for candidate in document.figures
                    if candidate.page_index == figure.page_index and candidate.bbox
                    and candidate.visual_type != "decoration"
                    and self._inside(item.bbox, candidate.bbox)),
                    key=lambda candidate: ((candidate.bbox.x1 - candidate.bbox.x0)
                        * (candidate.bbox.y1 - candidate.bbox.y0), candidate.figure_id))]
            if not native:
                continue
            words = [Word(item.text, item.bbox.x0, item.bbox.x1,
                item.bbox.y1 - 0.2 * (item.bbox.y1 - item.bbox.y0),
                item.bbox.y1 - item.bbox.y0) for _, item in native]
            readable, reason = text_layer_quality(words)
            native_text = "".join(word.text for word in words)
            if (not readable and reason != "too_little_text") or CID_RE.search(native_text) or any(
                char == "\ufffd" or "\ue000" <= char <= "\uf8ff" for char in native_text
            ):
                continue
            text = "\n".join(line.text for line in build_lines(words))
            region_blocks = [block for block in document.blocks
                if block.page_index == figure.page_index
                and block.block_type in {"paragraph", "heading", "caption"}
                and block.bbox and bbox_containment(block.bbox, figure.bbox) >= 0.98]
            if not any(comparison_key(item.text) and not self._word_present(item, region_blocks)
                for _, item in native):
                continue
            # Only wholly represented fragments are retired. A partial overlap
            # or OCR text missing from the PDF keeps its original evidence.
            source_characters = Counter(self._literal_key(text))
            duplicates = [block for block in region_blocks
                if self._contained_characters(block.text, source_characters, text)]
            sequence = len([block for block in document.blocks
                if block.page_index == figure.page_index]) + 1
            block_id = page_object_id("block", figure.page_index, sequence)
            while any(block.block_id == block_id for block in document.blocks):
                sequence += 1
                block_id = page_object_id("block", figure.page_index, sequence)
            block = BlockIR(
                block_id=block_id, page_index=figure.page_index,
                order=max((item.order for item in document.blocks
                    if item.page_index == figure.page_index), default=-1) + 1,
                block_type="paragraph", text=text, figure_id=figure.figure_id,
                bbox=figure.bbox.model_copy(deep=True),
                quality_flags=["native_figure_text", "figure_relationships_not_interpreted"],
                source_trace=SourceTrace(parser="pdf-native-figure-text", parser_version="1.0",
                    artifact_ids=["artifact-source-pdf"],
                    artifact_path="observations/local-pdf/forensics.json",
                    raw_object_path=f"forensics.pages[{local_positions[figure.page_index]}].native_text_blocks",
                    notes=["native_word_indices:" + ",".join(str(index) for index, _ in native)]),
            )
            for duplicate in duplicates:
                replacements[duplicate.block_id] = block_id
                document.retired_entities.append(RetiredEntityIR(
                    entity_id=duplicate.block_id, entity_type="block",
                    page_index=duplicate.page_index, disposition="duplicate_fragment",
                    snapshot=duplicate.model_dump(mode="json"), canonical_target_ids=[block_id],
                    evidence_refs=["artifact-source-pdf"],
                    reason="Native figure words cover this physical text fragment.",
                ))
            document.blocks.append(block)
            created += 1
        document.blocks = [block for block in document.blocks if block.block_id not in replacements]
        for page in document.pages:
            page.block_ids = list(dict.fromkeys(
                replacements.get(identity, identity) for identity in page.block_ids))
            page.block_ids.extend(block.block_id for block in document.blocks
                if block.page_index == page.page_index and block.block_id not in page.block_ids)
        for layout in document.layout_objects:
            layout.block_id = replacements.get(layout.block_id, layout.block_id)
        for figure in document.figures:
            figure.element_block_ids = list(dict.fromkeys(
                [replacements.get(identity, identity) for identity in figure.element_block_ids]
                + [block.block_id for block in document.blocks if block.figure_id == figure.figure_id]
            ))
        for edge in document.structure_edges:
            edge.source_id = replacements.get(edge.source_id, edge.source_id)
            edge.target_id = replacements.get(edge.target_id, edge.target_id)
        for table in document.tables:
            table.footnote_block_ids = list(dict.fromkeys(
                replacements.get(identity, identity) for identity in table.footnote_block_ids))
        document.metadata.source_artifacts["native_figure_text"] = {
            "created_regions": created, "retired_duplicate_fragments": len(replacements),
            "interpretation": "Printed text and locations only; visual relationships remain unassigned.",
        }
        return document

    @staticmethod
    def _inside(word, region):
        return (region.x0 <= (word.x0 + word.x1) / 2 <= region.x1
            and region.y0 <= (word.y0 + word.y1) / 2 <= region.y1)

    @staticmethod
    def _word_present(word, blocks):
        key = NativeFigureTextBuilder._literal_key(word.text)
        return any(key and key in NativeFigureTextBuilder._literal_key(block.text)
            and bbox_containment(word.bbox, block.bbox) >= 0.8 for block in blocks)

    @staticmethod
    def _literal_key(text):
        return "".join(text.split()).casefold()

    @staticmethod
    def _contained_characters(text, source, source_text):
        literal = NativeFigureTextBuilder._literal_key(text)
        characters = Counter(literal)
        source_literal = NativeFigureTextBuilder._literal_key(source_text)
        numbers = re.findall(r"\d+(?:[,.]\d+)*", literal)
        return bool(characters) and not (characters - source) and all(
            re.search(r"(?<![\d,.])" + re.escape(number) + r"(?![\d,.])", source_literal)
            for number in numbers
        )
