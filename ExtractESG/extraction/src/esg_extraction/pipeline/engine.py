"""Read and transcribe original PDF pages into the two existing fact types."""

import json
import re
import time
import unicodedata
from collections import defaultdict
from hashlib import sha256

from esg_clauses.reading import matches_reported_statement, reported_combined_ghg_scope
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
from esg_data.literals import has_reported_approximation, parse_printed_measure
from esg_data.normalize import fold
from esg_data.periods import YEAR, parse_period, period_payload
from esg_data.quantities import standard_unit
from esg_data.text_search import lexical_score
from esg_extraction.models.errors import ModelCancelled, ModelRunFailure
from esg_extraction.models.structured_template import output_properties

from .ports import DocumentUnavailable
from .statistics import RunStatistics


ENGINE_VERSION = "page-reader-3.11"
PAGE_INSTRUCTIONS = """Fill each requested clause independently from the current PDF page. Read the first image when present. Use its text to check spelling and digits. Copy the applicable printed label, period and attributes. Observe which rows and years a footnote covers. Copy the named entity, boundary, method and qualifiers when stated. Omit unknown fields."""
MEASUREMENT_INSTRUCTIONS = """Read totals and relevant breakdowns in every printed column, including the first data column. For a table row or chart series, put label, unit, attributes and relation on the item once; measurements lists every printed period's value or explicit missing disclosure, with overrides only when fields change. Use the preceding_page header only for a visible continuation. Copy the complete printed quantity including qualifiers such as about, ~, ≥, and both range endpoints. Copy unit, multiplier, chemistry and denominator. Keep printed zeros. Skip unexplained blanks, dashes and N/A. A counted Not Disclosed group still has a value. Return explicit numeric roles with code and printed raw_text. A year alone is not a baseline. A target or limit is different when actual results are requested."""
ASSERTION_INSTRUCTIONS = """Use the relevant complete sentence or bullet as value. If stated, copy short phrases for structure.topic (subject matter), structure.subject (actor or object) and structure.action (what happens). Leave the remaining fields out."""
RELATION_INSTRUCTIONS = """Return compact JSON with items; omit unrelated items and duplicates. Use match, different for a distinct stated meaning or basis, or uncertain, and give a short printed basis in relation_reason for different or uncertain. Scope_ids apply only to matching scopes. For explicit absence write disclosure.state and verbatim disclosure.statement_raw, with the affected label and period. If a table cell carries a note, follow the printed note to the affected period in that row. disclosure_text contains candidate quotes; use them only where applicable. If you find no disclosure, omit the item instead of commenting on the page."""
LOCATE_INSTRUCTIONS = """The images are numbered PDF pages in the same order as page_indices. Identify all pages likely to have relevant disclosures for any requested topic, including tables and graphs and their continuation. Return a JSON object with page_indices. This highlights pages for detailed reading; do not extract values."""
LOCATE_SCHEMA = {"type": "object", "required": ["page_indices"], "properties": {
    "page_indices": {"type": "array", "items": {"type": "integer"}}}}
EMPTY_CELL = re.compile(r"(?:[-–—－]+|n[/\.]?a|none|null|无)[.。]?", re.I)
ABSENCE_TEXT = {
    "not_disclosed": re.compile(
        r"(?:\bnot\s+(?:disclosed|reported)\b|\bno\s+data\s+(?:reported|disclosed)\b|未披露|未报告)(?:\s+(?:for|in)\b.+)?[.。]?", re.I,
    ),
    "not_applicable": re.compile(r"(?:\bnot\s+applicable\b|\binapplicable\b|不适用)[.。]?", re.I),
    "not_available": re.compile(
        r"(?:\bnot\s+(?:available|collected)\b|\bno\s+data(?:\s+available)?\b|无数据|未收集)(?:\s+(?:for|in)\b.+)?[.。]?", re.I,
    ),
}
PAGE_COMMENT = re.compile(r"^\s*(?:on\s+)?(?:this|the)\s+(?:page|report|document|table)\b", re.I)
MEASURE_ROLES = tuple(code for code in VALUE_ROLE_CODES if code not in {"unknown", "structure", "context"})
PERCENT_UNITS = {"percent", "percentage-point"}
ENERGY_UNITS = {"megajoule", "tonne-coal-equivalent"}
CARBON_UNITS = {"tonne-co2", "tonne-co2e"}
WINDOW_MAX_CALLS = 10
PAGE_SHORTLIST = 8
MEASUREMENT_LINE = re.compile(r"^[ \t]*[-−<>≈]?\d[\d,. ]*(?:%|[ \t]*[A-Za-z]{1,4})?[ \t\r]*$", re.M)
TABLE_NUMBER = re.compile(r"(?<![\w.,])[-−<>≈]?\d[\d,]*(?:\.\d+)?%?(?![\w.,])")
YEAR_COLUMNS = re.compile(r"(?<![\d.,])(?:19|20)\d{2}(?:[ \t]+(?:19|20)\d{2})+(?![\d.,])")
YEAR_LIST = r"(?:19|20|21)\d{2}"
YEAR_QUALIFIER = re.compile(rf"\s+in\s+({YEAR_LIST}(?:(?:\s*,\s*|\s+and\s+){YEAR_LIST})*)[.。]?$", re.I)
PROVIDER_STOP = {"provider_authentication", "provider_balance", "request_config"}


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


