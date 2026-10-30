"""One backbone. Clause targets are values; readers differ only by data kind."""

from __future__ import annotations
import json
import re
import time
from esg_data.hashing import digest
from esg_contracts.evidence import SearchIntent
from esg_contracts.facts import ClauseCoverage
from esg_contracts.results import FactBundle
from esg_data.normalize import fold
from .decisions import QuantitativeDecision, QualitativeDecision, decision_schema
from .binding import numeric_fact, qualitative_fact
from .structure import period_columns
from .ports import DocumentPort, ModelPort, ArtifactPort
from .organization import build_organized_dataset

ENGINE_VERSION = "clause-backbone-1.1"
QUANTITATIVE_INSTRUCTIONS = """Read ONE bounded evidence frame for ONE independent clause target.
The target owns the definition, dictionary, exclusions, scope and reading rules.
Return only row IDs and column IDs that directly report the requested measurement. Never output, compute or convert values.
Select EVERY relevant row, not one example. Each selected row must use its exact visible row_id; one response may contain many matches. Follow target.reading.instructions, and keep unrelated measurement types out of the selection.
When physical_period_columns is provided, the period column is already determined by structure; select rows and use those columns.
The receiver reads actual values from those physical cells. Different years, entities, methods and members remain separate.
Place shared frame identity in the top-level identity list ONCE; put only supported row-specific overrides in each match. Omit unsupported axes entirely; do not enumerate missing fields. An empty identity list is valid. Keep each reason to one short phrase.
State supported identity axes as value + exact quote + source_id. Useful axes are reporting_period,
reporting_entity (literal company name), entity_aggregation_role (group/subsidiary/site/segment), reporting_boundary,
measurement_method, value_role (actual/target/baseline/context), aggregation_role (total/subtotal/component/share/intensity),
denominator and dimensions explicitly stated in the source. Do not repeat a physically determined reporting period in model identity.
Do not copy fixed clause axes into the report identity. A clause defines what to seek; it is not evidence of what the report says.
Missing identity, unit, period, or other metadata MUST NOT remove a relevant physically reported value. Select it as uncertain; the raw source is admitted separately from clause compliance.
requested_identity_fields declares this clause's result columns and any local vocabulary. Read those fields only from cited report evidence. A target definition or report-resource company is not a measured identity. Unlisted categories stay literal and uncertain; never force them into a known code.
When relevance or identity is ambiguous, use uncertain. No supported rows means matches=[]."""
QUALITATIVE_INSTRUCTIONS = """Read ONE bounded evidence frame for ONE independent qualitative clause.
Return span IDs containing directly reported statements that satisfy the clause definition and reading rules.
You may quote an exact substring; the receiver must find it uniquely in that span. Otherwise select the entire span.
A heading, index entry, or mere occurrence of the topic name does not establish a substantive statement. Prefer a complete paragraph or sentence with actual plan/policy/action content. Partial evidence must be uncertain.
Never paraphrase or invent report text. Numerical values and units are not qualitative value fields.
State identity fields only with an exact supporting quote and source_id. Omit unsupported axes entirely, never enumerate missing fields; keep each reason to one short phrase. Missing evidence is not a negative assertion.
requested_identity_fields declares this clause's actual result fields and local vocabulary. Read them from the report only; never fill them from target definitions or report-resource metadata. Preserve unlisted categories literally and mark uncertainty.
Do not claim non-disclosure from a search miss. No supported statements means matches=[]."""


class PipelineCancelled(RuntimeError):
    pass


