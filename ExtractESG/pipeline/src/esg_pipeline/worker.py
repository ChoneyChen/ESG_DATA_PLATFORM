from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from esg_document.config import get_settings
from esg_contracts.document import OcrRunRequest
from esg_contracts.worker import encode_event
from esg_document.document.contracts import (
    DocumentIrBuildRequest,
    DocumentIrRepairRequest,
    ReviewRetryRequest,
)
from esg_document.document.revision_service import DocumentIrRevisionService
from esg_document.runtime_telemetry import RunTelemetryTracker
from esg_document.workflows.ir_workflow import DocumentIrWorkflow
from esg_document.workflows.ocr_workflow import OcrWorkflow


def emit(kind: str, **payload: Any) -> None:
    print(encode_event(kind, **payload), flush=True)


def _run_worker() -> int:
    parser = argparse.ArgumentParser(description="Isolated ESG pipeline worker")
    parser.add_argument("--operation", required=True)
    parser.add_argument("--native-job-id", required=True)
    parser.add_argument("--request-file", required=True)
    args = parser.parse_args()
    from esg_pipeline.execution_slot import inherited_slot_is_valid

    if not inherited_slot_is_valid():
        raise RuntimeError(
            "Worker requires the unified queue's inherited execution slot"
        )
    payload = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    settings = get_settings()

    def log(message: str) -> None:
        emit("log", stage="running", message=message)

    if args.operation == "ocr":
        if payload.pop("_requires_source_url", False):
            source_url = os.getenv("ESG_OCR_SOURCE_URL")
            if not source_url:
                raise RuntimeError(
                    "Signed source URL was unavailable after restart; resubmit the source"
                )
            payload["file_url"] = source_url
        request = OcrRunRequest.model_validate(payload)

        def publish_ocr_progress(snapshot: dict[str, Any]) -> None:
            current = int(snapshot.get("extractedPages") or 0)
            total = int(snapshot.get("totalPages") or 0)
            state = str(snapshot.get("state") or "running")
            message = str(
                snapshot.get("message")
                or f"OCR {state}: {current}/{total or '?'} pages"
            )
            emit(
                "telemetry",
                stage=f"ocr_{state}",
                message=message,
                progress_current=current,
                progress_total=total,
                detail=snapshot,
            )

        result = OcrWorkflow(settings).run(
            request,
            run_id=args.native_job_id,
            log=log,
            progress=publish_ocr_progress,
        )
        emit("result", payload=result.model_dump(mode="json"))
        return 0

    def publish_telemetry(snapshot: dict[str, Any]) -> None:
        current = snapshot.get("current_stage")
        current = current if isinstance(current, dict) else {}
        emit(
            "telemetry",
            stage=str(current.get("name") or "document_ir"),
            message=str(current.get("name") or "Document IR running"),
            progress_current=int(current.get("index") or 0),
            progress_total=int(current.get("total") or 0),
            detail=snapshot,
        )

    tracker = RunTelemetryTracker(publish_telemetry)
    if args.operation == "document_ir_build":
        request = DocumentIrBuildRequest.model_validate(payload)
        result = DocumentIrWorkflow(settings).run(
            request,
            run_id=args.native_job_id,
            log=log,
            telemetry=tracker.event,
        )
        telemetry = tracker.finish("done")
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        emit(
            "result",
            payload={
                **result.model_dump(mode="json"),
                "manifest": manifest,
                "status": "completed"
                if manifest.get("can_build_evidence")
                else "partial",
                "telemetry": telemetry,
            },
        )
        return 0

    service = DocumentIrRevisionService(settings)
    if args.operation == "document_ir_repair":
        request = DocumentIrRepairRequest.model_validate(payload)
        manifest = service.repair(
            request,
            run_id=args.native_job_id,
            log=log,
            telemetry=tracker.event,
        )
    elif args.operation == "document_ir_review_retry":
        parent_run_id = str(payload.pop("parent_run_id"))
        request = ReviewRetryRequest.model_validate(payload)
        manifest = service.resume_reviews(
            parent_run_id,
            request,
            run_id=args.native_job_id,
            log=log,
            telemetry=tracker.event,
        )
    else:
        raise ValueError(f"Unsupported worker operation: {args.operation}")
    retention = manifest.get("retention") if isinstance(manifest, dict) else None
    if isinstance(retention, dict):
        pruned_count = len(retention.get("pruned_run_ids") or [])
        protected_count = len(retention.get("protected_run_ids") or [])
        if pruned_count:
            log(
                f"Best-only IR retention kept {retention.get('retained_run_id')} and "
                f"removed {pruned_count} superseded revision(s)"
            )
        elif protected_count:
            log(
                f"Best-only IR retention promoted {retention.get('retained_run_id')}; "
                f"cleanup of {protected_count} active reference(s) is deferred"
            )
    telemetry = tracker.finish("done")
    emit(
        "result",
        payload={
            "manifest": manifest,
            "telemetry": telemetry,
            "status": "completed" if manifest.get("can_build_evidence") else "partial",
        },
    )
    return 0


def main() -> int:
    # Share the same owner-scoped assertion as extraction projection workers.
    from esg_runtime.power import idle_sleep_assertion

    with idle_sleep_assertion(
        lambda detail: emit(
            "log",
            stage="resource_lifecycle",
            message=str(detail["kind"]),
            detail=detail,
        )
    ):
        return _run_worker()


if __name__ == "__main__":
    raise SystemExit(main())
