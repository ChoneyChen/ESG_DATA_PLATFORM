from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from esg_v2.config import Settings
from esg_v2.contracts import OcrRunRequest, OcrRunResult
from esg_v2.ocr.output_writer import OcrOutputWriter
from esg_v2.ocr.pdf_preflight import PdfCanvasPreflight, PreparedPdfInput
from esg_v2.ocr.providers import OcrProviderRouter
from esg_v2.storage.package_layout import create_package_root, new_run_id, package_dir, require_run_id
from esg_v2.utils.hashing import sha256_file
from esg_v2.utils.sanitization import portable_input_reference, sanitize_remote_url


LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]


class OcrWorkflow:
    def __init__(self, settings: Settings, *, provider_router: OcrProviderRouter | None = None):
        self.settings = settings
        self.provider_router = provider_router or OcrProviderRouter(settings)

    def run(
        self,
        request: OcrRunRequest,
        *,
        run_id: str | None = None,
        log: LogFn | None = None,
        progress: ProgressFn | None = None,
    ) -> OcrRunResult:
        run_id = run_id or self._new_run_id()
        require_run_id(run_id, "ocr")
        output_dir = package_dir(self.settings.output_root, run_id)
        create_package_root(output_dir)
        writer = OcrOutputWriter(output_dir)

        source_summary = self._source_summary(request)
        prepared = self._prepare_provider_input(request, writer, log)
        expected_pages = int(((prepared.report.get("source") or {}).get("page_count") or 0)) if prepared else 0
        writer.write_request(self._redacted_request(request, run_id))
        self._log(log, f"Routing OCR for {source_summary}: requested={request.ocr_provider}")
        selection = self.provider_router.execute(
            request,
            input_path=prepared.provider_path if prepared else (Path(request.file_path) if request.file_path else None),
            provider_root=writer.raw_dir,
            run_id=run_id,
            expected_page_count=expected_pages,
            progress_callback=progress,
            log=log,
        )
        execution = selection.execution
        submit_response_path = writer.write_submit_response(execution.submit_response)
        for event in execution.poll_events:
            writer.append_poll_event(event)
        if not execution.poll_events:
            writer.append_poll_event({"provider": execution.provider, "data": {"state": "done"}})
        jsonl_text = execution.jsonl_text
        raw_jsonl_path = writer.save_result_jsonl(jsonl_text)
        markdown_files, downloaded_files, artifact_rows, page_count = writer.extract_artifacts_from_jsonl(
            jsonl_text=jsonl_text,
            resource_reader=execution.resource_reader,
        )
        if prepared:
            if page_count != expected_pages:
                raise RuntimeError(
                    f"{execution.provider} returned incomplete page coverage after canvas normalization: "
                    f"{page_count}/{expected_pages} pages"
                )

        source = portable_input_reference(
            run_id=run_id,
            file_path=request.file_path,
            file_url=request.file_url,
        )
        source["sha256"] = prepared.source_sha256 if prepared else (sha256_file(request.file_path) if request.file_path else None)
        provider_input = prepared.report.get("provider_input") if prepared else {
            "reference": "remote-provider-fetch",
            "sha256": None,
            "transformed": False,
        }
        manifest = {
            "run_id": run_id,
            "job_id": execution.job_id,
            "model": execution.metadata.get("model", request.model),
            "ocr_provider": execution.provider,
            "provider_route": {
                "requested": selection.requested_provider,
                "requested_model": request.model,
                "selected": execution.provider,
                "allow_api_fallback": request.allow_api_fallback,
                "fallback_reason": selection.fallback_reason,
                "attempts": selection.attempts,
            },
            "provider_metadata": execution.metadata,
            "source": source,
            "pdf_preflight": self._preflight_summary(prepared),
            "provider_input": provider_input,
            "optional_payload": request.optional_payload.model_dump(),
            "page_count": page_count,
            "artifact_count": len(artifact_rows),
        }
        manifest_path = writer.write_manifest(manifest)

        return OcrRunResult(
            run_id=run_id,
            provider=execution.provider,
            job_id=execution.job_id,
            output_dir=output_dir,
            manifest_path=manifest_path,
            raw_jsonl_path=raw_jsonl_path,
            page_markdown_files=markdown_files,
            downloaded_files=downloaded_files,
            page_count=page_count,
            result_json_url=sanitize_remote_url(execution.result_json_url) if execution.result_json_url else None,
            poll_history_path=writer.layout.poll_events,
            submit_response_path=submit_response_path,
        )

    def _prepare_provider_input(
        self,
        request: OcrRunRequest,
        writer: OcrOutputWriter,
        log: LogFn | None,
    ) -> PreparedPdfInput | None:
        if not request.file_path:
            writer.write_preflight(
                {
                    "schema_version": "ocr-pdf-canvas-preflight-v1",
                    "status": "remote_unavailable",
                    "strategy": "remote_provider_fetch",
                    "reasons": ["source_bytes_not_local"],
                    "provider_input": {"reference": "remote-provider-fetch", "transformed": False},
                    "pages": [],
                }
            )
            self._log(log, "PDF canvas preflight skipped: remote URL source is not available locally")
            return None
        prepared = PdfCanvasPreflight(
            max_canvas_points=self.settings.ocr_provider_max_canvas_points,
            max_file_bytes=self.settings.ocr_provider_max_local_file_bytes,
            max_pages=self.settings.ocr_provider_max_pdf_pages,
            normalization_enabled=self.settings.ocr_pdf_canvas_normalization_enabled,
        ).prepare(request.file_path, writer.layout.provider_input)
        writer.write_preflight(prepared.report)
        source = prepared.report["source"]
        self._log(
            log,
            "PDF canvas preflight passed: "
            f"{source['page_count']} pages, strategy={prepared.report['strategy']}",
        )
        if prepared.transformed:
            reasons = ", ".join(str(item) for item in prepared.report.get("reasons") or [])
            self._log(
                log,
                "Provider-safe PDF created with per-page aspect-preserving transforms: "
                f"{reasons}",
            )
        return prepared

    @staticmethod
    def _preflight_summary(prepared: PreparedPdfInput | None) -> dict[str, object]:
        if prepared is None:
            return {
                "status": "remote_unavailable",
                "strategy": "remote_provider_fetch",
                "transformed": False,
            }
        return {
            "status": prepared.report.get("status"),
            "strategy": prepared.report.get("strategy"),
            "transformed": prepared.transformed,
            "reasons": prepared.report.get("reasons") or [],
            "source_page_count": (prepared.report.get("source") or {}).get("page_count"),
        }

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
