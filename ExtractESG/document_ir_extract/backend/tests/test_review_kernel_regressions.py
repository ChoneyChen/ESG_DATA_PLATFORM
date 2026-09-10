from __future__ import annotations

import json
from pathlib import Path

import pytest

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    AtomicPatch,
    AtomicPatchProposal,
    BlockIR,
    BoundingBox,
    CellIR,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    LayoutObjectIR,
    PageIR,
    ReviewPlan,
    ReviewerPayload,
    ReviewerResult,
    ReviewScopeItem,
    SourceTrace,
    SpreadIR,
    TableIR,
    VlmReviewTask,
)
from esg_v2.document.convergence_engine import ConvergenceEngine
from esg_v2.document.model_runner import ModelRunner
from esg_v2.document.operation_registry import OperationRegistry
from esg_v2.document.patch_guard import PatchGuard
from esg_v2.document.review_context import ReviewContextCompiler
from esg_v2.document.review_plan_compiler import ReviewPlanCompiler, ReviewPlanContractError
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.review_response_adapter import ReviewResponseAdapter
from esg_v2.document.review_scheduler import ReviewScheduler
from esg_v2.document.transaction_coordinator import TransactionCoordinator
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.models.contracts import CloudChatResult
from esg_v2.models.model_registry import ModelProfile
from esg_v2.models.provider_rate_limit import ProviderRateLimitCoordinator
from esg_v2.models.qiniu_adapter import QiniuApiError


def _trace() -> SourceTrace:
    return SourceTrace(parser="paddleocr-vl")


def _document() -> DocumentIR:
    return DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-regression", ocr_run_id="ocr-regression", source_pdf_path="report.pdf"),
        pages=[
            PageIR(page_id=f"page-{index + 1}", page_index=index, page_number=index + 1, width=1000, height=1400, source_trace=_trace())
            for index in range(2)
        ],
    )


def _table(table_id: str, page_index: int, rows: int, columns: int) -> TableIR:
    return TableIR(
        table_id=table_id,
        page_index=page_index,
        page_indices=[page_index],
        order=0,
        row_count=rows,
        column_count=columns,
        cells=[
            CellIR(
                cell_id=f"{table_id}-r{row}-c{column}",
                table_id=table_id,
                page_index=page_index,
                row_index=row,
                col_index=column,
                text=f"cell {row} {column}",
                source_trace=_trace(),
            )
            for row in range(rows)
            for column in range(columns)
        ],
        source_trace=_trace(),
    )


def test_page_16_17_spread_plan_and_repairs_share_one_transaction() -> None:
    document = _document()
    left = _table("table-left", 0, 11, 2)
    right = _table("table-right", 1, 8, 1)
    spread = SpreadIR(
        spread_id="spread-page16-page17",
        page_ids=["page-1", "page-2"],
        page_indices=[0, 1],
        composite_artifact_id="artifact-spread-page16-page17",
        member_entity_ids=[left.table_id, right.table_id],
        source_trace=_trace(),
    )
    document.tables = [left, right]
    document.spreads = [spread]
    task = VlmReviewTask(
        task_id="review-spread",
        task_type="horizontal_spread_review",
        target_type="spread",
        target_id=spread.spread_id,
        page_index=0,
        blocking=True,
        reason_codes=["possible_horizontal_page_spread", "local_table_dimension_conflict"],
        scope=[
            ReviewScopeItem(target_type="spread", target_id=spread.spread_id),
            ReviewScopeItem(target_type="table", target_id=right.table_id),
        ],
        prompt_intent="review spread",
    )
    ReviewPlanCompiler().compile(document, task)
    assert right.table_id in task.review_plan.mutable_target_ids
    assert left.table_id in task.review_plan.mutable_target_ids
    with pytest.raises(ReviewPlanContractError):
        ReviewPlanCompiler().compile(document, task)

    reviewer = ReviewerResult(
        reviewer_result_id="reviewer-1",
        task_id=task.task_id,
        model_id="fake-vlm",
        model_family="fake",
        attempt=1,
        verdict="correct",
        confidence=0.95,
    )
    right_grid = {
        "row_count": 11,
        "column_count": 1,
        "cells": [
            {
                "row_index": row,
                "col_index": 0,
                "text": f"right {row}",
                "source_cell_ids": [right.cells[row].cell_id] if row < len(right.cells) else [],
                "visual_evidence_refs": ["spread.png"],
            }
            for row in range(11)
        ],
        "missing_text": [],
        "visual_evidence_refs": ["spread.png"],
        "repair_reason": "Restore the complete right-hand continuation column.",
    }
    patches = [
        AtomicPatch(patch_id="patch-grid", source_task_id=task.task_id, target_type="table", target_id=right.table_id, operation="set_table_grid", proposed_value=right_grid, evidence_refs=["spread.png"]),
        AtomicPatch(patch_id="patch-confirm", source_task_id=task.task_id, target_type="spread", target_id=spread.spread_id, operation="confirm_spread", proposed_value={"reading_direction": "left_to_right"}, evidence_refs=["spread.png"]),
        AtomicPatch(
            patch_id="patch-link",
            source_task_id=task.task_id,
            target_type="spread",
            target_id=spread.spread_id,
            operation="link_horizontal_continuation",
            proposed_value={"links": [{"source_id": left.table_id, "target_id": right.table_id, "confidence": 0.96}]},
            evidence_refs=["spread.png"],
        ),
    ]
    transactions = TransactionCoordinator().plan(document, task, reviewer, patches, {spread.spread_id, right.table_id})
    assert len(transactions) == 1
    assert set(transactions[0].patch_ids) == {"patch-grid", "patch-confirm", "patch-link"}


