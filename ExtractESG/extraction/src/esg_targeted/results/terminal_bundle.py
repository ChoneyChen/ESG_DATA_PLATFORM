"""Model-free, immutable materialization of terminal durable fragments.

Source candidates and Core have independent quality contracts. Retaining a
SourceFact never supplies a metric mapping, missing identity, unit or approval.
Every export is a new, content-addressed artifact revision; old job artifacts
remain evidence of what the original writer actually produced.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from esg_standard_packages.contracts import CompiledStandardPackage
from esg_targeted.contracts import ExtractionRequest
from esg_targeted.io import write_json
from esg_targeted.results.validation import ResultContractError, ResultContractValidator
from esg_targeted.standards.runtime_snapshot import load_runtime_package

REVISION_SCHEMA = "terminal-result-revision-v1"
TERMINAL_STATUSES = {"completed", "partial", "finished", "failed", "cancelled", "interrupted"}
FIXED_INPUTS = ("input/request.json", "input/standard-package.json", "input/ir-manifest.json",
                "input/runtime-standard-snapshot.json", "input/evidence-transition.json")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def terminal_snapshot(snapshot, status, error=None):
    if status not in TERMINAL_STATUSES:
        raise ResultContractError("Materialization requires a terminal execution state")
    frozen = deepcopy(snapshot)
    summary = frozen["summary"]
    summary["execution_status"] = status
    summary["is_partial"] = True
    entered = set(summary.get("unfinished_metric_ids", [])) | set(summary.get("processing_metric_ids", []))
    summary["unfinished_metric_ids"] = sorted(entered)
    summary["processing_metric_ids"] = []
    summary["counts"]["unfinished_metrics"] = len(entered)
    summary["counts"]["processing_metrics"] = 0
    if error and error not in summary["errors"]:
        summary["errors"].append(error)
    return frozen


def _validate_sources(snapshot, request):
    """The neutral SQL archive's structural/provenance contract, not semantics."""
    document = snapshot["summary"].get("document", {})
    document_id, run_id, revision = (document.get(k) for k in ("document_id", "ir_run_id", "ir_revision"))
    if not document_id or not run_id or request.ir_run_id != run_id:
        raise ResultContractError("Partial Bundle requires a frozen document and IR identity")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ResultContractError("Partial Bundle requires a frozen IR revision")
    allowed = {run_id: revision}
    transition = snapshot.get("evidence_transition")
    if transition:
        allowed[transition["from_ir_run_id"]] = transition.get("from_ir_revision")
    seen = {}
    sources = [*snapshot["source_facts"].get("facts", []),
               *snapshot.get("internal_dependency_scope", {}).get("source_facts", [])]
    for fact in sources:
        ref = fact.get("source_fact_id")
        if not isinstance(ref, str) or not ref.strip() or fact.get("kind") not in {"quantitative", "assertion"}:
            raise ResultContractError("Invalid terminal SourceFact identity/kind")
        immutable = {k: v for k, v in fact.items() if k != "review_status"}
        if ref in seen and seen[ref] != immutable:
            raise ResultContractError(f"Conflicting terminal SourceFact {ref}")
        seen[ref] = immutable
        if fact.get("document_id") != document_id or fact.get("ir_run_id") not in allowed:
            raise ResultContractError("Partial Bundle source belongs to another frozen document")
        source_revision = fact.get("ir_revision")
        if not isinstance(source_revision, int) or isinstance(source_revision, bool) or source_revision < 1 \
                or allowed[fact["ir_run_id"]] is not None and source_revision != allowed[fact["ir_run_id"]]:
            raise ResultContractError("Partial Bundle source differs from its frozen IR revision")
        for key in ("source_row_label", "value_raw", "statement_raw", "unit_raw"):
            if fact.get(key) is not None and not isinstance(fact[key], str):
                raise ResultContractError(f"SourceFact {key} must preserve source text")
        if fact.get("identity_raw") is not None and not isinstance(fact["identity_raw"], dict):
            raise ResultContractError("SourceFact identity_raw must be an object")
        if fact.get("quality_status") not in {None, "grounded", "needs_confirmation", "unbound"}:
            raise ResultContractError("Unsupported terminal SourceFact quality")
        physical = fact.get("physical_sources", [])
        if not isinstance(physical, list):
            raise ResultContractError("SourceFact physical_sources must be an array")
        for source in physical:
            page = source.get("pdf_page_index") if isinstance(source, dict) else None
            if not isinstance(source, dict) or not source.get("ir_object_id") or not isinstance(page, int) \
                    or isinstance(page, bool) or page < 0:
                raise ResultContractError("Invalid terminal SourceFact physical reference")
            if any(source.get(key) is not None and source[key] != fact[key]
                   for key in ("document_id", "ir_run_id", "ir_revision")):
                raise ResultContractError("SourceFact physical reference differs from frozen identity")
    for evidence in [*snapshot["records"]["evidence_references"],
                     *snapshot.get("internal_dependency_scope", {}).get("evidence_references", [])]:
        if evidence.get("document_id") != document_id or evidence.get("ir_run_id") not in allowed:
            raise ResultContractError("Partial Bundle evidence belongs to another frozen document")
    return seen


