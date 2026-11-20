"""Read and transcribe original PDF pages into the two existing fact types."""

import json
import re
import time
from collections import defaultdict
from hashlib import sha256

from esg_clauses.reading import (
    matches_reported_statement, reported_combined_ghg_scope, reported_ghg_axes, reported_label_conflict,
    reported_scope3_category,
)
from esg_clauses.scope import VALUE_ROLE_CODES
from esg_clauses.search import compile_search_plan
from esg_contracts.data_kinds.qualitative import QualitativeStructure
from esg_contracts.evidence import PDFPageText
from esg_contracts.facts import (
    ClauseCoverage, ClauseMapping, DisclosureObservation, EvidenceLocation,
    QualitativePayload, QuantitativePayload, ReportedContext, ReportedField,
    ScopeDifference, SourceFact,
)
from esg_contracts.results import FactBundle
from esg_data.hashing import digest
from esg_data.literals import has_reported_approximation, parse_number, parse_printed_measure
from esg_data.normalize import fold
from esg_data.periods import YEAR, parse_period, period_payload
from esg_data.quantities import standard_unit
from esg_data.text_search import lexical_score
from esg_data.units import unit_dimension
from esg_extraction.models.errors import ModelCancelled, ModelRunFailure
from esg_extraction.models.structured_template import output_properties

from .page_reading import (
    ANNUAL_HEADER, EMPTY_PRINTED, NOTE_MARKER, annual_headers, applicable_numbered_note, declared_annual_rows,
    missing_annual_periods, numbered_notes, quote_key,
)
from .ports import DocumentUnavailable, ProviderInterruption
from .statistics import RunStatistics


ENGINE_VERSION = "page-reader-3.12"
PAGE_INSTRUCTIONS = """Fill each requested clause independently from the current PDF page. Read the first image when present. Use its text to check spelling and digits. Copy the applicable printed label, period and attributes. Observe which rows and years a footnote covers. Copy the named entity, boundary, method and qualifiers when stated. Omit unknown fields."""
MEASUREMENT_INSTRUCTIONS = """Read totals and relevant breakdowns across all printed columns, in print order. table_year_headers lists possible native headings; use the heading actually attached to each row, including earlier and first columns. If annual_rows is present, check those explicit source rows in the current image as well. For a row or series, use the printed row label and put unit, attributes and relation on the item once. measurements lists each printed period's value or explicit missing disclosure. Use overrides only where they apply. Use the preceding_page header only for a visible continuation. Copy the complete printed quantity including about, ~, ≥, range endpoints, multiplier, chemistry and denominator. Keep printed zeros. Skip unexplained blanks, dashes, slash and N/A. A counted Not Disclosed group still has a value. Return explicit numeric roles with code and printed raw_text. A year alone is not a baseline. A target or limit is different when actual results are requested."""
ASSERTION_INSTRUCTIONS = """Use the relevant complete sentence or bullet as value. If stated, copy short phrases for structure.topic (subject matter), structure.subject (actor or object) and structure.action (what happens). Leave the remaining fields out."""
RELATION_INSTRUCTIONS = """Return compact JSON with items; omit unrelated items and duplicates. Use match, different for a distinct stated meaning or basis, or uncertain, and give a short printed basis in relation_reason for different or uncertain. Scope_ids apply only to matching scopes. For explicit absence write disclosure.state and verbatim disclosure.statement_raw, with the printed row label and affected period. A numbered note in disclosure_text or referenced_disclosure_notes applies only to the row that visibly cites its number and that row's missing period. A slash or other marker with a printed explanation applies to the marked cell. A notes-only page does not disclose an absence across unrelated metrics. If you find no disclosure, omit the item."""
MISSING_PERIOD_INSTRUCTIONS = """Read only the listed missing_period_rows on this original page. Find the printed row and copy each requested column, including its period, unit and actual qualifiers. Check the native printed_cells hints against the original page. Where slash_legend is specified, read the requested years directly from the image: copy printed numbers (including zero), report '/' only in a cell marked by that legend, and omit blanks. Return the supplied printed label for the row and only the requested periods."""
LOCATE_INSTRUCTIONS = """The images are numbered PDF pages in the same order as page_indices. Identify all pages likely to have relevant disclosures for any requested topic, including tables and graphs and their continuation. Return a JSON object with page_indices. This highlights pages for detailed reading; do not extract values."""
LOCATE_SCHEMA = {"type": "object", "required": ["page_indices"], "properties": {
    "page_indices": {"type": "array", "items": {"type": "integer"}}}}