def test_spread_confirmation_compiles_required_link_for_page_targeted_repair() -> None:
    document = _document()
    left = _table("table-left", 0, 10, 2)
    right = _table("table-right", 1, 10, 1)
    left.bbox = BoundingBox(x0=500, y0=200, x1=1000, y1=1100, unit="points")
    right.bbox = BoundingBox(x0=0, y0=210, x1=600, y1=1110, unit="points")
    spread = SpreadIR(
        spread_id="spread-page16-page17",
        page_ids=["page-1", "page-2"],
        page_indices=[0, 1],
        composite_artifact_id="artifact-spread-page16-page17",
        member_entity_ids=[left.table_id, right.table_id],
        source_trace=_trace(),
    )
    document.tables = [left, right]
    document.spreads = [spread]
    task = VlmReviewTask(
        task_id="repair-page-16",
        task_type="low_confidence_region_review",
        target_type="page",
        target_id="page-1",
        page_index=0,
        prompt_intent="repair the confirmed horizontal spread",
        scope=[
            ReviewScopeItem(target_type="spread", target_id=spread.spread_id),
            ReviewScopeItem(target_type="page", target_id="page-1"),
        ],
    )
    proposals = AgentReviewOrchestrator._complete_spread_transaction(
        document,
        task,
        [
            AtomicPatchProposal(
                target_type="spread",
                target_id=spread.spread_id,
                operation="confirm_spread",
                proposed_value={"reading_direction": "left_to_right"},
            )
        ],
        confidence=0.97,
    )
    links = [item for item in proposals if item.operation == "link_horizontal_continuation"]
    assert len(links) == 1
    assert links[0].proposed_value["links"] == [
        {"source_id": left.table_id, "target_id": right.table_id, "confidence": 0.97}
    ]


def test_page_24_table_feedback_names_missing_source_cells_and_text() -> None:
    document = _document()
    table = _table("table-page24", 0, 4, 3)
    document.tables = [table]
    patch = AtomicPatch(
        patch_id="patch-page24",
        source_task_id="review-page24",
        target_type="table",
        target_id=table.table_id,
        operation="set_table_grid",
        proposed_value={
            "row_count": 4,
            "column_count": 3,
            "cells": [
                {
                    "row_index": row,
                    "col_index": column,
                    "text": ("Action Plan" if (row, column) == (3, 2) else table.cells[row * 3 + column].text),
                    "source_cell_ids": ([] if (row, column) == (3, 2) else [table.cells[row * 3 + column].cell_id]),
                    "visual_evidence_refs": ["page24.png"],
                }
                for row in range(4)
                for column in range(3)
            ],
            "missing_text": [{"text": "Action Plan", "visual_evidence_refs": ["page24.png"]}],
            "visual_evidence_refs": ["page24.png"],
            "repair_reason": "Restore the visually present Action Plan column without dropping source text.",
        },
        evidence_refs=["page24.png"],
    )
    document.atomic_patches = [patch]
    guard = PatchGuard().evaluate(document, "review-page24", [patch])
    assert guard.passed is False
    mapping_check = next(check for check in guard.checks if check.code.endswith("table_source_cell_mapping"))
    assert mapping_check.details["missing_source_cell_ids"] == [table.cells[-1].cell_id]
    classification = ConvergenceEngine().classify_guard_failure(document, guard)
    assert table.cells[-1].text in " ".join(classification["messages"])
    assert "set_table_grid" in " ".join(classification["messages"])