class ExtractionBackbone:
    def __init__(
        self,
        documents: DocumentPort,
        model: ModelPort,
        artifacts: ArtifactPort,
        engine_identity=None,
    ):
        self.documents = documents
        self.model = model
        self.artifacts = artifacts
        self.engine_identity = engine_identity or {"version": ENGINE_VERSION}

    def run(
        self,
        run_id,
        request,
        clauses,
        *,
        document=None,
        cancel_check=lambda: False,
        on_progress=lambda event: None,
    ):
        started = time.monotonic()
        calls = 0
        facts = []
        mappings = []
        coverage = []
        errors = []
        trace = []
        document = document or self.documents.document(request.ir_run_id)
        if document.ir_run_id != request.ir_run_id:
            raise ValueError("Pinned document differs from requested evidence")
        self.artifacts.write(
            run_id, "input/document.json", document.model_dump(mode="json")
        )
        self.artifacts.write(
            run_id,
            "input/clauses.json",
            {k: v.model_dump(mode="json") for k, v in clauses.items()},
        )
        self.artifacts.write(
            run_id, "input/request.json", request.model_dump(mode="json")
        )
        self.artifacts.write(run_id, "input/engine.json", self.engine_identity)
        budget = request.budget
        for index, (key, clause) in enumerate(clauses.items(), 1):
            if cancel_check():
                raise PipelineCancelled("Extraction cancelled")
            clause.verify()
            if clause.readiness != "ready":
                raise ValueError(f"Blocked clause {key}")
            on_progress(
                {
                    "stage": "retrieving",
                    "clause_key": key,
                    "current": index - 1,
                    "total": len(clauses),
                    "model_calls": calls,
                }
            )
            intent = SearchIntent(
                target_id=key,
                contract_digest=clause.clause_digest,
                data_kind=clause.data_kind,
                positive_terms=clause.retrieval.positive_terms,
                negative_terms=clause.retrieval.negative_terms,
                exact_terms=clause.exact_labels,
                source_priority=clause.retrieval.source_priority,
                structural_anchors=clause.retrieval.structural_anchors,
                limit=budget.max_objects_per_clause,
            )
            intent = intent.model_copy(
                update={
                    name: getattr(clause.retrieval, name)
                    for name in (
                        "topic_term_groups",
                        "role_term_groups",
                        "required_term_groups",
                        "excluded_object_terms",
                        "context_terms",
                    )
                }
            )
            try:
                candidates = self.documents.search(request.ir_run_id, intent)
            except Exception as exc:
                errors.append(
                    {
                        "clause_key": key,
                        "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                    }
                )
                coverage.append(
                    ClauseCoverage(
                        clause_key=key,
                        clause_digest=clause.clause_digest,
                        state="failed",
                        reasons=["document_search_failed"],
                    )
                )
                self._write_outputs(
                    run_id,
                    document,
                    clauses,
                    facts,
                    mappings,
                    coverage,
                    calls,
                    started,
                    errors,
                )
                continue
            local_facts = {}
            local_mappings = {}
            reasons = []
            inspected = 0
            complete = True
            for candidate in candidates:
                for chunk in range(candidate.chunk_count):
                    if cancel_check():
                        raise PipelineCancelled("Extraction cancelled")
                    if (
                        calls >= budget.max_model_calls
                        or time.monotonic() - started >= budget.timeout_seconds
                    ):
                        complete = False
                        reasons.append("execution_budget_exhausted")
                        break
                    try:
                        frame = self.documents.read(request.ir_run_id, candidate, chunk)
                    except Exception as exc:
                        complete = False
                        reasons.append("document_read_failed")
                        errors.append(
                            {
                                "clause_key": key,
                                "object_id": candidate.object_id,
                                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                            }
                        )
                        continue
                    if clause.data_kind == "quantitative":
                        frame = frame.model_copy(
                            update={
                                "rows": [
                                    r for r in frame.rows if r.row_role != "header"
                                ]
                            }
                        )
                    if clause.data_kind == "quantitative" and not frame.rows:
                        reasons.append("object_has_no_bindable_quantitative_cells")
                        continue
                    if clause.data_kind == "qualitative" and not frame.texts:
                        reasons.append("object_has_no_bindable_statements")
                        continue
                    frame_body = frame.model_dump(mode="json")
                    self.artifacts.write(
                        run_id,
                        f"evidence/{digest([key, frame.object_id, chunk])}.json",
                        frame_body,
                    )
                    context = {
                        "target": clause.reading_card(),
                        "document": document.model_dump(mode="json"),
                        "frame": frame_body,
                    }
                    if clause.data_kind == "quantitative":
                        # Cells already occur in rows and explicit header/label sources.
                        # Do not ask the model to read a second copy of every cell.
                        context["frame"] = {
                            **frame_body,
                            "texts": [
                                t
                                for t in frame_body["texts"]
                                if t["object_type"] != "cell"
                            ],
                        }
                    decided_columns = (
                        period_columns(frame, document)
                        if clause.data_kind == "quantitative"
                        and all(
                            s.period == "annual"
                            for s in clause.output_scopes
                            if s.required
                        )
                        else None
                    )
                    context["physical_period_columns"] = decided_columns
                    decision_type = (
                        QuantitativeDecision
                        if clause.data_kind == "quantitative"
                        else QualitativeDecision
                    )
                    instructions = (
                        QUANTITATIVE_INSTRUCTIONS
                        if clause.data_kind == "quantitative"
                        else QUALITATIVE_INSTRUCTIONS
                    )
                    on_progress(
                        {
                            "stage": "reading",
                            "clause_key": key,
                            "object_id": frame.object_id,
                            "current": index - 1,
                            "total": len(clauses),
                            "model_calls": calls,
                        }
                    )
                    calls += 1
                    call = {
                        "call_index": calls,
                        "clause_key": key,
                        "clause_digest": clause.clause_digest,
                        "object_id": frame.object_id,
                        "frame_digest": frame.digest,
                        "data_kind": clause.data_kind,
                    }
                    try:
                        raw, telemetry = self.model.complete(
                            instructions=instructions,
                            context=context,
                            schema=decision_schema(clause.data_kind, frame),
                            max_tokens=budget.max_output_tokens,
                            cancel_check=cancel_check,
                        )
                        decision = decision_type.model_validate(
                            raw if isinstance(raw, dict) else json.loads(raw)
                        )
                        call.update(
                            status="ok",
                            telemetry=telemetry,
                            decision=decision.model_dump(mode="json"),
                        )
                        interpretation = digest(
                            {
                                "engine": self.engine_identity,
                                "clause": clause.clause_digest,
                                "frame": frame.digest,
                                "decision": decision.model_dump(mode="json"),
                            }
                        )
                        for match in decision.matches:
                            match = match.model_copy(
                                update={
                                    "identity": [*decision.identity, *match.identity]
                                }
                            )
                            try:
                                if clause.data_kind == "quantitative":
                                    row = next(
                                        (
                                            r
                                            for r in frame.rows
                                            if r.row_id == match.row_id
                                        ),
                                        None,
                                    )
                                    if row is None:
                                        raise ValueError("Unknown physical source row")
                                    if decided_columns:
                                        match = match.model_copy(
                                            update={"columns": decided_columns}
                                        )
                                    cells = [
                                        cell
                                        for cell in row.cells
                                        if cell.column_id in match.columns
                                    ]
                                    if {cell.column_id for cell in cells} != set(
                                        match.columns
                                    ):
                                        raise ValueError(
                                            "Unknown physical source column"
                                        )
                                    results = [
                                        numeric_fact(
                                            clause,
                                            document,
                                            frame,
                                            match,
                                            row,
                                            cell,
                                            interpretation,
                                        )
                                        for cell in cells
                                    ]
                                else:
                                    results = [
                                        qualitative_fact(
                                            clause,
                                            document,
                                            frame,
                                            match,
                                            interpretation,
                                        )
                                    ]
                                for fact, mapping, rejected in results:
                                    if rejected:
                                        reasons.append(rejected)
                                        continue
                                    local_facts[fact.source_fact_id] = fact
                                    local_mappings[fact.source_fact_id] = mapping
                            except (ValueError, TypeError, KeyError) as exc:
                                reasons.append("binding_rejected:" + str(exc))
                        reasons.extend(decision.missing_context)
                        inspected += 1
                    except Exception as exc:
                        if cancel_check():
                            raise PipelineCancelled("Extraction cancelled") from exc
                        call.update(
                            status="failed",
                            error=f"{type(exc).__name__}: {str(exc)[:400]}",
                        )
                        if hasattr(exc, "telemetry"):
                            call["telemetry"] = exc.telemetry
                        if getattr(exc, "raw_output", None):
                            call["incomplete_output"] = exc.raw_output
                        if hasattr(exc, "category"):
                            call["failure_category"] = exc.category
                        reasons.append("model_or_contract_failure")
                        errors.append(
                            {
                                "clause_key": key,
                                "object_id": frame.object_id,
                                "error": call["error"],
                            }
                        )
                        complete = False
                    trace.append(call)
                    self.artifacts.write(run_id, "diagnostics/model-calls.json", trace)
                    active = ClauseCoverage(
                        clause_key=key,
                        clause_digest=clause.clause_digest,
                        state="not_covered",
                        candidate_objects=len(candidates),
                        inspected_objects=inspected,
                        fact_count=len(local_facts),
                        reasons=["reading_in_progress"],
                    )
                    self._write_outputs(
                        run_id,
                        document,
                        clauses,
                        [*facts, *local_facts.values()],
                        [*mappings, *local_mappings.values()],
                        [*coverage, active],
                        calls,
                        started,
                        errors,
                    )
                if not complete and (
                    calls >= budget.max_model_calls
                    or time.monotonic() - started >= budget.timeout_seconds
                ):
                    break
            facts.extend(local_facts.values())
            mappings.extend(local_mappings.values())
            required = {s.scope_id for s in clause.output_scopes if s.required}
            covered = {
                scope
                for mapping in local_mappings.values()
                if mapping.match == "match"
                for scope in mapping.scope_ids
            }
            if not complete:
                state = "not_covered"
            elif required <= covered and local_facts:
                state = "covered"
            elif local_facts:
                state = "unconfirmed"
            elif inspected:
                state = "not_found"
            else:
                state = "not_covered"
            if not candidates:
                reasons.append(
                    "No compatible candidate evidence was retrieved; non-disclosure is unproven."
                )
            coverage.append(
                ClauseCoverage(
                    clause_key=key,
                    clause_digest=clause.clause_digest,
                    state=state,
                    candidate_objects=len(candidates),
                    inspected_objects=inspected,
                    fact_count=len(local_facts),
                    required_scopes=sorted(required),
                    covered_scopes=sorted(covered),
                    reasons=list(dict.fromkeys(reasons)),
                )
            )
            self._write_outputs(
                run_id,
                document,
                clauses,
                facts,
                mappings,
                coverage,
                calls,
                started,
                errors,
            )
            on_progress(
                {
                    "stage": "extracting",
                    "clause_key": key,
                    "current": index,
                    "total": len(clauses),
                    "model_calls": calls,
                    "fact_count": len(facts),
                }
            )
        return self._write_outputs(
            run_id, document, clauses, facts, mappings, coverage, calls, started, errors
        )

    def _write_outputs(
        self,
        run_id,
        document,
        clauses,
        facts,
        mappings,
        coverage,
        calls,
        started,
        errors,
    ):
        # Every record crosses a typed, value-only boundary before persistence.
        body = {
            "schema_version": "extraction-fact-bundle-2.1",
            "engine_version": ENGINE_VERSION,
            "engine_identity": self.engine_identity,
            "run_id": run_id,
            "document": document.model_dump(mode="json"),
            "clause_targets": {k: c.clause_digest for k, c in clauses.items()},
            "facts": [f.model_dump(mode="json") for f in facts],
            "clause_mappings": [m.model_dump(mode="json") for m in mappings],
            "coverage": [c.model_dump(mode="json") for c in coverage],
            "summary": {
                "selected_clauses": len(clauses),
                "processed_clauses": len(coverage),
                "source_facts": len(facts),
                "quantitative": sum(f.kind == "quantitative" for f in facts),
                "qualitative": sum(f.kind == "assertion" for f in facts),
                "model_calls": calls,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "errors": errors,
            },
        }
        body["organization"] = {
            "schema_version": "organized-extraction-1.0",
            "status": "pending",
            "url": f"/api/runs/{run_id}/organized",
        }
        body = FactBundle.model_validate(body).model_dump(
            mode="json", exclude_none=True
        )
        for fact in body["facts"]:
            self.artifacts.write(run_id, f"facts/{fact['source_fact_id']}.json", fact)
        # Raw output is committed before optional table organization. Layout
        # problems must never erase a correctly bound source value.
        self.artifacts.write(run_id, "results/facts.json", body)
        try:
            organized = build_organized_dataset(
                run_id, document, clauses, facts, mappings
            )
            self.artifacts.write(
                run_id, "results/organized.json", organized.model_dump(mode="json")
            )
            body["organization"].update(
                status=organized.status,
                table_count=len(organized.tables),
                unorganized_fact_count=len(organized.unorganized_fact_ids),
            )
        except Exception as exc:
            body["organization"].update(
                status="failed", error=f"{type(exc).__name__}: {str(exc)[:400]}"
            )
        body = FactBundle.model_validate(body).model_dump(
            mode="json", exclude_none=True
        )
        self.artifacts.write(run_id, "results/facts.json", body)
        return body
