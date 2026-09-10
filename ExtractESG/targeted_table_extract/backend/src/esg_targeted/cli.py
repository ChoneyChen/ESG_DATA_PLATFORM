from __future__ import annotations

import argparse
import json
import time
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

    run = subparsers.add_parser("run", help="Run one extraction synchronously")
    run.add_argument("--job-id", default=None, help="Preallocated control-plane job ID")
    run.add_argument("--ir-run-id", required=True)
    run.add_argument("--package-id", required=True)
    run.add_argument("--package-version", required=True)
    run.add_argument("--standard-snapshot", type=Path, default=None)
    run.add_argument("--metric", action="append", default=[])
    run.add_argument("--no-semantic-search", action="store_true")
    run.add_argument("--retrieval-only", action="store_true")
    run.add_argument(
        "--semantic-provider",
        choices=("local_nuextract", "qiniu_vlm"),
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
    run.add_argument("--force-unready-ir", action="store_true")

    resume = subparsers.add_parser("resume", help="Resume one extraction synchronously")
    resume.add_argument("--job-id", required=True)

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

    if args.command == "resume":
        job_store = JobStore(settings.state_db)
        artifact_store = ArtifactStore(settings.output_root)
        job_store.prepare_resume(args.job_id)
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
        semantic_search=not args.no_semantic_search,
        semantic_fill=not args.retrieval_only,
        semantic_provider=args.semantic_provider,
        semantic_model=args.semantic_model,
        retrieval_object_top_n=args.retrieval_object_top_n,
        visual_fallback=not args.no_visual_fallback,
        force_unready_ir=args.force_unready_ir,
    )
    job_id = args.job_id or new_job_id()
    catalog = StandardPackageCatalog(settings.standard_dist_root)
    package = (catalog.compiler.validate_compiled(args.standard_snapshot) if args.standard_snapshot
               else catalog.load(request.package_id, request.package_version))
    if (package.manifest.package_id, package.manifest.package_version) != (request.package_id, request.package_version):
        raise ValueError("Queued standard snapshot does not match the requested package")
    job_store.create(job_id, request, str(artifact_store.job_dir(job_id)))
    artifact_store.write_json(job_id, "input/standard-package.json", package.model_dump(mode="json"))
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
