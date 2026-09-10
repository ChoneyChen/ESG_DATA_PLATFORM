from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    ArtifactRef,
    AtomicPatch,
    BlockIR,
    BoundingBox,
    CellIR,
    ConflictGroup,
    CoordinateSystem,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    GuardCheck,
    GuardResult,
    PageIR,
    LayoutObjectIR,
    PatchTransactionIR,
    ReviewScopeItem,
    ReviewerResult,
    SourceTrace,
    SpreadIR,
    TableIR,
    VerifierPayload,
    VlmReviewTask,
)
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.review_plan_compiler import ReviewPlanCompiler
from esg_v2.document.review_context import ReviewContextCompiler
from esg_v2.document.review_response_adapter import ReviewResponseAdapter
from esg_v2.document.convergence_engine import ConvergenceEngine
from esg_v2.document.transaction_coordinator import TransactionCoordinator
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.models.contracts import CloudChatResult, CloudModelInfo
from esg_v2.models.model_registry import ModelHealthRegistry
from esg_v2.models.model_router import QiniuVisionModelRouter
from esg_v2.models.qiniu_adapter import QiniuApiError
from esg_v2.models.vision_input import QiniuVisionInputResolver


CURRENT_MODELS = [
    "qwen/qwen3.5-plus",
    "doubao-seed-2.0-pro",
    "qwen3.5-397b-a17b",
    "stepfun/step-3.7-flash",
    "moonshotai/kimi-k2.6",
]


class CatalogAdapter:
    def list_models(self):
        return [CloudModelInfo(id=model_id) for model_id in CURRENT_MODELS]