def test_table_contract_completion_never_maps_blank_source_cells() -> None:
    document = _document()
    table = _table("table-with-blank", 0, 1, 2)
    table.cells[1].text = ""
    document.tables = [table]
    task = VlmReviewTask(
        task_id="review-table-with-blank",
        task_type="table_structure_review",
        target_type="table",
        target_id=table.table_id,
        page_index=0,
        scope=[ReviewScopeItem(target_type="table", target_id=table.table_id)],
        prompt_intent="review table",
    )
    payload = {
        "verdict": "propose_patch",
        "scope_decisions": [
            {
                "target_type": "table",
                "target_id": table.table_id,
                "decision": "propose_patch",
                "confidence": 0.95,
            }
        ],
        "patches": [
            {
                "target_type": "table",
                "target_id": table.table_id,
                "operation": "set_table_grid",
                "proposed_value": {
                    "row_count": 1,
                    "column_count": 2,
                    "cells": [
                        {"row_index": 0, "col_index": 0, "text": table.cells[0].text},
                        {"row_index": 0, "col_index": 1, "text": ""},
                    ],
                },
                "confidence": 0.95,
            }
        ],
        "confidence": 0.95,
    }
    normalized = ReviewResponseAdapter().normalize_reviewer(
        document,
        payload,
        task,
        alias_normalizer=ReviewResponseAdapter.normalize_legacy_aliases,
    )
    cells = normalized["patches"][0]["proposed_value"]["cells"]
    assert cells[0]["source_cell_ids"] == [table.cells[0].cell_id]
    assert cells[1]["source_cell_ids"] == []


def test_spread_operation_contract_rejects_decorative_only_continuity() -> None:
    confirm = OperationRegistry.get("confirm_spread").prompt_instruction
    reject = OperationRegistry.get("reject_spread").prompt_instruction
    assert "information-bearing" in confirm
    assert "Decorative continuity" in confirm
    assert "decorative" in reject


class _OneProfileRegistry:
    def __init__(self, profile):
        self.profile = profile

    def candidates(self, role, *, exclude_family=None, limit=3):
        return [self.profile]


class _ExcludeAwareOneProfileRegistry(_OneProfileRegistry):
    def candidates(self, role, *, exclude_family=None, limit=3):
        return [] if exclude_family == self.profile.family else [self.profile]


class _LengthThenSuccessAdapter:
    def __init__(self):
        self.requests = []

    def chat_completions(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raw = {"choices": [{"finish_reason": "length", "message": {"content": '{"verdict":"confirm"'}}]}
        else:
            payload = {
                "verdict": "confirm",
                "findings": ["chart is faithfully represented"],
                "scope_decisions": [{
                    "target_type": "figure", "target_id": "figure-page89",
                    "decision": "confirm", "rationale": "visible match", "confidence": 0.96,
                }],
                "patches": [],
                "confidence": 0.96,
                "quality_flags": [],
            }
            raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}
        return CloudChatResult(request_id=request.request_id, model_id=request.model_id, raw_response=raw, latency_ms=1.0)