EMPTY_CELL = EMPTY_PRINTED
SLASH_LEGEND = re.compile(r"[“”\"'「」][/／][“”\"'「」]\s*(?:indicates?|means?|表示)\s*[^\r\n]+", re.I)
SLASH_CELLS = re.compile(r"(?:^|[ \t]{2,])[/／](?=[ \t]*\r?$|[ \t]{2,})", re.M)
ABSENCE_TEXT = {
    "not_disclosed": re.compile(
        r"(?:\bnot\s+(?:disclosed|reported)\b|\bno\s+data\s+(?:reported|disclosed)\b|未披露|未报告)(?:\s+(?:for|in)\b.+)?[.。]?", re.I,
    ),
    "not_applicable": re.compile(r"(?:\bnot\s+applicable\b|\binapplicable\b|不适用)[.。]?", re.I),
    "not_available": re.compile(
        r"(?:\bnot\s+(?:available|collected)\b|\bno\s+(?:data(?:\s+available)?|statistical\s+summari[sz]ation)\b|无数据|無數據|未(?:收集|统计|統計|汇总|彙總))(?:\s+(?:for|in)\b.+)?[.。]?", re.I,
    ),
}
PAGE_COMMENT = re.compile(r"^\s*(?:on\s+)?(?:this|the)\s+(?:page|report|document|table)\b", re.I)
MEASURE_ROLES = tuple(code for code in VALUE_ROLE_CODES if code not in {"unknown", "structure", "context"})
PERCENT_UNITS = {"percent", "percentage-point"}
ENERGY_UNITS = {"megajoule", "tonne-coal-equivalent"}
CARBON_UNITS = {"tonne-co2", "tonne-co2e"}
WINDOW_MAX_CALLS = 10
PAGE_SHORTLIST = 8
DENSE_READ_MIN_TARGETS = 25
DENSE_READ_MIN_NUMERIC_LINES = 96
DENSE_READ_MAX_OUTPUT_TOKENS = 6144
MEASUREMENT_LINE = re.compile(r"^[ \t]*[-−<>≈]?\d[\d,. ]*(?:%|[ \t]*[A-Za-z]{1,4})?[ \t\r]*$", re.M)
TABLE_NUMBER = re.compile(r"(?<![\w.,])[-−<>≈]?\d[\d,]*(?:\.\d+)?%?(?![\w.,])")
YEAR_COLUMNS = ANNUAL_HEADER
YEAR_LIST = r"(?:19|20|21)\d{2}"
YEAR_QUALIFIER = re.compile(rf"\s+in\s+({YEAR_LIST}(?:(?:\s*,\s*|\s+and\s+){YEAR_LIST})*)[.。]?$", re.I)
PROVIDER_INTERRUPTION = {"provider_authentication", "provider_balance", "provider_rate_limit"}


class PipelineCancelled(RuntimeError):
    pass


class ExecutionYield(RuntimeError):
    """The next slice uses the saved page plan and results."""

    def __init__(self, window):
        super().__init__("Reading window checkpoint saved")
        self.window = window


def _reading_instructions(kinds):
    instructions = [PAGE_INSTRUCTIONS]
    if "quantitative" in kinds:
        instructions.append(MEASUREMENT_INSTRUCTIONS)
    if "qualitative" in kinds:
        instructions.append(ASSERTION_INSTRUCTIONS)
    instructions.append(RELATION_INSTRUCTIONS)
    return "\n".join(instructions)


def _schema(cards, kinds):
    def object_shape(properties, required=()):
        shape = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            shape["required"] = list(required)
        return shape

    string = {"type": "string"}
    attributes = dict.fromkeys((name for card in cards.values() for name in card["attributes"]), string)
    disclosure = object_shape({
        "state": {"type": "string", "enum": list(ABSENCE_TEXT)}, "statement_raw": string,
    }, required=("state", "statement_raw"))
    fields = {
        "value": string, "period": string,
        "relation": {"type": "string", "enum": ["match", "different", "uncertain"]},
        "relation_reason": string,
        "scope_ids": {"type": "array", "items": string},
        "attributes": object_shape(attributes),
        "disclosure": disclosure,
    }
    row = {"clause_key": {"type": "string", "enum": list(cards)}, "label": string, **fields}
    if "quantitative" in kinds:
        quantitative = {"unit": string, "value_role": object_shape({
            "code": {"type": "string", "enum": list(MEASURE_ROLES)}, "raw_text": string,
        }, required=("code", "raw_text"))}
        row.update(quantitative, measurements={"type": "array", "items": object_shape({**fields, **quantitative})})
    if "qualitative" in kinds:
        row["structure"] = object_shape(dict.fromkeys(("topic", "subject", "action"), string))
    return object_shape({"items": {"type": "array", "items": object_shape(row, required=("clause_key",))}},
        required=("items",))


def _page_score(text, intent):
    if not text.strip():
        return 0
    normalized = fold(text)
    phrases = tuple(dict.fromkeys([*intent["exact_terms"], *intent["positive_terms"]]))
    score = sum(3 if term in intent["exact_terms"] else 1 for term in phrases
                if not term.startswith("re:") and fold(term) in normalized)
    return score or lexical_score(phrases, text)


def _disclosure_table(text):
    if len(MEASUREMENT_LINE.findall(text)) >= 6:
        return True
    counts = [len(TABLE_NUMBER.findall(line)) for line in text.splitlines()]
    return sum(count >= 2 for count in counts) >= 3 and sum(counts) >= 12


def _preceding_header(page_text, page_index):
    """Find an adjacent header to show with a possible continuation."""
    current = page_text[page_index]
    previous = page_text.get(page_index - 1, "")
    if not previous or YEAR_COLUMNS.search(current) or not _disclosure_table(current):
        return None
    return next((line.strip() for line in previous.splitlines()[:24] if YEAR_COLUMNS.search(line)), None)