class AgentAdapter(CatalogAdapter):
    def __init__(self, mode: str):
        self.mode = mode
        self.verifier_calls = 0

    def list_models(self):
        if self.mode == "catalog-account-limit":
            raise QiniuApiError("UID rate limit reached for TPD (domestic)", status_code=429)
        if self.mode == "verifier-empty-once":
            return [CloudModelInfo(id=model_id) for model_id in ("qwen/qwen3.5-plus", "moonshotai/kimi-k2.6")]
        return super().list_models()

    def chat_completions(self, request):
        if self.mode == "failure":
            raise QiniuApiError("temporary cloud failure", status_code=503)
        if self.mode == "account-limit":
            raise QiniuApiError("UID rate limit reached for TPD (domestic)", status_code=429)
        role = request.metadata["role"]
        if role == "verifier":
            self.verifier_calls += 1
            if self.mode == "verifier-failure":
                raise QiniuApiError("temporary verifier service failure", status_code=503)
            if self.mode == "verifier-empty-once" and self.verifier_calls == 1:
                raw = {"choices": [{"message": {"content": ""}}], "usage": {"total_tokens": 0}}
                return CloudChatResult(
                    request_id=request.request_id,
                    model_id=request.model_id,
                    raw_response=raw,
                    usage=raw["usage"],
                    latency_ms=12.0,
                )
            if self.mode == "verifier-partial-accept":
                content = request.messages[-1]["content"]
                prompt_text = "\n".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
                transaction_ids = sorted(set(re.findall(r"transaction-\d{6}", prompt_text)))
                payload = {
                    "verdict": "reject",
                    "disagreements": ["The second correction is not visually supported."],
                    "confidence": 0.91,
                    "transaction_decisions": [
                        {
                            "transaction_id": transaction_id,
                            "verdict": "accept" if index == 0 else "reject",
                            "disagreements": (
                                []
                                if index == 0
                                else ["The second correction is not visually supported."]
                            ),
                            "confidence": 0.95 if index == 0 else 0.84,
                        }
                        for index, transaction_id in enumerate(transaction_ids)
                    ],
                }
            else:
                payload = {
                    "verdict": "reject" if self.mode == "disagree" else "accept",
                    "disagreements": ["replacement is not visually supported"] if self.mode == "disagree" else [],
                    "confidence": 0.94,
                }
        elif self.mode == "abstain":
            payload = {
                "verdict": "abstain",
                "findings": ["The supplied visual does not support a reliable decision."],
                "scope_decisions": [
                    {
                        "target_type": "block",
                        "target_id": "block-1",
                        "decision": "abstain",
                        "rationale": "Insufficient visual evidence.",
                        "confidence": 0.4,
                    }
                ],
                "patches": [],
                "confidence": 0.4,
                "abstain_reason": "Insufficient visual evidence.",
                "quality_flags": [],
            }
        elif self.mode == "confirm":
            payload = {
                "verdict": "confirm",
                "findings": ["candidate matches the page"],
                "scope_decisions": [
                    {
                        "target_type": "block",
                        "target_id": "block-1",
                        "decision": "confirm",
                        "rationale": "visible candidate matches",
                        "confidence": 0.97,
                    }
                ],
                "patches": [],
                "confidence": 0.97,
                "quality_flags": [],
            }
        elif self.mode == "legacy-correct-no-patches":
            payload = {
                "verdict": "correct",
                "findings": ["the candidate is correct"],
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
        elif self.mode in {"partial-transaction", "verifier-partial-accept"}:
            payload = {
                "verdict": "propose_patch",
                "findings": ["two localized corrections"],
                "scope_decisions": [
                    {
                        "target_type": "block",
                        "target_id": "block-1",
                        "decision": "propose_patch",
                        "rationale": "visible OCR typo",
                        "confidence": 0.96,
                    },
                    {
                        "target_type": "block",
                        "target_id": "block-2",
                        "decision": "propose_patch",
                        "rationale": "malformed structure proposal",
                        "confidence": 0.80,
                    },
                ],
                "patches": [
                    {
                        "target_type": "block",
                        "target_id": "block-1",
                        "operation": "replace_block_text",
                        "proposed_value": "the metric",
                        "evidence_refs": ["page image"],
                        "rationale": "visible text correction",
                        "confidence": 0.96,
                    },
                    {
                        "target_type": "block",
                        "target_id": "block-2",
                        "operation": (
                            "set_table_grid"
                            if self.mode == "partial-transaction"
                            else "replace_block_text"
                        ),
                        "proposed_value": (
                            {
                                "row_count": 1,
                                "column_count": 1,
                                "cells": [{"row_index": 0, "col_index": 0, "text": "second"}],
                            }
                            if self.mode == "partial-transaction"
                            else "second corrected"
                        ),
                        "evidence_refs": ["page image"],
                        "rationale": "invalid operation for this target",
                        "confidence": 0.80,
                    },
                ],
                "confidence": 0.90,
                "quality_flags": [],
            }
        else:
            payload = {
                "verdict": "propose_patch",
                "findings": ["one OCR typo"],
                "scope_decisions": [
                    {
                        "target_type": "block",
                        "target_id": "block-1",
                        "decision": "propose_patch",
                        "rationale": "visible OCR typo",
                        "confidence": 0.96,
                    }
                ],
                "patches": [
                    {
                        "target_type": "block",
                        "target_id": "block-1",
                        "operation": "replace_block_text",
                        "before_value": "model-must-not-control-before-value" if self.mode == "wrong-before" else "teh metric",
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
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-test", ocr_run_id="ocr-test", source_pdf_path=str(image_path)),
        pages=[page],
        blocks=[block],
        layout_objects=[layout],
        review_tasks=[task],
    )
    ReviewPlanCompiler().compile(document, task)
    return document


def _compile_changed_task(document: DocumentIR) -> None:
    task = document.review_tasks[0]
    task.review_plan = None
    ReviewPlanCompiler().compile(document, task)


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
    assert router.candidates() == [
        "qwen/qwen3.5-plus",
        "doubao-seed-2.0-pro",
        "stepfun/step-3.7-flash",
    ]
    assert router.candidates(role="verifier", exclude_family="qwen") == [
        "doubao-seed-2.0-pro",
        "stepfun/step-3.7-flash",
        "moonshotai/kimi-k2.6",
    ]


def test_agent_confirm_is_guarded_verified_and_auto_resolved(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "confirm").execute(_document(tmp_path), max_auto_review_rounds=2)
    assert document.review_tasks[0].status == "auto_resolved"
    assert document.final_decisions[0].outcome == "auto_confirmed"
    assert document.atomic_patches[0].status == "accepted"
    assert [call.role for call in document.model_calls] == ["reviewer"]
    assert document.guard_results[0].requires_independent_verifier is False


def test_agent_correction_applies_only_after_independent_acceptance(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "correct").execute(_document(tmp_path), max_auto_review_rounds=2)
    assert document.blocks[0].text == "the metric"
    assert document.final_decisions[0].outcome == "auto_corrected"
    assert document.candidate_revisions[0].diffs
    assert document.guard_results[0].passed is True


def test_local_system_injects_before_value_instead_of_trusting_model(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "wrong-before").execute(_document(tmp_path), max_auto_review_rounds=2)

    assert document.blocks[0].text == "the metric"
    assert document.atomic_patches[0].before_value == "teh metric"
    assert document.guard_results[0].passed is True


def test_legacy_correct_without_patches_is_normalized_to_confirmation(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "legacy-correct-no-patches").execute(
        _document(tmp_path),
        max_auto_review_rounds=2,
    )

    assert document.final_decisions[0].outcome == "auto_confirmed"
    assert document.reviewer_results[0].verdict == "confirm"
    assert "non_contract_verdict_normalized" in document.reviewer_results[0].quality_flags


def test_non_contract_replace_verdict_is_protocol_repaired(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _document(tmp_path)
    normalized = ReviewResponseAdapter.normalize_legacy_aliases(
        document,
        {
            "verdict": "replace",
            "scope_decisions": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "decision": "replace",
                    "rationale": "visible correction",
                    "confidence": 0.9,
                }
            ],
            "patches": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "operation": "replace_block_text",
                    "proposed_value": "the metric",
                }
            ],
        },
    )

    assert normalized["verdict"] == "propose_patch"
    assert normalized["scope_decisions"][0]["decision"] == "propose_patch"


def test_protocol_failures_do_not_open_transport_circuit() -> None:
    ModelHealthRegistry.reset()
    model_id = "qwen/qwen3.5-plus"
    ModelHealthRegistry.record_failure(model_id, "invalid json", category="protocol")
    ModelHealthRegistry.record_failure(model_id, "invalid schema", category="protocol")
    state = ModelHealthRegistry.state(model_id)

    assert state.protocol_failures == 2
    assert state.transport_failures == 0
    assert state.protocol_circuit_open_until == 0
    assert ModelHealthRegistry.is_available(model_id) is True


def test_transport_and_protocol_circuits_have_independent_thresholds() -> None:
    ModelHealthRegistry.reset()
    transport_model = "moonshotai/kimi-k2.6"
    protocol_model = "qwen/qwen3.5-plus"
    for _ in range(2):
        ModelHealthRegistry.record_failure(transport_model, "timeout", category="transport")
    for _ in range(3):
        ModelHealthRegistry.record_failure(protocol_model, "truncated json", category="protocol")

    assert ModelHealthRegistry.state(transport_model).transport_circuit_open_until > 0
    assert ModelHealthRegistry.state(protocol_model).protocol_circuit_open_until > 0
    assert ModelHealthRegistry.is_available(transport_model) is False
    assert ModelHealthRegistry.is_available(protocol_model) is False


