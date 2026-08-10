from __future__ import annotations

import html
import re
from collections import Counter

from esg_v2.document.contracts import DocumentIR, PageIR


_IMAGE_TAG = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_MARKDOWN_IMAGE = re.compile(r"!\[[^]]*]\([^)]+\)")
_STANDALONE_SUPERSCRIPT_NUMBER = re.compile(
    r"\$\s*\^\s*\{\s*([0-9]+)\s*}\s*\$"
)


def visible_text(value: str | None, *, preserve_lines: bool = False) -> str:
    """Return source-visible text while removing presentation markup."""
    text = str(value or "")
    text = _IMAGE_TAG.sub(" ", text)
    text = _MARKDOWN_IMAGE.sub(" ", text)
    text = _HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    if preserve_lines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)
    return re.sub(r"\s+", " ", text).strip()


def normalize_ocr_text(value: str | None, *, preserve_lines: bool = True) -> str:
    """Normalize deterministic OCR presentation noise without semantic rewriting."""
    text = _STANDALONE_SUPERSCRIPT_NUMBER.sub(r"\1", str(value or ""))
    return visible_text(text, preserve_lines=preserve_lines)


def comparison_key(value: str | None) -> str:
    return re.sub(r"\W+", "", visible_text(value).casefold(), flags=re.UNICODE)


def source_character_recall(source: str | None, candidate: str | None) -> float:
    source_chars = Counter(
        character.casefold()
        for character in visible_text(source)
        if character.isalnum()
    )
    candidate_chars = Counter(
        character.casefold()
        for character in visible_text(candidate)
        if character.isalnum()
    )
    if not source_chars:
        return 1.0
    return sum((source_chars & candidate_chars).values()) / sum(source_chars.values())


def canonical_page_text(document: DocumentIR, page: PageIR) -> str:
    """Project canonical page entities into one comparison/search text view."""
    values: list[str] = []
    blocks = sorted(
        (block for block in document.blocks if block.page_index == page.page_index),
        key=lambda block: (block.order, block.block_id),
    )
    table_block_ids = {table.block_id for table in document.tables if table.block_id}
    for block in blocks:
        if block.block_id in table_block_ids or block.block_type == "table_markdown":
            continue
        if block.text:
            values.append(block.text)

    for table in sorted(
        (table for table in document.tables if table.page_index == page.page_index),
        key=lambda table: (table.order, table.table_id),
    ):
        values.extend(
            cell.text
            for cell in sorted(table.cells, key=lambda cell: (cell.row_index, cell.col_index))
            if cell.text
        )

    for figure in sorted(
        (figure for figure in document.figures if figure.page_index == page.page_index),
        key=lambda figure: (figure.order, figure.figure_id),
    ):
        if figure.caption:
            values.append(figure.caption)
        values.extend(figure.legend_text)

    projection = "\n".join(
        dict.fromkeys(normalized for value in values if (normalized := normalize_ocr_text(value)))
    )
    return projection or normalize_ocr_text(page.text)
