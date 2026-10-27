from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from esg_v2.config import Settings
from esg_v2.contracts import OcrRunRequest, OcrRunResult
from esg_v2.ocr import text_layer, vision_prescan
from esg_v2.ocr.output_writer import OcrOutputWriter
from esg_v2.ocr.page_selection import Foundation16PageSelector, PageSelection
from esg_v2.ocr.pdf_preflight import PdfCanvasPreflight, PreparedPdfInput
from esg_v2.ocr.providers import OcrProviderRouter
from esg_v2.ocr.resources import LocalOcrResourceReader
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
        requested_pages, selection = self._resolve_page_targets(request, writer, log)
        prepared = self._prepare_provider_input(request, writer, log, page_indices=requested_pages)
        page_indices = list(prepared.page_indices) if prepared and prepared.page_indices else None
        source_page_count = int(((prepared.report.get("source") or {}).get("page_count") or 0)) if prepared else 0
        expected_pages = len(page_indices) if page_indices else source_page_count
        writer.write_request(self._redacted_request(request, run_id))
        targets = page_indices if page_indices is not None else list(range(source_page_count))
        read = self._read_text_layer(request, targets, writer, run_id, log, progress) if prepared else None
        text_pages = read.pages if read else {}
        visual_targets = [index for index in targets if index not in text_pages]
        provider_selection = None
        execution = None
        poll_events: list[dict[str, Any]] = list(read.poll_events) if read else []
        if visual_targets or not prepared:
            input_path = prepared.provider_path if prepared else (Path(request.file_path) if request.file_path else None)
            if text_pages:
                input_path = self._visual_input(prepared, targets, visual_targets, writer)
                self._log(log, f"Routing OCR for {source_summary}: requested={request.ocr_provider}, "
                               f"{len(visual_targets)}/{len(targets)} pages need visual OCR")
            else:
                self._log(log, f"Routing OCR for {source_summary}: requested={request.ocr_provider}")
            provider_selection = self.provider_router.execute(
                request,
                input_path=input_path,
                provider_root=writer.raw_dir,
                run_id=run_id,
                expected_page_count=len(visual_targets) if text_pages else expected_pages,
                progress_callback=self._shifted_progress(progress, len(text_pages), len(targets)) if text_pages else progress,
                log=log,
            )
            execution = provider_selection.execution
            poll_events.extend(execution.poll_events)
        else:
            self._log(log, f"All {len(targets)} pages read from the PDF text layer; no visual OCR needed")
        submit_response = execution.submit_response if execution else {
            "provider": self.provider_router.local.name,
            "route": "pdf_text_layer",
            "data": {"jobId": f"text-layer-{run_id}", "state": "accepted"},
            "layout_model": read.layout_model if read else None,
        }
        submit_response_path = writer.write_submit_response(submit_response)
        for event in poll_events:
            writer.append_poll_event(event)
        if not poll_events:
            writer.append_poll_event({"provider": execution.provider if execution else "pdf_text_layer", "data": {"state": "done"}})
        jsonl_text = execution.jsonl_text if execution else ""
        if text_pages:
            jsonl_text = self._merged_pages(targets, text_pages, visual_targets, jsonl_text, run_id)
        raw_jsonl_path = writer.save_result_jsonl(jsonl_text)
        resource_reader = execution.resource_reader if execution and not text_pages else LocalOcrResourceReader(
            writer.raw_dir / "local-resources")
        if execution and text_pages and execution.provider != self.provider_router.local.name:
            resource_reader = _RoutedResourceReader(resource_reader, execution.resource_reader)
        markdown_files, downloaded_files, artifact_rows, page_count = writer.extract_artifacts_from_jsonl(
            jsonl_text=jsonl_text,
            resource_reader=resource_reader,
            page_indices=page_indices,
        )
        provider_name = execution.provider if execution else self.provider_router.local.name
        if prepared:
            if page_count != expected_pages:
                raise RuntimeError(
                    f"{provider_name} returned incomplete page coverage after canvas normalization: "
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
        attempts = list(provider_selection.attempts) if provider_selection else []
        model = execution.metadata.get("model", request.model) if execution else "pdf-text-layer"
        if read and read.pages:
            attempts.insert(0, {"provider": "pdf_text_layer", "outcome": "selected", "pages": len(read.pages),
                                "layout_model": read.layout_model})
            if execution:
                model = f"{model}+pdf-text-layer"
        manifest = {
            "run_id": run_id,
            "job_id": execution.job_id if execution else f"text-layer-{run_id}",
            "model": model,
            "ocr_provider": provider_name,
            "provider_route": {
                "requested": provider_selection.requested_provider if provider_selection else request.ocr_provider,
                "requested_model": request.model,
                "selected": provider_name,
                "allow_api_fallback": request.allow_api_fallback,
                "fallback_reason": provider_selection.fallback_reason if provider_selection else None,
                "attempts": attempts,
            },
            "provider_metadata": execution.metadata if execution else {
                "transport": "isolated-local-worker",
                "route": "pdf_text_layer",
                "layout_model": read.layout_model if read else None,
            },
            "source": source,
            "pdf_preflight": self._preflight_summary(prepared),
            "provider_input": provider_input,
            "optional_payload": request.optional_payload.model_dump(),
            "page_count": page_count,
            "artifact_count": len(artifact_rows),
        }
        page_selection = self._page_selection_manifest(selection, page_indices, source_page_count)
        if page_selection:
            manifest["page_selection"] = page_selection
        if read:
            manifest["text_layer"] = read.report(targets)
        manifest_path = writer.write_manifest(manifest)

        return OcrRunResult(
            run_id=run_id,
            provider=provider_name,
            job_id=manifest["job_id"],
            output_dir=output_dir,
            manifest_path=manifest_path,
            raw_jsonl_path=raw_jsonl_path,
            page_markdown_files=markdown_files,
            downloaded_files=downloaded_files,
            page_count=page_count,
            page_indices=page_indices,
            result_json_url=sanitize_remote_url(execution.result_json_url) if execution and execution.result_json_url else None,
            poll_history_path=writer.layout.poll_events,
            submit_response_path=submit_response_path,
        )

    def _read_text_layer(
        self,
        request: OcrRunRequest,
        targets: list[int],
        writer: OcrOutputWriter,
        run_id: str,
        log: LogFn | None,
        progress: ProgressFn | None,
    ) -> "_TextLayerRun | None":
        """Fill the pages a digital PDF prints cleanly from its own text layer.

        Only for foundation16 page-targeted runs: the layout model of the OCR
        pipeline finds the regions; words, tables and figures come from the
        PDF. Pages it cannot fill (scans, pictures of tables, broken fonts)
        keep going to visual OCR.
        """
        if not self.settings.ocr_text_layer_enabled or (
                request.page_selection != "foundation16" and request.page_indices is None) or not request.file_path:
            return None
        local = self.provider_router.local
        ready, reason = local.layout_ready()
        if not ready:
            self._log(log, f"Text-layer route unavailable ({reason}); all pages go to visual OCR")
            return None
        started = time.monotonic()
        candidates, needs_ocr = text_layer.readable_pages(request.file_path, targets)
        timings = {"precheck": time.monotonic() - started}
        run = _TextLayerRun(text_layer.TextLayerRead(needs_ocr=dict(needs_ocr), timings=timings))
        if not candidates:
            self._log(log, f"Text layer unusable on all {len(targets)} pages; visual OCR for every page")
            return run
        self._log(log, f"Text layer readable on {len(candidates)}/{len(targets)} pages; detecting layout")
        layout_started = time.monotonic()
        try:
            layouts, run.poll_events = local.detect_layout(
                input_path=Path(request.file_path),
                page_indices=candidates,
                provider_root=writer.raw_dir,
                run_id=run_id,
                progress_callback=self._layout_progress(progress, len(targets)),
                log=log,
            )
        except Exception as exc:
            self._log(log, f"Layout detection failed ({type(exc).__name__}: {exc}); all pages go to visual OCR")
            run.read.needs_ocr.update({index: "layout_detection_failed" for index in candidates})
            return run
        timings["layout"] = time.monotonic() - layout_started
        fill_started = time.monotonic()
        pages, reasons = text_layer.fill_pages(request.file_path, layouts, len(targets))
        timings["fill"] = time.monotonic() - fill_started
        # Titles set as pictures leave a text region empty; on-device OCR of
        # those pages fills them instead of sending the whole page to the VLM.
        retry = [index for index, reason in reasons.items() if reason == "text_region_without_text_layer"]
        if retry and self.settings.ocr_vision_prescan_enabled and vision_prescan.available():
            ocr_started = time.monotonic()
            try:
                by_index = {int(item["page_index"]): item for item in layouts}
                lines = vision_prescan.region_lines(request.file_path, {index: by_index[index] for index in retry},
                                                    log=lambda message: self._log(log, message))
                filled, still = text_layer.fill_pages(
                    request.file_path, [by_index[index] for index in retry], len(targets), lines)
                pages.update(filled)
                for index in filled:
                    reasons.pop(index, None)
                reasons.update(still)
                run.read.on_device_ocr_pages = sorted(filled)
            except Exception as exc:
                self._log(log, f"On-device OCR of empty text regions failed ({type(exc).__name__}); keeping visual OCR")
            timings["on_device_ocr"] = time.monotonic() - ocr_started
        run.read.pages = pages
        run.read.needs_ocr.update(reasons)
        run.read.layout_model = next((item.get("layout_model") for item in layouts), None)
        summary = ", ".join(f"{key}={count}" for key, count in sorted(run.read.report(targets)["visual_ocr_reason_counts"].items()))
        self._log(log, f"Text layer filled {len(pages)}/{len(targets)} pages in {sum(timings.values()):.1f}s"
                       + (f"; visual OCR needed for {len(targets) - len(pages)} ({summary})" if len(pages) < len(targets) else ""))
        if progress:
            progress({"provider": "pdf_text_layer", "state": "running", "extractedPages": len(pages),
                      "totalPages": len(targets), "message": f"Text layer: {len(pages)}/{len(targets)} pages"})
        return run

    @staticmethod
    def _layout_progress(progress: ProgressFn | None, total: int) -> ProgressFn | None:
        if progress is None:
            return None

        def relay(event: dict[str, Any]) -> None:
            done = int(event.get("extractedPages") or 0)
            state = "heartbeat" if event.get("state") == "heartbeat" else "running"
            progress({**event, "state": state, "phase": "layout", "extractedPages": 0, "totalPages": total,
                      "message": f"Layout detection: {done}/{event.get('totalPages') or '?'} pages"})

        return relay

    @staticmethod
    def _shifted_progress(progress: ProgressFn | None, offset: int, total: int) -> ProgressFn | None:
        """Visual OCR progress counted over all target pages."""
        if progress is None:
            return None

        def relay(event: dict[str, Any]) -> None:
            progress({**event, "extractedPages": offset + int(event.get("extractedPages") or 0), "totalPages": total})

        return relay

    @staticmethod
    def _visual_input(
        prepared: PreparedPdfInput,
        targets: list[int],
        visual_targets: list[int],
        writer: OcrOutputWriter,
    ) -> Path:
        """The provider input reduced to the pages that need visual OCR."""
        from pypdf import PdfReader, PdfWriter

        positions = {index: position for position, index in enumerate(targets)}
        reader = PdfReader(str(prepared.provider_path), strict=False)
        output = PdfWriter()
        for index in visual_targets:
            output.add_page(reader.pages[positions[index]])
        path = writer.layout.provider_input.with_name("provider-input-visual.pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            output.write(handle)
        return path

    @staticmethod
    def _merged_pages(
        targets: list[int],
        text_pages: dict[int, dict[str, Any]],
        visual_targets: list[int],
        provider_jsonl: str,
        run_id: str,
    ) -> str:
        """One result line per target page, in page order, whichever route read it."""
        provider_results = [
            result
            for line in provider_jsonl.splitlines() if line.strip()
            for result in ((json.loads(line).get("result") or {}).get("layoutParsingResults") or [])
        ]
        if len(provider_results) != len(visual_targets):
            raise RuntimeError(
                f"Visual OCR returned {len(provider_results)} pages for {len(visual_targets)} requested pages"
            )
        visual = dict(zip(visual_targets, provider_results))
        lines = []
        for position, index in enumerate(targets):
            lines.append(json.dumps({
                "errorCode": 0,
                "errorMsg": "Success",
                "logId": run_id,
                "result": {
                    "layoutParsingResults": [text_pages[index] if index in text_pages else visual[index]],
                    "dataInfo": {"pageIndex": position,
                                 "source": "pdf_text_layer" if index in text_pages else "visual_ocr"},
                },
            }, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(lines) + "\n"

    def _resolve_page_targets(
        self,
        request: OcrRunRequest,
        writer: OcrOutputWriter,
        log: LogFn | None,
    ) -> tuple[list[int] | None, PageSelection | None]:
        if request.page_indices is not None:
            self._log(log, f"Page-targeted OCR: {len(request.page_indices)} explicitly selected source pages")
            return list(request.page_indices), None
        if request.page_selection != "foundation16" or not request.file_path:
            return None, None
        prescan = None
        if self.settings.ocr_vision_prescan_enabled and vision_prescan.available():
            def prescan(path: Path, page_count: int) -> list[str]:
                self._log(log, f"No usable text layer; reading {page_count} pages on-device to choose OCR targets")
                return vision_prescan.page_texts(path, page_count, log=lambda message: self._log(log, message))
        selection = Foundation16PageSelector().select(request.file_path, prescan=prescan)
        writer.write_page_selection(selection.report())
        if not selection.targeted:
            self._log(
                log,
                f"Page selector {request.page_selection} fell back to whole-document OCR: {selection.reason}",
            )
            return None, selection
        pages = ", ".join(str(index + 1) for index in selection.page_indices or ())
        self._log(
            log,
            f"Page selector {request.page_selection} ({selection.text_source}) chose {len(selection.page_indices or ())}/"
            f"{selection.source_page_count} pages in {selection.elapsed_seconds:.2f}s: {pages}",
        )
        return list(selection.page_indices or ()), selection

    @staticmethod
    def _page_selection_manifest(
        selection: PageSelection | None,
        page_indices: list[int] | None,
        source_page_count: int,
    ) -> dict[str, Any] | None:
        """Coverage contract every consumer maps OCR page positions through."""
        if page_indices is None and selection is None:
            return None
        if selection is not None:
            selector = selection.provenance()
        else:
            selector = {"name": "explicit", "reason": "requested_page_indices"}
        return {
            "mode": "targeted" if page_indices is not None else "all",
            "source_page_count": source_page_count,
            "page_indices": page_indices,
            "selector": selector,
        }

    def _prepare_provider_input(
        self,
        request: OcrRunRequest,
        writer: OcrOutputWriter,
        log: LogFn | None,
        *,
        page_indices: list[int] | None = None,
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
        ).prepare(request.file_path, writer.layout.provider_input, page_indices=page_indices)
        writer.write_preflight(prepared.report)
        source = prepared.report["source"]
        self._log(
            log,
            "PDF canvas preflight passed: "
            f"{source['page_count']} pages, strategy={prepared.report['strategy']}",
        )
        if prepared.page_indices:
            self._log(
                log,
                f"Provider input holds {len(prepared.page_indices)}/{source['page_count']} selected source pages",
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


class _TextLayerRun:
    def __init__(self, read: text_layer.TextLayerRead):
        self.read = read
        self.poll_events: list[dict[str, Any]] = []

    @property
    def pages(self) -> dict[int, dict[str, Any]]:
        return self.read.pages

    @property
    def layout_model(self) -> str | None:
        return self.read.layout_model

    def report(self, targets: list[int]) -> dict[str, Any]:
        return self.read.report(targets)


class _RoutedResourceReader:
    """Local text-layer resources beside a remote provider's resources."""

    def __init__(self, local: LocalOcrResourceReader, remote: Any):
        self.local = local
        self.remote = remote

    def read_bytes(self, reference: str) -> bytes:
        from esg_v2.ocr.resources import LOCAL_RESOURCE_PREFIX

        if reference.startswith(LOCAL_RESOURCE_PREFIX):
            return self.local.read_bytes(reference)
        return OcrOutputWriter._read_resource(self.remote, reference)