def test_model_health_survives_registry_reconfiguration(tmp_path: Path) -> None:
    state_path = tmp_path / "model-health.json"
    ModelHealthRegistry.configure(state_path)
    ModelHealthRegistry.reset()
    model_id = "qwen/qwen3.5-plus"
    ModelHealthRegistry.record_failure(
        model_id,
        "truncated json",
        category="protocol",
    )
    assert state_path.exists()

    ModelHealthRegistry.configure(None)
    ModelHealthRegistry.configure(state_path)
    restored = ModelHealthRegistry.state(model_id)

    assert restored.protocol_failures == 1
    assert restored.last_failure_category == "protocol"
    ModelHealthRegistry.reset()


def test_cloud_failure_is_deferred_not_sent_to_human(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "failure").execute(_document(tmp_path), max_auto_review_rounds=2)
    document = DocumentIrValidator().validate(document, expected_page_count=1)
    assert document.review_tasks[0].status == "deferred"
    assert document.final_decisions[0].outcome == "deferred"
    assert document.readiness == "auto_review_pending"
    assert document.validation_report.metrics["human_review_required_count"] == 0


def test_transient_empty_verifier_response_uses_remaining_round_budget(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "verifier-empty-once").execute(
        _document(tmp_path),
        max_auto_review_rounds=2,
    )

    assert document.review_tasks[0].status == "auto_resolved"
    assert document.final_decisions[0].outcome == "auto_corrected"
    assert len(document.reviewer_results) == 1
    assert len(document.guard_results) == 1
    assert len(document.verifier_results) == 1
    assert [call.status for call in document.model_calls] == [
        "succeeded",
        "invalid_response",
        "succeeded",
    ]


def test_structured_verifier_disagreements_are_normalized_at_the_contract_boundary() -> None:
    payload = VerifierPayload.model_validate(
        {
            "verdict": "reject",
            "disagreements": [
                {"patch_id": "patch-000001", "issue": "Grid does not match the page.", "severity": "high"}
            ],
            "confidence": 0.92,
        }
    )

    assert payload.disagreements == [
        '{"issue": "Grid does not match the page.", "patch_id": "patch-000001", "severity": "high"}'
    ]


def test_account_daily_limit_stops_cross_model_failover(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "account-limit").execute(_document(tmp_path), max_auto_review_rounds=2)

    assert document.review_tasks[0].status == "deferred"
    assert document.final_decisions[0].outcome == "deferred"
    assert document.final_decisions[0].reason.startswith("qiniu_account_rate_limited")
    assert len(document.model_calls) == 1


