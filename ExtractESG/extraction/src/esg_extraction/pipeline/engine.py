"""One backbone. Clause targets are values; readers differ only by data kind."""

from __future__ import annotations
import json
import re
import time
from collections import Counter
from esg_data.periods import period_label
from esg_data.hashing import digest
from esg_contracts.evidence import SearchIntent
from esg_contracts.facts import ClauseCoverage
from esg_contracts.results import FactBundle
from esg_data.normalize import fold
from esg_clauses.search import compile_search_plan
from .decisions import QuantitativeDecision, QualitativeDecision, NumericMatch, decision_schema
from .binding import numeric_fact, qualitative_fact
from .structure import period_columns, column_structure, period_coverage
from .retrieval import retrieve_candidates
from .ports import DocumentPort, ModelPort, ArtifactPort
from .organization import build_organized_dataset

ENGINE_VERSION = "clause-backbone-1.3"
QUANTITATIVE_INSTRUCTIONS = """Read ONE bounded evidence frame for ONE independent clause target.
The target owns the definition, dictionary, exclusions, scope and reading rules.
Return row IDs and column IDs for real measurements in this bounded evidence frame. Never output, compute or convert values.
Set selection=measurement only for a real reported quantity. Formula digits (SF6, CO2), identifiers, years, dates, reference/footnote numbers are NOT quantities. Reject them with selection=nonmeasurement. Use selection=unresolved when measurement meaning itself is unsupported; these remain diagnostics, never facts. A real count may have no printed unit.
Select EVERY relevant row, not one example. Each selected row must use its exact visible row_id; one response may contain many matches. Follow target.reading.instructions. A real measurement outside the target remains selection=measurement with match=different, never nonmeasurement. Preserve such rows from the same candidate table separately from target rows. For example, a table may contain both the requested category and another real category; the latter is still a measurement. Do not select identifier/year/header/footnote cells as measurements.
Read ALL reported periods in each selected row, including historical years. The report's primary year never filters measurements. physical_structure lists observed columns and ambiguities; it is not a selected-year instruction. Columns with different entities, methods or roles remain separate. Use column_identity for column-specific overrides; row identity must not overwrite those differences.
The receiver reads actual values from those physical cells. Different years, entities, methods and members remain separate.
Place shared frame identity in the top-level identity list ONCE; put only supported row-specific overrides in each match. Omit unsupported axes entirely; do not enumerate missing fields. An empty identity list is valid. Keep each reason to one short phrase.
State supported identity axes as value + exact quote + source_id. Useful axes are reporting_period,
reporting_entity (literal company name), entity_aggregation_role (group/subsidiary/site/segment), reporting_boundary,
measurement_method, value_role (actual/target/baseline/context), aggregation_role (total/subtotal/member/share/intensity),
denominator and dimensions explicitly stated in the source. Do not repeat a physically determined reporting period in model identity.
Do not copy fixed clause axes into the report identity. A clause defines what to seek; it is not evidence of what the report says.
Missing identity, unit, period, or other optional metadata MUST NOT remove a relevant physically reported value or demote its target match. Use match=match when measurement meaning fits the target; leave unsupported attributes absent. Comparison completeness is assessed separately.
requested_identity_fields declares this clause's result columns and any local vocabulary. Read those fields only from cited report evidence. A target definition or report-resource company is not a measured identity. Unlisted categories stay literal and uncertain; never force them into a known code.
Use match=uncertain only when measurement meaning or target relevance itself is ambiguous. No supported rows means matches=[]."""
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
        rejected_candidates = []
        self.artifacts.write(run_id, "diagnostics/candidates.json", rejected_candidates)
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
            plan = compile_search_plan(clause, limit=min(32, budget.max_objects_per_clause))
            self.artifacts.write(run_id, f"input/search/{key}.json", plan)
            intent = SearchIntent.model_validate(plan["objects"])
            try:
                candidates, search_observation = retrieve_candidates(self.documents, request.ir_run_id, intent, budget.max_objects_per_clause)
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
            read_frames = []
            reasons = ["object_budget_exhausted"] if search_observation["truncated"] else []
            inspected = 0
            complete = not search_observation["truncated"]
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
                    telemetry = {}
                    try:
                        frame = self.documents.read(request.ir_run_id, candidate, chunk)
                        read_frames.append(frame)
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
                        rows = []
                        for row in frame.rows:
                            if row.row_role == "header":
                                continue
                            cells = []
                            for cell in row.cells:
                                if cell.candidate_kind in {"measurement", "unresolved"}:
                                    cells.append(cell)
                                else:
                                    rejected_candidates.append({
                                        "clause_key": key, "object_id": frame.object_id,
                                        "row_id": row.row_id, "cell_id": cell.cell_id,
                                        "value_raw": cell.raw_text, "page_index": frame.page_index,
                                        "candidate_kind": cell.candidate_kind,
                                        "selection": "nonmeasurement", "reason": "physical_literal_type",
                                    })
                            if cells:
                                rows.append(row.model_copy(update={"cells": cells}))
                        self.artifacts.write(run_id, "diagnostics/candidates.json", rejected_candidates)
                        frame = frame.model_copy(
                            update={"rows": rows}
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
                        "execution": {"run_id": run_id, "task_type": "clause_" + clause.data_kind,
                            "evidence_ids": [frame.object_id], "remaining_seconds": max(0.001, budget.timeout_seconds - (time.monotonic() - started))},
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
                    structure = column_structure(frame) if clause.data_kind == "quantitative" else None
                    decided_columns = period_columns(frame) if structure else None
                    if structure:
                        context["physical_structure"] = structure.model_dump(mode="json")
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
                        model_decision = decision.model_dump(mode="json")
                        supplements = []
                        if clause.data_kind == "quantitative":
                            supplements = adjacent_table_quantities(frame, decided_columns, decision.matches)
                            if supplements:
                                decision = decision.model_copy(update={"matches": [*decision.matches, *supplements]})
                        if telemetry.get("execution"):
                            telemetry["execution"]["output_validation"] = "passed"
                        call.update(
                            status="ok",
                            telemetry=telemetry,
                            model_decision=model_decision,
                            structural_supplements=[item.model_dump(mode="json") for item in supplements],
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
                                    accepted = []
                                    for cell in cells:
                                        if match.selection != "measurement" or cell.candidate_kind not in {"measurement", "unresolved"}:
                                            rejected_candidates.append({
                                                "clause_key": key, "object_id": frame.object_id,
                                                "row_id": row.row_id, "cell_id": cell.cell_id,
                                                "value_raw": cell.raw_text, "page_index": frame.page_index,
                                                "candidate_kind": cell.candidate_kind,
                                                "selection": match.selection, "reason": match.reason,
                                            })
                                        else:
                                            accepted.append(cell)
                                    self.artifacts.write(run_id, "diagnostics/candidates.json", rejected_candidates)
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
                                        for cell in accepted
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
                                        rejected_candidates.append({"clause_key": key, "object_id": frame.object_id,
                                            "candidate_kind": "unresolved", "selection": "nonmeasurement", "reason": rejected})
                                        self.artifacts.write(run_id, "diagnostics/candidates.json", rejected_candidates)
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
                        if telemetry.get("execution"):
                            telemetry["execution"]["output_validation"] = "failed"
                            call["telemetry"] = telemetry
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
                    search=search_observation,
                    periods=period_coverage(read_frames, list(local_facts.values())),
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
            run_id, document, clauses, facts, mappings, coverage, calls, started, errors,
            final=True, rejected_count=len(rejected_candidates),
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
        final=False,
        rejected_count=0,
    ):
        # Every record crosses a typed, value-only boundary before persistence.
        body = {
            "schema_version": "extraction-fact-bundle-2.2",
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
                "assessment": assess_results(facts, mappings, coverage, clauses, errors, final, rejected_count),
            },
        }
        body["organization"] = {
            "schema_version": "organized-extraction-1.1",
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


def adjacent_table_quantities(frame, decided_columns, matches):
    """Retain explicit quantities in a selected table independently of relevance.

    Only data rows with a physically printed unit and a structurally selected
    period qualify. Ambiguous prose, unitless counts and headers remain model
    decisions. Relevance is still evaluated from the clause, never guessed here.
    """
    if frame.kind != "table" or not decided_columns:
        return []
    selected = {(match.row_id, col) for match in matches if match.selection == "measurement" for col in match.columns}
    plain_periods = {column.column_id for column in column_structure(frame).columns if column.period_only}
    result = []
    for row in frame.rows:
        if row.row_role != "data" or not row.label.strip() or not row.unit_raw or not row.unit_sources:
            continue
        if not any(row.unit_raw in source.text for source in row.unit_sources):
            continue
        columns = [cell.column_id for cell in row.cells if cell.column_id in decided_columns
            and (row.row_id, cell.column_id) not in selected and cell.candidate_kind == "measurement"
            and re.fullmatch(r"\s*[-+−]?\d[\d,，]*(?:\.\d+)?\s*", cell.raw_text)]
        if columns:
            row_relations = {match.match for match in matches if match.row_id == row.row_id and match.selection == "measurement"}
            for column in columns:
                relation = next(iter(row_relations)) if len(row_relations) == 1 and column in plain_periods else "uncertain"
                result.append(NumericMatch(row_id=row.row_id, columns=[column], selection="measurement", match=relation,
                    reason="Printed table quantity retained independently of target selection"))
    return result


def assess_results(facts, mappings, coverage, clauses, errors, final, rejected_count=0):
    """One result projection, independent of views and publication decisions."""
    interrupted = bool(errors) or len(coverage) != len(clauses) or any(
        "execution_budget_exhausted" in c.reasons or "object_budget_exhausted" in c.reasons or c.state == "failed" or "document_read_failed" in c.reasons
        for c in coverage
    )
    required = {
        key: clause.purposes.enrichment.identity_axes if clause.purposes else [
            field.binding.key for field in clause.organization.fields
            if field.binding.source in {"identity", "dimension"}
        ]
        for key, clause in clauses.items()
    }
    by_id = {fact.source_fact_id: fact for fact in facts}
    incomplete = False
    comparable = bool(mappings) and {f.source_fact_id for f in facts} <= {m.source_fact_id for m in mappings}
    for link in mappings:
        fact = by_id[link.source_fact_id]
        clause = clauses[link.clause_key]
        policy = clause.purposes.comparison if clause.purposes else None
        if link.match != "match" or any(flag.startswith(("comparison_axis_missing:", "clause_evidence_missing:", "output_vocabulary_", "identity_conflict:", "unit_source_unconfirmed")) for flag in link.flags):
            comparable = False
        if (policy is None or policy.require_complete_scopes) and not {s.scope_id for s in clause.output_scopes if s.required} <= set(link.scope_ids):
            comparable = False
        for axis in required[link.clause_key]:
            field = getattr(fact.identity, axis, None) or fact.identity.dimensions.get(axis)
            if field is None or field.state not in {"stated", "inherited"}:
                incomplete = True
    return {
        "execution_state": "running" if not final else "interrupted" if interrupted else "completed",
        "search_coverage": "in_progress" if not final else "incomplete" if interrupted else "bounded_complete",
        "capture_result": "usable" if facts else "empty",
        "metadata_completeness": "not_requested" if not any(required.values()) or not facts else "partial" if incomplete else "complete",
        "comparison_status": "not_evaluated" if not facts else "eligible" if comparable else "insufficient_context",
        "target_facts": len({m.source_fact_id for m in mappings if m.match == "match"}),
        "other_facts": len({m.source_fact_id for m in mappings if m.match == "different"}),
        "unresolved_facts": len({m.source_fact_id for m in mappings if m.match == "uncertain"}),
        "rejected_candidates": rejected_count,
        "periods": dict(Counter(period_label(fact.identity.reporting_period.value) for fact in facts)),
    }