def page_plan(pages, clauses, page_limit, hint_pages=()):
    """Order useful pages by a cheap text scan and retain a route for scanned pages."""
    by_index = {page.page_index: page.text for page in pages}
    jobs = defaultdict(list)
    unreadable = {index for index, text in by_index.items() if len(text.strip()) < 80}
    table_pages = {index for index, text in by_index.items() if len(MEASUREMENT_LINE.findall(text)) >= 6}
    intents = {key: compile_search_plan(clause)["page"] for key, clause in clauses.items()}
    scores = {key: {index: _page_score(text, intent) for index, text in by_index.items()}
              for key, intent in intents.items()}
    literal_targets = {key: clause for key, clause in clauses.items() if clause.reading.vocabulary.get("annual_read_labels")}
    if literal_targets:
        for index, text in by_index.items():
            headings = annual_headers(text)
            if len(headings) == 1:
                for matched in declared_annual_rows(text, headings[0], literal_targets):
                    scores[matched["clause_key"]][index] += 8
    clause_hits = {index: sum(bool(scored[index]) for scored in scores.values()) for index in by_index}
    rank = {index: sum(scored[index] for scored in scores.values()) + 2 * (index in table_pages)
            for index in by_index}
    limited = []
    for key, scored in scores.items():
        def priority(index):
            return (-scored[index], index not in hint_pages, index not in table_pages, -clause_hits[index], index)

        matches = sorted((index for index in by_index if scored[index]), key=priority)
        focus = set(matches[:PAGE_SHORTLIST]) | {index for index in matches if scored[index] >= 3 and index in table_pages}
        adjacent = {neighbor for index in matches[:3] for neighbor in (index - 1, index + 1) if neighbor in by_index}
        if not focus:
            focus = set(sorted(by_index, key=lambda index: (index not in table_pages, -rank[index], index))[:PAGE_SHORTLIST])
        eligible = focus | adjacent | unreadable | set(hint_pages)
        order = sorted(eligible, key=priority)
        if len(order) > page_limit:
            limited.append(key)
        for index in order[:page_limit]:
            jobs[index].append(key)
    shared_tables = [index for index in jobs if _disclosure_table(by_index[index])]
    for key, scored in scores.items():
        room = page_limit - sum(key in targets for targets in jobs.values())
        extra = (index for index in shared_tables if scored[index] > 0 and key not in jobs[index])
        for index in sorted(extra, key=lambda index: (-scored[index], -rank[index], index))[:room]:
            jobs[index].append(key)
    ordered = sorted(jobs, key=lambda index: (index not in hint_pages, index not in table_pages, -rank[index], index))
    return [[index, jobs[index]] for index in ordered], limited


def _reading_groups(jobs, page_text, max_output_tokens):
    """Split exceptionally dense annual tables before a predictable output cut-off."""
    for index, keys in jobs:
        text = page_text[index]
        if (max_output_tokens <= DENSE_READ_MAX_OUTPUT_TOKENS and len(keys) >= DENSE_READ_MIN_TARGETS
                and YEAR_COLUMNS.search(text) and len(MEASUREMENT_LINE.findall(text)) >= DENSE_READ_MIN_NUMERIC_LINES):
            middle = len(keys) // 2
            yield [index, keys[:middle]]
            yield [index, keys[middle:]]
        else:
            yield [index, keys]


def _reading_fields(clause):
    names = set(clause.purposes.enrichment.identity_axes)
    names.update(clause.purposes.target_scope.discriminating_axes)
    names.add("entity_aggregation_role")
    return {name: definition for name, definition in clause.attributes.items()
            if name in names and name != "reporting_period"}


def _reading_card(clause):
    original = clause.reading_card()
    fields = ("labels", "definition", "distinguish_from", "exclusions", "data_kind", "value", "exact_labels")
    card = {key: original[key] for key in fields}
    card["value"] = {key: value for key, value in card["value"].items() if key != "canonical_unit"}
    card["reading"] = clause.reading.instructions
    card["output_scopes"] = [
        {"scope_id": scope.scope_id, "label": scope.label_zh,
         **{key: value for key, value in scope.canonical().items() if value is not None}}
        for scope in clause.output_scopes
    ]
    card["attributes"] = {}
    for name, definition in _reading_fields(clause).items():
        parts = dict.fromkeys((definition.label, definition.reading_instruction or definition.description))
        card["attributes"][name] = "。".join(part for part in parts if part)
    return card


def _text(item, key):
    value = item.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _absence_state(raw):
    return next((state for state, pattern in ABSENCE_TEXT.items() if pattern.fullmatch(raw)), None)


def _disclosure_text(text):
    return [line.strip() for line in text.splitlines()
            if any(pattern.search(line) for pattern in ABSENCE_TEXT.values())][:8]


def _slash_legend(page_text):
    return next((match[0] for match in SLASH_LEGEND.finditer(page_text)
                 if ABSENCE_TEXT["not_available"].search(match[0])), None)


def _missing_slash_rows(items, page_text, quantitative_keys):
    """Ask vision to associate printed markers with rows and year columns."""
    legend = _slash_legend(page_text)
    if not legend or not SLASH_CELLS.search(page_text):
        return []
    years = list(dict.fromkeys(re.findall(YEAR, page_text)))
    page_key = quote_key(page_text)
    seen = defaultdict(dict)
    for item in items:
        if not isinstance(item, dict) or item.get("clause_key") not in quantitative_keys:
            continue
        label = _text(item, "label")
        if not label or not (key := quote_key(label)) or key not in page_key:
            continue
        series = item.get("measurements")
        for cell in series if isinstance(series, list) else [item]:
            if (isinstance(cell, dict) and cell.get("relation", item.get("relation")) == "match"
                    and (period := _text(cell, "period")) in years):
                seen[(item["clause_key"], label)][period] = _text(cell, "value")
    rows = []
    for (key, label), observed in seen.items():
        periods = [year for year in years if year not in observed]
        if periods and any(value and parse_number(value) is not None for value in observed.values()):
            rows.append({"clause_key": key, "label": label, "periods": periods,
                         "printed_cells": {}, "slash_legend": legend})
    return rows


def _related_disclosure_notes(page_text, index):
    current = page_text[index]
    cited = set(NOTE_MARKER.findall(current))
    notes = [(number, note, index) for number, note in numbered_notes(current) if _disclosure_text(note)]
    if cited:
        for other_index in range(index + 1, index + 4):
            notes.extend(
                (number, note, other_index) for number, note in numbered_notes(page_text.get(other_index, ""))
                if number in cited and _disclosure_text(note)
            )
    return notes


