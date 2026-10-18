"""One bounded understanding call per table; deterministic value binding."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, asdict

from esg_targeted.evidence.units import canonical_unit_id, normalize_unit_text
from esg_targeted.foundation.catalog import BY_CODE, GROUP_BY_KEY, SCOPE2_UNSPECIFIED
from esg_targeted.foundation.guards import (
    GENERIC_TOTAL_RE, QUALIFIER_ONLY_RE, check as concept_check, is_method_text, is_pollution_method_text, pollutant_key,
    row_is_subset,
)
from esg_targeted.foundation.quantities import unit_scale
from esg_targeted.foundation.tables import (
    DataLine, TableView, _is_unit_like, boundary_hint, entity_sum_role, explicit_total_column, is_number_text,
    number_text, parse_number, period_years, render_table, structural_boundary, textual_subset_evidence,
    title_subset_evidence,
)
from esg_targeted.foundation.textnorm import fold

MAX_LINES_PER_CALL = 40
UNIT_IN_LABEL_RE = re.compile(r"[（(]\s*([^（）()]{1,24})\s*[)）]\s*$")
TRAILING_MARK_RE = re.compile(r"(?:\s*\$?\s*\^?\{*[\d,，、\s*⁰¹²³⁴⁵⁶⁷⁸⁹]+\}*\s*\$?\s*)$")
LOCATION_TERMS_RE = re.compile(r"(?:location[\s-]*based|地点法|基于(?:地点|位置)|位置法|绿电未抵扣|未抵扣绿电|不抵扣绿电)", re.I)
# A grid emission factor is used under both methods, so on its own it says
# nothing about which one a row reports; only a row label that names it counts.
GRID_FACTOR_RE = re.compile(r"(?:(?:当地|所在地|区域|地区|全国|所在国)(?:的)?电网(?:平均)?(?:二氧化碳)?(?:排放)?因子|grid[\s-]*average|average\s+grid|grid\s+emission\s+factor)", re.I)
MARKET_TERMS_RE = re.compile(r"(?:market[\s-]*based|市场法|基于市场|(?<!未)(?<!不)绿电抵扣|抵扣绿电后|供应商特定|supplier[\s-]*specific|contractual\s+instrument|residual\s+mix|剩余组合)", re.I)
FAMILY_DIMENSION = {"ghg": "ghg-emissions", "energy": "energy", "energy_tce": "energy", "volume": "volume",
                    "mass": "mass", "ratio": "ratio"}
FALLBACK_DIMENSIONS = (
    ("ghg-emissions", ("co2", "二氧化碳", "碳当量")),
    ("ratio", ("%", "％", "百分比")),
    ("energy", ("wh", "千瓦时", "瓦时", "焦", "tj", "gj", "mj", "tce", "toe", "标准煤", "标煤", "btu")),
    ("volume", ("m3", "m³", "立方米", "升", "megalit", "litre", "liter")),
    ("currency", ("元", "usd", "rmb", "cny", "hkd", "eur", "dollar")),
    ("mass", ("吨", "噸", "kg", "千克", "公斤", "tonne")),
    ("count", ("件", "次", "个", "家", "项", "起", "台", "辆", "宗", "人", "number", "cases", "no.")),
)
# A quantity datapoint whose share sibling sits beside it as "<same label> 占比".
SHARE_PARTNERS = {"E1-5_05": "E1-5_09"}
SHARE_WORD_RE = re.compile(r"(?:占比|比例|比重|占总|share|percentage|proportion|ratio|%|％)", re.I)
UNIT_PAREN_RE = re.compile(r"[（(][^（）()]*[)）]")
GROUP_SIBLINGS = {
    "energy": ("E1-5_01", "E1-5_05", "E1-5_09"),
    "ghg": ("E1-6_07", "SCOPE2", "E1-6_11"),
    "water": ("E3-4_01", "E3-4_02", "E3-4_03", "E3-4_11", "E3-4_12"),
    "air": ("E2-4_02", "E2-4_03"),
    "water_pollutants": ("E2-4_03", "E2-4_02"),
}


NEGATED_METHOD_RE = re.compile(
    r"(?:instead\s+of|rather\s+than|not)\s+(?:the\s+|a\s+)?(?:location|market)[\s-]*based(?:\s+(?:method|approach))?"
    r"|(?:而非|而不是|不采用|未采用|并非)\s*(?:基于)?(?:市场|地点|位置)(?:法|的方法)?", re.I)


def scope2_method_from_text(*texts: str | None, grid_factor: bool = False) -> str | None:
    """The single Scope 2 method a text states; a rejected alternative does not count.

    `grid_factor` admits the weak "grid emission factor" wording, which is
    evidence only in a row label (the row itself names how it was computed).
    """
    joined = NEGATED_METHOD_RE.sub(" ", fold(" ".join(t for t in texts if t)))
    location = bool(LOCATION_TERMS_RE.search(joined)) or (grid_factor and bool(GRID_FACTOR_RE.search(joined)))
    market = bool(MARKET_TERMS_RE.search(joined))
    if location and not market:
        return "E1-6_09"
    if market and not location:
        return "E1-6_10"
    return None


def unit_dimension(unit: str | None) -> str | None:
    if not unit:
        return None
    scaled = unit_scale(unit)
    if scaled:
        return FAMILY_DIMENSION.get(scaled[0])
    text, raw = normalize_unit_text(unit), unit.casefold()
    for dimension, tokens in FALLBACK_DIMENSIONS:
        if any(token in text or token in raw for token in tokens):
            return dimension
    return None


@dataclass
class TableTask:
    view: TableView
    lines: list[DataLine]
    codes: list[str]
    groups: list[str]
    chunk: int = 0

    @property
    def task_id(self) -> str:
        return f"{self.view.table_id}#{self.chunk}"


def plan_table_tasks(view: TableView, groups: list[str]) -> list[TableTask]:
    codes: list[str] = []
    for key in groups:
        for code in GROUP_BY_KEY[key].codes:
            if code not in codes:
                codes.append(code)
    lines = view.lines
    if len(lines) <= MAX_LINES_PER_CALL:
        return [TableTask(view, lines, codes, groups, 0)]
    tasks = []
    for index, start in enumerate(range(0, len(lines), MAX_LINES_PER_CALL - 4)):
        chunk = lines[start:start + MAX_LINES_PER_CALL]
        if len(chunk) <= 4 and index:
            break
        tasks.append(TableTask(view, chunk, codes, groups, index))
    return tasks


def _target_lines(codes: list[str]) -> list[str]:
    out = [f"- {code} {BY_CODE[code].label}: {BY_CODE[code].definition}" for code in codes]
    if any(BY_CODE[c].group == "ghg" for c in codes):
        out.append(f"- {SCOPE2_UNSPECIFIED}: Scope 2 GHG emissions whose method is NOT stated as location-based or market-based in the table or notes.")
    return out


def _has_method_scope(task: TableTask) -> bool:
    return any(g in task.groups for g in ("air", "water_pollutants", "pollution_method"))


def build_request(task: TableTask, reporting_year: int | None) -> tuple[str, str, dict, int]:
    view = task.view
    year_text = str(reporting_year) if reporting_year else "the latest actual period shown"
    method = _has_method_scope(task)
    rules = [
        "You map rows of ONE table from a company sustainability report to target ESG datapoints.",
        f"Reporting period of this report: {year_text}.",
        "TARGET DATAPOINTS:",
        *_target_lines(task.codes),
        "TASK:",
        f"1. current_col: the value column holding the {year_text} ACTUAL value for the whole reporting group. If that period is split by "
        "subsidiary/segment/site and a total column exists, choose the total. If only one column covers the period, choose it. Never choose a "
        "prior year, target, baseline, change or ratio column. Use header order and ARITHMETIC lines (a column equal to the sum of others is their total). "
        "null if no column holds the reporting period.",
        "2. current_year: the period label of current_col exactly as written in the headers, or null.",
        "3. current_scope: whole_reporting_group if current_col covers the whole reporting company/group; part_of_group if the table "
        "or column is limited to a subsidiary, segment, site, plant, production base, region, office or data centre; otherwise unclear.",
        "4. items: every row that DIRECTLY reports one target datapoint. Skip components or sub-items of a total (rows starting with -, •, 其中 are usually components), "
        "single fuels/carriers/sources, intensities (per revenue/unit), changes, targets and different concepts; a ratio row only maps to a ratio target. "
        "Each row maps to at most one target.",
        "5. note: the NOTE id stating the meaning you relied on, else null. Scope 2 method comes from the row label or NOTES: "
        "'location based', 地点法 or the local/average grid emission factor means location-based (E1-6_09); 'market based', 市场法, "
        "contractual instruments or supplier-specific factors mean market-based (E1-6_10). Only if neither is stated use "
        f"{SCOPE2_UNSPECIFIED}.",
    ]
    if method:
        rules.append("6. method_notes: NOTE ids that state how pollutant emission amounts are measured, monitored or estimated ([] if none).")
    rules.append("If no row matches, items is []. Output only the JSON object.")
    instructions = "\n".join(rules)
    context = render_table(view, task.lines)

    cols = [f"c{c}" for c in view.value_cols]
    rids = [line.rid for line in task.lines]
    nids = [note.nid for note in view.notes]
    years = sorted({text for header in view.header_lines for _, _, text in header if period_years(text)})
    target_enum = list(task.codes) + ([SCOPE2_UNSPECIFIED] if any(BY_CODE[c].group == "ghg" for c in task.codes) else [])

    def nullable_enum(values):
        return {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]} if values else {"type": "null"}

    properties = {
        "current_col": nullable_enum(cols),
        "current_year": nullable_enum(years),
        "current_scope": {"type": "string", "enum": ["whole_reporting_group", "part_of_group", "unclear"]},
        "items": {
            "type": "array",
            "maxItems": len(rids),
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "string", "enum": rids},
                    "target": {"type": "string", "enum": target_enum},
                    "note": nullable_enum(nids),
                },
                "required": ["row", "target", "note"],
                "additionalProperties": False,
            },
        },
    }
    if method:
        properties["method_notes"] = ({"type": "array", "items": {"type": "string", "enum": nids}} if nids
                                      else {"type": "array", "maxItems": 0, "items": {"type": "string"}})
    schema = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    max_tokens = 96 + 26 * min(len(rids), 32) + (16 * len(nids) if method else 0)
    return instructions, context, schema, max_tokens


@dataclass
class Observation:
    code: str
    metric_id: str
    package_id: str
    status: str  # accepted | review | rejected
    value_raw: str | None
    value: str | None
    unit_raw: str | None
    unit_id: str | None
    period: str | None
    period_year: int | None
    entity_basis: str
    member: str | None
    table_id: str
    page_number: int
    row_id: str
    row_label: str
    cell_id: str | None
    column: str | None
    note_block_id: str | None
    proposed_code: str | None = None
    flags: list[str] = field(default_factory=list)
    rejected_by: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def infer_unit(line: DataLine, view: TableView, col: int | None) -> tuple[str | None, str]:
    if line.unit and not is_number_text(line.unit):
        return line.unit, "unit_column"
    last = line.label.split(" › ")[-1]
    if " › " in line.label and _is_unit_like(last) and unit_dimension(last):
        return last.strip(), "row_label"
    match = UNIT_IN_LABEL_RE.search(TRAILING_MARK_RE.sub("", line.label))
    if match and unit_dimension(match.group(1)):
        return match.group(1).strip(), "row_label"
    if col is not None:
        for text in reversed(view.column_paths.get(col, [])):
            inner = UNIT_IN_LABEL_RE.search(text)
            if inner and unit_dimension(inner.group(1)):
                return inner.group(1).strip(), "column_header"
            if unit_dimension(text) and not period_years(text):
                return text, "column_header"
    for text in [line.section or "", view.title or "", view.caption or "", view.heading_above or "", *view.headings]:
        inner = (UNIT_IN_LABEL_RE.search(text) or re.search(r"(?:单位|unit)\s*[:：]\s*(\S{1,16})", text, re.I)
                 or re.search(r"[,，]\s*([^,，()（）]{1,20})\s*$", text))
        if inner and unit_dimension(inner.group(1)):
            return inner.group(1).strip(), "context"
        # "Table 29. Total water use, m 3, European Operating Segment, 2018–2020"
        for segment in re.split(r"[,，;；]", text)[1:]:
            segment = segment.strip()
            if 0 < len(segment) <= 20 and not period_years(segment) and unit_dimension(segment):
                return segment, "context"
    if view.trailing_text and unit_dimension(view.trailing_text):
        return view.trailing_text.strip(" ,，"), "unit_line_after_table"
    return None, "missing"


def _resolve_scope2(proposed: str, line: DataLine, note, page_notes) -> tuple[list[str], list[str], bool]:
    """Return metric codes, flags and whether the method is unresolved.

    The method must be stated by the row label, the cited note, or exactly one
    method across this page's notes; the model's label alone is not evidence.
    """
    label_method = scope2_method_from_text(line.label, line.section if QUALIFIER_ONLY_RE.match(
        fold(line.label.split(" › ")[-1])) else None, grid_factor=True)
    note_method = scope2_method_from_text(note.text) if note else None
    page_method = scope2_method_from_text(*(n.text for n in page_notes)) if page_notes else None
    stated = label_method or note_method or page_method
    source = ("scope2_method_from_label" if label_method else
              "scope2_method_from_cited_note" if note_method else "scope2_method_from_page_note")
    if proposed in (SCOPE2_UNSPECIFIED, "SCOPE2"):
        if stated:
            return [stated], [source], False
        return ["E1-6_09", "E1-6_10"], ["scope2_method_unstated"], True
    if label_method and label_method != proposed:
        # The row itself names its method; that outranks the model's reading.
        return [label_method], ["scope2_method_from_label", "scope2_method_model_overruled"], False
    if stated and stated != proposed:
        return [proposed], ["scope2_method_contradicts_source_text"], True
    if not stated:
        return [proposed], ["scope2_method_not_stated_in_source"], True
    return [proposed], [source], False


def _label_stem(label: str) -> str:
    last = TRAILING_MARK_RE.sub("", label.split(" › ")[-1])
    return re.sub(r"\s+", "", UNIT_PAREN_RE.sub("", fold(last)))


def scope2_partner_items(task: TableTask, items: list[dict]) -> list[dict]:
    """Propose the other Scope 2 method row printed next to a mapped one.

    A market-based row mapped while the adjacent row names the location-based
    method (or the reverse) is the other datapoint; the row label decides.
    """
    mapped = {item.get("row") for item in items}
    extra = []
    order = [line.rid for line in task.lines]
    by_rid = {line.rid: line for line in task.lines}
    for item in items:
        if item.get("target") not in ("E1-6_09", "E1-6_10", SCOPE2_UNSPECIFIED) or item.get("row") not in by_rid:
            continue
        line = by_rid[item["row"]]
        own = scope2_method_from_text(line.label, line.section, grid_factor=True)
        if own is None:
            continue
        index = order.index(line.rid)
        for neighbour in order[max(0, index - 2):index] + order[index + 1:index + 3]:
            other = by_rid[neighbour]
            if neighbour in mapped or other.section != line.section:
                continue
            method = scope2_method_from_text(other.label, other.section)
            if method and method != own and not any(e["row"] == neighbour for e in extra):
                extra.append({"row": neighbour, "target": method, "note": None,
                              "derived": "scope2_other_method_row_beside_mapped_row"})
    return extra


# Row labels that name exactly one datapoint (folded, without spaces,
# brackets or footnote marks). A row printed under one of them is that
# datapoint even when the table call left it out; it is proposed for review.
EXACT_LABELS = {
    "E1-5_01": ("能源消耗总量", "总能源消耗量", "能源总消耗量", "综合能源消耗量", "综合能源消耗总量", "总能耗",
                "totalenergyconsumption", "totalenergyconsumed"),
    "E1-6_07": ("范围一温室气体排放量", "范围一温室气体排放总量", "温室气体排放量范围一", "范围一排放量", "范围1温室气体排放量",
                "直接温室气体排放总量范围一", "直接温室气体排放量范围一",
                "scope1ghgemissions", "scope1emissions", "scope1greenhousegasemissions"),
    "E1-6_11": ("范围三温室气体排放量", "范围三温室气体排放总量", "温室气体排放量范围三", "范围三排放量", "范围3温室气体排放量",
                "scope3ghgemissions", "scope3emissions", "scope3greenhousegasemissions"),
    "E3-4_01": ("耗水总量", "总耗水量", "耗水量", "totalwaterconsumption", "waterconsumption"),
    "E3-4_11": ("取水总量", "总取水量", "totalwaterwithdrawal", "totalwaterwithdrawn", "waterwithdrawal"),
    "E3-4_12": ("排水总量", "总排水量", "废水排放总量", "totalwaterdischarge", "totalwaterdischarged", "waterdischarge"),
}


def _exact_stem(label: str) -> str:
    last = TRAILING_MARK_RE.sub("", label.split(" › ")[-1])
    return re.sub(r"[\s（）()\[\]【】:：\-–—_/]+", "", fold(last))


def exact_label_items(task: TableTask, items: list[dict], payload: dict) -> list[dict]:
    """Propose the one row whose label names a target the call did not map.

    A mapped row only settles a code when that row's own label names the
    datapoint; a combined or neighbouring row ("范围一+范围二") does not, so
    the row printed under the datapoint's standard name is still proposed.
    """
    if not payload.get("current_col"):
        return []
    by_rid = {line.rid: line for line in task.lines}
    mapped_rows = {item.get("row") for item in items}
    extra = []
    for code in task.codes:
        names = EXACT_LABELS.get(code)
        if not names:
            continue
        named = any(_exact_stem(by_rid[item["row"]].label) in names
                    for item in items if item.get("target") == code and item.get("row") in by_rid)
        if named:
            continue
        rows = [line.rid for line in task.lines if _exact_stem(line.label) in names]
        if len(rows) == 1 and rows[0] not in mapped_rows:
            extra.append({"row": rows[0], "target": code, "note": None, "derived": "row_label_names_datapoint"})
            mapped_rows.add(rows[0])
    return extra


def share_partner_items(task: TableTask, items: list[dict]) -> list[dict]:
    """Propose the share row printed beside a mapped quantity row ("X" and "X 占比")."""
    targets = {item.get("target") for item in items}
    extra = []
    for item in items:
        partner = SHARE_PARTNERS.get(item.get("target"))
        line = next((l for l in task.lines if l.rid == item.get("row")), None)
        if partner is None or partner in targets or line is None:
            continue
        stem = _label_stem(line.label)
        matches = [l for l in task.lines if l.rid != line.rid and stem and stem in _label_stem(l.label)
                   and SHARE_WORD_RE.search(_label_stem(l.label).replace(stem, "", 1) + " " + (l.unit or ""))]
        if len(matches) == 1:
            extra.append({"row": matches[0].rid, "target": partner, "note": None,
                          "derived": "share_row_beside_mapped_quantity"})
    return extra


def bind(task: TableTask, payload: dict, reporting_year: int | None) -> tuple[list[Observation], dict]:
    view = task.view
    lines = {line.rid: line for line in task.lines}
    notes = {note.nid: note for note in view.notes}
    col_text = payload.get("current_col")
    col = int(col_text[1:]) if isinstance(col_text, str) and col_text.startswith("c") else None
    current_year = payload.get("current_year") if isinstance(payload.get("current_year"), str) else None
    year_values = period_years(current_year) if current_year else []
    period_year = year_values[-1] if year_values else None
    column_flags: list[str] = []
    if period_year is None and col is not None and view.headers_aligned:
        header_years = sorted(set(period_years(" ".join(view.column_paths.get(col, [])))))
        if len(header_years) == 1:
            period_year = header_years[0]
            current_year = current_year or " ".join(view.column_paths.get(col, []))
            column_flags.append("period_from_column_header")
    if col is None:
        column_flags.append("no_current_column")
    if reporting_year and period_year and period_year != reporting_year:
        column_flags.append("current_column_year_differs_from_reporting_period")
    if reporting_year and period_year is None and col is not None:
        column_flags.append("current_column_period_not_labelled")
        period_year = reporting_year if len(view.value_cols) == 1 else None

    model_scope = {"whole_reporting_group": "group", "part_of_group": "subset"}.get(payload.get("current_scope"))
    hint = boundary_hint(view, col) or structural_boundary(view, col, period_year)
    if hint:
        boundary = hint
    elif model_scope == "subset" and textual_subset_evidence(view):
        boundary = "subset"
    else:
        boundary = "unknown"
    boundary_flags: list[str] = []
    if hint and model_scope and hint != model_scope:
        boundary_flags.append(f"boundary_model_{model_scope}_vs_header_{hint}")
    if boundary == "subset":
        boundary_flags.append("boundary_part_of_group")
    observations: list[Observation] = []
    seen_rows: set[str] = set()
    items = list(payload.get("items") or [])
    items += share_partner_items(task, items) + scope2_partner_items(task, items)
    items += exact_label_items(task, items, payload)
    # One page-level method note cannot disambiguate two different Scope 2 rows.
    scope2_rows = len({item.get("row") for item in items
                       if item.get("target") in ("E1-6_09", "E1-6_10", SCOPE2_UNSPECIFIED)})
    for item in items:
        rid, proposed = item.get("row"), item.get("target")
        if rid not in lines or rid in seen_rows:
            continue
        seen_rows.add(rid)
        line = lines[rid]
        note = notes.get(item.get("note")) if item.get("note") else None
        # A value that is one addend of a same-period column sum in this row is
        # a segment; the sum column is the reporting-group value.
        row_col, row_flags = col, []
        role, other = entity_sum_role(view, col, line)
        if role == "part":
            row_col, role = other, "sum"
            row_flags.append("column_switched_to_sum_of_parts")
        if role is None:
            # Entity columns may not add up (overlapping or nested boundaries),
            # but an explicit Total column still marks the group figure.
            total_col = explicit_total_column(view, col, line, period_year)
            if total_col is not None:
                row_col, role = total_col, "sum"
                row_flags.append("column_switched_to_explicit_total")
        if role == "sum":
            row_boundary = "subset" if title_subset_evidence(view) else "group"
            row_flags += ["boundary_part_of_group"] if row_boundary == "subset" else []
        else:
            row_boundary = boundary
            row_flags += boundary_flags
        cell = line.values.get(row_col) if row_col is not None else None
        value_raw = number_text(cell.text) if cell else None
        if cell and value_raw != cell.text:
            row_flags.append("value_footnote_mark_removed")
        if cell and cell.cell_id in view.corrected_cells:
            value_raw = view.corrected_cells[cell.cell_id]
            row_flags.append("value_corrected_from_pdf_text_layer")
        number = parse_number(value_raw) if value_raw else None
        if cell and cell.cell_id in view.unconfirmed_cells:
            row_flags.append("value_not_in_pdf_text_layer")
        if line.ocr_label:
            row_flags.append("label_reconciled_with_pdf_text_layer")
        unit_raw, unit_source = infer_unit(line, view, row_col)
        dimension = unit_dimension(unit_raw)

        def guard_code(code):
            return "SCOPE2" if code in (SCOPE2_UNSPECIFIED, "E1-6_09", "E1-6_10") else code

        chosen = proposed
        # A bare total row ("TOTAL", "合计") takes its subject from the section and table title.
        context = line.section
        if GENERIC_TOTAL_RE.fullmatch(fold(line.label.split(" › ")[-1]).strip()):
            context = " ".join(filter(None, [line.section, view.title, view.caption, view.heading_above]))
        # A one-row table is described by its title ("Scope 2 emissions" over "Electricity").
        label = line.label
        subject = view.title or view.heading_above or view.caption
        if view.numeric_line_count == 1 and subject and fold(subject) not in fold(label):
            label = f"{subject} › {line.label}"
        guard = concept_check(guard_code(proposed), label, context, unit_raw, dimension)
        reassigned = None
        if not guard.ok:
            group = BY_CODE["E1-6_09" if proposed == SCOPE2_UNSPECIFIED else proposed].group
            passing = []
            for sibling in GROUP_SIBLINGS.get(group, ()):
                if sibling == guard_code(proposed):
                    continue
                result = concept_check(sibling, label, context, unit_raw, dimension)
                if result.ok:
                    passing.append((sibling, result))
            if len(passing) == 1:
                reassigned, guard = passing[0]
                chosen = SCOPE2_UNSPECIFIED if reassigned == "SCOPE2" else reassigned
        if chosen in (SCOPE2_UNSPECIFIED, "E1-6_09", "E1-6_10"):
            metric_codes, method_flags, unresolved = _resolve_scope2(
                chosen, line, note, view.notes if scope2_rows <= 1 else [])
        else:
            metric_codes, method_flags, unresolved = [chosen], [], False

        for metric_code in metric_codes:
            metric = BY_CODE[metric_code]
            flags = list(column_flags) + row_flags + method_flags + list(guard.flags)
            if reassigned:
                flags.append(f"reassigned_from_{proposed}")
            if item.get("derived"):
                flags.append(item["derived"])
            status, rejected_by = "accepted", None
            if not guard.ok:
                status, rejected_by = "rejected", (guard.flags or ("concept",))[0]
            elif guard.status == "review" or unresolved or item.get("derived") or {
                    "value_not_in_pdf_text_layer", "value_corrected_from_pdf_text_layer"} & set(flags):
                status = "review"
            if value_raw is None:
                flags.append("no_value_in_current_column")
                status, rejected_by = "rejected", rejected_by or "no_value_in_current_column"
            elif number is None:
                flags.append("value_not_numeric")
                status, rejected_by = "rejected", rejected_by or "value_not_numeric"
            if unit_source == "missing":
                flags.append("unit_missing")
                if status == "accepted":
                    status = "review"
            if metric.kind == "quantity" and dimension in ("count", "currency"):
                flags.append(f"unit_is_{dimension}")
                status, rejected_by = "rejected", rejected_by or f"unit_is_{dimension}"
            if metric.dimension == "volume" and dimension == "mass":
                flags.append("water_reported_as_mass_unit")
            entity_basis = row_boundary
            if row_is_subset(line.label):
                entity_basis = "subset"
                flags.append("row_limited_to_part_of_group")
            observations.append(Observation(
                code=metric_code, metric_id=metric.metric_id, package_id=metric.package_id, status=status,
                value_raw=value_raw, value=str(number) if number is not None else None, unit_raw=unit_raw,
                unit_id=canonical_unit_id(unit_raw) if unit_raw else None,
                period=current_year, period_year=period_year, entity_basis=entity_basis,
                member=pollutant_key(line.label) if metric.group in ("air", "water_pollutants") else None,
                table_id=view.table_id, page_number=view.page_number, row_id=rid, row_label=line.label,
                cell_id=cell.cell_id if cell else None, column=f"c{row_col}" if row_col is not None else None,
                note_block_id=note.block_id if note else None,
                proposed_code=proposed, flags=flags, rejected_by=rejected_by,
            ))
    # A note is a pollutant method statement when it says so, or when an accepted
    # pollutant row cites it (a footnote under "SO2 排放量³¹" need not repeat "SO2").
    cited = {o.note_block_id for o in observations
             if o.status != "rejected" and o.code in ("E2-4_02", "E2-4_03") and o.note_block_id}
    extras = {"method_notes": [{"block_id": notes[n].block_id, "text": notes[n].text, "page_number": view.page_number}
                               for n in payload.get("method_notes") or []
                               if n in notes and (is_pollution_method_text(notes[n].text)
                                                  or (notes[n].block_id in cited and is_method_text(notes[n].text)))]}
    return observations, extras


def request_digest(instructions: str, context: str, schema: dict) -> str:
    """Identity of one model request; a saved answer is reusable only for the same request."""
    text = json.dumps([instructions, context, schema], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def task_digest(task: TableTask, reporting_year: int | None) -> str:
    instructions, context, schema, _ = build_request(task, reporting_year)
    return request_digest(instructions, context, schema)


def run_task(model, task: TableTask, reporting_year: int | None) -> dict:
    instructions, context, schema, max_tokens = build_request(task, reporting_year)
    started = time.perf_counter()
    record = {"task_id": task.task_id, "table_id": task.view.table_id, "page_number": task.view.page_number,
              "groups": task.groups, "codes": task.codes, "lines": len(task.lines), "context_chars": len(context),
              "request_sha": request_digest(instructions, context, schema)}
    try:
        raw, telemetry = model.generate_structured(
            context=context, instructions=instructions, template=schema, max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - recorded per task, others continue
        record.update(status="model_failed", error=f"{type(exc).__name__}: {exc}"[:500],
                      raw_output=getattr(exc, "raw_output", None), elapsed_seconds=round(time.perf_counter() - started, 2))
        return record
    record.update(
        raw_output=raw, input_tokens=telemetry.get("input_tokens"), output_tokens=telemetry.get("generation_tokens"),
        prompt_tps=telemetry.get("prompt_tps"), generation_tps=telemetry.get("generation_tps"),
        elapsed_seconds=round(time.perf_counter() - started, 2),
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        record.update(status="invalid_json", error=str(exc))
        return record
    observations, extras = bind(task, payload, reporting_year)
    record.update(status="ok", payload=payload, observations=[o.to_dict() for o in observations], **extras)
    return record