def test_account_daily_limit_short_circuits_remaining_tasks_without_http(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    source = _document(tmp_path)
    second = source.review_tasks[0].model_copy(deep=True)
    second.task_id = "review-2"
    second.final_decision_id = None
    source.review_tasks.append(second)

    document = _orchestrator(tmp_path, "account-limit").execute(
        source,
        max_auto_review_rounds=2,
    )

    assert len(document.model_calls) == 1
    assert [task.status for task in document.review_tasks] == ["deferred", "deferred"]
    assert document.review_tasks[0].execution_count == 1
    assert document.review_tasks[1].execution_count == 0
    assert document.review_tasks[1].result["short_circuited"] is True
    assert document.review_tasks[1].result["http_attempted"] is False
    assert document.quality_report["provider_rate_limit"]["http_skipped_task_count"] == 1


def test_catalog_daily_limit_opens_provider_circuit_before_task_http(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "catalog-account-limit").execute(
        _document(tmp_path),
        max_auto_review_rounds=2,
    )

    assert document.review_tasks[0].status == "deferred"
    assert document.review_tasks[0].execution_count == 0
    assert document.review_tasks[0].result["short_circuited"] is True
    assert document.model_calls == []
    assert document.quality_report["provider_rate_limit"]["limit_trigger_stage"] == "model_catalog"


def test_verifier_service_failure_resumes_checkpoint_without_reviewer_replay(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    first = _orchestrator(tmp_path, "verifier-failure").execute(
        _document(tmp_path),
        max_auto_review_rounds=1,
    )

    task = first.review_tasks[0]
    assert task.status == "deferred"
    assert task.resume_stage == "verifier_pending"
    assert len(first.reviewer_results) == 1
    assert first.patch_transactions[0].status == "guard_passed"
    assert first.atomic_patches[0].status == "guard_passed"

    previous_call_count = len(first.model_calls)
    ModelHealthRegistry.reset()
    resumed = _orchestrator(tmp_path, "correct").execute(
        first,
        review_target_ids=[task.task_id],
        max_auto_review_rounds=1,
    )

    assert resumed.review_tasks[0].status == "auto_resolved"
    assert resumed.blocks[0].text == "the metric"
    assert len(resumed.reviewer_results) == 1
    assert [call.role for call in resumed.model_calls[previous_call_count:]] == ["verifier"]
    assert resumed.review_tasks[0].execution_count == 2
    assert resumed.review_tasks[0].resume_stage == "complete"


def test_two_round_model_disagreement_is_the_human_boundary(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "disagree").execute(_document(tmp_path), max_auto_review_rounds=2)
    assert document.review_tasks[0].status == "human_required"
    assert document.final_decisions[-1].outcome == "human_required"
    assert len(document.reviewer_results) == 2
    assert len(document.verifier_results) == 2
    assert document.blocks[0].text == "teh metric"


def test_reviewer_abstention_uses_a_different_model_family_before_human_boundary(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "abstain").execute(_document(tmp_path), max_auto_review_rounds=2)

    assert document.review_tasks[0].status == "human_required"
    assert len(document.reviewer_results) == 2
    assert len({result.model_family for result in document.reviewer_results}) == 2
    assert document.final_decisions[-1].reason == "reviewer_abstained_after_repair"


def test_invalid_model_target_is_repaired_or_safely_unresolved(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _orchestrator(tmp_path, "wrong-target-type").execute(_document(tmp_path), max_auto_review_rounds=2)

    assert document.review_tasks[0].status == "deferred"
    assert document.final_decisions[-1].outcome == "deferred"
    assert document.final_decisions[-1].reason == "automation_proposal_invalid_after_repair"
    assert len(document.guard_results) == 2
    assert all(result.passed is False for result in document.guard_results)
    assert all(patch.status == "guard_failed" for patch in document.atomic_patches)
    assert document.blocks[0].text == "teh metric"


def test_guard_transactions_preserve_valid_correction_when_sibling_patch_fails(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _document(tmp_path)
    trace = SourceTrace(parser="paddleocr-vl")
    document.blocks.append(
        BlockIR(
            block_id="block-2",
            page_index=0,
            order=1,
            block_type="paragraph",
            text="second",
            bbox=BoundingBox(x0=1, y0=30, x1=90, y1=50, unit="points"),
            source_trace=trace,
        )
    )
    document.pages[0].block_ids.append("block-2")
    document.review_tasks[0].scope.append(
        ReviewScopeItem(
            target_type="block",
            target_id="block-2",
            reason_codes=["ocr_typo"],
        )
    )
    _compile_changed_task(document)

    result = _orchestrator(tmp_path, "partial-transaction").execute(
        document,
        max_auto_review_rounds=2,
    )

    assert result.blocks[0].text == "the metric"
    assert result.review_tasks[0].status == "deferred"
    assert result.review_tasks[0].failure_class == "repeated_failure"
    assert result.review_tasks[0].retryable is False
    assert any(item.status == "accepted" for item in result.patch_transactions)
    assert any(item.status == "guard_failed" for item in result.patch_transactions)
    validated = DocumentIrValidator().validate(result, expected_page_count=1)
    assert validated.readiness == "repair_required"
    assert validated.validation_report.metrics["system_repair_required_count"] == 1
    assert any(
        issue.code == "review_chain_repair_required"
        for issue in validated.validation_report.issues
    )


def test_verifier_decides_guard_passing_transactions_independently(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _document(tmp_path)
    trace = SourceTrace(parser="paddleocr-vl")
    document.blocks.append(
        BlockIR(
            block_id="block-2",
            page_index=0,
            order=1,
            block_type="paragraph",
            text="second",
            bbox=BoundingBox(x0=1, y0=30, x1=90, y1=50, unit="points"),
            source_trace=trace,
        )
    )
    document.pages[0].block_ids.append("block-2")
    document.review_tasks[0].scope.append(
        ReviewScopeItem(
            target_type="block",
            target_id="block-2",
            reason_codes=["ocr_typo"],
        )
    )
    _compile_changed_task(document)

    result = _orchestrator(tmp_path, "verifier-partial-accept").execute(
        document,
        max_auto_review_rounds=1,
    )

    assert result.blocks[0].text == "the metric"
    assert result.blocks[1].text == "second"
    assert result.review_tasks[0].status == "human_required"
    assert [item.status for item in result.patch_transactions] == [
        "accepted",
        "rejected",
    ]
    assert [item.verdict for item in result.verifier_results[0].transaction_decisions] == [
        "accept",
        "reject",
    ]
    assert [patch.status for patch in result.atomic_patches] == [
        "accepted",
        "human_required",
    ]


def test_accepted_transaction_closes_only_its_covered_conflict(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _document(tmp_path)
    trace = SourceTrace(parser="paddleocr-vl")
    document.blocks.append(
        BlockIR(
            block_id="block-2",
            page_index=0,
            order=1,
            block_type="paragraph",
            text="second",
            bbox=BoundingBox(x0=1, y0=30, x1=90, y1=50, unit="points"),
            source_trace=trace,
        )
    )
    document.pages[0].block_ids.append("block-2")
    document.review_tasks[0].scope.append(
        ReviewScopeItem(
            target_type="block",
            target_id="block-2",
            reason_codes=["ocr_typo"],
            blocking=False,
        )
    )
    _compile_changed_task(document)
    document.conflict_groups = [
        ConflictGroup(
            conflict_id="conflict-000001",
            target_id="block-1",
            conflict_type="ocr_typo",
            blocking=True,
            routing_disposition="blocking_task",
        ),
        ConflictGroup(
            conflict_id="conflict-000002",
            target_id="block-2",
            conflict_type="optional_visual_text",
            blocking=False,
            routing_disposition="optional_task",
        ),
    ]

    result = _orchestrator(tmp_path, "verifier-partial-accept").execute(
        document,
        max_auto_review_rounds=1,
    )

    assert result.review_tasks[0].status == "auto_resolved"
    assert result.conflict_groups[0].status == "auto_resolved"
    assert result.conflict_groups[1].status == "open"
    assert result.blocks[0].text == "the metric"
    assert result.blocks[1].text == "second"


def test_child_patch_does_not_implicitly_confirm_its_parent_page(tmp_path: Path) -> None:
    document = _document(tmp_path)
    patch = AtomicPatch(
        patch_id="patch-000001",
        source_task_id="review-1",
        target_type="block",
        target_id="block-1",
        operation="replace_block_text",
        proposed_value="the metric",
        evidence_refs=[document.pages[0].page_image_path],
        confidence=0.95,
    )

    assert TransactionCoordinator.covered_targets(document, [patch]) == {
        "block-1",
    }


def test_non_contract_table_rejection_is_normalized_to_retirement_patch(tmp_path: Path) -> None:
    document = _document(tmp_path)
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=1,
        column_count=1,
        cells=[
            CellIR(
                cell_id="cell-1",
                table_id="table-1",
                page_index=0,
                row_index=0,
                col_index=0,
                text="案例",
                source_trace=SourceTrace(parser="pdfplumber"),
            )
        ],
        bbox=BoundingBox(x0=1, y0=1, x1=10, y1=10, unit="points"),
        quality_flags=["local_only_table_candidate"],
        source_trace=SourceTrace(parser="pdfplumber"),
    )
    document.tables = [table]
    document.pages[0].table_ids = [table.table_id]
    task = document.review_tasks[0]
    task.target_type = "table"
    task.target_id = table.table_id
    task.scope = [
        ReviewScopeItem(
            target_type="table",
            target_id=table.table_id,
            reason_codes=["local_only_table_candidate"],
        )
    ]

    normalized = ReviewResponseAdapter.normalize_legacy_aliases(
        document,
        {
            "verdict": "reject_as_nontable",
            "findings": "This is a decorative label, not a table.",
            "confidence": 0.97,
        },
        task=task,
    )

    assert normalized["verdict"] == "propose_patch"
    assert normalized["patches"][0]["operation"] == "retire_table_candidate"
    assert normalized["patches"][0]["proposed_value"] == {"disposition": "non_table_visual"}


def test_explicit_non_table_visual_abstention_becomes_retirement_patch(tmp_path: Path) -> None:
    document = _document(tmp_path)
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=1,
        column_count=1,
        cells=[],
        bbox=BoundingBox(x0=1, y0=1, x1=80, y1=80, unit="points"),
        quality_flags=["local_only_table_candidate"],
        source_trace=SourceTrace(parser="pdfplumber"),
    )
    document.tables = [table]
    document.pages[0].table_ids = [table.table_id]
    task = document.review_tasks[0]
    task.target_type = "table"
    task.target_id = table.table_id
    task.scope = [
        ReviewScopeItem(
            target_type="table",
            target_id=table.table_id,
            reason_codes=["local_only_table_candidate"],
        )
    ]
    _compile_changed_task(document)

    normalized = ReviewResponseAdapter().normalize_reviewer(
        document,
        {
            "verdict": "abstain",
            "findings": ["This is a treemap visualization, not a table."],
            "scope_decisions": [
                {
                    "target_type": "table",
                    "target_id": table.table_id,
                    "decision": "abstain",
                    "rationale": "The region is non-tabular chart content.",
                    "confidence": 0.96,
                }
            ],
            "patches": [],
            "confidence": 0.96,
            "abstain_reason": "Treemap visualization rather than a table.",
        },
        task,
        alias_normalizer=ReviewResponseAdapter.normalize_legacy_aliases,
    )

    assert normalized["verdict"] == "propose_patch"
    assert normalized["patches"][0]["operation"] == "retire_table_candidate"
    assert normalized["patches"][0]["proposed_value"] == {"disposition": "non_table_visual"}


def test_needs_repair_scope_decision_is_preserved_as_patch_decision(tmp_path: Path) -> None:
    document = _document(tmp_path)
    task = document.review_tasks[0]

    normalized = ReviewResponseAdapter.normalize_legacy_aliases(
        document,
        {
            "verdict": "needs_repair",
            "scope_decisions": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "decision": "needs_repair",
                    "rationale": "The OCR text needs a localized correction.",
                    "confidence": 0.96,
                }
            ],
            "patches": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "operation": "replace_block_text",
                    "proposed_value": "the metric",
                    "confidence": 0.96,
                }
            ],
            "confidence": 0.96,
        },
        task=task,
    )

    assert normalized["verdict"] == "propose_patch"
    assert normalized["scope_decisions"][0]["decision"] == "propose_patch"
    assert normalized["patches"][0]["operation"] == "replace_block_text"

    missing_patch = ReviewResponseAdapter.normalize_legacy_aliases(
        document,
        {
            "verdict": "needs_repair",
            "scope_decisions": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "decision": "needs_repair",
                }
            ],
            "patches": [],
            "confidence": 0.8,
        },
        task=task,
    )
    assert missing_patch["verdict"] == "propose_patch"
    assert missing_patch["scope_decisions"][0]["decision"] == "propose_patch"