def _disclosure(item, page_index, quantitative, page_text, referenced_notes=()):
    raw = _text(item, "value")
    if quantitative and raw and raw.casefold() in ABSENCE_TEXT:
        raw = None  # Model status codes are not printed disclosures.
    raw_state = _absence_state(raw) if quantitative and raw else None
    if raw and not raw_state and not EMPTY_CELL.fullmatch(raw):
        return None
    details = item.get("disclosure")
    stated = _text(details, "statement_raw") if isinstance(details, dict) else None
    state = _text(details, "state") if isinstance(details, dict) else None
    if raw in {"/", "／"} and (legend := _slash_legend(page_text)):
        stated, state = legend, "not_available"
    if not stated and raw_state:
        stated = raw
        state = raw_state
    if not stated or state not in ABSENCE_TEXT or PAGE_COMMENT.match(stated):
        return None
    observed_state = _absence_state(stated)
    if observed_state:
        state = observed_state
    elif not ABSENCE_TEXT[state].search(stated):
        return None
    label, period = _text(item, "label"), _text(item, "period")
    quotation = quote_key(stated)
    notes = referenced_notes or [(number, note, page_index) for number, note in numbered_notes(page_text)]
    matching_notes = [(number, note, source_page) for number, note, source_page in notes
                      if quotation and quotation in quote_key(note)]
    if matching_notes:
        applicable = [source_page for number, note, source_page in matching_notes
                      if applicable_numbered_note(label, period, page_text, note, number)]
        if len(applicable) != 1:
            return None
        page_index = applicable[0]
    elif page_text and any(character.isalpha() for character in page_text):
        if not quotation or quotation not in quote_key(page_text):
            return None
    if raw in {"/", "／"} and (not label or not period or quote_key(label) not in quote_key(page_text)):
        return None
    return DisclosureObservation(state=state, statement_raw=stated, page_index=page_index,
        period_raw=period, source_row_label=label)


def _period_repair_items(items, missing, page_text):
    """Use the supplemental reading only for its requested printed cells."""
    wanted = defaultdict(list)
    for row in missing:
        wanted[(row["clause_key"], quote_key(row["label"]))].append(row)
    page_literals = {parse_number(match[0]) for match in TABLE_NUMBER.finditer(page_text)} if any(
        row.get("slash_legend") for row in missing) else set()
    page_literals.discard(None)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("label"), str):
            continue
        rows = wanted.get((item.get("clause_key"), quote_key(item["label"])))
        if not rows:
            continue
        columns = item.get("measurements")
        columns = columns if isinstance(columns, list) else [item]
        readings = []
        for cell in columns:
            if not isinstance(cell, dict):
                continue
            period, value = _text(cell, "period"), _text(cell, "value")
            if not period or not value:
                continue
            for row in rows:
                if period not in row["periods"]:
                    continue
                if row.get("slash_legend"):
                    if value not in {"/", "／"} and parse_number(value) not in page_literals:
                        continue
                else:
                    expected = parse_number(row["printed_cells"][period])
                    if expected is None or parse_number(value) != expected:
                        break
                readings.append(cell)
                break
        if readings:
            yield {**item, "label": rows[0]["label"], "measurements": readings}


def _annual_attribute(text, period):
    """Select the literal phrase when a model groups explicit year-specific scopes."""
    if not period or period.kind != "annual":
        return text
    parts = re.split(r"\s*[;；]\s*", text)
    if len(parts) == 1:
        return text
    selected = []
    for part in parts:
        qualifier = YEAR_QUALIFIER.search(part)
        if qualifier is None:
            return text
        if str(period.year) in re.findall(YEAR, qualifier[1]):
            selected.append(part)
    return selected[0] if len(selected) == 1 else text


def _completed_items(prefix):
    """Keep each whole returned row before a token-limited JSON answer ends."""
    body = prefix.lstrip()
    if not body.startswith("{"):
        return
    body = body[1:].lstrip()
    decoder = json.JSONDecoder(object_pairs_hook=output_properties)
    try:
        field, end = decoder.raw_decode(body)
    except ValueError:
        return
    if not isinstance(field, str) or field.strip() != "items":
        return
    start = re.match(r"\s*:\s*\[", body[end:])
    if start is None:
        return
    tail = body[end + start.end():].lstrip()
    while tail.startswith("{"):
        try:
            item, end = decoder.raw_decode(tail)
        except ValueError:
            return
        yield item
        tail = tail[end:].lstrip()
        if not tail.startswith(","):
            return
        tail = tail[1:].lstrip()


def _unit_scope_difference(printed_unit, canonical_unit, target_dimension=None):
    actual_dimension = unit_dimension(printed_unit)
    rate_for_quantity = (actual_dimension and actual_dimension.endswith("intensity")
                         and target_dimension in {"energy", "mass", "volume", "ghg-emissions"})
    actual = standard_unit(printed_unit)
    expected = standard_unit(canonical_unit)
    if not actual or not expected:
        if rate_for_quantity:
            return ScopeDifference(axis="unit_dimension", reported=actual_dimension,
                                   expected=target_dimension, raw_text=printed_unit)
        return None
    numerator, _, denominator = expected[0].partition("/")
    actual_numerator = actual[0].split("/", 1)[0]
    percent_for_quantity = actual[0] in PERCENT_UNITS and numerator not in PERCENT_UNITS and numerator != denominator
    non_energy = CARBON_UNITS | {"cubic-metre"}
    incompatible_media = ((numerator in non_energy and actual_numerator in ENERGY_UNITS)
        or (actual_numerator in non_energy and numerator in ENERGY_UNITS))
    if not (percent_for_quantity or incompatible_media or rate_for_quantity):
        return None
    return ScopeDifference(axis="unit_dimension", reported=actual[0], expected=expected[0], raw_text=printed_unit)


