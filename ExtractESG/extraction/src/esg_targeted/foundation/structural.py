"""Decide a table structurally before spending a model call.

A table is decided without the model only when the value column is unambiguous
(exactly one period column, or the one its own arithmetic marks as the total)
and every non-target row is clearly outside the requested clauses while at least
one row names a clause exactly. Anything ambiguous goes to the model unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from esg_targeted.foundation.tables import parse_measure, textual_subset_evidence

from .kernel import BY_CODE, TableTask

_WS = re.compile(r"[\s　]+")
_PUNCT = re.compile(r"[（）()【】\[\]：:，,。.、;；\-—_/]+")
_QUALIFIER = re.compile(r"^(?:其中|其中：|of which|including)")
_UNIT_HINT = re.compile(r"[（(]([^)）]{1,16})[)）]$")
_UNIT_CONTENT = re.compile(r"[A-Za-z%‰0-9]")
_CN_UNIT_START = ("吨", "万", "千", "兆", "立方米", "平方米", "吉焦", "太焦", "标准煤", "公顷", "人", "次", "小时", "元")


def _strip_unit(text: str) -> str:
    """Only a unit suffix is removed; a qualifier such as ``（含车辆燃料）`` is meaning."""
    match = _UNIT_HINT.search(text)
    if not match:
        return text
    inner = match.group(1)
    if _UNIT_CONTENT.search(inner) or inner.startswith(_CN_UNIT_START):
        return text[:match.start()]
    return text


def _norm(text: str | None) -> str:
    value = _strip_unit(_WS.sub("", str(text or "")))
    value = _QUALIFIER.sub("", value)
    return _PUNCT.sub("", value).casefold()


def code_names(code: str) -> set[str]:
    """Every name a clause accepts, taken from its own label and definition.

    The catalog label is bilingual and the definition lists accepted synonyms in
    parentheses (``能源消耗总量/综合能耗/能耗总量``); both are real report wording.
    """
    metric = BY_CODE.get(code)
    if metric is None:
        return set()
    parts = [metric.label, *_CJK_LATIN_BOUNDARY.split(metric.label)]
    # Only the first parenthetical group lists accepted common names; later
    # groups describe exclusions ("not direct/indirect only") and would turn
    # generic words into false matches.
    first = re.search(r"[（(]([^）)]+)[）)]", metric.definition or "")
    if first:
        parts.extend(re.split(r"[/、,，]", first.group(1)))
    return {name for name in (_norm(part) for part in parts) if name}


_CJK_LATIN_BOUNDARY = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z])")


@dataclass(frozen=True)
class StructuralDecision:
    payload: dict
    reason: str


def _period_columns(task: TableTask, reporting_year: int | None) -> list[int]:
    if reporting_year is None:
        return []
    columns = []
    for column in task.view.value_cols:
        header = " ".join(task.view.column_paths.get(column, []))
        if re.search(rf"(?<!\d){reporting_year}(?!\d)", header):
            columns.append(column)
    return columns


def _arithmetic_total(task: TableTask, columns: list[int]) -> int | None:
    """A column equal to the sum of other same-period columns is the total."""
    for line in task.view.arithmetic or []:
        match = re.match(r"c(\d+)\s*=\s*(c\d+(?:\s*\+\s*c\d+)+)", line)
        if not match:
            continue
        total = int(match.group(1))
        members = [int(item.strip().lstrip("c")) for item in match.group(2).split("+")]
        if total in columns and set(members) <= set(columns) and members:
            return total
    return None


_TOTAL_HEADER = re.compile(r"合计|总计|总計|小计|total|group|consolidated|合并", re.I)


def _sibling_total(task: TableTask, column: int) -> bool:
    """A total column whose period is implied by position, not written in its header.

    When one exists, the only column that names the year may still not be the
    right total (2025 子公司 | 合计 | 2024 子公司), so the model decides.
    """
    for other in task.view.value_cols:
        if other == column:
            continue
        header = " ".join(task.view.column_paths.get(other, []))
        if _TOTAL_HEADER.search(header):
            return True
    return False


def structural_column(task: TableTask, reporting_year: int | None) -> int | None:
    """The value column when the table itself decides it.

    Exactly one period column, or the column its own arithmetic marks as the
    total of the other same-period columns. Everything else stays the model's
    decision.
    """
    columns = _period_columns(task, reporting_year)
    if not columns:
        return None
    if len(columns) == 1:
        return None if _sibling_total(task, columns[0]) else columns[0]
    total = _arithmetic_total(task, columns)
    return None if total is None or _sibling_total(task, total) else total


def structural_decision(task: TableTask, reporting_year: int | None) -> StructuralDecision | None:
    columns = _period_columns(task, reporting_year)
    column = structural_column(task, reporting_year)
    if column is None:
        return None
    header = " › ".join(task.view.column_paths.get(column, []))
    year_label = str(reporting_year) if reporting_year else header

    codes = list(task.codes)
    names = {code: code_names(code) for code in codes}
    items: list[dict] = []
    for line in task.lines:
        label = _norm(line.label)
        has_value = any(parse_measure(cell.text) is not None for cell in line.values.values())
        if not label:
            if has_value:
                return None
            continue
        exact = [code for code, accepted in names.items() if label in accepted]
        if len(exact) == 1:
            items.append({"row": line.rid, "target": exact[0], "note": None})
            continue
        if len(exact) > 1:
            return None
        # Every row that carries a value must be accounted for. A row the
        # structure cannot name exactly may still be a target for the model,
        # so the whole table goes to the model rather than losing recall.
        if has_value:
            return None
    if not items:
        return None
    scope = "part_of_group" if textual_subset_evidence(task.view) else "whole_reporting_group"
    return StructuralDecision(payload={"current_col": f"c{column}", "current_year": year_label,
                                       "current_scope": scope, "items": items},
                              reason=f"unique_period_column:{len(columns)}:exact_rows:{len(items)}")
