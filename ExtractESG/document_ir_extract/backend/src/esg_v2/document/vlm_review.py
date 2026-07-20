from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from esg_v2.config import Settings
from esg_v2.document.contracts import DocumentIR, VlmReviewTask
from esg_v2.models.contracts import CloudChatRequest
from esg_v2.models.model_router import QiniuVisionModelRouter
from esg_v2.models.qiniu_adapter import QiniuApiError, QiniuModelAdapter
from esg_v2.models.vision_input import QiniuVisionInputResolver


LogFn = Callable[[str], None]


class VlmReviewExecutor:
    def __init__(self, settings: Settings, *, api_key: str | None = None):
        self.settings = settings
        self.adapter = QiniuModelAdapter(settings, api_key=api_key)
        self.model_router = QiniuVisionModelRouter(settings, self.adapter)
        self.input_resolver = QiniuVisionInputResolver()

    def execute(self, document: DocumentIR, *, log: LogFn | None = None) -> DocumentIR:
        try:
            model_ids = self.model_router.candidates()
        except Exception as exc:
            for task in document.review_tasks:
                task.status = "skipped"
                task.result = {"error": f"model_selection_failed: {exc}"}
            document.quality_report["vlm_execution"] = "skipped"
            document.quality_report["vlm_execution_error"] = str(exc)
            return document

        attempted = 0
        executed = 0
        skipped = 0
        failed = 0
        max_reviews = max(0, self.settings.max_vlm_reviews_per_ir_run)

        for task in document.review_tasks:
            task.model_name = model_ids[0]
            if attempted >= max_reviews:
                task.status = "skipped"
                task.result = {"reason": "max_vlm_reviews_per_ir_run_reached"}
                skipped += 1
                continue

            image_refs = self.input_resolver.resolve(task.input_refs)
            if task.task_type in {"page_visual_read", "figure_chart_review", "table_structure_review"} and not image_refs:
                task.status = "skipped"
                task.result = {"reason": "no_local_image_input_available"}
                skipped += 1
                continue

            try:
                if log:
                    log(f"Running Qiniu VLM review: {task.task_id}")
                attempted += 1
                cloud_result = None
                model_errors: list[dict[str, object]] = []
                for model_id in model_ids:
                    task.model_name = model_id
                    try:
                        cloud_request = self._build_request(task, model_id=model_id, image_refs=image_refs)
                        cloud_result = self.adapter.chat_completions(cloud_request)
                        break
                    except QiniuApiError as model_exc:
                        model_errors.append({"model_id": model_id, "error": str(model_exc), "status_code": model_exc.status_code})
                        if not model_exc.retryable:
                            raise
                if cloud_result is None:
                    raise QiniuApiError(f"All Qiniu VLM candidates failed: {model_errors}")
                task.status = "done"
                task.result = {
                    "provider": cloud_result.provider,
                    "model_id": cloud_result.model_id,
                    "usage": cloud_result.usage,
                    "latency_ms": cloud_result.latency_ms,
                    "raw_response": cloud_result.raw_response,
                    "parsed_json": self._try_parse_message_json(cloud_result.raw_response),
                    "model_attempts": model_errors,
                }
                executed += 1
            except (QiniuApiError, OSError, ValueError) as exc:
                task.status = "failed"
                task.result = {"error": str(exc)}
                failed += 1

        document.quality_report["vlm_execution"] = "executed"
        document.quality_report["vlm_model_candidates"] = model_ids
        document.quality_report["vlm_executed_count"] = executed
        document.quality_report["vlm_attempted_count"] = attempted
        document.quality_report["vlm_skipped_count"] = skipped
        document.quality_report["vlm_failed_count"] = failed
        document.quality_report["vlm_execution_limit"] = max_reviews
        return document

    def _build_request(self, task: VlmReviewTask, *, model_id: str, image_refs: list[str]) -> CloudChatRequest:
        text_context = self._text_context(task)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    f"Review task {task.task_id} for target {task.target_type}/{task.target_id} "
                    f"on page {task.page_index + 1}.\n"
                    f"Reason codes: {', '.join(task.reason_codes)}\n"
                    f"Intent: {task.prompt_intent}\n"
                    f"Parser context:\n{text_context or '[no text context available]'}\n"
                    "Return strict JSON with keys: findings, corrected_text_or_table, confidence, "
                    "needs_human_review, quality_flags. Do not invent content outside the image."
                ),
            }
        ]
        for image_ref in image_refs:
            content.append({"type": "image_url", "image_url": {"url": image_ref}})

        return CloudChatRequest(
            request_id=task.task_id,
            model_id=model_id,
            max_tokens=1800,
            timeout_seconds=self.settings.qiniu_default_timeout_seconds,
            metadata={"task_type": task.task_type, "target_id": task.target_id},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are ESG Evidence Hub's visual review worker. "
                        "You only review supplied images and OCR context. "
                        "You are not doing final ESG fact extraction."
                    ),
                },
                {"role": "user", "content": content},
            ],
        )

    @staticmethod
    def _text_context(task: VlmReviewTask) -> str:
        snippets: list[str] = []
        for ref in task.input_refs:
            path = Path(ref)
            if not path.exists() or path.suffix.lower() not in {".md", ".txt", ".json", ".jsonl"}:
                continue
            try:
                snippets.append(path.read_text(encoding="utf-8")[:6000])
            except OSError:
                continue
        return "\n\n".join(snippets)[:10000]

    @staticmethod
    def _try_parse_message_json(raw_response: dict[str, object]) -> dict[str, object] | None:
        try:
            choices = raw_response.get("choices")  # type: ignore[assignment]
            if not isinstance(choices, list) or not choices:
                return None
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                text = content.strip()
                if text.startswith("```"):
                    text = text.strip("`")
                    if text.lower().startswith("json"):
                        text = text[4:].strip()
                return json.loads(text)
        except Exception:
            return None
        return None
