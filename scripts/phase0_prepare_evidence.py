"""Rebuild a small, isolated evidence set from explicitly selected real PDF pages.

No queue, service, cloud OCR or production artifact root is used. Input and
output roots are injected so evidence can be replaced independently.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", type=int, nargs="+", required=True, help="one-based PDF pages")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pdf = args.pdf.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if output == pdf.parent or output in pdf.parents:
        raise SystemExit("Use a separate evidence output root, never the report asset directory")
    if any(page < 1 for page in args.pages):
        raise SystemExit("Pages are one-based positive integers")
    os.environ["ESG_V2_OCR_OUTPUT_DIR"] = str(output / "ocr")
    os.environ["ESG_V2_DOCUMENT_IR_OUTPUT_DIR"] = str(output / "ir")
    os.environ["ESG_ALLOW_CLOUD_OCR"] = "false"
    from esg_v2.config import get_settings
    from esg_v2.contracts import OcrRunRequest
    from esg_v2.document.contracts import DocumentIrBuildRequest
    from esg_v2.workflows.ir_workflow import DocumentIrWorkflow
    from esg_v2.workflows.ocr_workflow import OcrWorkflow

    started = time.perf_counter()
    ocr = OcrWorkflow(get_settings()).run(OcrRunRequest(
        file_path=str(pdf), ocr_provider="local_paddleocr", allow_api_fallback=False,
        page_indices=[page - 1 for page in args.pages]),
        log=lambda message: print(f"[{time.perf_counter() - started:.1f}s] {message}", flush=True))
    ir = DocumentIrWorkflow(get_settings()).run(DocumentIrBuildRequest(
        ocr_run_id=ocr.run_id, pdf_path=str(pdf), document_label=pdf.name,
        execute_vlm_reviews=False, execute_required_reviews=False))
    result = {"pdf": pdf.name, "ocr_run_id": ocr.run_id, "ir_run_id": ir.run_id,
              "ir_root": str(output / "ir" / ir.run_id),
              "wall_seconds": round(time.perf_counter() - started, 3),
              "cloud_ocr": False, "vlm_review": False}
    (output / "evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