def test_page_text_review_normalizes_ocr_and_caption_repairs_to_typed_operations(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    task = document.review_tasks[0]
    task.reason_codes = ["native_ocr_text_length_divergence"]
    task.scope = [
        ReviewScopeItem(
            target_type="page",
            target_id=document.pages[0].page_id,
            reason_codes=["native_ocr_text_length_divergence"],
        )
    ]
    figure = FigureIR(
        figure_id="figure-1",
        page_index=0,
        order=0,
        bbox=BoundingBox(x0=10, y0=20, x1=90, y1=80, unit="points"),
        source_trace=SourceTrace(parser="paddleocr-vl"),
    )
    document.figures = [figure]
    document.pages[0].figure_ids = [figure.figure_id]
    _compile_changed_task(document)

    normalized = ReviewResponseAdapter.normalize_legacy_aliases(
        document,
        {
            "verdict": "propose_patch",
            "patches": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "operation": "replace_block_text",
                    "proposed_value": "the metric",
                    "confidence": 0.97,
                },
                {
                    "target_type": "page",
                    "target_id": "page-1",
                    "operation": "add_visual_text_block",
                    "proposed_value": {
                        "text": "Visible figure caption",
                        "block_type": "figure_caption",
                        "figure_id": "figure-1",
                    },
                    "confidence": 0.95,
                },
            ],
            "confidence": 0.96,
        },
        task=task,
    )

    assert normalized["patches"][0]["operation"] == "correct_ocr_text"
    assert normalized["patches"][1]["operation"] == "set_caption"
    assert normalized["patches"][1]["target_id"] == "figure-1"
    assert normalized["patches"][1]["proposed_value"] == "Visible figure caption"