def _quotation_text(text):
    normalized = fold(unicodedata.normalize("NFKC", text))
    return "".join(character for character in normalized if character.isalnum())


def _disclosure_text(text):
    return [line.strip() for line in text.splitlines()
            if any(pattern.search(line) for pattern in ABSENCE_TEXT.values())][:8]


def _disclosure(item, page_index, quantitative, page_text):
    raw = _text(item, "value")
    raw_state = _absence_state(raw) if quantitative and raw else None
    if raw and not raw_state and not EMPTY_CELL.fullmatch(raw):
        return None
    details = item.get("disclosure")
    stated = _text(details, "statement_raw") if isinstance(details, dict) else None
    state = _text(details, "state") if isinstance(details, dict) else None
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
    if page_text and any(character.isalpha() for character in page_text):
        quotation = _quotation_text(stated)
        if not quotation or quotation not in _quotation_text(page_text):
            return None
    return DisclosureObservation(state=state, statement_raw=stated, page_index=page_index,
        period_raw=_text(item, "period"), source_row_label=_text(item, "label"))


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


def _unit_scope_difference(printed_unit, canonical_unit):
    actual = standard_unit(printed_unit)
    expected = standard_unit(canonical_unit)
    if not actual or not expected:
        return None
    numerator, _, denominator = expected[0].partition("/")
    actual_numerator = actual[0].split("/", 1)[0]
    percent_for_quantity = actual[0] in PERCENT_UNITS and numerator not in PERCENT_UNITS and numerator != denominator
    non_energy = CARBON_UNITS | {"cubic-metre"}
    incompatible_media = ((numerator in non_energy and actual_numerator in ENERGY_UNITS)
        or (actual_numerator in non_energy and numerator in ENERGY_UNITS))
    if not percent_for_quantity and not incompatible_media:
        return None
    return ScopeDifference(axis="unit_dimension", reported=actual[0], expected=expected[0], raw_text=printed_unit)


def _fact(document, clause, page_index, item, visual, page_text=""):
    raw = _text(item, "value")
    quantitative = clause.data_kind == "quantitative"
    if not raw or (quantitative and (EMPTY_CELL.fullmatch(raw) or _absence_state(raw))):
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
    if quantitative and (unit_difference := _unit_scope_difference(printed_unit, clause.value.canonical_unit)):
        differences.append(unit_difference)
    if differences:
        relation = "different"
        details = ", ".join(f"{change.axis}={change.reported} (target {change.expected})" for change in differences)
        reason = f"Reported scope differs: {details}" + (f". {reason}" if reason else "")
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
                        state.setdefault("location_warnings", []).append(str(exc)[:240])
                        if getattr(exc, "category", None) in PROVIDER_STOP:
                            break
                    checkpoint(located=offset + len(group))
                    if remaining() > 0 and window_used():
                        yield_window()
            jobs, limited = page_plan(catalog, clauses, budget.max_objects_per_clause, hint_pages)
            checkpoint(jobs=jobs, limited=limited, position=0)
        jobs = state["jobs"]
        cards = {key: _reading_card(clause) for key, clause in clauses.items()}
        position = state["position"]
        stop_provider = False
        visual = getattr(self.model, "accepts_images", False)

        def record_disclosure(key, row, index):
            disclosure = _disclosure(row, index, clauses[key].data_kind == "quantitative", page_text[index])
            if disclosure is not None:
                observations = state.setdefault("disclosure_observations", {}).setdefault(key, [])
                body = disclosure.model_dump(mode="json", exclude_none=True)
                if body not in observations:
                    observations.append(body)

        def record_rows(items, keys, index):
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = item.get("clause_key")
                if key not in keys:
                    continue
                series = item.get("measurements")
                if isinstance(series, list):
                    record_disclosure(key, item, index)
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
                    record_disclosure(key, row, index)
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
                header = _preceding_header(page_text, index) if "quantitative" in kinds else None
                if header:
                    context["preceding_page"] = {"page_index": index - 1, "column_header": header}
                    if visual:
                        try:
                            image += (self.documents.image(asset, index - 1, thumbnail=True),)
                        except DocumentUnavailable:
                            pass  # The active page and the saved header are already available.
                answer = call(self.model, [index], _reading_instructions(kinds), context, _schema(page_cards, kinds), "reading", image)
                if not isinstance(answer.get("items"), list):
                    raise ValueError("Extraction model did not return an items array")
                record_rows(answer["items"], keys, index)
            except (ModelRunFailure, ValueError, KeyError, TypeError) as exc:
                category = getattr(exc, "category", "invalid_model_output")
                stop_provider = category in PROVIDER_STOP
                if len(keys) > 1 and category in {
                    "generation_incomplete", "invalid_model_output", "input_budget"}:
                    split = len(keys) // 2
                    jobs[position:position + 1] = [[index, keys[:split]], [index, keys[split:]]]
                    checkpoint(jobs=jobs, position=position)
                    if window_used():
                        break
                    continue
                if category == "generation_incomplete":
                    record_rows(_completed_items(getattr(exc, "cleaned_output", "")), keys, index)
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
