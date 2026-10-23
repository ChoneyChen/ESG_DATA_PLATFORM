"""Incremental, model-free projections of durable source and Core artifacts.

A stage snapshot is not a second result database. It can always be rebuilt from
immutable inputs, source caches and validated checkpoints. Read APIs never create
or update artifacts; writers publish atomically after a durable unit of work.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from esg_standard_packages.contracts import CompiledStandardPackage
from esg_targeted.contracts import ExtractionRequest
from esg_targeted.io import write_json
from esg_targeted.results.spec import RECORD_LAYOUT, RESULT_SCHEMA_VERSION, RESULT_BUNDLE_LAYOUT_VERSION
from esg_targeted.results.validation import ResultContractError, ResultContractValidator
from esg_targeted.results.durable_sources import adapt_durable_direct_core_facts, partition_metric_records
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.standards.runtime_snapshot import (
    build_internal_dependency_scope, declared_internal_ids, execution_digest, load_runtime_package,
    selected_output_ids, selected_output_counts, validate_internal_dependency_scope,
)


STAGE_SCHEMA_VERSION = "report-stage-snapshot-v1"
INTERNAL_PROJECTION_VERSION = 'durable-internal-core-v1'


def _read(path, default=None):
    if not path.is_file():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def collect_stage(artifact_store, job_id, *, use_snapshot=True, request=None, package=None):
    """Read persisted progress, including legacy jobs without a final Bundle."""
    try:
        root = artifact_store.existing_job_dir(job_id)
    except FileNotFoundError:
        # The output folder was deleted (for example by a cleanup) while the
        # session record remained. The record must stay readable and
        # deletable instead of failing the whole session list.
        return {"schema_version": STAGE_SCHEMA_VERSION, "missing_artifact": True,
                "summary": {"status": "artifact_missing", "execution_status": "unavailable",
                            "result_status": "not_evaluated", "counts": {},
                            "unfinished_metric_ids": [], "processing_metric_ids": []},
                "records": {key: [] for key in RECORD_LAYOUT},
                "source_facts": {"schema_version": "source-facts-v1", "facts": [], "objects": [],
                                 "historical_facts": [], "source_objects": [], "gaps": []},
                "metric_source_mappings": {"schema_version": "metric-source-mappings-v1",
                                           "mappings": [], "core_bindings": []}}
    diagnostics: list[str] = []
    snapshot_path = root / "progress/snapshot.json"
    durable_paths = [root / "agent/source-invalidations.json", root / "agent/state.json", root / "progress/metric-attempts.json",
                     root / 'input/runtime-standard-snapshot.json',
                     root / "derivations.json", root / "progress/core-postprocessing.json",
                     *list((root / "checkpoints").glob("*.json")), *list((root / "agent/sources").glob("*.json")),
                     *list((root / 'packets').glob('*.regions.json')), *list((root / 'decisions').glob('*.json')),
                     *list((root / "disclosures/projections").glob("*.json")),
                     *list((root / "agent/mapping-fragments").rglob("*.json"))]
    durable_version = max((p.stat().st_mtime_ns for p in durable_paths if p.is_file()), default=0)
    raw_request = _read(root / "input/request.json")
    request = request or (ExtractionRequest.model_validate(raw_request) if raw_request else None)
    shared_invalidations = artifact_store.report_session_path(request.report_session_id) / "source-invalidations.json" if request and request.report_session_id else None
    if shared_invalidations and shared_invalidations.is_file():
        durable_version = max(durable_version, shared_invalidations.stat().st_mtime_ns)
    raw_package = _read(root / "input/standard-package.json")
    package = package or (CompiledStandardPackage.model_validate(raw_package) if raw_package else None)
    if package and not hasattr(package, 'runtime_snapshot'):
        package = load_runtime_package(package, artifact_root=root, request=request)
    selected = selected_output_ids(request, package) if request and package else list(request.metric_ids if request else [])
    if not selected and package:
        selected = [m.metric_id for m in package.metrics]
    internal_ids = set(getattr(request,'internal_metric_ids', []))
    if package and hasattr(package,'runtime_snapshot'):
        internal_ids = declared_internal_ids(package,selected)
    internal_ids -= set(selected)
    bundle_path = root / "exports/extraction-result.json"
    if use_snapshot and not internal_ids and bundle_path.is_file() and bundle_path.stat().st_mtime_ns >= durable_version and (not snapshot_path.is_file() or bundle_path.stat().st_mtime_ns >= snapshot_path.stat().st_mtime_ns):
        bundle = _read(bundle_path)
        summary = bundle.get("summary", {})
        records = bundle.get("records", {})
        if package and summary.get("standard", {}).get("source_digest") == package.compilation.source_digest and summary.get('standard', {}).get('runtime_standard_digest', package.compilation.source_digest) == execution_digest(package):
            try:
                ResultContractValidator(package.core).validate(records)
            except ResultContractError as contract_error:
                # Fall through to independently checked durable fragments, but
                # keep the reason so a broken bundle is diagnosable.
                diagnostics.append(f"bundle_contract_invalid: {contract_error}")
            else:
                source_payload = bundle.get("source_facts", _read(root / "results/source-facts.json"))
                mapping_payload = bundle.get("metric_source_mappings", _read(root / "results/metric-source-mappings.json"))
                done = {o.get("metric_id") for o in summary.get("outcomes", [])} & set(selected)
                unfinished = set(summary.get("unfinished_metric_ids", [])) | set(summary.get("processing_metric_ids", []))
                unfinished -= done
                unexecuted = set(summary.get("not_executed_metric_ids", set(selected) - done - unfinished))
                counts = {"selected_metrics": len(selected), "processed_metrics": len(done), "processing_metrics": 0,
                    "attempted_metrics": len(done | unfinished), "unfinished_metrics": len(unfinished),
                    "not_executed_metrics": len(unexecuted), "candidate_source_facts": len(source_payload.get("facts", [])),
                    "source_objects": len(source_payload.get("source_objects", source_payload.get("objects", []))),
                    "complete_source_objects": sum(bool(o.get("complete", o.get("interpretation", {}).get("complete"))) for o in source_payload.get("source_objects", source_payload.get("objects", []))),
                    "core_facts": sum(len(records.get(k, [])) for k in ("quantitative_observations", "qualitative_assertions")),
                    "core_bindings": len(mapping_payload.get("core_bindings", [])), "evidence_references": len(records.get("evidence_references", []))}
                counts.update(selected_output_counts(package, selected))
                summary = {**summary, "counts": counts, "processing_metric_ids": [],
                    "not_executed_metric_ids": sorted(unexecuted), "unfinished_metric_ids": sorted(unfinished),
                    "result_status": "usable" if summary.get("execution_status") == "completed" and counts["core_facts"] else
                        "partial" if counts["core_facts"] else "needs_confirmation" if counts["candidate_source_facts"] or any(o.get('dependency_status') == 'blocked' for o in summary.get('outcomes', [])) else "no_evidence_in_bound",
                    "maturity": "core_validated" if counts["core_facts"] else "source_candidates" if counts["candidate_source_facts"] else "no_facts"}
                return {"schema_version": STAGE_SCHEMA_VERSION, "summary": summary, "records": records,
                    "source_facts": {**source_payload, "objects": source_payload.get("source_objects", source_payload.get("objects", []))},
                    "metric_source_mappings": mapping_payload}
    if use_snapshot and snapshot_path.is_file() and snapshot_path.stat().st_mtime_ns >= durable_version:
        cached = _read(snapshot_path)
        if (not internal_ids or cached.get('summary',{}).get('internal_projection_version') == INTERNAL_PROJECTION_VERSION) and (not package or cached.get('summary', {}).get('standard', {}).get('runtime_standard_digest', package.compilation.source_digest) == execution_digest(package)):
            return cached
    records = {key: [] for key in RECORD_LAYOUT}
    ids = {key: {} for key in RECORD_LAYOUT}
    outcomes, errors, checkpoints = [], [], []
    validator = ResultContractValidator(package.core) if package else None
    invalidated_ids = set(_read(root / "agent/source-invalidations.json").get("source_fact_ids", []))
    if shared_invalidations and shared_invalidations.is_file():
        invalidated_ids = set(_read(shared_invalidations).get("source_fact_ids", []))
    stale_core_ids = {binding.get("core_record_id") for path in [root / 'results/metric-source-mappings.json', *list((root / "disclosures/projections").glob("*.json"))]
                     for binding in _read(path).get("core_bindings", []) if binding.get("source_fact_id") in invalidated_ids}
    prior_internal = _read(root / 'results/internal-dependencies.json')
    stale_core_ids.update(b.get('core_record_id') for b in prior_internal.get('core_bindings',[])
                          if b.get('source_fact_id') in invalidated_ids)
    stale_metrics = []
    for path in sorted((root / "checkpoints").glob("*.json")):
        checkpoint = _read(path)
        metric_id = checkpoint.get("metric_id")
        if metric_id not in set(selected) | internal_ids:
            continue  # only the selected output and its frozen dependencies
        if checkpoint.get("package_source_digest") != (package.compilation.source_digest if package else None):
            continue
        if package and checkpoint.get('runtime_standard_digest', package.compilation.source_digest) != execution_digest(package):
            continue
        candidate = {key: checkpoint.get("records", {}).get(key, []) for key in RECORD_LAYOUT}
        if any((r.get("observation_id") or r.get("assertion_id")) in stale_core_ids for key in ("quantitative_observations", "qualitative_assertions") for r in candidate[key]):
            stale_metrics.append(metric_id)
            errors.append(f"Source interpretation changed; projection pending for {metric_id}")
            continue
        if checkpoint.get("contract_validation", {}).get("status") != "passed":
            errors.append(f"Quarantined checkpoint for {metric_id}: contract not passed")
            continue
        try:
            if validator:
                validator.validate(candidate)
            combined = {key: [*records[key]] for key in RECORD_LAYOUT}
            for key, rows in candidate.items():
                primary_key = RECORD_LAYOUT[key]["primary_key"]
                for row in rows:
                    previous = ids[key].get(row.get(primary_key))
                    if previous is not None and previous != row:
                        raise ResultContractError("Conflicting checkpoint primary key")
                    if previous is None:
                        combined[key].append(row)
            if validator:
                validator.validate(combined)
        except ResultContractError as exc:
            errors.append(f"Quarantined checkpoint for {metric_id}: {exc}")
            continue
        records = combined
        ids = {key: {r[RECORD_LAYOUT[key]["primary_key"]]: r for r in rows} for key, rows in records.items()}
        outcomes.append(checkpoint.get("outcome", {}))
        checkpoints.append(metric_id)
    internal_records = partition_metric_records(records,internal_ids)
    internal_outcomes = [o for o in outcomes if o.get('metric_id') in internal_ids]
    internal_checkpoint_ids = set(checkpoints) & internal_ids
    stale_internal_ids = set(stale_metrics) & internal_ids
    records = partition_metric_records(records,selected)
    outcomes = [o for o in outcomes if o.get('metric_id') in selected]
    checkpoints = [mid for mid in checkpoints if mid in selected]
    stale_metrics = [mid for mid in stale_metrics if mid in selected]
    # Completed deterministic postprocessing is a durable Core unit as well.
    # It preserves accepted family resolutions and calculated facts if export
    # is interrupted, while a later source/checkpoint change invalidates it.
    post_path = root / "progress/core-postprocessing.json"
    core_paths = [p for p in durable_paths if p.name not in {"state.json", "metric-attempts.json", "core-postprocessing.json"}]
    if shared_invalidations:
        core_paths.append(shared_invalidations)
    core_version = max((p.stat().st_mtime_ns for p in core_paths if p.is_file()), default=0)
    post = _read(post_path)
    post_current = bool(post and validator and post_path.stat().st_mtime_ns >= core_version
        and post.get("package_source_digest") == package.compilation.source_digest
        and post.get('runtime_standard_digest', package.compilation.source_digest) == execution_digest(package))
    post_accepted = False
    if post_current:
        try:
            validator.validate(post["records"])
        except (ResultContractError, KeyError) as exc:
            errors.append(f"Quarantined postprocessing Core: {exc}")
        else:
            records, outcomes = post["records"], post["outcomes"]
            checkpoints = [o["metric_id"] for o in outcomes if o.get("metric_id") in selected]
            post_accepted = True
    if post and not post_accepted:
        # A selected calculated target may have no fact in its original
        # checkpoint: its only Core was produced from internal operands later.
        # Changed inputs invalidate the postprocessing unit and require replay,
        # including targets invisible to checkpoint binding invalidation.
        stale_metrics = sorted(set(stale_metrics) | {
            o["metric_id"] for o in post.get("outcomes", []) if o.get("metric_id") in selected})
        checkpoints = [mid for mid in checkpoints if mid not in stale_metrics]
        outcomes = [o for o in outcomes if o.get("metric_id") not in stale_metrics]
        errors.append("Core postprocessing inputs changed; family resolution and derivation require replay")
    internal_path = root / 'results/internal-dependencies.json'
    finalized_internal = None
    if internal_ids and internal_path.is_file() and post:
        # A completed scope includes family adjudication. Do not resurrect its
        # older rejected checkpoint facts if any durable source later changes.
        if internal_path.stat().st_mtime_ns >= core_version or post_accepted:
            try:
                finalized_internal = validate_internal_dependency_scope(_read(internal_path),package)
                internal_records = finalized_internal.records
                internal_outcomes = finalized_internal.outcomes
                internal_checkpoint_ids = {o['metric_id'] for o in internal_outcomes
                    if o.get('status') not in {'not_executed','processing','unfinished','stale','contract_failed'}}
            except (ValueError, KeyError) as exc:
                errors.append(f'Quarantined finalized internal scope: {exc}')
        if finalized_internal is None:
            internal_records = {key:[] for key in RECORD_LAYOUT}
            internal_outcomes = []
            internal_checkpoint_ids = set()
            stale_internal_ids.update(internal_ids)
            errors.append('Internal postprocessing inputs changed; dependency Core requires replay')
    derivations = _read(root / "derivations.json")
    derivation_path = root / "derivations.json"
    derivation_inputs = max((p.stat().st_mtime_ns for p in core_paths if p.is_file() and p != derivation_path), default=0)
    if derivations and derivation_path.stat().st_mtime_ns >= derivation_inputs:
        from esg_targeted.results.derivation import DerivationExecutor
        pending_extensions = {o['metric_id']:{key:o.get(key) for key in ('dependency_status','dependency_blocks')}
            for o in outcomes if o.get('metric_id') in getattr(package,'derived_only_metric_ids',set())}
        DerivationExecutor.apply_dependency_state(records=records, outcomes=outcomes, report=derivations)
        for outcome in outcomes:
            if outcome.get('metric_id') in pending_extensions:
                outcome.update(pending_extensions[outcome['metric_id']])

    sources, facts, gaps, mappings, bindings = [], {}, {}, [], {}
    quarantined_sources = set()
    for path in sorted((root / "agent/sources").glob("*.json")):
        source = _read(path)
        sources.append(source)
        for fact in source.get("facts", []):
            fact_id = fact.get("source_fact_id")
            if not fact_id:
                continue
            if fact_id in quarantined_sources:
                continue
            if fact_id in facts and facts[fact_id] != fact:
                errors.append(f"Conflicting source fact {fact_id}")
                facts.pop(fact_id, None)
                quarantined_sources.add(fact_id)
            else:
                facts[fact_id] = fact
        for gap in source.get("gaps", []):
            if gap.get("status", "open") != "resolved":
                gaps[gap["gap_id"]] = gap
    agent = _read(root / "agent/state.json")
    for gap in agent.get("gaps", []):
        if gap.get("status", "open") != "resolved":
            gaps[gap["gap_id"]] = gap
    published_sources = _read(root / "results/source-facts.json")
    for fact in published_sources.get("facts", []):
        if fact["source_fact_id"] not in quarantined_sources | invalidated_ids:
            facts.setdefault(fact["source_fact_id"], fact)
    facts = {fid: fact for fid, fact in facts.items() if fid not in invalidated_ids}
    historical = {f["source_fact_id"]: {**f, "interpretation_status": "superseded"} for path in (root / "agent/source-history").glob("*.json")
                  for f in _read(path).get("facts", []) if f["source_fact_id"] in invalidated_ids}
    for fact in published_sources.get("facts", []):
        if fact["source_fact_id"] in invalidated_ids:
            historical.setdefault(fact["source_fact_id"], {**fact, "interpretation_status": "superseded"})
    core_ids = {r.get("observation_id") or r.get("assertion_id") for key in ("quantitative_observations", "qualitative_assertions") for r in records[key]}
    internal_core_ids = {r.get('observation_id') or r.get('assertion_id')
        for key in ('quantitative_observations','qualitative_assertions') for r in internal_records[key]}
    known_internal = finalized_internal
    if internal_ids and prior_internal and not known_internal:
        try:
            known_internal = validate_internal_dependency_scope(prior_internal,package)
        except (ValueError,KeyError) as scope_error:
            # Stale/untrusted material cannot supply current bindings; record
            # why it was ignored instead of silently dropping it.
            diagnostics.append(f"internal_scope_untrusted: {scope_error}")
    if known_internal:
        facts.update({f['source_fact_id']:f for f in known_internal.source_facts
                      if f['source_fact_id'] not in invalidated_ids | quarantined_sources})
        bindings.update({b['core_record_id']:b for b in known_internal.core_bindings
                         if b['source_fact_id'] in facts and b['core_record_id'] in internal_core_ids})
    for path in [root / "results/metric-source-mappings.json", *sorted((root / "disclosures/projections").glob("*.json"))]:
        projection = _read(path)
        mappings.extend(projection.get("mappings", projection.get("metric_mappings", [])))
        for binding in projection.get("core_bindings", []):
            if binding.get("core_record_id") in core_ids | internal_core_ids and binding.get("source_fact_id") in facts:
                bindings[binding["core_record_id"]] = binding
    for path in sorted((root / "agent/mapping-fragments").rglob("*.json")):
        fragment = _read(path)
        mappings.extend({"source_object_id": fragment.get("object_id"), "topic_id": fragment.get("topic_id"), **item}
                        for item in fragment.get("mapping", {}).get("matches", []))
    attempts = _read(root / "progress/metric-attempts.json")
    if package and request and internal_ids:
        direct_sources,direct_bindings = adapt_durable_direct_core_facts(root,internal_records,package,
            request=request,bound_core_ids=set(bindings))
        facts.update({f['source_fact_id']:f for f in direct_sources
                      if f['source_fact_id'] not in invalidated_ids | quarantined_sources})
        bindings.update({b['core_record_id']:b for b in direct_bindings if b['source_fact_id'] in facts})
    internal_scope = None
    if internal_ids:
        current = agent.get('current_metric_id')
        pending_states = {mid:{'status':'stale' if mid in stale_internal_ids else
            ('processing' if agent.get('state') in {'running','idle',None} else 'unfinished')
                if mid == current else 'unfinished' if mid in attempts else 'not_executed'}
            for mid in internal_ids - internal_checkpoint_ids}
        unbound_internal = {row['metric_id'] for name,key in (('quantitative_observations','observation_id'),
            ('qualitative_assertions','assertion_id')) for row in internal_records[name] if row[key] not in bindings}
        if unbound_internal:
            # Preserve the checkpoint for repair; do not deliver a Core fact
            # lacking its report source/binding as an auditable operand.
            internal_records = partition_metric_records(internal_records,internal_ids - unbound_internal)
            internal_outcomes = [o for o in internal_outcomes if o['metric_id'] not in unbound_internal]
            internal_checkpoint_ids -= unbound_internal
            internal_core_ids = {row.get('observation_id') or row.get('assertion_id')
                for name in ('quantitative_observations','qualitative_assertions') for row in internal_records[name]}
            pending_states.update({mid:{'status':'source_binding_pending',
                'reason':'Committed internal Core has no current preserved report source binding.'} for mid in unbound_internal})
            errors.append(f'Quarantined unbound internal Core for {sorted(unbound_internal)}')
        internal_scope = build_internal_dependency_scope(package,internal_ids,selected,internal_records,
            internal_outcomes,list(bindings.values()),list(facts.values()),pending_states=pending_states)
        validate_internal_dependency_scope(internal_scope,package)
    attempted = (set(attempts) | set(checkpoints) | set(stale_metrics)) & set(selected)
    processing = [agent["current_metric_id"]] if agent.get("current_metric_id") in selected else []
    if not processing:
        processing = next((step.get("metric_ids", []) for step in reversed(agent.get("steps", [])) if step.get("status") == "running"), [])
    processing = [mid for mid in processing if mid in selected and mid not in checkpoints]
    if not processing and agent.get("current_metric_id") in selected and agent["current_metric_id"] not in checkpoints:
        processing = [agent["current_metric_id"]]
    attempted.update(processing)
    unfinished = attempted - set(checkpoints) - set(processing)
    counts = {"selected_metrics": len(selected), "processed_metrics": len(checkpoints),
              "attempted_metrics": len(attempted), "unfinished_metrics": len(unfinished),
              "processing_metrics": len(processing), "not_executed_metrics": len(set(selected) - attempted),
              "candidate_source_facts": len(facts), "source_objects": len(sources),
              "complete_source_objects": sum(bool(s.get("complete")) for s in sources),
              "core_facts": len(core_ids), "core_bindings": len(bindings), "evidence_references": len(records["evidence_references"])}
    counts['core_bindings'] = sum(b['core_record_id'] in core_ids for b in bindings.values())
    if internal_scope:
        counts.update(internal_dependency_metrics=len(internal_ids),internal_processed_metrics=len(internal_checkpoint_ids),
            internal_core_facts=len(internal_core_ids),internal_core_bindings=len(internal_scope['core_bindings']),
            internal_evidence_references=len(internal_scope['evidence_references']))
    result_status = "partial" if core_ids else "needs_confirmation" if facts or errors or gaps else "not_evaluated"
    if package:
        counts.update(selected_output_counts(package, selected))
    validation = validator.validate(records) if validator else {"status": "not_run"}
    ir = _read(root / "input/ir-manifest.json")
    summary = {"schema_version": RESULT_SCHEMA_VERSION, "bundle_layout_version": RESULT_BUNDLE_LAYOUT_VERSION,
        "job_id": job_id, "generated_at": datetime.now(UTC).isoformat(),
        "execution_status": "running", "result_status": result_status, "counts": counts,
        "core_schema": {"core_schema_id": package.core.core_schema_id, "core_schema_version": package.core.core_schema_version} if package else {},
        "document": {"document_id": ir.get("document_id"), "ir_run_id": ir.get("run_id") or (request.ir_run_id if request else None),
                     "ir_revision": ir.get("ir_revision"), "document_label": ir.get("document_label"), "evidence_policy": ir.get("evidence_policy", {})},
        "standard": {"package_id": request.package_id, "package_version": request.package_version,
                     "runtime_standard_digest": execution_digest(package) if package else None,
                     "source_digest": package.compilation.source_digest if package else None} if request else {},
        "configuration": request.model_dump(mode="json") if request else {},
        "record_counts": {key: len(rows) for key, rows in records.items()}, "contract_validation": validation,
        "diagnostics": diagnostics,
        "task_count": len(outcomes), "outcomes": outcomes, "errors": errors, "gaps": list(gaps.values()),
        "pending_metric_ids": sorted(set(selected) - set(checkpoints)), "processing_metric_ids": processing,
        "not_executed_metric_ids": sorted(set(selected) - attempted), "unfinished_metric_ids": sorted(unfinished),
        "stale_projection_metric_ids": stale_metrics,
        "is_partial": True, "maturity": "core_partial" if core_ids else "source_candidates" if facts else "no_facts"}
    if internal_scope:
        summary.update(internal_projection_version=INTERNAL_PROJECTION_VERSION,
            internal_dependency_contract_validation=internal_scope['contract_validation'],
            internal_dependency_progress={o['metric_id']:o['status'] for o in internal_scope['outcomes']})
        if internal_core_ids and not core_ids:
            summary.update(result_status='needs_confirmation',maturity='internal_core_partial')
    return {"schema_version": STAGE_SCHEMA_VERSION, "summary": summary, "records": records,
            **({'internal_dependency_scope':internal_scope} if internal_scope else {}),
            "source_facts": {"schema_version": "source-facts-v1", "facts": list(facts.values()), "objects": sources,
                             "historical_facts": list(historical.values()),
                             "source_objects": [{"source_object_id": s.get("object_id"), "interpretation": s} for s in sources], "gaps": list(gaps.values())},
            "metric_source_mappings": {"schema_version": "metric-source-mappings-v1", "mappings": mappings,
                                      "core_bindings": [b for b in bindings.values() if b['core_record_id'] in core_ids]}}


def publish_stage(artifact_store, job_id, request=None, package=None, *, execution_status=None, error=None):
    snapshot = collect_stage(artifact_store, job_id, use_snapshot=False, request=request, package=package)
    if execution_status:
        snapshot["summary"]["execution_status"] = execution_status
        if execution_status not in {"queued", "running"}:
            snapshot["summary"]["unfinished_metric_ids"] = sorted(set(snapshot["summary"]["unfinished_metric_ids"]) | set(snapshot["summary"]["processing_metric_ids"]))
            snapshot["summary"]["counts"]["unfinished_metrics"] = len(snapshot["summary"]["unfinished_metric_ids"])
            snapshot["summary"]["processing_metric_ids"] = []
            snapshot["summary"]["counts"]["processing_metrics"] = 0
    if error and error not in snapshot["summary"]["errors"]:
        snapshot["summary"]["errors"].append(error)
    write_json(artifact_store.existing_job_dir(job_id) / "progress/snapshot.json", snapshot)
    return snapshot


def publish_stage_from_inputs(output_dir, job_id, status, error=None, *, export_bundle=False):
    """Worker/watchdog exit hook; call only after the owner stopped writing."""
    root = Path(output_dir)
    store = ArtifactStore(root.parent)
    agent_path = root / 'agent/state.json'
    if status not in {'queued','running'} and agent_path.is_file():
        agent = _read(agent_path)
        agent.update(state=status,terminal_reason=error)
        write_json(agent_path,agent)
    snapshot = publish_stage(store, job_id, execution_status=status, error=error)
    summary = snapshot["summary"]
    if export_bundle:
        from esg_targeted.results.terminal_bundle import materialize_terminal_bundle
        artifact = materialize_terminal_bundle(root, job_id, status, error, snapshot=snapshot)
        if artifact:
            summary['delivery_artifact'] = artifact
            # The mutable stage index is not the frozen artifact revision.
            write_json(root / 'progress/snapshot.json', snapshot)
    return snapshot
