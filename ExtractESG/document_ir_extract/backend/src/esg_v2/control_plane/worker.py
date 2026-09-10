from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from esg_v2.config import get_settings
from esg_v2.contracts import OcrRunRequest
from esg_v2.document.contracts import (
    DocumentIrBuildRequest,
    DocumentIrRepairRequest,
    ReviewRetryRequest,
)
from esg_v2.document.revision_service import DocumentIrRevisionService
from esg_v2.runtime_telemetry import RunTelemetryTracker
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
from esg_v2.workflows.ocr_workflow import OcrWorkflow


EVENT_PREFIX = "ESG_PIPELINE_EVENT "


def emit(kind: str, **payload: Any) -> None:
    print(
        EVENT_PREFIX
        + json.dumps(
            {"kind": kind, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated ESG pipeline worker")
    parser.add_argument("--operation", required=True)
    parser.add_argument("--native-job-id", required=True)
    parser.add_argument("--request-file", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    settings = get_settings()

    def log(message: str) -> None:
        emit("log", stage="running", message=message)

    if args.operation == "ocr":
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
        emit("result", payload={**result.model_dump(mode="json"), "telemetry": telemetry})
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
    emit("result", payload={"manifest": manifest, "telemetry": telemetry})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
