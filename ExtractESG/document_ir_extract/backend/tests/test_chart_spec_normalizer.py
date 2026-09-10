from __future__ import annotations

from esg_v2.document.chart_spec_normalizer import ChartSpecNormalizer
from esg_v2.document.contracts import (
    ArtifactRef,
    AtomicPatch,
    BlockIR,
    ChartSpec,
    DocumentIR,
    DocumentIRMetadata,
    FigureIR,
    PatchTransactionIR,
    SourceTrace,
    VlmReviewTask,
)
from esg_v2.document.convergence_engine import ConvergenceEngine
from esg_v2.document.patch_guard import PatchGuard
from esg_v2.document.review_response_adapter import ReviewResponseAdapter


CROP = "artifacts/crops/figures/figure-p0087-0003.png"
PAGE_IMAGE = "artifacts/page-images/page-0087.png"


def _document() -> DocumentIR:
    figure = FigureIR(
        figure_id="figure-p0087-0003",
        page_index=86,
        order=0,
        crop_artifact_id="artifact-crop-figure-p0087-0003",
        visual_type="chart",
        source_trace=SourceTrace(parser="paddleocr-vl"),
    )
    return DocumentIR(
        metadata=DocumentIRMetadata(run_id="ir-chart", ocr_run_id="ocr-chart"),
        figures=[figure],
        artifacts=[
            ArtifactRef(artifact_id="artifact-crop-figure-p0087-0003", kind="region_crop", path=CROP),
            ArtifactRef(artifact_id="artifact-page-image-p0087", kind="page_image", path=PAGE_IMAGE),
        ],
    )


def _point_values(chart: ChartSpec) -> list[float | str | None]:
    return [point.value for series in chart.series for point in series.points]


def test_normalizes_object_categories_donut_without_losing_values() -> None:
    raw = {
        "type": "donut",
        "title": "Age distribution",
        "categories": [
            {"label": "Under 30", "value": 50128, "percentage": 19.4},
            {"label": "30-50", "value": 197327, "percentage": 76.2},
            {"label": "Over 50", "value": 11351, "percentage": 4.4},
        ],
        "total_value": 258806,
        "unit": "people",
        "evidence_refs": ["figure-p0087-0003"],
    }

    normalized = ChartSpecNormalizer.normalize(raw, evidence_refs=[CROP, PAGE_IMAGE])
    chart = ChartSpec.model_validate(normalized)

    assert chart.chart_type == "donut"
    assert [series.name for series in chart.series] == ["value", "percentage"]
    assert _point_values(chart) == [50128.0, 197327.0, 11351.0, 19.4, 76.2, 4.4]
    assert chart.visual_evidence_refs == [CROP, PAGE_IMAGE]
    assert "total_value=258806" in chart.notes


def test_normalizes_x_categories_and_series_data_or_values() -> None:
    raw = {
        "chart_type": "stacked_bar_chart",
        "title": "Physical risk exposure",
        "x_categories": ["2040 low", "2040 high", "2060 low", "2060 high"],
        "series": [
            {"name": "minimal", "values": [61, 50, 54, 50]},
            {"name": "minor", "data": [32, 32, 36, 21]},
            {"name": "material", "data": [7, 18, 10, 29]},
        ],
        "y_max": 100,
        "y_unit": "%",
    }

    chart = ChartSpec.model_validate(
        ChartSpecNormalizer.normalize(raw, evidence_refs=[CROP])
    )

    assert chart.chart_type == "bar"
    assert chart.categories == raw["x_categories"]
    assert all(series.unit == "%" for series in chart.series)
    assert [len(series.points) for series in chart.series] == [4, 4, 4]
    assert _point_values(chart) == [61.0, 50.0, 54.0, 50.0, 32.0, 32.0, 36.0, 21.0, 7.0, 18.0, 10.0, 29.0]
    assert "source_chart_type=stacked_bar_chart" in chart.notes
    assert "layout=stacked" in chart.notes


def test_normalizes_independent_group_values_without_forcing_shared_categories() -> None:
    raw = {
        "title": "Board diversity",
        "categories": [
            {"label": "Under 50", "value": 1},
            {"label": "51-55", "value": 6},
            {"label": "56-60", "value": 1},
            {"label": "61+", "value": 7},
        ],
        "series": [
            {
                "name": "tenure",
                "values": [
                    {"label": "Under 2 years", "value": 5},
                    {"label": "2-9 years", "value": 6},
                    {"label": "10+ years", "value": 4},
                ],
            },
            {
                "name": "director type",
                "values": [
                    {"label": "executive", "value": 5},
                    {"label": "non-executive", "value": 4},
                    {"label": "independent", "value": 6},
                ],
            },
            {"name": "gender", "values": [{"label": "women", "value": 3}, {"label": "men", "value": 12}]},
        ],
    }

    chart = ChartSpec.model_validate(
        ChartSpecNormalizer.normalize(raw, evidence_refs=[CROP])
    )

    assert chart.chart_type == "other"
    assert [series.name for series in chart.series] == [
        "categories", "tenure", "director type", "gender",
    ]
    assert [len(series.points) for series in chart.series] == [4, 3, 3, 2]
    assert len(chart.categories) == 12