def test_page_89_chart_context_is_compact_and_length_retries_inside_task(tmp_path: Path) -> None:
    document = _document()
    document.pages[0].text = "very long unrelated report text " * 1000
    figure = FigureIR(
        figure_id="figure-page89",
        page_index=0,
        order=0,
        bbox=BoundingBox(x0=100, y0=100, x1=900, y1=800, unit="points"),
        crop_artifact_id="crop-page89",
        visual_type="chart",
        source_trace=_trace(),
    )
    document.figures = [figure]
    task = VlmReviewTask(
        task_id="review-page89",
        task_type="figure_chart_review",
        target_type="figure",
        target_id=figure.figure_id,
        page_index=0,
        reason_codes=["material_chart_structure"],
        scope=[ReviewScopeItem(target_type="figure", target_id=figure.figure_id)],
        prompt_intent="review chart",
    )
    ReviewPlanCompiler().compile(document, task)
    context = ReviewContextCompiler().reviewer(document, task, [])
    assert len(context) < 9000
    profile = ModelProfile(
        model_id="fake-chart-vlm",
        family="fake",
        roles=("reviewer",),
        vision=True,
        structured_output=True,
        min_output_tokens=1200,
        max_output_tokens=8000,
        transient_retries=1,
    )
    adapter = _LengthThenSuccessAdapter()
    settings = Settings(output_root=tmp_path, document_ir_output_root=tmp_path, upload_root=tmp_path)
    runner = ModelRunner(settings, adapter, _OneProfileRegistry(profile), ReviewResponseAdapter())
    payload, _, _ = runner.run(
        document,
        task,
        role="reviewer",
        round_index=1,
        images=["data:image/png;base64,AA=="],
        context=context,
        payload_type=ReviewerPayload,
        alias_normalizer=lambda document, payload, *, task: payload,
    )
    assert payload.verdict == "confirm"
    assert len(adapter.requests) == 2
    assert adapter.requests[1].max_tokens > adapter.requests[0].max_tokens
    assert [call.failure_category for call in document.model_calls] == ["request_config", "none"]


def test_reviewer_repair_falls_back_to_same_family_when_no_alternative_exists(tmp_path: Path) -> None:
    document = _document()
    figure = FigureIR(figure_id="figure-page89", page_index=0, order=0, source_trace=_trace())
    document.figures = [figure]
    task = VlmReviewTask(
        task_id="review-page89",
        task_type="figure_chart_review",
        target_type="figure",
        target_id=figure.figure_id,
        page_index=0,
        reason_codes=["material_chart_structure"],
        scope=[ReviewScopeItem(target_type="figure", target_id=figure.figure_id)],
        prompt_intent="review",
    )
    ReviewPlanCompiler().compile(document, task)
    profile = ModelProfile(
        model_id="only-reviewer",
        family="only-family",
        roles=("reviewer",),
        vision=True,
        structured_output=True,
        transient_retries=1,
    )
    adapter = _LengthThenSuccessAdapter()
    settings = Settings(output_root=tmp_path, document_ir_output_root=tmp_path, upload_root=tmp_path)
    runner = ModelRunner(
        settings,
        adapter,
        _ExcludeAwareOneProfileRegistry(profile),
        ReviewResponseAdapter(),
    )
    payload, _, selected = runner.run(
        document,
        task,
        role="reviewer",
        round_index=2,
        images=[],
        context="{}",
        payload_type=ReviewerPayload,
        alias_normalizer=lambda document, payload, *, task: payload,
        exclude_family=profile.family,
    )
    assert payload.verdict == "confirm"
    assert selected.family == profile.family


class _TransientThenSuccessAdapter(_LengthThenSuccessAdapter):
    def chat_completions(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise QiniuApiError("temporary upstream timeout", status_code=503)
        payload = {
            "verdict": "confirm", "findings": [],
            "scope_decisions": [{"target_type": "figure", "target_id": "figure-page89", "decision": "confirm", "confidence": 0.9}],
            "patches": [], "confidence": 0.9, "quality_flags": [],
        }
        raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}
        return CloudChatResult(request_id=request.request_id, model_id=request.model_id, raw_response=raw, latency_ms=1.0)


