from __future__ import annotations

import re

from esg_targeted.contracts import EvidenceSpan


MAX_RETRIEVAL_SPAN_CHARS = 12000
NAVIGATION_FLAGS = {
    "index_page",
    "navigation_page",
    "table_of_contents",
    "toc_page",
}
NAVIGATION_TITLES = {"目录", "索引", "contents", "table of contents", "index"}
LEADER_RE = re.compile(r"(?:\.{2,}|…{2,})\s*\d{1,4}\s*$", re.MULTILINE)


def is_navigation_span(span: EvidenceSpan) -> bool:
    if NAVIGATION_FLAGS.intersection(span.quality_flags):
        return True
    title = (span.section_title or "").strip().lower()
    if title in NAVIGATION_TITLES:
        return True
    prefix = span.text[:200].strip().lower()
    titled_navigation = any(
        prefix.startswith(value) for value in ("目录", "索引", "contents", "table of contents")
    )
    return bool(titled_navigation and len(LEADER_RE.findall(span.text)) >= 3)


def is_retrieval_eligible(span: EvidenceSpan) -> bool:
    if "retrieval_quarantined" in span.quality_flags:
        return False
    if len(span.context_text) > MAX_RETRIEVAL_SPAN_CHARS:
        return False
    return not is_navigation_span(span)