def test_normalizes_quadrant_series_objects_nested_under_categories() -> None:
    raw = {
        "chart_type": "quadrant_chart",
        "categories": [
            {
                "name": "流域风险",
                "unit": None,
                "points": [
                    {"category": "低风险", "value": 1, "evidence_refs": []},
                    {"category": "流域风险", "value": 3, "evidence_refs": []},
                    {"category": "高风险", "value": 5, "evidence_refs": []},
                ],
            },
            {
                "name": "运营风险",
                "unit": None,
                "points": [
                    {"category": "低风险", "value": 1, "evidence_refs": []},
                    {"category": "高风险", "value": 5, "evidence_refs": []},
                ],
            },
        ],
        "visual_evidence_refs": ["figure-p0087-0003"],
    }

    normalized = ChartSpecNormalizer.normalize(raw, evidence_refs=[CROP, PAGE_IMAGE])
    chart = ChartSpec.model_validate(normalized)

    assert chart.chart_type == "other"
    assert chart.categories == ["低风险", "流域风险", "高风险"]
    assert [series.name for series in chart.series] == ["流域风险", "运营风险"]
    assert _point_values(chart) == [1.0, 3.0, 5.0, 1.0, 5.0]
    assert "source_chart_type=quadrant_chart" in chart.notes
    assert chart.visual_evidence_refs == [CROP, PAGE_IMAGE]


def test_adapter_drops_ambiguous_figure_binding_and_empty_caption_without_crashing() -> None:
    document = _document()
    figure = document.figures[0]
    document.blocks.extend(
        [
            BlockIR(
                block_id="block-a",
                page_index=figure.page_index,
                order=0,
                block_type="paragraph",
                text="Above",
                source_trace=SourceTrace(parser="paddleocr-vl"),
            ),
            BlockIR(
                block_id="block-b",
                page_index=figure.page_index,
                order=1,
                block_type="paragraph",
                text="Below",
                source_trace=SourceTrace(parser="paddleocr-vl"),
            ),
        ]
    )
    task = VlmReviewTask(
        task_id="review-binding",
        task_type="figure_chart_review",
        target_type="figure",
        target_id=figure.figure_id,
        page_index=figure.page_index,
        prompt_intent="Review figure binding",
        input_refs=[CROP, PAGE_IMAGE],
    )
    raw = {
        "verdict": "confirm",
        "findings": ["complete figure"],
        "scope_decisions": [{
            "target_type": "figure",
            "target_id": figure.figure_id,
            "decision": "confirm",
            "confidence": 0.95,
        }],
        "patches": [
            {
                "target_type": "figure",
                "target_id": figure.figure_id,
                "operation": "bind_block_to_figure",
                "proposed_value": None,
                "evidence_refs": ["block-a", "block-b"],
                "confidence": 0.9,
            },
            {
                "target_type": "figure",
                "target_id": figure.figure_id,
                "operation": "set_caption",
                "proposed_value": None,
                "evidence_refs": [],
                "confidence": 0.9,
            },
            {
                "target_type": "figure",
                "target_id": figure.figure_id,
                "operation": "set_figure_legend_text",
                "proposed_value": ["A", "B"],
                "evidence_refs": [],
                "confidence": 0.95,
            },
        ],
        "confidence": 0.95,
    }

    normalized = ReviewResponseAdapter().normalize_reviewer(
        document,
        raw,
        task,
        alias_normalizer=ReviewResponseAdapter.normalize_legacy_aliases,
    )

    assert [patch["operation"] for patch in normalized["patches"]] == [
        "set_figure_legend_text"
    ]
    assert "invalid_figure_binding_patch_dropped" in normalized["quality_flags"]
    assert "empty_caption_patch_dropped" in normalized["quality_flags"]
    invalid_patch = AtomicPatch(
        patch_id="patch-invalid-binding",
        source_task_id=task.task_id,
        target_type="figure",
        target_id=figure.figure_id,
        operation="bind_block_to_figure",
    )
    assert PatchGuard._current_value(invalid_patch, figure) == {
        "figure_id": figure.figure_id,
        "visual_role": None,
    }