def test_transient_cloud_failure_is_consumed_inside_current_task(tmp_path: Path) -> None:
    document = _document()
    figure = FigureIR(figure_id="figure-page89", page_index=0, order=0, source_trace=_trace())
    document.figures = [figure]
    task = VlmReviewTask(
        task_id="review-page89", task_type="figure_chart_review", target_type="figure",
        target_id=figure.figure_id, page_index=0, reason_codes=["material_chart_structure"],
        scope=[ReviewScopeItem(target_type="figure", target_id=figure.figure_id)], prompt_intent="review",
    )
    ReviewPlanCompiler().compile(document, task)
    profile = ModelProfile(model_id="fake-vlm", family="fake", roles=("reviewer",), vision=True, structured_output=True)
    adapter = _TransientThenSuccessAdapter()
    settings = Settings(output_root=tmp_path, document_ir_output_root=tmp_path, upload_root=tmp_path)
    runner = ModelRunner(settings, adapter, _OneProfileRegistry(profile), ReviewResponseAdapter())
    payload, _, _ = runner.run(
        document, task, role="reviewer", round_index=1, images=[], context="{}",
        payload_type=ReviewerPayload, alias_normalizer=lambda document, payload, *, task: payload,
    )
    assert payload.verdict == "confirm"
    assert [call.failure_category for call in document.model_calls] == ["transport", "none"]


class _RpmThenSuccessAdapter(_LengthThenSuccessAdapter):
    def chat_completions(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise QiniuApiError(
                "rate limit reached for RPM",
                status_code=429,
                headers={"Retry-After": "0"},
            )
        payload = {
            "verdict": "confirm",
            "findings": [],
            "scope_decisions": [
                {
                    "target_type": "figure",
                    "target_id": "figure-page89",
                    "decision": "confirm",
                    "confidence": 0.9,
                }
            ],
            "patches": [],
            "confidence": 0.9,
            "quality_flags": [],
        }
        raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}
        return CloudChatResult(
            request_id=request.request_id,
            model_id=request.model_id,
            raw_response=raw,
            latency_ms=1.0,
        )


def test_rpm_limit_is_paced_and_retried_inside_current_task(tmp_path: Path) -> None:
    document = _document()
    figure = FigureIR(figure_id="figure-page89", page_index=0, order=0, source_trace=_trace())
    document.figures = [figure]
    task = VlmReviewTask(
        task_id="review-page89",
        task_type="figure_chart_review",
        target_type="figure",
        target_id=figure.figure_id,
        page_index=0,
        reason_codes=["material_chart_structure"],
        scope=[ReviewScopeItem(target_type="figure", target_id=figure.figure_id)],
        prompt_intent="review",
    )
    ReviewPlanCompiler().compile(document, task)
    profile = ModelProfile(
        model_id="fake-vlm",
        family="fake",
        roles=("reviewer",),
        vision=True,
        structured_output=True,
        transient_retries=1,
    )
    adapter = _RpmThenSuccessAdapter()
    settings = Settings(
        output_root=tmp_path,
        document_ir_output_root=tmp_path,
        upload_root=tmp_path,
        qiniu_min_request_interval_seconds=0,
    )
    rate_limits = ProviderRateLimitCoordinator(
        settings,
        "test-scope",
        sleep=lambda _: None,
    )
    runner = ModelRunner(
        settings,
        adapter,
        _OneProfileRegistry(profile),
        ReviewResponseAdapter(),
        rate_limits=rate_limits,
    )

    payload, _, _ = runner.run(
        document,
        task,
        role="reviewer",
        round_index=1,
        images=[],
        context="{}",
        payload_type=ReviewerPayload,
        alias_normalizer=lambda document, payload, *, task: payload,
    )

    assert payload.verdict == "confirm"
    assert [call.failure_category for call in document.model_calls] == ["rate_limit_rpm", "none"]
    assert len(adapter.requests) == 2


def test_qiniu_error_distinguishes_tpd_rpm_and_retry_after() -> None:
    tpd = QiniuApiError(
        "UID rate limit reached for TPD (domestic)",
        status_code=429,
        headers={"Retry-After": "60"},
    )
    rpm = QiniuApiError(
        "rate limit reached for RPM",
        status_code=429,
        headers={"Retry-After": "2.5"},
    )

    assert tpd.rate_limit_scope == "tpd"
    assert tpd.account_wide_rate_limit is True
    assert tpd.retryable is False
    assert tpd.retry_after_seconds == 60
    assert rpm.rate_limit_scope == "rpm"
    assert rpm.minute_rate_limit is True
    assert rpm.retryable is True
    assert rpm.retry_after_seconds == 2.5