def test_page_text_review_accepts_after_value_reason_and_operation_scope_aliases(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    task = document.review_tasks[0]
    figure = FigureIR(
        figure_id="figure-1",
        page_index=0,
        order=0,
        bbox=BoundingBox(x0=10, y0=20, x1=90, y1=80, unit="points"),
        source_trace=SourceTrace(parser="paddleocr-vl"),
    )
    document.figures = [figure]
    normalized = ReviewResponseAdapter.normalize_legacy_aliases(
        document,
        {
            "verdict": "propose_patch",
            "scope_decisions": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "decision": "replace_block_text",
                    "reason": "The visible paragraph continues beyond the OCR truncation.",
                },
                {
                    "target_type": "figure",
                    "target_id": "figure-1",
                    "operation": "set_caption",
                    "reason": "The caption is visible below the figure.",
                },
            ],
            "patches": [
                {
                    "target_type": "block",
                    "target_id": "block-1",
                    "operation": "replace_block_text",
                    "after_value": "the metric",
                    "reason": "Visible corrected paragraph text.",
                },
                {
                    "target_type": "figure",
                    "target_id": "figure-1",
                    "operation": "set_caption",
                    "after_value": "Visible figure caption",
                    "visual_evidence_refs": ["page-18.png"],
                    "reason": "Visible caption text.",
                },
            ],
            "confidence": 0.96,
        },
        task=task,
    )

    assert [item["decision"] for item in normalized["scope_decisions"]] == [
        "propose_patch",
        "propose_patch",
    ]
    assert normalized["patches"][0]["proposed_value"] == "the metric"
    assert normalized["patches"][0]["rationale"] == "Visible corrected paragraph text."
    assert normalized["patches"][1]["proposed_value"] == "Visible figure caption"
    assert normalized["patches"][1]["evidence_refs"] == ["page-18.png"]


def test_spread_link_operation_is_normalized_to_formal_spread_target(tmp_path: Path) -> None:
    document = _document(tmp_path)
    trace = SourceTrace(parser="paddleocr-vl")
    document.pages.append(
        PageIR(
            page_id="page-2",
            page_index=1,
            page_number=2,
            width=100,
            height=100,
            source_trace=trace,
        )
    )
    left = TableIR(
        table_id="table-left",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=11,
        column_count=2,
        source_trace=trace,
    )
    right = TableIR(
        table_id="table-right",
        page_index=1,
        page_indices=[1],
        order=0,
        row_count=8,
        column_count=1,
        source_trace=trace,
    )
    spread = SpreadIR(
        spread_id="spread-1-2",
        page_ids=["page-1", "page-2"],
        page_indices=[0, 1],
        composite_artifact_id="artifact-spread",
        member_entity_ids=[left.table_id, right.table_id],
        source_trace=trace,
    )
    document.tables = [left, right]
    document.spreads = [spread]
    task = document.review_tasks[0]
    task.task_type = "horizontal_spread_review"
    task.target_type = "spread"
    task.target_id = spread.spread_id
    task.scope = [ReviewScopeItem(target_type="spread", target_id=spread.spread_id)]
    _compile_changed_task(document)

    normalized = ReviewResponseAdapter.normalize_legacy_aliases(
        document,
        {
            "verdict": "propose_patch",
            "patches": [
                {
                    "target_type": "table",
                    "target_id": left.table_id,
                    "operation": "link_horizontal_continuation",
                    "proposed_value": {
                        "links": [
                            {
                                "source_id": left.table_id,
                                "target_id": right.table_id,
                                "confidence": 0.96,
                            }
                        ]
                    },
                    "confidence": 0.96,
                }
            ],
            "confidence": 0.96,
        },
        task=task,
    )

    assert normalized["patches"][0]["target_type"] == "spread"
    assert normalized["patches"][0]["target_id"] == spread.spread_id
    context = ReviewContextCompiler().candidate(document, task)
    pair = context["spread_composition_diagnostics"]["table_pairs"][0]
    assert pair["left_grid"] == "11x2"
    assert pair["right_grid"] == "8x1"
    assert pair["likely_composition_mode"] == "horizontal_continue_last_column"


