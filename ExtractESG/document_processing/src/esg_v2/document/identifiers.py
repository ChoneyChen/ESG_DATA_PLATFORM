from __future__ import annotations

import re
from collections.abc import Iterable


CANONICAL_ID_PATTERNS = {
    "page": re.compile(r"^page-\d{4}$"),
    "section": re.compile(r"^section-\d{4}$"),
    "block": re.compile(r"^block-p\d{4}-\d{4}$"),
    "layout": re.compile(r"^layout-p\d{4}-\d{4}$"),
    "table": re.compile(r"^table-p\d{4}-\d{4}$"),
    "logical-table": re.compile(r"^logical-table-\d{6}$"),
    "figure": re.compile(r"^figure-p\d{4}-\d{4}$"),
    "spread": re.compile(r"^spread-p\d{4}-p\d{4}$"),
    "cell": re.compile(r"^cell-p\d{4}-t\d{4}-r\d{4}-c\d{4}$"),
    "review": re.compile(r"^review-\d{6}$"),
    "model-call": re.compile(r"^model-call-\d{6}$"),
    "reviewer": re.compile(r"^reviewer-\d{6}$"),
    "guard": re.compile(r"^guard-\d{6}$"),
    "verifier": re.compile(r"^verifier-\d{6}$"),
    "patch": re.compile(r"^patch-\d{6}$"),
    "candidate": re.compile(r"^candidate-\d{6}$"),
    "transaction": re.compile(r"^transaction-\d{6}$"),
    "decision": re.compile(r"^decision-\d{6}$"),
    "conflict": re.compile(r"^conflict-\d{6}$"),
}


def sequential_id(prefix: str, existing_ids: Iterable[str]) -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{6}})$")
    current = 0
    for value in existing_ids:
        match = pattern.fullmatch(value)
        if match:
            current = max(current, int(match.group(1)))
    return f"{prefix}-{current + 1:06d}"


def identifiers_match(kind: str, values: Iterable[str]) -> tuple[bool, list[str]]:
    pattern = CANONICAL_ID_PATTERNS[kind]
    invalid = sorted({value for value in values if not pattern.fullmatch(value)})
    return not invalid, invalid
