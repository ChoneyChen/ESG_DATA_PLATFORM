from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from esg_v2.config import Settings
from esg_v2.contracts import OcrRunRequest, OcrRunResult
from esg_v2.ocr.client import PaddleOcrVlClient
from esg_v2.ocr.output_writer import OcrOutputWriter
from esg_v2.storage.package_layout import create_package_root, new_run_id, package_dir, require_run_id
from esg_v2.utils.hashing import sha256_file
from esg_v2.utils.sanitization import portable_input_reference, sanitize_remote_url


LogFn = Callable[[str], None]


class OcrWorkflow:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, request: OcrRunRequest, *, run_id: str | None = None, log: LogFn | None = None) -> OcrRunResult:
        token = request.token or self.settings.paddle_token
        if not token:
            raise ValueError("Missing PaddleOCR-VL API token. Provide token in request or PADDLEOCR_VL_API_TOKEN.")

        run_id = run_id or self._new_run_id()
        require_run_id(run_id, "ocr")
        output_dir = package_dir(self.settings.output_root, run_id)
        create_package_root(output_dir)
        writer = OcrOutputWriter(output_dir)
        client = PaddleOcrVlClient(
            job_url=self.settings.paddle_job_url,
            token=token,
            timeout_seconds=self.settings.request_timeout_seconds,
        )

        source_summary = self._source_summary(request)
        writer.write_request(self._redacted_request(request, run_id))
        self._log(log, f"Submitting OCR job for {source_summary}")

        submit_response = client.submit(
            model=request.model,
            optional_payload=request.optional_payload,
            file_path=request.file_path,
            file_url=request.file_url,
        )
        submit_response_path = writer.write_submit_response(submit_response)
        job_id = submit_response["data"]["jobId"]
        self._log(log, f"Job submitted: {job_id}")

        result_json_url = ""
        while True:
            job_result = client.get_job(job_id)
            writer.append_poll_event(job_result)
            data = job_result.get("data") or {}
            state = data.get("state")
            if state in {"pending", "running"}:
                progress = data.get("extractProgress") or {}
                total = progress.get("totalPages")
                extracted = progress.get("extractedPages")
                if total is not None and extracted is not None:
                    self._log(log, f"OCR {state}: {extracted}/{total} pages")
                else:
                    self._log(log, f"OCR {state}")
                time.sleep(request.poll_interval_seconds)
                continue
            if state == "done":
                result_json_url = data["resultUrl"]["jsonUrl"]
                progress = data.get("extractProgress") or {}
                self._log(log, f"OCR done: {progress.get('extractedPages', 'unknown')} pages")
                break
            if state == "failed":
                raise RuntimeError(data.get("errorMsg") or "PaddleOCR-VL job failed")
            raise RuntimeError(f"Unexpected PaddleOCR-VL job state: {state}")

        jsonl_text = client.download_text(result_json_url)
        raw_jsonl_path = writer.save_result_jsonl(jsonl_text)
        markdown_files, downloaded_files, artifact_rows, page_count = writer.extract_artifacts_from_jsonl(
            jsonl_text=jsonl_text,
            client=client,
        )

        source = portable_input_reference(
            run_id=run_id,
            file_path=request.file_path,
            file_url=request.file_url,
        )
        source["sha256"] = sha256_file(request.file_path) if request.file_path else None
        manifest = {
            "run_id": run_id,
            "job_id": job_id,
            "model": request.model,
            "source": source,
            "optional_payload": request.optional_payload.model_dump(),
            "page_count": page_count,
            "artifact_count": len(artifact_rows),
        }
        manifest_path = writer.write_manifest(manifest)

        return OcrRunResult(
            run_id=run_id,
            job_id=job_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            raw_jsonl_path=raw_jsonl_path,
            page_markdown_files=markdown_files,
            downloaded_files=downloaded_files,
            page_count=page_count,
            result_json_url=sanitize_remote_url(result_json_url),
            poll_history_path=writer.layout.poll_events,
            submit_response_path=submit_response_path,
        )

    @staticmethod
    def _new_run_id() -> str:
        return new_run_id("ocr")

    @staticmethod
    def _source_summary(request: OcrRunRequest) -> str:
        if request.file_url:
            return sanitize_remote_url(request.file_url)
        return Path(request.file_path).name if request.file_path else "unknown input"

    @staticmethod
    def _redacted_request(request: OcrRunRequest, run_id: str) -> dict[str, object]:
        data = request.model_dump()
        if data.get("token"):
            data["token"] = "***redacted***"
        source = portable_input_reference(
            run_id=run_id,
            file_path=request.file_path,
            file_url=request.file_url,
        )
        data["file_path"] = source["request_reference"] if request.file_path else None
        data["file_url"] = source["request_reference"] if request.file_url else None
        return data

    @staticmethod
    def _log(log: LogFn | None, message: str) -> None:
        if log:
            log(message)