def test_verified_figure_correction_closes_superseded_optional_binding_task(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    figure = FigureIR(
        figure_id="figure-1",
        page_index=0,
        order=0,
        visual_type="diagram",
        visual_status="reviewed",
        bbox=BoundingBox(x0=10, y0=20, x1=90, y1=80, unit="points"),
        source_trace=SourceTrace(parser="paddleocr-vl"),
    )
    document.figures = [figure]
    optional = VlmReviewTask(
        task_id="review-optional-binding",
        task_type="figure_chart_review",
        target_type="figure",
        target_id=figure.figure_id,
        page_index=0,
        blocking=False,
        reason_codes=["material_visual_binding_unresolved"],
        prompt_intent="review figure binding",
    )
    document.review_tasks.append(optional)
    orchestrator = _orchestrator(tmp_path, "confirm")
    orchestrator.policy.attach(document, optional)
    document.atomic_patches.append(
        AtomicPatch(
            patch_id="patch-verified-figure",
            source_task_id="review-1",
            target_type="figure",
            target_id=figure.figure_id,
            operation="set_visual_type",
            proposed_value="diagram",
            status="accepted",
        )
    )

    orchestrator._resolve_superseded_optional_tasks(document, "review-1")

    assert optional.status == "auto_resolved"
    assert optional.retryable is False
    assert optional.result["strategy"] == "superseded_by_verified_patch"


def test_guard_failure_repetition_is_scoped_to_same_task_target_and_operation(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    trace = SourceTrace(parser="paddleocr-vl")
    document.blocks.append(
        BlockIR(
            block_id="block-2",
            page_index=0,
            order=1,
            block_type="paragraph",
            text="second",
            bbox=BoundingBox(x0=1, y0=30, x1=90, y1=50, unit="points"),
            source_trace=trace,
        )
    )
    patches = [
        AtomicPatch(
            patch_id=f"patch-{index:06d}",
            source_task_id=task_id,
            target_type="block",
            target_id=target_id,
            operation="replace_block_text",
            proposed_value="corrected",
        )
        for index, (task_id, target_id) in enumerate(
            (
                ("review-1", "block-1"),
                ("review-2", "block-2"),
                ("review-1", "block-1"),
            ),
            start=1,
        )
    ]
    document.atomic_patches = patches
    document.patch_transactions = [
        PatchTransactionIR(
            transaction_id=f"transaction-{index:06d}",
            task_id=patch.source_task_id,
            patch_ids=[patch.patch_id],
            target_ids=[patch.target_id, "page-1"],
            status="proposed",
        )
        for index, patch in enumerate(patches, start=1)
    ]
    convergence = ConvergenceEngine()

    def guard(index: int, task_id: str) -> GuardResult:
        return GuardResult(
            guard_result_id=f"guard-{index:06d}",
            task_id=task_id,
            transaction_id=f"transaction-{index:06d}",
            patch_ids=[f"patch-{index:06d}"],
            passed=False,
            checks=[
                GuardCheck(
                    code="required_review_targets_covered",
                    passed=False,
                    severity="blocking",
                    message="Required review target has no explicit decision.",
                )
            ],
        )

    first = convergence.classify_guard_failure(document, guard(1, "review-1"))
    document.patch_transactions[0].status = "guard_failed"
    document.patch_transactions[0].failure_fingerprint = first["fingerprint"]

    unrelated = convergence.classify_guard_failure(document, guard(2, "review-2"))
    assert unrelated["failure_class"] == "model_protocol"
    assert unrelated["retryable"] is True
    assert unrelated["fingerprint"] != first["fingerprint"]

    repeated = convergence.classify_guard_failure(document, guard(3, "review-1"))
    assert repeated["failure_class"] == "repeated_failure"
    assert repeated["retryable"] is True


def test_spread_composition_is_one_transaction_with_linked_table_repairs(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    trace = SourceTrace(parser="paddleocr-vl")
    document.pages.append(
        PageIR(
            page_id="page-2",
            page_index=1,
            page_number=2,
            width=100,
            height=100,
            source_trace=trace,
        )
    )
    left = TableIR(
        table_id="table-left",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=1,
        column_count=1,
        cells=[],
        source_trace=trace,
    )
    right = TableIR(
        table_id="table-right",
        page_index=1,
        page_indices=[1],
        order=0,
        row_count=1,
        column_count=1,
        cells=[],
        source_trace=trace,
    )
    spread = SpreadIR(
        spread_id="spread-1-2",
        page_ids=["page-1", "page-2"],
        page_indices=[0, 1],
        composite_artifact_id="artifact-spread",
        member_entity_ids=[left.table_id, right.table_id],
        source_trace=trace,
    )
    document.tables = [left, right]
    document.spreads = [spread]
    task = document.review_tasks[0]
    task.task_type = "horizontal_spread_review"
    task.target_type = "spread"
    task.target_id = spread.spread_id
    task.scope = [
        ReviewScopeItem(target_type="spread", target_id=spread.spread_id),
        ReviewScopeItem(target_type="table", target_id=left.table_id),
        ReviewScopeItem(target_type="table", target_id=right.table_id),
    ]
    patches = [
        AtomicPatch(
            patch_id="patch-000001",
            source_task_id=task.task_id,
            target_type="spread",
            target_id=spread.spread_id,
            operation="confirm_spread",
            proposed_value={"reading_direction": "left_to_right"},
        ),
        AtomicPatch(
            patch_id="patch-000002",
            source_task_id=task.task_id,
            target_type="spread",
            target_id=spread.spread_id,
            operation="link_horizontal_continuation",
            proposed_value={
                "links": [
                    {
                        "source_id": left.table_id,
                        "target_id": right.table_id,
                        "confidence": 0.97,
                    }
                ]
            },
        ),
        AtomicPatch(
            patch_id="patch-000003",
            source_task_id=task.task_id,
            target_type="table",
            target_id=left.table_id,
            operation="set_table_grid",
            proposed_value={
                "row_count": 1,
                "column_count": 1,
                "cells": [{"row_index": 0, "col_index": 0, "text": "left"}],
            },
        ),
        AtomicPatch(
            patch_id="patch-000004",
            source_task_id=task.task_id,
            target_type="table",
            target_id=right.table_id,
            operation="set_table_grid",
            proposed_value={
                "row_count": 1,
                "column_count": 1,
                "cells": [{"row_index": 0, "col_index": 0, "text": "right"}],
            },
        ),
        AtomicPatch(
            patch_id="patch-000005",
            source_task_id=task.task_id,
            target_type="block",
            target_id="block-1",
            operation="replace_block_text",
            proposed_value="the metric",
        ),
    ]
    reviewer = ReviewerResult(
        reviewer_result_id="reviewer-000001",
        task_id=task.task_id,
        model_id="reviewer",
        model_family="reviewer",
        attempt=1,
        verdict="propose_patch",
        patch_ids=[patch.patch_id for patch in patches],
        confidence=0.95,
    )

    transactions = TransactionCoordinator().plan(
        document,
        task,
        reviewer,
        patches,
        {spread.spread_id, left.table_id, right.table_id},
    )

    assert len(transactions) == 2
    composition = next(
        item
        for item in transactions
        if spread.spread_id in item.target_ids
        and left.table_id in item.target_ids
        and right.table_id in item.target_ids
    )
    assert composition.patch_ids == [
        "patch-000001",
        "patch-000002",
        "patch-000003",
        "patch-000004",
    ]
    assert next(item for item in transactions if item is not composition).patch_ids == [
        "patch-000005"
    ]


def test_figure_structure_normalizes_visible_text_and_both_bbox_spaces(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    canonical = CoordinateSystem(
        coordinate_system_id="page-0001-pdf-points",
        page_index=0,
        name="canonical_pdf_points",
        width=600,
        height=800,
        unit="points",
    )
    rendered = CoordinateSystem(
        coordinate_system_id="page-0001-render-144dpi",
        page_index=0,
        name="rendered_page_pixels",
        width=1200,
        height=1600,
        unit="pixels",
        maps_to=canonical.coordinate_system_id,
    )
    document.coordinate_systems = [canonical, rendered]
    document.pages[0].width = 600
    document.pages[0].height = 800
    document.pages[0].coordinate_system_ids = [
        canonical.coordinate_system_id,
        rendered.coordinate_system_id,
    ]
    document.artifacts.append(
        ArtifactRef(
            artifact_id="artifact-figure-crop",
            kind="figure_image",
            path=str(tmp_path / "figure.png"),
            page_index=0,
            width_pixels=400,
            height_pixels=200,
            page_pixel_bbox=BoundingBox(
                x0=200,
                y0=400,
                x1=600,
                y1=600,
                unit="pixels",
                coordinate_system_id=rendered.coordinate_system_id,
            ),
        )
    )
    figure = FigureIR(
        figure_id="figure-1",
        page_index=0,
        order=0,
        crop_artifact_id="artifact-figure-crop",
        bbox=BoundingBox(
            x0=100,
            y0=200,
            x1=300,
            y1=300,
            unit="points",
            coordinate_system_id=canonical.coordinate_system_id,
        ),
        source_trace=SourceTrace(parser="paddleocr-vl"),
    )
    document.figures = [figure]
    patch = AtomicPatch(
        patch_id="patch-figure",
        source_task_id="review-1",
        target_type="figure",
        target_id=figure.figure_id,
        operation="upsert_figure_structure",
        proposed_value={
            "elements": [
                {
                    "element_id": "normalized",
                    "visible_text": "Normalized label",
                    "bbox_space": "figure_normalized",
                    "bbox": {
                        "x0": 0.1,
                        "y0": 0.2,
                        "x1": 0.5,
                        "y1": 0.6,
                        "unit": "normalized",
                    },
                },
                {
                    "element_id": "legacy",
                    "visible_text": "Legacy crop label",
                    "bbox": {
                        "x0": 40,
                        "y0": 20,
                        "x1": 200,
                        "y1": 100,
                        "unit": "points",
                    },
                },
            ],
            "relations": [],
        },
    )

    _orchestrator(tmp_path, "confirm")._normalize_figure_structure_patch(
        document,
        patch,
    )
    normalized, legacy = patch.proposed_value["elements"]

    assert normalized["text"] == "Normalized label"
    assert normalized["coordinate_normalization"] == "figure_normalized_to_page_pdf_points"
    assert normalized["bbox"]["x0"] == 120
    assert normalized["bbox"]["y0"] == 220
    assert normalized["bbox"]["x1"] == 200
    assert normalized["bbox"]["y1"] == 260
    assert normalized["bbox"]["unit"] == "points"
    assert normalized["bbox"]["coordinate_system_id"] == canonical.coordinate_system_id
    assert legacy["text"] == "Legacy crop label"
    assert legacy["coordinate_normalization"] == "legacy_crop_pixels_to_page_pdf_points"
    assert legacy["bbox"]["x0"] == 120
    assert legacy["bbox"]["y0"] == 210
    assert legacy["bbox"]["x1"] == 200
    assert legacy["bbox"]["y1"] == 250
    assert legacy["bbox"]["unit"] == "points"
    assert legacy["bbox"]["coordinate_system_id"] == canonical.coordinate_system_id


def test_tiny_sparse_local_candidate_is_retired_before_any_model_call(tmp_path: Path) -> None:
    ModelHealthRegistry.reset()
    document = _document(tmp_path)
    trace = SourceTrace(parser="pdfplumber")
    table = TableIR(
        table_id="table-1",
        page_index=0,
        page_indices=[0],
        order=0,
        row_count=4,
        column_count=4,
        cells=[
            CellIR(
                cell_id=f"cell-{row}-{column}",
                table_id="table-1",
                page_index=0,
                row_index=row,
                col_index=column,
                text="案例" if (row, column) == (0, 0) else "",
                source_trace=trace,
            )
            for row in range(4)
            for column in range(4)
        ],
        bbox=BoundingBox(x0=1, y0=1, x1=10, y1=10, unit="points"),
        quality_flags=["local_only_table_candidate"],
        source_trace=trace,
    )
    document.tables = [table]
    document.pages[0].table_ids = [table.table_id]
    document.review_tasks[0].target_type = "table"
    document.review_tasks[0].target_id = table.table_id
    document.review_tasks[0].reason_codes = ["blank_visual_encoding_cells"]
    document.review_tasks[0].scope = [
        ReviewScopeItem(
            target_type="table",
            target_id=table.table_id,
            reason_codes=["blank_visual_encoding_cells"],
        )
    ]
    _compile_changed_task(document)

    result = _orchestrator(tmp_path, "failure").execute(document, max_auto_review_rounds=2)

    assert result.review_tasks[0].status == "auto_resolved"
    assert result.model_calls == []
    assert result.tables == []
    assert result.retired_entities[0].entity_id == table.table_id
    assert result.retired_entities[0].disposition == "decoration"
    assert result.atomic_patches[0].operation == "retire_table_candidate"
    assert result.atomic_patches[0].status == "accepted"