def test_response_adapter_normalizes_chart_before_payload_validation() -> None:
    document = _document()
    task = VlmReviewTask(
        task_id="review-chart",
        task_type="figure_chart_review",
        target_type="figure",
        target_id="figure-p0087-0003",
        page_index=86,
        prompt_intent="Review material chart structure",
        input_refs=[CROP, PAGE_IMAGE, "external-local://page-0087.md"],
    )
    raw = {
        "verdict": "confirm",
        "findings": ["visible chart values"],
        "scope_decisions": [{
            "target_type": "figure",
            "target_id": task.target_id,
            "decision": "confirm",
            "confidence": 0.95,
        }],
        "patches": [{
            "target_type": "figure",
            "target_id": task.target_id,
            "operation": "upsert_chart_spec",
            "proposed_value": {
                "type": "donut",
                "categories": [{"label": "A", "value": 2}, {"label": "B", "value": 3}],
            },
            "evidence_refs": [task.target_id],
            "confidence": 0.95,
        }],
        "confidence": 0.95,
        "quality_flags": [],
    }

    normalized = ReviewResponseAdapter().normalize_reviewer(
        document,
        raw,
        task,
        alias_normalizer=ReviewResponseAdapter.normalize_legacy_aliases,
    )

    assert normalized["verdict"] == "propose_patch"
    assert "chart_spec_aliases_normalized" in normalized["quality_flags"]
    chart = ChartSpec.model_validate(normalized["patches"][0]["proposed_value"])
    assert chart.visual_evidence_refs == [CROP, PAGE_IMAGE]
    assert _point_values(chart) == [2.0, 3.0]


def test_guard_keeps_resolved_evidence_visible_when_chart_schema_is_invalid() -> None:
    document = _document()
    figure = document.figures[0]
    patch = AtomicPatch(
        patch_id="patch-000001",
        source_task_id="review-chart",
        transaction_id="transaction-000001",
        target_type="figure",
        target_id=figure.figure_id,
        operation="upsert_chart_spec",
        before_value=figure.model_dump(mode="json"),
        proposed_value={
            "type": "donut",
            "categories": [{"label": "A", "value": 2}],
        },
        evidence_refs=[CROP],
    )
    transaction = PatchTransactionIR(
        transaction_id="transaction-000001",
        task_id="review-chart",
        patch_ids=[patch.patch_id],
        target_ids=[figure.figure_id],
        required_target_ids=[figure.figure_id],
    )
    document.atomic_patches.append(patch)
    document.patch_transactions.append(transaction)

    guard = PatchGuard().evaluate(
        document,
        "review-chart",
        [patch],
        allowed_target_ids={figure.figure_id},
        required_target_ids={figure.figure_id},
        allowed_operations={"upsert_chart_spec"},
        transaction_id=transaction.transaction_id,
    )
    checks = {check.code: check for check in guard.checks}

    assert checks["patch-000001:chart_spec_schema"].passed is False
    assert checks["patch-000001:chart_spec_schema"].details["adapter_contract_mismatch"] is True
    assert checks["patch-000001:chart_spec_evidence"].passed is True
    failure = ConvergenceEngine().classify_guard_failure(document, guard)
    assert failure["failure_class"] == "system_contract"
    assert failure["failure_owner"] == "system"
    assert failure["retryable"] is False


def test_terminal_repeated_failure_fingerprint_keeps_task_and_target_identity() -> None:
    transaction_a = PatchTransactionIR(
        transaction_id="transaction-000001",
        task_id="review-a",
        target_ids=["figure-a"],
    )
    transaction_b = PatchTransactionIR(
        transaction_id="transaction-000002",
        task_id="review-b",
        target_ids=["figure-b"],
    )
    patch_a = AtomicPatch(
        patch_id="patch-000001",
        source_task_id="review-a",
        target_type="figure",
        target_id="figure-a",
        operation="upsert_chart_spec",
    )
    patch_b = AtomicPatch(
        patch_id="patch-000002",
        source_task_id="review-b",
        target_type="figure",
        target_id="figure-b",
        operation="upsert_chart_spec",
    )
    feedback = ["ChartSpec requires at least one series and one visible data point."]

    fingerprint_a = ConvergenceEngine.terminal_repeated_failure_fingerprint(
        "review-a", [transaction_a], [patch_a], feedback
    )
    fingerprint_b = ConvergenceEngine.terminal_repeated_failure_fingerprint(
        "review-b", [transaction_b], [patch_b], feedback
    )

    assert fingerprint_a != fingerprint_b
