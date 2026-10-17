from __future__ import annotations

import argparse
import json
from pathlib import Path

from esg_targeted.config import settings
from esg_targeted.contracts import ExtractionRequest, JobStatus
from esg_targeted.ids import new_job_id
from esg_targeted.ir.catalog import DocumentIrCatalog
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore
from esg_targeted.workflow import TargetedExtractionWorkflow


PIPELINE_EVENT_PREFIX = "ESG_PIPELINE_EVENT "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ExtractESG targeted table extraction")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("catalog", help="List Document IR runs and standard packages")

    run = subparsers.add_parser("run", help="Submit one extraction to the unified queue")
    run.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--backend-url", default=None)
    run.add_argument("--job-id", default=None, help="Preallocated control-plane job ID")
    run.add_argument("--ir-run-id", required=True)
    run.add_argument("--package-id", required=True)
    run.add_argument("--package-version", required=True)
    run.add_argument("--standard-snapshot", type=Path, default=None)
    run.add_argument('--extension-selections', type=Path, default=None,
                     help='JSON array of explicit approved extension selections; frozen before enqueue')
    run.add_argument("--metric", action="append", default=[])
    run.add_argument("--no-semantic-search", action="store_true")
    run.add_argument("--retrieval-only", action="store_true")
    run.add_argument(
        "--semantic-provider",
        choices=("local_nuextract", "local_qwen", "qiniu_vlm"),
        default="local_nuextract",
    )
    run.add_argument("--semantic-model", default=None)
    run.add_argument(
        "--retrieval-object-top-n",
        type=int,
        choices=range(1, 6),
        default=settings.retrieval_object_top_n,
        metavar="1-5",
    )
    run.add_argument("--no-visual-fallback", action="store_true")
    run.add_argument("--extraction-mode", choices=("auto", "per_metric"), default="auto")
    run.add_argument("--shared-object-budget", type=int, choices=range(1, 9), default=3)
    run.add_argument("--agent-max-steps", type=int, choices=range(1, 25), default=8)
    run.add_argument("--force-unready-ir", action="store_true")

    resume = subparsers.add_parser("resume", help="Resume through the unified queue")
    resume.add_argument("--job-id", required=True)
    resume.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    resume.add_argument("--backend-url", default=None)

    existing = subparsers.add_parser("existing", help="Submit an existing immutable extraction")
    existing.add_argument("--job-id", required=True)
    existing.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    existing.add_argument("--backend-url", default=None)

    report = subparsers.add_parser("report-session", help="Run or resume the immutable projections of one report session")
    report.add_argument("--session-id", required=True)
    report.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    report.add_argument("--backend-url", default=None)

    serve = subparsers.add_parser("serve", help="Start the backend API")
    serve.add_argument("--host", default=settings.backend_host)
    serve.add_argument("--port", type=int, default=settings.backend_port)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings.ensure_runtime_dirs()
    if args.command == "catalog":
        payload = {
            "ir_runs": [
                item.model_dump(mode="json")
                for item in DocumentIrCatalog(settings.ir_roots).list()
            ],
            "standard_packages": [
                item.model_dump(mode="json")
                for item in StandardPackageCatalog(settings.standard_dist_root).list()
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run("esg_targeted.api.main:app", host=args.host, port=args.port)
        return 0

    if getattr(args, "worker", False):
        from esg_targeted.storage.execution_slot import inherited_slot_is_valid
        if not inherited_slot_is_valid():
            raise RuntimeError("Worker requires the unified queue's inherited execution slot")

    if args.command == "report-session":
        from esg_targeted.report_session import ReportSessionService
        from esg_targeted.queue_client import enqueue_existing
        service = ReportSessionService(settings)
        if args.worker:
            service.run(args.session_id)
        else:
            current = service.detail(args.session_id)
            if current["execution_status"] != "queued":
                service.resume(args.session_id)
            print(json.dumps(enqueue_existing("report-session", args.session_id, backend_url=args.backend_url), ensure_ascii=False, indent=2))
            return 0
        record = service.detail(args.session_id)
        print(PIPELINE_EVENT_PREFIX + json.dumps({"kind": "result", "payload": record}, ensure_ascii=False, default=str), flush=True)
        return 0 if record["execution_status"] == "finished" else 1

    if args.command in {"resume", "existing"}:
        job_store = JobStore(settings.state_db)
        artifact_store = ArtifactStore(settings.output_root)
        current = job_store.get(args.job_id)
        if current.status != JobStatus.QUEUED:
            job_store.prepare_resume(args.job_id)
        if not args.worker:
            from esg_targeted.queue_client import enqueue_existing
            print(json.dumps(enqueue_existing("targeted-extraction", args.job_id, backend_url=args.backend_url), ensure_ascii=False, indent=2))
            return 0
        TargetedExtractionWorkflow(settings, job_store, artifact_store).run(args.job_id)
        record = job_store.get(args.job_id)
        print(
            PIPELINE_EVENT_PREFIX
            + json.dumps(
                {"kind": "result", "payload": record.model_dump(mode="json")},
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
            flush=True,
        )
        print(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
        return 0 if record.status in {JobStatus.COMPLETED, JobStatus.PARTIAL} else 1

    job_store = JobStore(settings.state_db)
    artifact_store = ArtifactStore(settings.output_root)
    request = ExtractionRequest(
        ir_run_id=args.ir_run_id,
        package_id=args.package_id,
        package_version=args.package_version,
        metric_ids=args.metric,
        extension_selections=json.loads(args.extension_selections.read_text()) if args.extension_selections else [],
        semantic_search=not args.no_semantic_search,
        semantic_fill=not args.retrieval_only,
        semantic_provider=args.semantic_provider,
        semantic_model=args.semantic_model,
        retrieval_object_top_n=args.retrieval_object_top_n,
        extraction_mode=args.extraction_mode,
        shared_object_budget=args.shared_object_budget,
        agent_max_steps=args.agent_max_steps,
        visual_fallback=not args.no_visual_fallback,
        force_unready_ir=args.force_unready_ir,
    )
    job_id = args.job_id or new_job_id()
    catalog = StandardPackageCatalog(settings.standard_dist_root)
    package = (catalog.compiler.validate_compiled(args.standard_snapshot) if args.standard_snapshot
               else catalog.load(request.package_id, request.package_version))
    if (package.manifest.package_id, package.manifest.package_version) != (request.package_id, request.package_version):
        raise ValueError("Queued standard snapshot does not match the requested package")
    from esg_targeted.standards.runtime_snapshot import freeze_extraction_request
    request, runtime_snapshot = freeze_extraction_request(package, request, settings.standard_dist_root.parent)
    job_store.create(job_id, request, str(artifact_store.job_dir(job_id)))
    artifact_store.write_json(job_id, "input/standard-package.json", package.model_dump(mode="json"))
    if runtime_snapshot is not None:
        artifact_store.write_json(job_id, 'input/runtime-standard-snapshot.json', runtime_snapshot.model_dump(mode='json'))
    artifact_store.write_json(job_id, "input/request.json", request.model_dump(mode="json"))
    if not args.worker:
        from esg_targeted.queue_client import enqueue_existing
        print(json.dumps(enqueue_existing("targeted-extraction", job_id, backend_url=args.backend_url), ensure_ascii=False, indent=2))
        return 0
    TargetedExtractionWorkflow(settings, job_store, artifact_store).run(job_id)
    record = job_store.get(job_id)
    print(
        PIPELINE_EVENT_PREFIX
        + json.dumps(
            {"kind": "result", "payload": record.model_dump(mode="json")},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )
    print(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
    return 0 if record.status in {JobStatus.COMPLETED, JobStatus.PARTIAL} else 1


if __name__ == "__main__":
    raise SystemExit(main())
