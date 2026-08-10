from __future__ import annotations

import argparse
import json
from pathlib import Path

from esg_v2.config import get_settings, load_env_file
from esg_v2.contracts import OcrRunRequest, OptionalPayload
from esg_v2.document.contracts import DocumentIrBuildRequest
from esg_v2.evidence.builder import EvidenceInventoryBuilder
from esg_v2.evidence.contracts import EvidenceBuildRequest
from esg_v2.targeted.contracts import TargetedRunRequest
from esg_v2.targeted.workflow import TargetedRecallWorkflow
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
from esg_v2.workflows.ocr_workflow import OcrWorkflow


def main() -> int:
    parser = argparse.ArgumentParser(prog="esg-v2")
    parser.add_argument("--env-file", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    ocr = sub.add_parser("ocr", help="Run PaddleOCR-VL OCR on a PDF path or URL")
    source = ocr.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", dest="file_path")
    source.add_argument("--url", dest="file_url")
    ocr.add_argument("--token", default=None)
    ocr.add_argument("--model", default="PaddleOCR-VL-1.6")
    ocr.add_argument("--poll-interval", type=float, default=5)
    ocr.add_argument("--use-doc-orientation-classify", action="store_true")
    ocr.add_argument("--use-doc-unwarping", action="store_true")
    ocr.add_argument("--use-chart-recognition", action="store_true")

    ir = sub.add_parser("ir", help="Build Document IR from an OCR run")
    ir.add_argument("--ocr-run-id", required=True)
    ir.add_argument("--pdf", dest="pdf_path", default=None)
    ir.add_argument("--run-id", default=None)
    ir.add_argument("--parent-ir-run-id", default=None)
    ir.add_argument("--render-dpi", type=int, default=144)
    ir.add_argument("--execute-vlm-reviews", action="store_true")
    ir.add_argument("--review-target-id", action="append", default=[])

    evidence = sub.add_parser("evidence", help="Build deterministic Evidence Inventory packages")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_build = evidence_sub.add_parser("build")
    evidence_build.add_argument("--ir-run-id", required=True)
    evidence_build.add_argument("--run-id", default=None)

    targeted = sub.add_parser("targeted", help="Run local-first standard-task extraction")
    targeted_sub = targeted.add_subparsers(dest="targeted_command", required=True)
    targeted_plan = targeted_sub.add_parser("plan")
    targeted_plan.add_argument("--template", required=True)
    targeted_run = targeted_sub.add_parser("run")
    targeted_run.add_argument("--ir-run-id", required=True)
    targeted_run.add_argument("--template", required=True)
    targeted_run.add_argument("--evidence-run-id", default=None)
    targeted_run.add_argument("--run-id", default=None)
    targeted_run.add_argument("--mode", choices=["local_strict", "local_semantic"], default="local_strict")
    targeted_run.add_argument("--top-k", type=int, default=30)
    targeted_run.add_argument("--local-embedding-model", default="intfloat/multilingual-e5-small")
    targeted_run.add_argument("--allow-local-model-download", action="store_true")


    args = parser.parse_args()
    load_env_file(args.env_file)

    if args.command == "ocr":
        request = OcrRunRequest(
            file_path=args.file_path,
            file_url=args.file_url,
            token=args.token,
            model=args.model,
            poll_interval_seconds=args.poll_interval,
            optional_payload=OptionalPayload(
                useDocOrientationClassify=args.use_doc_orientation_classify,
                useDocUnwarping=args.use_doc_unwarping,
                useChartRecognition=args.use_chart_recognition,
            ),
        )
        result = OcrWorkflow(get_settings()).run(request, log=lambda msg: print(msg, flush=True))
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.command == "ir":
        request = DocumentIrBuildRequest(
            ocr_run_id=args.ocr_run_id,
            pdf_path=args.pdf_path,
            run_id=args.run_id,
            parent_ir_run_id=args.parent_ir_run_id,
            render_dpi=args.render_dpi,
            execute_vlm_reviews=args.execute_vlm_reviews,
            review_target_ids=args.review_target_id,
        )
        result = DocumentIrWorkflow(get_settings()).run(request, log=lambda msg: print(msg, flush=True))
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.command == "evidence" and args.evidence_command == "build":
        settings = get_settings()
        result = EvidenceInventoryBuilder(
            settings.document_ir_output_root,
            settings.evidence_output_root,
        ).build(
            EvidenceBuildRequest(ir_run_id=args.ir_run_id, run_id=args.run_id),
            log=lambda msg: print(msg, flush=True),
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.command == "targeted":
        settings = get_settings()
        workflow = TargetedRecallWorkflow(
            document_ir_root=settings.document_ir_output_root,
            evidence_output_root=settings.evidence_output_root,
            targeted_output_root=settings.targeted_output_root,
        )
        if args.targeted_command == "plan":
            print(json.dumps(workflow.plan(Path(args.template)), ensure_ascii=False, indent=2))
            return 0
        if args.targeted_command == "run":
            result = workflow.run(
                TargetedRunRequest(
                    ir_run_id=args.ir_run_id,
                    template_path=Path(args.template),
                    evidence_run_id=args.evidence_run_id,
                    run_id=args.run_id,
                    mode=args.mode,
                    top_k=args.top_k,
                    local_embedding_model=args.local_embedding_model,
                    allow_local_model_download=args.allow_local_model_download,
                ),
                log=lambda msg: print(msg, flush=True),
            )
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