def _fact(document, clause, page_index, item, visual, page_text=""):
    raw = _text(item, "value")
    quantitative = clause.data_kind == "quantitative"
    if not raw or (quantitative and (EMPTY_CELL.fullmatch(raw) or _absence_state(raw) or raw.casefold() in ABSENCE_TEXT)):
        return None
    unit = _text(item, "unit")
    label = _text(item, "label")
    period = _text(item, "period")
    role = "value" if quantitative else "statement"
    location = EvidenceLocation(object_id=f"pdf-page-{page_index + 1}", object_type="pdf_page",
        page_index=page_index, excerpt=raw, role=role)

    def field(text, value):
        # Page provenance describes the source of the transcription, not its verification status.
        return ReportedField(state="stated", raw_text=text, value=value,
            evidence=[location.model_copy(update={"excerpt": text})])

    axes = {}
    dimensions = {}
    recognized = {}
    parsed_period = parse_period(period)
    if period:
        axes["reporting_period"] = field(period, period_payload(parsed_period) if parsed_period else period)
    attributes = item.get("attributes")
    if isinstance(attributes, dict):
        for name, attribute in _reading_fields(clause).items():
            text = _text(attributes, name)
            if text:
                text = _annual_attribute(text, parsed_period)
            if text and attribute.accepts_value(text):
                target = axes if attribute.source == "identity" and name in ReportedContext.model_fields else dimensions
                category = attribute.classify({"state": "stated", "value": text, "raw_text": text})
                if category and category.state == "recognized":
                    recognized[name] = category.code
                target[name] = field(text, recognized.get(name, text))
    combined_scope = reported_combined_ghg_scope(clause, label)
    if combined_scope:
        dimensions["ghg_scope"] = field(label, combined_scope)
        recognized["ghg_scope"] = combined_scope
    if "scope3_category" not in dimensions and (category := reported_scope3_category(clause, label, unit)):
        dimensions["scope3_category"] = field(label, category)
        recognized["scope3_category"] = category
    method_specific = "aggregate_ghg_label" in clause.reading.vocabulary
    if method_specific:
        # Do not use target codes printed by the model as measurements of the source.
        dimensions.pop("ghg_scope", None)
        axes.pop("measurement_method", None)
        for name in ("ghg_scope", "measurement_method"):
            recognized.pop(name, None)
        relevant = {name: _text(attributes, name) for name in ("ghg_scope", "measurement_method")} if isinstance(attributes, dict) else {}
        for name, (words, code) in reported_ghg_axes(clause, label, relevant).items():
            (axes if name == "measurement_method" else dimensions)[name] = field(words, code)
            recognized[name] = code
    value_role = item.get("value_role")
    if quantitative and isinstance(value_role, dict):
        code, wording = _text(value_role, "code"), _text(value_role, "raw_text")
        if code in MEASURE_ROLES and wording and not re.fullmatch(YEAR, wording):
            axes["value_role"] = field(wording, code)
            recognized["value_role"] = code
    identity = ReportedContext(**axes, dimensions=dimensions)
    if quantitative:
        numeric, printed_unit = parse_printed_measure(raw, unit)
        parsed = {"parse_status": "parsed", "comparator": numeric["comparator"]} if numeric else {}
        if numeric:
            if numeric["comparator"] == "exact" and has_reported_approximation(label, raw, page_text):
                parsed["comparator"] = "approx"
            for source, target in (("value", "value_numeric"), ("lower", "value_lower"), ("upper", "value_upper")):
                if numeric[source] is not None:
                    parsed[target] = format(numeric[source], "f")
        value = QuantitativePayload(value_raw=raw, unit_raw=printed_unit, **parsed)
    else:
        structure = item.get("structure")
        fields = {name: _text(structure, name) for name in QualitativeStructure.model_fields} if isinstance(structure, dict) else {}
        value = QualitativePayload(statement_raw=raw,
            structure=QualitativeStructure(**fields) if any(fields.values()) else None)
    anchor = digest([document.source_sha256 or document.document_id, page_index, label, period, unit, raw])
    fact = SourceFact(schema_version="source-fact-2.1", source_fact_id=anchor, source_anchor_id=anchor,
        document_id=document.document_id, ir_run_id=document.ir_run_id, ir_revision=document.ir_revision,
        interpretation_id=anchor, report_resource=document.report_resource, value=value,
        source_row_label=label, identity=identity, evidence=[location], quality="grounded",
        flags=["visual_transcription" if visual else "text_transcription"])
    interpretation = digest(fact.model_dump(mode="json", exclude={"source_fact_id", "interpretation_id"}))
    fact = fact.model_copy(update={"source_fact_id": interpretation, "interpretation_id": interpretation})
    relation = _text(item, "relation")
    reason = _text(item, "relation_reason")
    expected = dict(clause.value.fixed_axes)
    if quantitative and "value_role" not in expected:
        roles = {scope.value_role for scope in clause.output_scopes}
        if len(roles) == 1 and None not in roles:
            expected["value_role"] = roles.pop()
    differences = [ScopeDifference(axis=name, reported=recognized[name], expected=expected[name],
                       raw_text=(axes.get(name) or dimensions[name]).raw_text)
                   for name in clause.purposes.target_scope.discriminating_axes
                   if name in recognized and name in expected and recognized[name] != expected[name]
                   and (name, recognized[name], expected[name]) != ("value_role", "restated", "actual")]
    if quantitative and (unit_difference := _unit_scope_difference(printed_unit, clause.value.canonical_unit, clause.dimension)):
        differences.append(unit_difference)
    label_conflict = reported_label_conflict(clause, label if quantitative else raw,
                                              unit_dimension=unit_dimension(printed_unit) if quantitative else None)
    if differences:
        relation = "different"
        details = ", ".join(f"{change.axis}={change.reported} (target {change.expected})" for change in differences)
        reason = f"Reported scope differs: {details}" + (f". {reason}" if reason else "")
    elif label_conflict:
        relation, reason = "different", label_conflict
    elif method_specific and relation == "match" and not {"ghg_scope", "measurement_method"} <= recognized.keys():
        relation, reason = "uncertain", "The row does not independently identify both emission scope and calculation method"
    elif relation in (None, "uncertain") and matches_reported_statement(clause, raw):
        relation = "match"
        reason = "The reported assertion states a practice described by the clause"
    reported_scopes = item.get("scope_ids")
    scope_ids = [scope.scope_id for scope in clause.output_scopes if scope.scope_id in reported_scopes
                 ] if relation == "match" and isinstance(reported_scopes, list) else []
    mapping = ClauseMapping(clause_key=clause.clause_key, clause_digest=clause.clause_digest,
        source_fact_id=fact.source_fact_id, match=relation if relation in {"match", "different", "uncertain"} else "uncertain",
        reason=reason or (f"Read under the printed label ‘{label}’" if label else "Reported on the selected PDF page"),
        scope_ids=scope_ids, scope_differences=differences)
    return fact, mapping


