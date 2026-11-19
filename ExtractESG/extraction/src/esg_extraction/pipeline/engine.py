"""Read and transcribe original PDF pages into the two existing fact types."""

import json
import re
import time
from collections import defaultdict
from hashlib import sha256

from esg_clauses.search import compile_search_plan
from esg_contracts.evidence import PDFPageText
from esg_contracts.facts import (
    ClauseCoverage, ClauseMapping, EvidenceLocation, QualitativePayload,
    QuantitativePayload, ReportedContext, ReportedField, SourceFact,
)
from esg_contracts.results import FactBundle
from esg_data.hashing import digest
from esg_data.literals import number_text, parse_measure
from esg_data.normalize import fold
from esg_data.periods import parse_period, period_payload
from esg_data.text_search import lexical_score
from esg_extraction.models.errors import ModelCancelled, ModelRunFailure

from .ports import DocumentUnavailable
from .statistics import RunStatistics


ENGINE_VERSION = "page-reader-3.3"
READ_INSTRUCTIONS = """Transcribe the requested independent fields from page_index. Read the first image when present; its text helps with spelling and exact digits but may lose table structure. A preceding_page describes the optional second image. Use its column headers only if the current table visibly continues that table. Copy measurements from the first page only. Map each cell to its printed column heading in order, and omit the period if the column cannot be identified.
For a quantitative table or chart, read totals and relevant breakdowns across all printed years and units. Use one item per row or chart series with shared clause_key, printed label, unit, relation and attributes. Put each value and period in measurements; put fields that vary by column or point (scope, period, unit, relation or attributes) there. Use separate items for different methods, statistics or subjects. Keep both disclosures when a quantity is printed in two units. Example: {"clause_key":"TARGET","label":"printed title","unit":"t","relation":"match","measurements":[{"value":"1,204.50","period":"2024"},{"value":"0","period":"2023"}]}. For a single quantity use value and period directly. For a qualitative item copy the relevant complete sentence or bullet into value.
Copy exact values, periods and printed units, including multipliers, chemical notation and denominators. Preserve a printed zero, range markers, punctuation and precision. A phrase such as no discharge is not a printed numerical zero. Skip blanks, missing-value dashes and reference IDs. Do not calculate, substitute a canonical unit or infer a date from the report title.
Fill stated attributes with short original words from the applicable row, heading, legend, surrounding statement or footnote. Use the card's field names for every relation. For a rate or intensity copy the actual bases from its own label or unit, even when its method or scope is unresolved. Observe which rows and periods each footnote covers, including small print below either panel of a spread. Keep comparative rows and targets under their own labels. Omit an unstated attribute. Target codes or nearby rows do not supply facts about this value.
Set relation to match for the target, different for a related quantity with a distinct stated meaning or basis, or uncertain if the match is unresolved. Stated practices can match qualitative targets even when optional dates or areas are unstated. A future target or permitted limit is different when the clause asks for actual results. Scope_ids are optional and apply only to a stated matching scope. Omit unrelated numbers and repeated rows. Return compact JSON with items only."""
LOCATE_INSTRUCTIONS = """The images are numbered PDF pages in the same order as page_indices. Identify all pages likely to have relevant disclosures for any requested topic, including tables and graphs and their continuation. Return a JSON object with page_indices. This highlights pages for detailed reading; do not extract values."""
LOCATE_SCHEMA = {"type": "object", "required": ["page_indices"], "properties": {
    "page_indices": {"type": "array", "items": {"type": "integer"}}}}
MISSING = re.compile(r"^\s*(?:[-–—－]+|n/?a|not\s+(?:available|disclosed)|不适用|未披露|无数据)\s*$", re.I)
WINDOW_MAX_CALLS = 10
PAGE_SHORTLIST = 8
MEASUREMENT_LINE = re.compile(r"^[ \t]*[-−<>≈]?\d[\d,. ]*(?:%|[ \t]*[A-Za-z]{1,4})?[ \t\r]*$", re.M)
TABLE_NUMBER = re.compile(r"(?<![\w.,])[-−<>≈]?\d[\d,]*(?:\.\d+)?%?(?![\w.,])")
YEAR_COLUMNS = re.compile(r"(?<![\d.,])(?:19|20)\d{2}(?:[ \t]+(?:19|20)\d{2})+(?![\d.,])")
PROVIDER_STOP = {"provider_authentication", "provider_balance", "request_config"}


class PipelineCancelled(RuntimeError):
    pass


class ExecutionYield(RuntimeError):
    """The next slice uses the saved page plan and results."""

    def __init__(self, window):
        super().__init__("Reading window checkpoint saved")
        self.window = window


def _schema(keys):
    measure_fields = {key: {"type": "string"} for key in ("value", "unit", "period")}
    row_fields = {
        "relation": {"type": "string", "enum": ["match", "different", "uncertain"]},
        "scope_ids": {"type": "array", "items": {"type": "string"}},
        "attributes": {"type": "object", "additionalProperties": {"type": "string"}},
    }
    return {"type": "object", "required": ["items"], "properties": {"items": {
        "type": "array", "items": {"type": "object", "required": ["clause_key"],
        "properties": {"clause_key": {"type": "string", "enum": keys}, **measure_fields,
            "label": {"type": "string"}, "measurements": {"type": "array", "items": {
                "type": "object", "required": ["value"], "properties": {**measure_fields, **row_fields}}},
            **row_fields}}}}}


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
    return {name: definition for name, definition in clause.attributes.items()
            if name in names and name != "reporting_period"}


