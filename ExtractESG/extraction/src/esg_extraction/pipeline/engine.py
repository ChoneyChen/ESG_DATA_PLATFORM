"""One backbone. Clause targets are values; readers differ only by data kind."""

from __future__ import annotations
import json
import re
import time
from esg_data.hashing import digest
from esg_contracts.evidence import SearchIntent, EvidencePreparationRequest, EvidenceFrame
from esg_contracts.search import PageSearchIntent
from esg_contracts.facts import ClauseCoverage, SourceFact, ClauseMapping
from esg_contracts.results import FactBundle
from esg_data.normalize import fold
from esg_clauses.search import compile_search_plan
from .decisions import QuantitativeDecision, QualitativeDecision, NumericMatch, decision_schema
from .binding import numeric_fact, qualitative_fact
from .structure import period_columns, column_structure, period_coverage
from .retrieval import retrieve_candidates
from .frames import split_frame, remainder_frames, budget_frames
from .reading_frame import reading_frame
from .enrichment import (context_frame, context_schema, ContextDecision, apply_context, missing_attributes, INSTRUCTIONS as CONTEXT_INSTRUCTIONS)
from .ports import DocumentPort, ModelPort, ArtifactPort, ResultPort
from .statistics import RunStatistics

ENGINE_VERSION = "clause-backbone-1.9"
QUANTITATIVE_INSTRUCTIONS = """Read ONE bounded evidence frame for ONE independent clause target.
The target owns the definition, dictionary, exclusions, scope and reading rules.
Return row IDs and column IDs for real measurements in this bounded evidence frame. Never output, compute or convert values.
Set selection=measurement only for a real reported quantity. Formula digits (SF6, CO2), identifiers, years, dates, reference/footnote numbers are NOT quantities. Reject them with selection=nonmeasurement. Use selection=unresolved when measurement meaning itself is unsupported; these remain diagnostics, never facts. A real count may have no printed unit.
Select EVERY target-relevant row, not one example. Use exact visible row IDs. Follow target.reading.instructions.
Explicit contrasting measurements may use match=different. Do not enumerate wholly unrelated rows solely to retain them; the receiver independently retains physically grounded adjacent table quantities. Identifier/year/header/footnote cells are not measurements.
Read ALL reported periods in each selected row, including historical years. The report's primary year never filters measurements. physical_structure lists observed columns and ambiguities; it is not a selected-year instruction. Columns with different entities, methods or roles remain separate. Use column_identity for column-specific overrides; row identity must not overwrite those differences.
The receiver reads actual values from those physical cells. Different years, entities, methods and members remain separate.
Place shared frame identity in the top-level identity list ONCE; put only supported row-specific overrides in each match. Omit unsupported axes entirely; do not enumerate missing fields. An empty identity list is valid. Keep each reason to one short phrase.
Use the short source IDs actually shown: a row label_source_id, a column source_id, a cell_id, or an object_id in texts/title_sources. Never cite a numeric cell for a header quote. Put column-specific identities in column_identity; a row identity must be true for every selected column.
A column name may span several physical header parts. Read the complete joined name, cite the column source_id, and quote its contiguous joined wording; do not truncate a name to its first header cell.
For an attribute without a supplied vocabulary, preserve its reported wording rather than inventing a normalized category.
State supported attributes from requested_identity_fields with value + exact quote + source_id. Other explicitly printed attributes may remain literal dimensions.
Never emit bound_identity_axes: those fields are already bound from physical evidence.
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


class ExecutionYield(RuntimeError):
    """Saved work continues in another turn of the shared execution queue."""


class EvidenceNeeded(RuntimeError):
    def __init__(self, request):
        self.request = request
        super().__init__(request.reason)


class ExtractionBackbone:
    def __init__(
        self,
        documents: DocumentPort,
        model: ModelPort,
        artifacts: ArtifactPort,
        results: ResultPort,
        engine_identity=None,
    ):
        self.documents = documents
        self.model = model
        self.artifacts = artifacts
        self.results = results
        self.engine_identity = engine_identity or {"version": ENGINE_VERSION}

    def run(
        self,
        run_id,
        request,
        clauses,
        *,
        document=None,
        sources=None,
        exhausted_queries=None,
        context_attempted=None,
        cancel_check=lambda: False,
        on_progress=lambda event: None,
    ):
        checkpoint = self.results.checkpoint()
        started = time.monotonic() - checkpoint.get("elapsed_seconds", 0)
        calls = checkpoint.get("calls", 0)
        turn_start_calls = calls
        coverage = []
        trace = []
        try:
            rejected_candidates = self.artifacts.read(run_id, "diagnostics/candidates.json")
        except FileNotFoundError:
            rejected_candidates = []
        document = document or self.documents.document(request.ir_run_id)
        if document.ir_run_id != request.ir_run_id:
            raise ValueError("Pinned document differs from requested evidence")
        self.sources = {document.snapshot_id:document, **(sources or {})}
        self.exhausted_queries = exhausted_queries or {}
        self.completed = set(checkpoint.get("completed_clauses", []))
        self.enriched = set(checkpoint.get("enriched_clauses", []))
        self.context_reads = set(checkpoint.get("context_reads", []))
        self.frame_paths = {key:set(paths) for key,paths in checkpoint.get("evidence_frames", {}).items()}
        self.context_attempted = set(context_attempted or [])
        self.visited = set(checkpoint.get("visited_frames", []))
        self.pending_reads = dict(checkpoint.get('pending_reads',{}))
        restored = self.results.collection() if checkpoint else {"facts":[],"clause_mappings":[]}
        restored_facts = {f["source_fact_id"]:SourceFact.model_validate(f) for f in restored["facts"]}
        restored_mappings = [ClauseMapping.model_validate(m) for m in restored["clause_mappings"]]
        self.statistics = RunStatistics(clauses)
        for mapping in restored_mappings:
            self.statistics.record(restored_facts[mapping.source_fact_id], mapping)
        previous_status = self.results.status() or {}
        errors = list((previous_status.get("summary") or {}).get("errors", []))
        coverage = [ClauseCoverage.model_validate(c) for c in previous_status.get("coverage",[]) if c["clause_key"] in self.completed]
        for call_index in range(1,calls+1):
            try:
                trace.append(self.artifacts.read(run_id,f"diagnostics/calls/{call_index}.json"))
            except FileNotFoundError:
                continue
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
            if key in self.completed:
                continue
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
                    "fact_count": self.statistics.fact_count,
                }
            )
            plan = compile_search_plan(clause, limit=min(32, budget.max_objects_per_clause))
            self.artifacts.write(run_id, f"input/search/{key}.json", plan)
            intent = SearchIntent.model_validate(plan["objects"])
            try:
                candidates, search_observation = retrieve_candidates(self.documents, request.ir_run_id, intent,
                    budget.max_objects_per_clause, sources=self.sources.values(),
                    broaden=lambda: calls < budget.max_model_calls and time.monotonic()-started < budget.timeout_seconds
                        and not any(m.match == "match" for m in local_mappings.values()))
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
                    coverage,
                    calls,
                    started,
                    errors,
                )
                continue
            local_mappings = {m.source_fact_id:m for m in restored_mappings if m.clause_key == key}
            local_facts = {fid:restored_facts[fid] for fid in local_mappings}
            physical_facts = {(fact.kind,e.object_id,e.page_index,e.excerpt,e.char_start):fid
                for fid,fact in local_facts.items()
                for e in fact.evidence if e.role in {"value","statement"}}

            def unread(physical):
                previous = physical_facts.get(physical)
                return previous is None or local_mappings[previous].match == "uncertain"

            read_frames = [EvidenceFrame.model_validate(self.artifacts.read(run_id,path))
                for path in sorted(self.frame_paths.get(key,()))]
            reasons = ["object_budget_exhausted"] if search_observation["truncated"] else []
            inspected_ids = {item.rsplit("/",1)[0] for item in self.visited if item.startswith(key+"/")}
            inspected = len(inspected_ids)
            complete = not search_observation["truncated"]
            for candidate in candidates:
                source_document = self.sources[candidate.source_snapshot]
                for chunk in range(candidate.chunk_count):
                    frame_key = f"{key}/{candidate.source_snapshot}/{candidate.object_id}/{chunk}"
                    if frame_key in self.visited:
                        continue
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
                        frame = self.documents.read(source_document.ir_run_id, candidate, chunk)
                        frame = stamp_frame(frame, source_document.snapshot_id)
                        read_frames.append(frame)
                        frame_path = f"evidence/{digest([key,frame.source_snapshot,frame.object_id,chunk,frame.digest])}.json"
                        self.artifacts.write(run_id,frame_path,frame.model_dump(mode="json"))
                        self.frame_paths.setdefault(key,set()).add(frame_path)
                    except Exception as exc:
                        complete = False
                        reasons.append("document_read_failed")
                        errors.append(
                            {
                                "clause_key": key,
                                "object_id": candidate.object_id,
                                "frame_key": frame_key,
                                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                            }
                        )
                        continue
                    saved = self.pending_reads.get(frame_key)
                    pending_frames = [(EvidenceFrame.model_validate(body),parent,attempt) for body,parent,attempt in saved] if saved else [
                        (piece,None,0) for piece in (budget_frames(frame,budget.max_output_tokens) if clause.data_kind == 'quantitative' else [frame])]
                    frame_succeeded = saved is None or not any(
                        error.get("frame_key") == frame_key for error in errors
                    )
                    complete = complete and frame_succeeded
                    while pending_frames:
                        if cancel_check():
                            raise PipelineCancelled("Extraction cancelled")
                        if calls >= budget.max_model_calls or time.monotonic() - started >= budget.timeout_seconds:
                            complete = False
                            frame_succeeded = False
                            reasons.append("execution_budget_exhausted")
                            break
                        frame, parent_call, attempt = pending_frames.pop(0)
                        telemetry = {}
                        if clause.data_kind == "quantitative":
                            rows = []
                            for row in frame.rows:
                                if row.row_role == "header":
                                    continue
                                cells = []
                                for cell in row.cells:
                                    physical = ("quantitative",cell.source_object_id or cell.cell_id,
                                        cell.page_index if cell.page_index is not None else frame.page_index,
                                        cell.raw_text,cell.char_start)
                                    if not unread(physical):
                                        continue
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
                            frame = frame.model_copy(
                                update={"rows": rows}
                            )
                        else:
                            frame = frame.model_copy(update={"texts":[text for text in frame.texts
                                if unread(("assertion",text.object_id,text.page_index,text.text,text.char_start))]})
                        if clause.data_kind == "quantitative" and not frame.rows:
                            reasons.append("object_has_no_bindable_quantitative_cells")
                            continue
                        if clause.data_kind == "qualitative" and not frame.texts:
                            reasons.append("object_has_no_bindable_statements")
                            continue
                        if frame.fragment is not None:
                            frame_path = f"evidence/{digest([key,frame.source_snapshot,frame.object_id,chunk,frame.digest])}.json"
                            self.artifacts.write(run_id,frame_path,frame.model_dump(mode="json"))
                        context = {
                            "target": clause.reading_card(),
                            "frame": reading_frame(frame),
                            "execution": {"run_id": run_id, "task_type": "clause_" + clause.data_kind,
                                "evidence_ids": [frame.object_id], "attempt": attempt, "parent_task_id": f"{run_id}/calls/{parent_call}" if parent_call else None, "remaining_seconds": max(0.001, budget.timeout_seconds - (time.monotonic() - started))},
                        }
                        structure = column_structure(frame) if clause.data_kind == "quantitative" else None
                        decided_columns = period_columns(frame) if structure else None
                        if decided_columns and all(cell.column_id in decided_columns and frame.column_sources.get(cell.column_id) for row in frame.rows for cell in row.cells):
                            context["target"]["requested_identity_fields"] = [field for field in context["target"]["requested_identity_fields"] if field["axis"] != "reporting_period"]
                            context["bound_identity_axes"] = ["reporting_period"]
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
                        calls += 1
                        on_progress(
                            {
                                "stage": "reading",
                                "clause_key": key,
                                "object_id": frame.object_id,
                                "current": index - 1,
                                "total": len(clauses),
                                "model_calls": calls,
                                "fact_count": self.statistics.fact_count,
                            }
                        )
                        call = {
                            "call_index": calls,
                            "clause_key": key,
                            "clause_digest": clause.clause_digest,
                            "object_id": frame.object_id,
                            "frame_digest": frame.digest,
                            "parent_call_index": parent_call, "attempt": attempt,
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
                            row_relations = {}
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
                                        results = [
                                            numeric_fact(
                                                clause,
                                                source_document,
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
                                                source_document,
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
                                            continue
                                        stable_id = digest([run_id, key, fact.source_anchor_id])
                                        location = next(e for e in fact.evidence if e.role in {"value","statement"})
                                        physical = (fact.kind,location.object_id,location.page_index,location.excerpt,location.char_start)
                                        previous = physical_facts.get(physical)
                                        if previous:
                                            if local_mappings[previous].match != "uncertain":
                                                continue
                                            stable_id = previous
                                            fact = fact.model_copy(update={"source_anchor_id":local_facts[previous].source_anchor_id})
                                        physical_facts[physical] = stable_id
                                        fact = fact.model_copy(update={"source_fact_id":stable_id})
                                        mapping = mapping.model_copy(update={"source_fact_id":stable_id})
                                        local_facts[fact.source_fact_id] = fact
                                        local_mappings[fact.source_fact_id] = mapping
                                        self._record(fact, mapping)
                                        if clause.data_kind == "quantitative":
                                            row_relations.setdefault(match.row_id,set()).add(mapping.match)
                                except (ValueError, TypeError, KeyError) as exc:
                                    reasons.append("binding_rejected:" + str(exc))
                            if clause.data_kind == "quantitative":
                                pieces = remainder_frames(frame,supplements,row_relations)
                                if pieces:
                                    pending_frames.extend((piece,calls,attempt+1) for piece in pieces)
                                    call["remainder_frames"] = [piece.digest for piece in pieces]
                            inspected_ids.add(frame_key.rsplit("/",1)[0])
                            inspected = len(inspected_ids)
                        except Exception as exc:
                            if isinstance(exc,PipelineCancelled) or cancel_check():
                                call.update(status="cancelled",error=str(exc)[:300],telemetry=getattr(exc,"telemetry",telemetry))
                                self.artifacts.write(run_id,f"diagnostics/calls/{calls}.json",call)
                                self._write_outputs(run_id,document,clauses,coverage,calls,started,errors)
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
                            pieces = split_frame(frame, exc.category,quantitative=clause.data_kind == 'quantitative') if getattr(exc, "category", None) in {"generation_incomplete", "input_budget", "timeout"} else []
                            if pieces:
                                call["status"] = "split_retry"
                                call["retry_frames"] = [piece.digest for piece in pieces]
                                pending_frames[0:0] = [(piece, calls, attempt + 1) for piece in pieces]
                                reasons.append("bounded_frame_split")
                            else:
                                reasons.append("model_or_contract_failure")
                                errors.append({"clause_key": key, "object_id": frame.object_id,
                                    "frame_key":frame_key,"error": call["error"]})
                                complete = False
                                frame_succeeded = False
                        trace.append(call)
                        self.artifacts.write(run_id, f"diagnostics/calls/{calls}.json", call)
                        self.artifacts.write(run_id, "diagnostics/candidates.json", rejected_candidates)
                        active = ClauseCoverage(
                            clause_key=key,
                            clause_digest=clause.clause_digest,
                            state="not_covered",
                            candidate_objects=len(candidates),
                            inspected_objects=inspected,
                            fact_count=len(local_facts),
                            reasons=["reading_in_progress"],
                        )
                        if pending_frames:
                            self.pending_reads[frame_key] = [
                                (piece.model_dump(mode="json"), parent, attempt)
                                for piece, parent, attempt in pending_frames
                            ]
                        else:
                            self.pending_reads.pop(frame_key, None)
                            if frame_succeeded:
                                self.visited.add(frame_key)
                                errors = [error for error in errors if error.get("frame_key") != frame_key]
                        self._write_outputs(
                            run_id,
                            document,
                            clauses,
                            [*coverage, active],
                            calls,
                            started,
                            errors,
                        )
                        on_progress({"stage": "extracting", "clause_key": key, "object_id": frame.object_id,
                                     "current": index - 1, "total": len(clauses), "model_calls": calls,
                                     "fact_count": self.statistics.fact_count})
                        if calls-turn_start_calls >= 2 and (pending_frames or frame_succeeded):
                            raise ExecutionYield()
                    if frame_succeeded:
                        self.visited.add(frame_key)
                        errors = [error for error in errors if error.get("frame_key") != frame_key]
                if not complete and (
                    calls >= budget.max_model_calls
                    or time.monotonic() - started >= budget.timeout_seconds
                ):
                    break
            if search_observation["truncated"]:
                complete = False
                reasons.append("object_budget_exhausted")
            inspected_ids.update(item.rsplit("/",1)[0] for item in self.visited if item.startswith(key+"/"))
            inspected = len(inspected_ids)
            if search_observation.get("error"):
                errors.append({"clause_key":key,"error":search_observation["error"]})
            if complete and calls < budget.max_model_calls and time.monotonic()-started < budget.timeout_seconds:
                page_intent = plan["page"]
                if not any(m.match == "match" for m in local_mappings.values()):
                    page_intent = {**page_intent,"required_term_groups":[]}
                need = self._page_request(key,"target",page_intent,"Prepared evidence read; locate remaining pages for this clause")
                if need:
                    self._write_outputs(run_id,document,clauses,coverage,calls,started,errors)
                    raise EvidenceNeeded(need)
            context_facts = {fid:fact for fid,fact in local_facts.items() if local_mappings[fid].match != "different" and missing_attributes(clause,fact)}
            if context_facts and key not in self.enriched and calls < budget.max_model_calls and time.monotonic()-started < budget.timeout_seconds:
                try:
                    context_evidence, context_search = context_frame(self.documents,self.sources,clause,list(context_facts.values()),plan["context"],read_frames)
                except (OSError,ValueError) as exc:
                    context_evidence, context_search = None, {"terms":[],"error":str(exc)[:300]}
                self.artifacts.write(run_id,f"diagnostics/context/{key}.json",context_search)
                if context_evidence is not None:
                    self.artifacts.write(run_id,f"evidence/context/{key}.json",context_evidence.model_dump(mode="json"))
                    context_values = [fact for fid,fact in context_facts.items()
                        if digest([context_evidence.digest,fid]) not in self.context_reads]
                    batch_size = max(1,budget.max_output_tokens//1024)
                    pending_context = [(context_values[offset:offset+batch_size],None,0)
                        for offset in range(0,len(context_values),batch_size)]
                    while pending_context:
                        if calls >= budget.max_model_calls or time.monotonic()-started >= budget.timeout_seconds:
                            reasons.append("enrichment_budget_exhausted")
                            break
                        batch,parent_call,attempt = pending_context.pop(0)
                        aliases = {f"f{i}":fact.source_fact_id for i,fact in enumerate(batch,1)}
                        inputs = [{"id":alias,"label":local_facts[fid].source_row_label,
                            "local_source_ids":context_search["fact_sources"][fid],
                            "missing_attributes":[axis for axis in missing_attributes(clause,local_facts[fid]) if axis in context_search["requested_attributes"]],
                            "identity":{axis:field.model_dump(mode="json",exclude={"evidence"}) for axis,field in
                                {**{name:getattr(local_facts[fid].identity,name) for name in type(local_facts[fid].identity).model_fields if name != "dimensions"},
                                **local_facts[fid].identity.dimensions}.items() if field.state in {"stated","inherited"}},
                            "local_evidence":[e.excerpt for e in local_facts[fid].evidence if e.role != "value"]} for alias,fid in aliases.items()]
                        requested_attributes = sorted({axis for item in inputs for axis in item["missing_attributes"]})
                        report_attributes = sorted(set(requested_attributes) & set(context_search["report_attributes"]))
                        report_sources = context_search["report_sources"] if report_attributes else []
                        source_ids = {sid for item in inputs for sid in item["local_source_ids"]} | set(report_sources)
                        batch_frame = context_evidence.model_copy(update={
                            "texts":[t for t in context_evidence.texts if t.span_id in source_ids],
                            "column_sources":{sid:parts for sid,parts in context_evidence.column_sources.items() if sid in source_ids}})
                        target_card = clause.reading_card()
                        target_card["requested_identity_fields"] = [field for field in target_card["requested_identity_fields"]
                            if field["axis"] in requested_attributes]
                        calls += 1
                        call = {"call_index":calls,"clause_key":key,"clause_digest":clause.clause_digest,
                            "task_type":"context_enrichment","data_kind":clause.data_kind,"frame_digest":context_evidence.digest,"fact_aliases":aliases,
                            "parent_call_index":parent_call,"attempt":attempt}
                        on_progress({"stage":"enriching","clause_key":key,"current":index-1,"total":len(clauses),
                            "model_calls":calls,"fact_count":self.statistics.fact_count})
                        try:
                            raw, telemetry = self.model.complete(instructions=CONTEXT_INSTRUCTIONS,
                                context={"target":target_card,"requested_attributes":requested_attributes,"facts":inputs,"context":reading_frame(batch_frame),
                                    "report_sources":report_sources,"report_attributes":report_attributes,
                                    "execution":{"run_id":run_id,"task_type":"clause_context","evidence_ids":list(dict.fromkeys([*[t.object_id for t in batch_frame.texts],
                                            *[p.object_id for parts in batch_frame.column_sources.values() for p in parts]])),
                                        "attempt":attempt,"parent_task_id":f"{run_id}/calls/{parent_call}" if parent_call else None,
                                        "remaining_seconds":max(.001,budget.timeout_seconds-(time.monotonic()-started))}},
                                schema=context_schema(batch_frame,aliases,requested_attributes),max_tokens=budget.max_output_tokens,cancel_check=cancel_check)
                            decision = ContextDecision.model_validate(raw if isinstance(raw,dict) else json.loads(raw))
                            if telemetry.get("execution"):
                                telemetry["execution"]["output_validation"] = "passed"
                            count, changed_ids = apply_context(clause,self.sources,context_evidence,decision,local_facts,local_mappings,aliases,context_search)
                            call.update(status="ok",telemetry=telemetry,decision=decision.model_dump(mode="json"),attributes_added=count)
                            for fid in changed_ids:
                                self._record(local_facts[fid],local_mappings[fid])
                            for fid in aliases.values():
                                self.context_reads.add(digest([context_evidence.digest,fid]))
                        except Exception as exc:
                            call.update(status="cancelled" if cancel_check() or isinstance(exc,PipelineCancelled) else "failed",error=str(exc)[:400],
                                telemetry=getattr(exc,"telemetry",{}))
                            self.artifacts.write(run_id,f"diagnostics/calls/{calls}.json",call)
                            if call["status"] == "cancelled":
                                self._write_outputs(run_id,document,clauses,coverage,calls,started,errors)
                                raise PipelineCancelled("Extraction cancelled") from exc
                            if getattr(exc,"category",None) in {"generation_incomplete","input_budget","timeout"} and len(batch)>1:
                                middle = len(batch)//2
                                pending_context[0:0] = [(batch[:middle],calls,attempt+1),(batch[middle:],calls,attempt+1)]
                                call.update(status="split_retry",retry_fact_batches=[middle,len(batch)-middle])
                                reasons.append("bounded_context_split")
                            else:
                                reasons.append("context_reading_unavailable")
                        trace.append(call)
                        self.artifacts.write(run_id,f"diagnostics/calls/{calls}.json",call)
                        self.results.flush()
                        self._save_checkpoint(calls,started)
                        if call['status'] == 'ok' and calls-turn_start_calls >= 2 and pending_context:
                            self._write_outputs(run_id,document,clauses,coverage,calls,started,errors)
                            raise ExecutionYield()
                unresolved = {axis for fid in context_facts
                    for axis in missing_attributes(clause,local_facts[fid])
                    if plan["context"]["attributes"].get(axis)
                    and "report" in clause.attributes[axis].context_levels}
                terms = list(dict.fromkeys(term for axis in sorted(unresolved)
                    for term in plan["context"]["attributes"][axis]))
                if terms and key not in self.context_attempted:
                    context_intent = {"target_id":key,"contract_digest":clause.clause_digest,
                        "data_kind":"qualitative","positive_terms":terms,
                        "exact_terms":terms,"source_priority":["text"]}
                    need = self._page_request(key,"context",context_intent,"Read existing context first; look for unresolved attributes outside prepared pages")
                    if need:
                        self._write_outputs(run_id,document,clauses,coverage,calls,started,errors)
                        raise EvidenceNeeded(need)
                self.enriched.add(key)
            self.completed.add(key)
            required = {s.scope_id for s in clause.output_scopes if s.required}
            covered = {
                scope
                for mapping in local_mappings.values()
                if mapping.match == "match"
                for scope in mapping.scope_ids
            }
            if not complete:
                state = "not_covered"
            elif required <= covered and any(m.match == "match" for m in local_mappings.values()):
                state = "covered"
            elif any(m.match in {"match","uncertain"} for m in local_mappings.values()):
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
                    candidate_objects=max(len(candidates),inspected),
                    inspected_objects=inspected,
                    fact_count=len(local_facts),
                    required_scopes=sorted(required),
                    covered_scopes=sorted(covered),
                    reasons=list(dict.fromkeys(reasons)),
                    search=search_observation,
                    periods=period_coverage(read_frames, list(local_facts.values()), data_kind=clause.data_kind),
                )
            )
            self._write_outputs(
                run_id,
                document,
                clauses,
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
                    "fact_count": self.statistics.fact_count,
                }
            )
            if calls-turn_start_calls >= 2 and index < len(clauses):
                raise ExecutionYield()
        self.artifacts.write(run_id, "diagnostics/model-calls.json", trace)
        self.artifacts.write(run_id, "diagnostics/candidates.json", rejected_candidates)
        return self._write_outputs(
            run_id, document, clauses, coverage, calls, started, errors,
            final=True, rejected_count=len(rejected_candidates),
        )

    def _write_outputs(
        self,
        run_id,
        document,
        clauses,
        coverage,
        calls,
        started,
        errors,
        final=False,
        rejected_count=0,
    ):
        self.results.flush()
        body = {
            "schema_version": "extraction-fact-bundle-2.4",
            "engine_version": ENGINE_VERSION,
            "engine_identity": self.engine_identity,
            "run_id": run_id,
            "document": document.model_dump(mode="json"),
            "sources": {key:source.model_dump(mode="json") for key,source in self.sources.items()},
            "clause_targets": {k: c.clause_digest for k, c in clauses.items()},
            "coverage": [c.model_dump(mode="json") for c in coverage],
            "summary": {
                "selected_clauses": len(clauses),
                "processed_clauses": len(coverage),
                "model_calls": calls,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "errors": errors,
                **self.statistics.summary(coverage, errors, final, rejected_count),
            },
        }
        self.results.status(body)
        self._save_checkpoint(calls,started)
        if not final:
            return body
        # Complete the transport bundle once. Running views read the indexed facts.
        body.update(self.results.collection())
        body = FactBundle.model_validate(body).model_dump(
            mode="json", exclude_none=True
        )
        self.artifacts.write(run_id, "results/facts.json", body)
        self.results.status({k:v for k,v in body.items() if k not in {"facts","clause_mappings"}})
        return body

    def _record(self, fact, mapping):
        self.results.put(fact, mapping)
        self.statistics.record(fact, mapping)

    def _save_checkpoint(self,calls,started):
        self.results.checkpoint({"calls":calls,"elapsed_seconds":time.monotonic()-started,
            "completed_clauses":sorted(self.completed),"enriched_clauses":sorted(self.enriched),
            "visited_frames":sorted(self.visited),"context_reads":sorted(self.context_reads),
            'pending_reads':self.pending_reads,
            "evidence_frames":{key:sorted(paths) for key,paths in self.frame_paths.items()}})

    def _page_request(self, key, phase, intent, reason):
        primary = next(iter(self.sources.values()))
        if not primary.source_sha256:
            return None
        excluded = sorted({page for source in self.sources.values()
            for page in (source.search_scope or {}).get("prepared_pages", (source.search_scope or {}).get("available_pages",[]))})
        query = dict(intent)
        request_id = digest([key,phase,sorted(self.sources),excluded,query])
        if request_id in self.exhausted_queries:
            return None
        return EvidencePreparationRequest(request_id=request_id,clause_key=key,phase=phase,
            source_ir_run_id=primary.ir_run_id,intent=PageSearchIntent.model_validate(query),
            excluded_pages=excluded,reason=reason)


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
        row_relations = {match.match for match in matches if match.row_id == row.row_id and match.selection == "measurement"}
        if row.row_role == "header" or not row.label.strip():
            continue
        # A model-confirmed measurement row can have unitless counts. A wholly
        # unselected row still needs the stronger printed data-row/unit proof.
        unit_grounded = bool(row.unit_raw and any(row.unit_raw in source.text for source in row.unit_sources))
        if not row_relations and (row.row_role != "data" or not unit_grounded):
            continue
        columns = [cell.column_id for cell in row.cells if cell.column_id in decided_columns
            and (row.row_id, cell.column_id) not in selected and cell.candidate_kind == "measurement"
            and re.fullmatch(r"\s*[-+−]?\d[\d,，]*(?:\.\d+)?\s*", cell.raw_text)]
        if columns:
            for column in columns:
                relation = next(iter(row_relations)) if len(row_relations) == 1 and column in plain_periods else "uncertain"
                result.append(NumericMatch(row_id=row.row_id, columns=[column], selection="measurement", match=relation,
                    reason="Printed table quantity retained independently of target selection"))
    return result


def stamp_frame(frame, snapshot):
    def stamp(source):
        return source.model_copy(update={"source_snapshot":snapshot})
    return frame.model_copy(update={"source_snapshot":snapshot,
        "texts":[stamp(t) for t in frame.texts], "title_sources":[stamp(t) for t in frame.title_sources],
        "column_sources":{key:[stamp(t) for t in values] for key,values in frame.column_sources.items()},
        "rows":[row.model_copy(update={"label_sources":[stamp(t) for t in row.label_sources],
            "unit_sources":[stamp(t) for t in row.unit_sources]}) for row in frame.rows]})