class ExtractionBackbone:
    def __init__(self, documents, model, artifacts, results, engine_identity=None, document_model=None):
        self.documents = documents
        self.model = model
        self.document_model = document_model
        self.artifacts = artifacts
        self.results = results
        self.engine_identity = engine_identity or {"version": ENGINE_VERSION}

    def run(self, run_id, request, clauses, *, document, cancel_check, on_progress, waiting_tasks=0, **_):
        budget = request.budget
        asset = request.asset_id or (document.report_resource.asset_id if document.report_resource else None)
        if asset is None and document.source_sha256:
            asset = "pdf-" + document.source_sha256[:24]
        if not asset:
            raise ValueError("The requested IR needs its original PDF asset")
        state = self.results.checkpoint()
        self.artifacts.write(run_id, "input/engine.json", self.engine_identity)
        window_started = time.monotonic()
        started = window_started - state.get("elapsed_seconds", 0)
        calls = state.get("calls", 0)
        turn_start = calls
        window_seconds = 45 if not waiting_tasks else 30 if waiting_tasks < 4 else 20 if waiting_tasks < 10 else 12
        errors = list(state.get("errors", []))
        inspected = state.get("inspected", {})
        stats = RunStatistics(clauses)
        existing = self.results.collection()
        old = {f["source_fact_id"]: SourceFact.model_validate(f) for f in existing["facts"]}
        covered_scopes = defaultdict(set)
        for entry in existing["clause_mappings"]:
            mapping = ClauseMapping.model_validate(entry)
            stats.record(old[mapping.source_fact_id], mapping)
            covered_scopes[mapping.clause_key].update(mapping.scope_ids)
        try:
            catalog = [PDFPageText.model_validate(page) for page in self.artifacts.read(run_id, "input/pages.json")]
        except FileNotFoundError:
            catalog = self.documents.pages(asset)
            self.artifacts.write(run_id, "input/pages.json", [page.model_dump(mode="json") for page in catalog])
        page_text = {page.page_index: page.text for page in catalog}
        hint_pages = set(state.get("hint_pages", []))
        scope = document.search_scope or {}
        for page in scope.get("available_pages") or scope.get("page_indices") or ():
            if page in page_text:
                hint_pages.add(page)

        def remaining():
            return budget.timeout_seconds - (time.monotonic() - started)

        def window_used():
            count = calls - turn_start
            if not count:
                return False
            elapsed = time.monotonic() - window_started
            return count >= WINDOW_MAX_CALLS or elapsed + elapsed / count >= window_seconds

        def yield_window():
            raise ExecutionYield({
                "model_calls": calls - turn_start,
                "elapsed_seconds": round(time.monotonic() - window_started, 3),
                "target_seconds": window_seconds,
                "waiting_at_dispatch": waiting_tasks,
            })

        def checkpoint(**updates):
            state.update(updates, calls=calls, elapsed_seconds=time.monotonic() - started,
                         errors=errors, inspected=inspected, hint_pages=sorted(hint_pages))
            self.results.checkpoint(state)

        def pause_provider(exc, model, phase, **resume_position):
            category = getattr(exc, "category", None)
            if category in PROVIDER_INTERRUPTION:
                self.results.flush()
                checkpoint(**resume_position)
                provider = getattr(model, "provider_name", "unknown")
                raise ProviderInterruption(provider, category, str(exc)[:240], phase) from exc

        def call(model, indices, instructions, values, schema, phase, images):
            nonlocal calls
            if cancel_check():
                raise PipelineCancelled()
            calls += 1
            on_progress({"stage": phase, "object_id": f"pdf-page-{indices[0] + 1}",
                "current": state.get("position", 0), "total": len(state.get("jobs", [])),
                "model_calls": calls, "fact_count": stats.fact_count})
            context_pages = [values["preceding_page"]["page_index"]] if values.get("preceding_page") else []
            context = {**values, "execution": {"run_id": run_id, "task_type": phase,
                "evidence_ids": [f"pdf-page-{index + 1}" for index in [*indices, *context_pages]],
                "remaining_seconds": max(0.001, remaining())}}
            trace = {"call_index": calls, "phase": phase, "page_indices": indices,
                     "image_digests": [sha256(image).hexdigest() for image in images]}
            if context_pages:
                trace["context_pages"] = context_pages
            try:
                result, info = model.complete(instructions=instructions, context=context,
                    schema=schema, max_tokens=budget.max_output_tokens, cancel_check=cancel_check,
                    **({"images": images} if images else {}))
                result = result if isinstance(result, dict) else json.loads(result)
                trace.update(status="ok", telemetry=info)
                return result
            except ModelCancelled as exc:
                raise PipelineCancelled() from exc
            except (ModelRunFailure, ValueError) as exc:
                trace.update(status="failed", telemetry=getattr(exc, "telemetry", {}), error=str(exc)[:300])
                if getattr(exc, "category", None) == "generation_incomplete":
                    trace["recoverable_items"] = sum(1 for _ in _completed_items(exc.cleaned_output))
                raise
            finally:
                self.artifacts.write(run_id, f"diagnostics/calls/{calls}.json", trace)

        if "jobs" not in state:
            # Optional document-stage vision orders pages with poor text. It never delays useful
            # extraction after exhausting its small share of the run budget or erases candidates.
            weak = [index for index, text in page_text.items() if len(text.strip()) < 80]
            loc_budget = min(8, budget.max_model_calls // 5)
            if self.document_model is not None:
                for offset in range(state.get("located", 0), min(len(weak), loc_budget * 6), 6):
                    if remaining() <= 0:
                        break
                    group = weak[offset:offset + 6]
                    images = tuple(self.documents.image(asset, index, thumbnail=True) for index in group)
                    try:
                        answer = call(self.document_model, group, LOCATE_INSTRUCTIONS,
                            {"page_indices": group, "topics": [c.title for c in clauses.values()]},
                            LOCATE_SCHEMA, "locating_pages", images)
                        if not isinstance(answer.get("page_indices"), list):
                            raise ValueError("Document locator did not return page indices")
                        hint_pages.update(index for index in answer["page_indices"] if index in group)
                    except (ModelRunFailure, ValueError) as exc:
                        pause_provider(exc, self.document_model, "locating_pages", located=offset)
                        state.setdefault("location_warnings", []).append(str(exc)[:240])
                        if getattr(exc, "category", None) == "request_config":
                            break
                    checkpoint(located=offset + len(group))
                    if remaining() > 0 and window_used():
                        yield_window()
            jobs, limited = page_plan(catalog, clauses, budget.max_objects_per_clause, hint_pages)
            groups = list(_reading_groups(jobs, page_text, budget.max_output_tokens))
            checkpoint(jobs=groups, limited=limited, position=0)
        jobs = state["jobs"]
        cards = {key: _reading_card(clause) for key, clause in clauses.items()}
        position = state["position"]
        stop_provider = False
        visual = getattr(self.model, "accepts_images", False)

        def record_disclosure(key, row, index, notes):
            disclosure = _disclosure(row, index, clauses[key].data_kind == "quantitative", page_text[index], notes)
            if disclosure is not None:
                observations = state.setdefault("disclosure_observations", {}).setdefault(key, [])
                body = disclosure.model_dump(mode="json", exclude_none=True)
                if body not in observations:
                    observations.append(body)

        def record_rows(items, keys, index, notes):
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = item.get("clause_key")
                if key not in keys:
                    continue
                series = item.get("measurements")
                if isinstance(series, list):
                    record_disclosure(key, item, index, notes)
                    base = {name: value for name, value in item.items() if name not in {"value", "disclosure"}}
                    columns = series
                else:
                    base, columns = item, [{}]
                for column in columns:
                    if not isinstance(column, dict):
                        continue
                    row = {**base, **column}
                    if "relation" in column and "relation_reason" not in column:
                        row.pop("relation_reason", None)
                    shared = item.get("attributes")
                    override = column.get("attributes")
                    if isinstance(shared, dict) and isinstance(override, dict):
                        row["attributes"] = {**shared, **override}
                    record_disclosure(key, row, index, notes)
                    output = _fact(document, clauses[key], index, row, visual, page_text[index])
                    if not output:
                        continue
                    fact, mapping = output
                    self.results.put(fact, mapping)
                    stats.record(fact, mapping)
                    covered_scopes[key].update(mapping.scope_ids)

        while position < len(jobs) and calls < budget.max_model_calls and remaining() > 0:
            if cancel_check():
                raise PipelineCancelled()
            index, keys = jobs[position]
            failure = None
            notes = _related_disclosure_notes(page_text, index)
            try:
                image = (self.documents.image(asset, index),) if visual else ()
                if not image and not page_text[index].strip():
                    raise ModelRunFailure("This page has no native text; choose an image-capable extraction model",
                                          category="page_text_missing")
                page_cards = {key: cards[key] for key in keys}
                kinds = {card["data_kind"] for card in page_cards.values()}
                context = {"page_index": index, "page_text": page_text[index][:12000], "clauses": page_cards}
                if disclosure_text := _disclosure_text(page_text[index]):
                    context["disclosure_text"] = disclosure_text
                if external_notes := [{"note": number, "text": note, "pdf_page_index": other_index}
                                      for number, note, other_index in notes if other_index != index]:
                    context["referenced_disclosure_notes"] = external_notes
                header = _preceding_header(page_text, index) if "quantitative" in kinds else None
                year_headers = annual_headers(page_text[index]) if "quantitative" in kinds else []
                if header:
                    context["preceding_page"] = {"page_index": index - 1, "column_header": header}
                    year_headers = annual_headers(header)
                    if visual:
                        try:
                            image += (self.documents.image(asset, index - 1, thumbnail=True),)
                        except DocumentUnavailable:
                            pass  # The active page and the saved header are already available.
                if year_headers:
                    context["table_year_headers"] = year_headers
                literal_rows = declared_annual_rows(page_text[index], year_headers[0],
                    {key: clauses[key] for key in keys if page_cards[key]["data_kind"] == "quantitative"}) if len(year_headers) == 1 else []
                if literal_rows:
                    context["annual_rows"] = literal_rows
                missing = state.get("missing_period_rows", [])
                if not missing:
                    answer = call(self.model, [index], _reading_instructions(kinds), context, _schema(page_cards, kinds), "reading", image)
                    if not isinstance(answer.get("items"), list):
                        raise ValueError("Extraction model did not return an items array")
                    record_rows(answer["items"], keys, index, notes)
                    if calls < budget.max_model_calls and remaining() > 0:
                        quantitative_keys = {key for key, card in page_cards.items() if card["data_kind"] == "quantitative"}
                        if len(year_headers) == 1:
                            missing = missing_annual_periods(answer["items"], page_text[index], year_headers[0], quantitative_keys, literal_rows)
                        if visual:
                            missing += _missing_slash_rows(answer["items"], page_text[index], quantitative_keys)
                        missing = missing[:16]
                if missing:
                    # Persist both completed work and this narrow read before a window yield or provider stop.
                    self.results.flush()
                    checkpoint(position=position, missing_period_rows=missing)
                    if window_used():
                        break
                    repair_cards = {row["clause_key"]: page_cards[row["clause_key"]] for row in missing}
                    repair_context = {**context, "clauses": repair_cards, "missing_period_rows": missing}
                    try:
                        repair = call(self.model, [index], MISSING_PERIOD_INSTRUCTIONS, repair_context,
                                      _schema(repair_cards, {"quantitative"}), "reading_missing_periods", image)
                        if isinstance(repair.get("items"), list):
                            record_rows(_period_repair_items(repair["items"], missing, page_text[index]), repair_cards, index, notes)
                    except (ModelRunFailure, ValueError) as exc:
                        pause_provider(exc, self.model, "reading_missing_periods", position=position)
                        if getattr(exc, "category", None) == "generation_incomplete":
                            partial = _completed_items(getattr(exc, "cleaned_output", ""))
                            record_rows(_period_repair_items(partial, missing, page_text[index]), repair_cards, index, notes)
                        state.setdefault("period_read_warnings", []).append(f"PDF page {index + 1}: {str(exc)[:220]}")
                    state.pop("missing_period_rows", None)
            except (ModelRunFailure, ValueError, KeyError, TypeError) as exc:
                pause_provider(exc, self.model, "reading", position=position)
                category = getattr(exc, "category", "invalid_model_output")
                stop_provider = category == "request_config"
                if len(keys) > 1 and calls < budget.max_model_calls and remaining() > 0 and category in {
                    "generation_incomplete", "invalid_model_output", "input_budget"}:
                    split = len(keys) // 2
                    jobs[position:position + 1] = [[index, keys[:split]], [index, keys[split:]]]
                    checkpoint(jobs=jobs, position=position)
                    if window_used():
                        break
                    continue
                if category == "generation_incomplete":
                    record_rows(_completed_items(getattr(exc, "cleaned_output", "")), keys, index, notes)
                    self.results.flush()
                failure = str(exc)[:300]
            if failure:
                errors.extend({"clause_key": key, "object_id": f"pdf-page-{index + 1}", "error": failure}
                              for key in keys)
                if stop_provider:
                    break
            else:
                for key in keys:
                    inspected[key] = inspected.get(key, 0) + 1
            position += 1
            self.results.flush()
            checkpoint(position=position)
            if window_used():
                break
        pending = position < len(jobs)
        continuing = pending and calls < budget.max_model_calls and remaining() > 0 and not stop_provider
        final = not continuing
        coverage = []
        completed = []
        future = {key for _, keys in jobs[position:] for key in keys}
        failed = {error["clause_key"] for error in errors}
        page_counts = defaultdict(int)
        fact_counts = defaultdict(int)
        matches = set()
        for _, keys in jobs:
            for key in keys:
                page_counts[key] += 1
        for (_, key), (relation, _) in stats.links.items():
            fact_counts[key] += 1
            if relation == "match":
                matches.add(key)
        for key, clause in clauses.items():
            if not final and key in future:
                continue
            reasons = []
            if key in state["limited"]:
                reasons.append("object_budget_exhausted")
            if key in future:
                reasons.append("execution_budget_exhausted")
            if key in failed:
                reasons.append("document_read_failed")
            if not reasons:
                completed.append(key)
            coverage.append(ClauseCoverage(clause_key=key, clause_digest=clause.clause_digest,
                state="covered" if key in matches else "failed" if key in failed else
                    "not_covered" if reasons else "unconfirmed" if fact_counts[key] else "not_found",
                candidate_objects=page_counts[key], inspected_objects=inspected.get(key, 0),
                fact_count=fact_counts[key], reasons=reasons,
                required_scopes=[scope.scope_id for scope in clause.output_scopes if scope.required],
                covered_scopes=sorted(covered_scopes[key]),
                disclosure_observations=state.get("disclosure_observations", {}).get(key, [])))
        body = {"engine_version": ENGINE_VERSION, "engine_identity": self.engine_identity,
            "run_id": run_id, "document": document.model_dump(mode="json"), "sources": {},
            "clause_targets": {key: clause.clause_digest for key, clause in clauses.items()},
            "coverage": [item.model_dump(mode="json") for item in coverage],
            "summary": {"selected_clauses": len(clauses), "processed_clauses": len(coverage),
                "model_calls": calls, "elapsed_seconds": round(time.monotonic() - started, 3),
                "errors": errors, **stats.summary(coverage, errors, final)}}
        self.results.status(body)
        checkpoint(completed_clauses=completed)
        if continuing:
            yield_window()
        body.update(self.results.collection())
        body = FactBundle.model_validate(body).model_dump(mode="json", exclude_none=True)
        self.artifacts.write(run_id, "results/facts.json", body)
        return body