def _reading_card(clause):
    original = clause.reading_card()
    fields = ("labels", "definition", "distinguish_from", "exclusions", "data_kind", "value", "exact_labels")
    card = {key: original[key] for key in fields}
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


def _completed_items(prefix):
    """Keep each whole returned row before a token-limited JSON answer ends."""
    start = re.match(r'^\s*\{\s*"items"\s*:\s*\[', prefix)
    if start is None:
        return
    decoder = json.JSONDecoder()
    tail = prefix[start.end():].lstrip()
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


def _fact(document, clause, page_index, item, visual):
    raw = _text(item, "value")
    if not raw or (clause.data_kind == "quantitative" and MISSING.fullmatch(raw)):
        return None
    unit = _text(item, "unit")
    label = _text(item, "label")
    period = _text(item, "period")
    role = "value" if clause.data_kind == "quantitative" else "statement"
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
            if text and attribute.accepts_value(text):
                target = axes if attribute.source == "identity" and name in ReportedContext.model_fields else dimensions
                category = attribute.classify({"state": "stated", "value": text, "raw_text": text})
                if category and category.state == "recognized":
                    recognized[name] = category.code
                target[name] = field(text, recognized.get(name, text))
    identity = ReportedContext(**axes, dimensions=dimensions)
    if role == "value":
        numeric = parse_measure(raw)
        parsed = {"parse_status": "parsed", "comparator": numeric["comparator"]} if numeric else {}
        if numeric:
            for source, target in (("value", "value_numeric"), ("lower", "value_lower"), ("upper", "value_upper")):
                if numeric[source] is not None:
                    parsed[target] = format(numeric[source], "f")
        implicit_unit = number_text(raw)[-1:] if unit is None else ""
        value = QuantitativePayload(value_raw=raw,
            unit_raw=unit or (implicit_unit if implicit_unit in {"%", "％"} else None), **parsed)
    else:
        value = QualitativePayload(statement_raw=raw)
    anchor = digest([document.source_sha256 or document.document_id, page_index, label, period, unit, raw])
    fact = SourceFact(schema_version="source-fact-2.1", source_fact_id=anchor, source_anchor_id=anchor,
        document_id=document.document_id, ir_run_id=document.ir_run_id, ir_revision=document.ir_revision,
        interpretation_id=anchor, report_resource=document.report_resource, value=value,
        source_row_label=label, identity=identity, evidence=[location], quality="grounded",
        flags=["visual_transcription" if visual else "text_transcription"])
    interpretation = digest(fact.model_dump(mode="json", exclude={"source_fact_id", "interpretation_id"}))
    fact = fact.model_copy(update={"source_fact_id": interpretation, "interpretation_id": interpretation})
    relation = _text(item, "relation")
    conflicts = [name for name in clause.purposes.target_scope.discriminating_axes
                 if name in recognized and name in clause.value.fixed_axes
                 and recognized[name] != clause.value.fixed_axes[name]]
    if conflicts:
        relation = "different"
    reported_scopes = item.get("scope_ids")
    scope_ids = [scope.scope_id for scope in clause.output_scopes if scope.scope_id in reported_scopes
                 ] if relation == "match" and isinstance(reported_scopes, list) else []
    mapping = ClauseMapping(clause_key=clause.clause_key, clause_digest=clause.clause_digest,
        source_fact_id=fact.source_fact_id, match=relation if relation in {"match", "different", "uncertain"} else "uncertain",
        reason="Reported scope differs in " + ", ".join(conflicts) if conflicts else "Reported on the selected PDF page",
        scope_ids=scope_ids)
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

        def record_rows(items, keys, index):
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = item.get("clause_key")
                if key not in keys:
                    continue
                series = item.get("measurements")
                columns = series if isinstance(series, list) else [{}]
                for column in columns:
                    if not isinstance(column, dict):
                        continue
                    row = {**item, **column}
                    shared = item.get("attributes")
                    override = column.get("attributes")
                    if isinstance(shared, dict) and isinstance(override, dict):
                        row["attributes"] = {**shared, **override}
                    output = _fact(document, clauses[key], index, row, visual)
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
                context = {"page_index": index, "page_text": page_text[index][:12000],
                           "clauses": {key: cards[key] for key in keys}}
                header = _preceding_header(page_text, index)
                if header:
                    context["preceding_page"] = {"page_index": index - 1, "column_header": header}
                    if visual:
                        try:
                            image += (self.documents.image(asset, index - 1, thumbnail=True),)
                        except DocumentUnavailable:
                            pass  # The active page and the saved header are already available.
                answer = call(self.model, [index], READ_INSTRUCTIONS, context, _schema(keys), "reading", image)
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
                covered_scopes=sorted(covered_scopes[key])))
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