def _inventory(root):
    return {p.relative_to(root).as_posix(): _sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _quality_counts(unique_sources):
    counts = {}
    for fact in unique_sources.values():
        quality = (fact.get('quality_status') or 'needs_confirmation') if fact.get('physical_sources') else 'unbound'
        counts[quality] = counts.get(quality, 0) + 1
    return counts


def _durable_inputs(root, request):
    """The files consumed by collect_stage(use_snapshot=False), plus lineage."""
    paths = {}
    for relative in (*FIXED_INPUTS, 'manifest.json', 'agent/state.json', 'agent/source-invalidations.json',
        'progress/metric-attempts.json', 'progress/core-postprocessing.json', 'derivations.json',
        'results/source-facts.json', 'results/metric-source-mappings.json', 'results/internal-dependencies.json'):
        path = root / relative
        if path.is_file():
            paths['job:'+relative] = {'role': 'frozen_input' if relative in FIXED_INPUTS else
                'parent_manifest' if relative == 'manifest.json' else 'durable_projection', 'sha256': _sha(path)}
    for prefix, pattern, role in (('checkpoints','*.json','validated_checkpoint'),
        ('agent/sources','*.json','source_interpretation'), ('agent/source-history','*.json','source_history'),
        ('disclosures/projections','*.json','core_binding_projection'), ('agent/mapping-fragments','**/*.json','mapping_fragment'),
        ('packets','*.regions.json','projection_freshness'), ('decisions','*.json','projection_freshness')):
        for path in sorted((root/prefix).glob(pattern)):
            if path.is_file():
                paths['job:'+path.relative_to(root).as_posix()] = {'role':role,'sha256':_sha(path)}
    if request.report_session_id:
        from esg_targeted.storage.artifacts import ArtifactStore
        shared = ArtifactStore(root.parent).report_session_path(request.report_session_id)
        for name, role in (('request.json','frozen_master_scope'), ('source-invalidations.json','shared_source_invalidation')):
            path = shared / name
            if path.is_file():
                paths['output:'+path.relative_to(root.parent).as_posix()] = {'role':role,'sha256':_sha(path)}
    return paths


def read_terminal_revision(job_root, revision_root):
    """Only a complete, unchanged revision under this original job is usable."""
    root, revision = Path(job_root).resolve(), Path(revision_root).resolve()
    revisions = root / "terminal-revisions"
    if revision.parent != revisions or not revision.name.startswith("partial-"):
        raise ResultContractError("Terminal revision escapes the original job")
    proof = json.loads((revision / "artifact-revision.json").read_text())
    if proof.get("schema_version") != REVISION_SCHEMA or proof.get("job_id") != root.name \
            or proof.get("revision_id") != revision.name:
        raise ResultContractError("Terminal revision identity mismatch")
    basis_sha = _sha(revision/'input/materialization-basis.json')
    if proof.get('materialization_digest') != basis_sha or revision.name != 'partial-'+basis_sha[:32]:
        raise ResultContractError('Terminal revision content-addressed identity mismatch')
    for relative, expected in proof["frozen_input_sha256"].items():
        if relative not in FIXED_INPUTS or _sha(root / relative) != expected:
            raise ResultContractError("Original frozen input changed after materialization")
    if master := proof.get('frozen_master_request'):
        path = (root.parent / master['relative_path']).resolve()
        if path.parent != root.parent/'.report-sessions'/master['session_id'] or _sha(path) != master['file_sha256']:
            raise ResultContractError('Original frozen master scope changed after materialization')
    for relative, expected in proof["artifact_sha256"].items():
        target = (revision / relative).resolve()
        if revision not in target.parents or _sha(target) != expected:
            raise ResultContractError("Terminal revision content changed")
    manifest = json.loads((revision / "manifest.json").read_text())
    if manifest.get("job_id") != root.name or manifest.get("artifact_revision", {}).get("revision_id") != revision.name \
            or manifest.get('artifact_revision', {}).get('materialization_digest') != basis_sha:
        raise ResultContractError("Terminal manifest does not identify its original job")
    return {"job_id": root.name, "artifact_revision_id": revision.name,
        "bundle_relative_path": revision.relative_to(root).as_posix(),
        "manifest_sha256": _sha(revision / "manifest.json"), "created_at": proof["created_at"],
        "source_fact_count": proof["source_fact_count"], "core_fact_count": proof["core_fact_count"],
        "internal_core_fact_count": proof.get('internal_core_fact_count', 0),
        "quality_counts": proof["quality_counts"], "status": "available", "publication_status": "not_published"}


def delivery_artifacts(job_root):
    root = Path(job_root).resolve()
    artifacts, blocked = [], []
    if (root / "manifest.json").is_file() and (root / "exports/extraction-result.json").is_file():
        try:
            manifest = json.loads((root / "manifest.json").read_text())
            sources_path = root / 'results/source-facts.json'
            sources = json.loads(sources_path.read_text()).get('facts', []) if sources_path.is_file() else []
            if manifest.get("job_id") == root.name and manifest.get("contract_validation", {}).get("status") == "passed":
                artifacts.append({"job_id": root.name, "artifact_revision_id": "original",
                "bundle_relative_path": ".", "manifest_sha256": _sha(root / "manifest.json"),
                "created_at": manifest.get("generated_at", ""),
                "source_fact_count": len({f['source_fact_id'] for f in sources}),
                "core_fact_count": sum(len([line for line in (root / 'results' / name).read_text().splitlines() if line.strip()])
                    for name in ('quantitative-observations.jsonl', 'qualitative-assertions.jsonl')),
                "internal_core_fact_count": manifest.get('counts',{}).get('internal_core_facts',0),
                "status": "available", "publication_status": "not_published"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            blocked.append({'job_id':root.name,'artifact_revision_id':'original','status':'blocked','reason':str(exc)})
    for revision in sorted((root / "terminal-revisions").glob("partial-*")):
        try:
            artifacts.append(read_terminal_revision(root, revision))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            blocked.append({"job_id": root.name, "artifact_revision_id": revision.name,
                "status": "blocked", "reason": str(exc)})
    return {"items": sorted(artifacts, key=lambda a: a["created_at"]), "blocked": blocked}


def saved_delivery_counts(output_root, batches):
    """Count unique immutable sources; shared sources are not multiplied by jobs."""
    sources, core, internal_core = set(), set(), set()
    for batch in batches:
        if not batch['items']:
            continue
        artifact = batch['items'][-1]
        root = Path(output_root) / artifact['job_id'] / artifact['bundle_relative_path']
        source_path = root / 'results/source-facts.json'
        facts = json.loads(source_path.read_text()).get('facts', []) if source_path.is_file() else []
        internal_path = root / 'results/internal-dependencies.json'
        if internal_path.is_file():
            internal = json.loads(internal_path.read_text())
            facts += internal.get('source_facts', [])
            for name, key in (('quantitative_observations','observation_id'), ('qualitative_assertions','assertion_id')):
                for row in internal.get('records',{}).get(name,[]):
                    internal_core.add((artifact['job_id'],row[key]))
        for fact in facts:
            sources.add(_digest({k:v for k,v in fact.items() if k!='review_status'}))
        for name, key in (('quantitative-observations.jsonl','observation_id'), ('qualitative-assertions.jsonl','assertion_id')):
            for line in (root/'results'/name).read_text().splitlines():
                if line.strip():
                    core.add((artifact['job_id'],json.loads(line)[key]))
    return {'saved_source_facts':len(sources),'saved_core_facts':len(core),
        'saved_internal_core_facts':len(internal_core),
        'available_bundle_count':sum(bool(batch['items']) for batch in batches),'published_observations':None}


def materialize_terminal_bundle(output_dir, job_id, status, error=None, *, snapshot=None,
                                reason="Retain terminal durable fragments", requested_by="terminal-finalizer"):
    from esg_targeted.results.stages import collect_stage
    from esg_targeted.results.exporter import ResultExporter
    from esg_targeted.storage.artifacts import ArtifactStore

    root = Path(output_dir).resolve()
    if root.name != job_id:
        raise ResultContractError("Materialization job/root identity mismatch")
    request = ExtractionRequest.model_validate(json.loads((root / "input/request.json").read_text()))
    durable_inputs = _durable_inputs(root, request)
    rebuilt = collect_stage(ArtifactStore(root.parent), job_id, use_snapshot=False)
    if snapshot is not None and any(snapshot.get(key) != rebuilt.get(key)
            for key in ('records','source_facts','metric_source_mappings','internal_dependency_scope')):
        raise ResultContractError('Terminal snapshot differs from current durable fragments')
    frozen = terminal_snapshot(rebuilt, status, error)
    package = CompiledStandardPackage.model_validate(json.loads((root / "input/standard-package.json").read_text()))
    package = load_runtime_package(package, artifact_root=root, request=request)
    core_count = frozen["summary"]["counts"]["core_facts"]
    internal_core_count = frozen['summary']['counts'].get('internal_core_facts',0)
    if not frozen["source_facts"].get("facts") and not core_count \
            and not frozen["summary"]["counts"].get("internal_core_facts", 0) \
            and not frozen.get("internal_dependency_scope", {}).get("source_facts"):
        return None
    transition_path = root / "input/evidence-transition.json"
    if transition_path.is_file():
        frozen["evidence_transition"] = json.loads(transition_path.read_text())
    ResultContractValidator(package.core).validate(frozen["records"])
    unique_sources = _validate_sources(frozen, request)
    source_count = len(unique_sources)
    if not source_count and not core_count and not frozen["summary"]["counts"].get("internal_core_facts", 0):
        return None
    inputs = {p: _sha(root / p) for p in FIXED_INPUTS if (root / p).is_file()}
    # Include the frozen master request. No output scope is narrowed to retained facts.
    master_scope = None
    master = None
    if request.report_session_id:
        master = ArtifactStore(root.parent).report_session_path(request.report_session_id) / "request.json"
        if not master.is_file():
            raise ResultContractError("Terminal report revision requires its frozen master scope")
        master_scope = json.loads(master.read_text())
    parent_sha = _sha(root / "manifest.json") if (root / "manifest.json").is_file() else None
    summary_identity = {k: v for k, v in frozen["summary"].items()
                        if k not in {"generated_at", "exports", "delivery_artifact", "artifact_revision"}}
    basis = {"snapshot": {**frozen, "summary": summary_identity}, "inputs": inputs, 'durable_inputs':durable_inputs,
             "master_scope": master_scope, "parent_manifest_sha256": parent_sha}
    basis_bytes = (json.dumps(basis,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    digest = hashlib.sha256(basis_bytes).hexdigest()
    revision_id = "partial-" + digest[:32]
    revisions = root / "terminal-revisions"
    target = revisions / revision_id
    if target.is_dir():
        return {**read_terminal_revision(root, target), "idempotent": True}
    revisions.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".staging-", dir=revisions))
    try:
        for relative in inputs:
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, destination)
        if master_scope is not None:
            shutil.copyfile(master, temporary / 'input/report-scope.json')
        (temporary/'input/materialization-basis.json').write_bytes(basis_bytes)
        created = datetime.now(UTC).isoformat()
        summary = frozen["summary"]
        summary["generated_at"] = created
        summary["artifact_revision"] = {"schema_version": REVISION_SCHEMA, "revision_id": revision_id,
            "source_job_id": job_id, "materialization_digest": digest,
            "parent_manifest_sha256": parent_sha, "source_only": not bool(core_count or internal_core_count),
            "frozen_report_scope_sha256": _digest(master_scope) if master_scope is not None else None}
        write_json(temporary / "results/source-facts.json", frozen["source_facts"])
        write_json(temporary / "results/metric-source-mappings.json", frozen["metric_source_mappings"])
        internal = frozen.get("internal_dependency_scope")
        if internal:
            write_json(temporary / "results/internal-dependencies.json", internal)
        summary["exports"] = ResultExporter().export(output_dir=temporary, records=frozen["records"],
            summary=summary, package=package, internal_dependency_scope=internal)
        write_json(temporary / "manifest.json", summary)
        qualities = _quality_counts(unique_sources)
        proof = {"schema_version": REVISION_SCHEMA, "revision_id": revision_id, "job_id": job_id,
            "created_at": created, "materialization_digest": digest, "reason": reason, "requested_by": requested_by,
            "terminal_status": status, "parent_manifest_sha256": parent_sha,
            "frozen_input_sha256": inputs, "artifact_sha256": _inventory(temporary),
            "durable_fragment_inputs": durable_inputs,
            "frozen_master_request": {'session_id':request.report_session_id,
                'relative_path':master.relative_to(root.parent).as_posix(),'file_sha256':_sha(master),
                'canonical_sha256':_digest(master_scope)} if master is not None else None,
            "source_fact_count": source_count, "core_fact_count": core_count, "quality_counts": qualities,
            "internal_core_fact_count": internal_core_count,
            "source_semantics_reinterpreted": False, "identity_or_unit_inferred": False,
            "approval_or_publication_performed": False}
        write_json(temporary / "artifact-revision.json", proof)
        # Detect mutation by another producer, even if this caller was misconfigured.
        if _durable_inputs(root, request) != durable_inputs:
            raise ResultContractError("Durable fragments changed during terminal materialization")
        try:
            os.rename(temporary, target)
        except OSError:
            if not target.is_dir():
                raise
            return {**read_terminal_revision(root, target), "idempotent": True}
        return {**read_terminal_revision(root, target), "idempotent": False}
    finally:
        if temporary.is_dir():
            shutil.rmtree(temporary)
