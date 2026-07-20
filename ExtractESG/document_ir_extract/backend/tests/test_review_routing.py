from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    BlockIR,
    BoundingBox,
    DocumentIR,
    DocumentIRMetadata,
    PageIR,
    LayoutObjectIR,
    ReviewScopeItem,
    SourceTrace,
    VlmReviewTask,
)
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.models.contracts import CloudChatResult, CloudModelInfo
from esg_v2.models.model_registry import ModelHealthRegistry
from esg_v2.models.model_router import QiniuVisionModelRouter
from esg_v2.models.qiniu_adapter import QiniuApiError
from esg_v2.models.vision_input import QiniuVisionInputResolver


CURRENT_MODELS = [
    "qwen/qwen3.5-plus",
    "bytedance/doubao-seed-2-1-pro",
    "moonshotai/kimi-k2.6",
]


class CatalogAdapter:
    def list_models(self):
        return [CloudModelInfo(id=model_id) for model_id in CURRENT_MODELS]


class AgentAdapter(CatalogAdapter):
    def __init__(self, mode: str):
        self.mode = mode

    def chat_completions(self, request):
        if self.mode == "failure":
            raise QiniuApiError("temporary cloud failure", status_code=503)
        if self.mode == "account-limit":
            raise QiniuApiError("UID rate limit reached for TPD (domestic)", status_code=429)
        role = request.metadata["role"]
        if role == "verifier":
            payload = {
                "verdict": "reject" if self.mode == "disagree" else "accept",
                "disagreements": ["replacement is not visually supported"] if self.mode == "disagree" else [],
                "confidence": 0.94,
            }
        elif self.mode == "confirm":
            payload = {
                "verdict": "confirm",
                "findings": ["candidate matches the page"],
                "patches": [],
                "confidence": 0.97,
                "quality_flags": [],
            }
        elif self.mode == "wrong-target-type":
            payload = {
                "verdict": "correct",
                "findings": ["malformed target declaration"],
                "patches": [
                    {
                        "target_type": "table",
                        "target_id": "block-1",
                        "operation": "set_table_grid",
                        "proposed_value": {
                            "row_count": 1,
                            "column_count": 1,
                            "cells": [{"row_index": 0, "col_index": 0, "text": "teh metric"}],
                        },
                        "evidence_refs": ["page image"],
                        "rationale": "invalid model proposal used to exercise the guard",
                        "confidence": 0.9,
                    }
                ],
                "confidence": 0.9,
                "quality_flags": [],
            }
        else:
            payload = {
                "verdict": "correct",
                "findings": ["one OCR typo"],
                "patches": [
                    {
                        "target_type": "block",
                        "target_id": "block-1",
                        "operation": "replace_block_text",
                        "before_value": "teh metric",
                        "proposed_value": "the metric",
                        "evidence_refs": ["page image"],
                        "rationale": "visible text correction",
                        "confidence": 0.96,
                    }
                ],
                "confidence": 0.96,
                "quality_flags": [],
            }
        raw = {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"total_tokens": 10}}
        return CloudChatResult(
            request_id=request.request_id,
            model_id=request.model_id,
            raw_response=raw,
            usage=raw["usage"],
            latency_ms=12.0,
        )


def _document(tmp_path: Path) -> DocumentIR:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    trace = SourceTrace(parser="paddleocr-vl")
    page = PageIR(
        page_id="page-1",
        page_index=0,
        page_number=1,
        page_image_path=str(image_path),
        text="teh metric",
        text_length=10,
        block_ids=["block-1"],
        layout_object_ids=["layout-1"],
        width=100,
        height=100,
        source_trace=trace,
    )
    block = BlockIR(
        block_id="block-1",
        page_index=0,
        order=0,
        block_type="paragraph",
        text="teh metric",
        bbox=BoundingBox(x0=1, y0=1, x1=90, y1=20, unit="points"),
        source_trace=trace,
    )
    layout = LayoutObjectIR(
        layout_object_id="layout-1",
        page_index=0,
        order=0,
        label="text",
        text="teh metric",
        bbox=BoundingBox(x0=1, y0=1, x1=90, y1=20, unit="points"),
        block_id=block.block_id,
        source_trace=trace,
    )
    task = VlmReviewTask(
        task_id="review-1",
        task_type="page_compound_review",
        target_type="page",
        target_id=page.page_id,
        page_index=0,
        prompt_intent="review",
        input_refs=[str(image_path)],
        blocking=True,
        scope=[ReviewScopeItem(target_type="block", target_id=block.block_id, reason_codes=["ocr_typo"])],
    )
    return DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test", source_pdf_path=str(image_path)),
        pages=[page],
        blocks=[block],
        layout_objects=[layout],
        review_tasks=[task],
    )


