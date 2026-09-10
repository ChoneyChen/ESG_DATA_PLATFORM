from __future__ import annotations

import argparse
import json

from esg_v2.config import get_settings, load_env_file
from esg_v2.contracts import OcrRunRequest, OptionalPayload
from esg_v2.document.contracts import DocumentIrBuildRequest
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
    ocr.add_argument(
        "--provider",
        choices=("local_first", "local_paddleocr", "paddle_api"),
        default=None,
        help="OCR execution provider (default: ESG_V2_OCR_PROVIDER or local_first)",
    )
    ocr.add_argument("--no-api-fallback", action="store_true")
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
    ir.add_argument(
        "--review-provider",
        choices=("qiniu", "local_nuextract"),
        default=None,
    )
    ir.add_argument("--review-target-id", action="append", default=[])

    args = parser.parse_args()
    load_env_file(args.env_file)

    if args.command == "ocr":
        settings = get_settings()
        request = OcrRunRequest(
            file_path=args.file_path,
            file_url=args.file_url,
            token=args.token,
            model=args.model,
            ocr_provider=args.provider or settings.default_ocr_provider,
            allow_api_fallback=not args.no_api_fallback,
            poll_interval_seconds=args.poll_interval,
            optional_payload=OptionalPayload(
                useDocOrientationClassify=args.use_doc_orientation_classify,
                useDocUnwarping=args.use_doc_unwarping,
                useChartRecognition=args.use_chart_recognition,
            ),
        )
        result = OcrWorkflow(settings).run(request, log=lambda msg: print(msg, flush=True))
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
            review_provider=args.review_provider or get_settings().default_review_provider,
            review_target_ids=args.review_target_id,
        )
        result = DocumentIrWorkflow(get_settings()).run(request, log=lambda msg: print(msg, flush=True))
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