def test_provider_tpd_circuit_is_persisted_without_storing_api_key(tmp_path: Path) -> None:
    current = [1_000.0]
    settings = Settings(
        output_root=tmp_path,
        document_ir_output_root=tmp_path,
        upload_root=tmp_path,
    )
    first = ProviderRateLimitCoordinator(
        settings,
        "api.example.test:credential-hash",
        now=lambda: current[0],
    )
    first.record_tpd("daily limit", retry_after_seconds=60, request_id="request-1")
    second = ProviderRateLimitCoordinator(
        settings,
        "api.example.test:credential-hash",
        now=lambda: current[0],
    )

    block = second.account_block()
    assert block is not None
    assert block["retry_after_seconds"] == 60
    state_text = (tmp_path / ".state" / "provider-rate-limits.json").read_text(encoding="utf-8")
    assert "credential-hash" in state_text
    assert "daily limit" in state_text


def test_completeness_mode_schedules_all_20_spreads_and_3_figures(tmp_path: Path) -> None:
    tasks = []
    for index in range(23):
        is_spread = index < 20
        kind = "horizontal_page_spread" if is_spread else "figure_semantic_structure"
        tasks.append(VlmReviewTask(
            task_id=f"review-{index:02d}",
            task_type="horizontal_spread_review" if is_spread else "figure_chart_review",
            target_type="spread" if is_spread else "figure",
            target_id=f"target-{index:02d}",
            page_index=index,
            blocking=False,
            prompt_intent="review",
            review_plan=ReviewPlan(
                review_kind=kind,
                question="review",
                current_risk="risk",
                allowed_operations=["confirm_spread"] if is_spread else ["upsert_chart_spec"],
            ),
        ))
    settings = Settings(
        output_root=tmp_path,
        document_ir_output_root=tmp_path,
        upload_root=tmp_path,
        max_vlm_reviews_per_ir_run=0,
        max_optional_vlm_reviews_per_ir_run=0,
        review_completeness_mode=True,
    )
    schedule = ReviewScheduler(settings).schedule(tasks, explicitly_targeted=False)
    assert len(schedule.scheduled_task_ids) == 23
    assert schedule.metrics["scheduler_deferred"] == 0
    assert schedule.metrics["batch_preflight_task_count"] == 23


def test_fully_resolved_complete_batch_allows_evidence_admission(tmp_path: Path) -> None:
    source_pdf = tmp_path / "report.pdf"
    page_image = tmp_path / "page.png"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    page_image.write_bytes(b"image")
    trace = _trace()
    page = PageIR(
        page_id="page-1",
        page_index=0,
        page_number=1,
        page_image_path=str(page_image),
        width=1000,
        height=1400,
        block_ids=["block-1"],
        layout_object_ids=["layout-1"],
        source_trace=trace,
    )
    block = BlockIR(
        block_id="block-1",
        page_index=0,
        order=0,
        block_type="paragraph",
        text="Sustainability report",
        bbox=BoundingBox(x0=10, y0=10, x1=500, y1=60, unit="points"),
        layout_object_id="layout-1",
        source_trace=trace,
    )
    layout = LayoutObjectIR(
        layout_object_id="layout-1",
        page_index=0,
        order=0,
        label="text",
        text=block.text,
        bbox=block.bbox,
        block_id=block.block_id,
        source_trace=trace,
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(
            run_id="ir-ready",
            ocr_run_id="ocr-ready",
            source_pdf_path=str(source_pdf),
        ),
        pages=[page],
        blocks=[block],
        layout_objects=[layout],
    )
    document.review_tasks = [
        VlmReviewTask(
            task_id=f"review-{index:02d}",
            task_type="figure_chart_review",
            target_type="page",
            target_id=page.page_id,
            page_index=0,
            blocking=False,
            status="auto_resolved",
            retryable=False,
            prompt_intent="resolved completeness item",
        )
        for index in range(23)
    ]
    DocumentIrValidator().validate(document, expected_page_count=1)
    assert document.validation_report.metrics["optional_unresolved_count"] == 0
    assert document.validation_report.checks["all_review_tasks_resolved"] is True
    assert document.validation_report.checks["can_build_evidence"] is True