def _orchestrator(tmp_path: Path, mode: str) -> AgentReviewOrchestrator:
    settings = Settings(
        output_root=tmp_path,
        document_ir_output_root=tmp_path,
        upload_root=tmp_path,
        qiniu_vlm_model=None,
        max_vlm_reviews_per_ir_run=3,
    )
    orchestrator = AgentReviewOrchestrator(settings, api_key="test-key")
    adapter = AgentAdapter(mode)
    orchestrator.adapter = adapter
    orchestrator.registry.adapter = adapter
    return orchestrator


def test_vision_input_prefers_cloud_addressable_reference(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (20, 20), "white").save(image_path)
    resolver = QiniuVisionInputResolver()
    assert resolver.resolve([str(image_path), "https://example.test/page.png"]) == ["https://example.test/page.png"]
    assert resolver.resolve([str(image_path)])[0].startswith("data:image/jpeg;base64,")


def test_vision_model_router_uses_current_non_retired_models(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    settings = Settings(output_root=tmp_path, document_ir_output_root=tmp_path, upload_root=tmp_path, qiniu_vlm_model=None)
    router = QiniuVisionModelRouter(settings, CatalogAdapter())
    assert router.candidates() == CURRENT_MODELS
    assert router.candidates(role="verifier", exclude_family="qwen") == [
        "bytedance/doubao-seed-2-1-pro",
        "moonshotai/kimi-k2.6",
    ]


def test_agent_confirm_is_guarded_verified_and_auto_resolved(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "confirm").execute(_document(tmp_path), max_auto_review_rounds=2)
    assert document.review_tasks[0].status == "auto_resolved"
    assert document.final_decisions[0].outcome == "auto_confirmed"
    assert document.atomic_patches[0].status == "accepted"
    assert [call.role for call in document.model_calls] == ["reviewer", "verifier"]


def test_agent_correction_applies_only_after_independent_acceptance(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "correct").execute(_document(tmp_path), max_auto_review_rounds=2)
    assert document.blocks[0].text == "the metric"
    assert document.final_decisions[0].outcome == "auto_corrected"
    assert document.candidate_revisions[0].diffs
    assert document.guard_results[0].passed is True


def test_cloud_failure_is_deferred_not_sent_to_human(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "failure").execute(_document(tmp_path), max_auto_review_rounds=2)
    document = DocumentIrValidator().validate(document, expected_page_count=1)
    assert document.review_tasks[0].status == "deferred"
    assert document.final_decisions[0].outcome == "deferred"
    assert document.readiness == "auto_review_pending"
    assert document.validation_report.metrics["human_review_required_count"] == 0


def test_account_daily_limit_stops_cross_model_failover(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "account-limit").execute(_document(tmp_path), max_auto_review_rounds=2)

    assert document.review_tasks[0].status == "deferred"
    assert document.final_decisions[0].outcome == "deferred"
    assert document.final_decisions[0].reason.startswith("qiniu_account_rate_limited")
    assert len(document.model_calls) == 1


def test_two_round_model_disagreement_is_the_human_boundary(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "disagree").execute(_document(tmp_path), max_auto_review_rounds=2)
    assert document.review_tasks[0].status == "human_required"
    assert document.final_decisions[-1].outcome == "human_required"
    assert len(document.reviewer_results) == 2
    assert len(document.verifier_results) == 2
    assert document.blocks[0].text == "teh metric"


def test_invalid_model_target_is_repaired_or_safely_unresolved(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "wrong-target-type").execute(_document(tmp_path), max_auto_review_rounds=2)

    assert document.review_tasks[0].status == "human_required"
    assert document.final_decisions[-1].outcome == "human_required"
    assert len(document.guard_results) == 2
    assert all(result.passed is False for result in document.guard_results)
    assert all(patch.status == "guard_failed" for patch in document.atomic_patches)
    assert document.blocks[0].text == "teh metric"
