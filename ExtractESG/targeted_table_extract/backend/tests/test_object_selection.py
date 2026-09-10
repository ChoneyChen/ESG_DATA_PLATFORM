from __future__ import annotations

import pytest

from esg_targeted.contracts import (
    EvidenceInventory,
    EvidenceSpan,
    RetrievalHit,
    SourceLocator,
)
from esg_targeted.evidence.harvester import LiteralHarvester
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler

from tests.helpers import standard_dist_root


def _table_cell(
    *,
    span_id: str,
    table_id: str,
    group_id: str,
    text: str,
    row: int,
    col: int,
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=span_id,
        document_id="doc-object-rank",
        ir_run_id="ir-object-rank",
        ir_revision=1,
        span_type="table_cell",
        text=text,
        context_text=text,
        page_index=0,
        object_ids=[table_id, f"cell-{span_id}"],
        locators=[
            SourceLocator(
                ir_object_type="cell",
                ir_object_id=f"cell-{span_id}",
                pdf_page_index=0,
            )
        ],
        context_group_id=group_id,
        visual_evidence_paths=[f"/tmp/{table_id}.png"],
        has_table_structure=True,
        structural_context={
            "object_type": "table_cell",
            "table_id": table_id,
            "cell_id": f"cell-{span_id}",
            "row_index": row,
            "col_index": col,
            "column_header_path": ["值" if col else "指标"],
            "row_header_path": [],
        },
    )


def _pipeline_inputs():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E2-4_02")
    query = MetricQueryCompiler().compile(package, metric)
    elements_by_id = {item.element_id: item for item in package.elements}
    elements = [
        elements_by_id[item] for item in package.elements_by_metric[metric.metric_id]
    ]
    return package, metric, query, elements


def _hit(span_id: str, score: float) -> RetrievalHit:
    return RetrievalHit(
        span_id=span_id,
        rrf_score=score,
        structural_boost=0.0,
        final_score=score,
    )


def test_object_ranking_prefers_fillable_table_over_higher_scoring_index_table() -> None:
    _, metric, query, elements = _pipeline_inputs()
    spans = [
        _table_cell(
            span_id="index-1",
            table_id="table-index",
            group_id="index-row-1",
            text="空气污染 大气污染物 排放量 披露回应",
            row=0,
            col=0,
        ),
        _table_cell(
            span_id="index-2",
            table_id="table-index",
            group_id="index-row-2",
            text="/",
            row=1,
            col=1,
        ),
        _table_cell(
            span_id="data-label",
            table_id="table-data",
            group_id="data-row",
            text="空气污染物二氧化硫排放量",
            row=0,
            col=0,
        ),
        _table_cell(
            span_id="data-value",
            table_id="table-data",
            group_id="data-row",
            text="125.6吨",
            row=0,
            col=1,
        ),
    ]
    inventory = EvidenceInventory(
        inventory_id="inventory-object-rank",
        document_id="doc-object-rank",
        ir_run_id="ir-object-rank",
        ir_revision=1,
        spans=spans,
        candidates=LiteralHarvester().harvest(spans),
        page_images={},
        visual_artifacts={
            "table-index": "/tmp/table-index.png",
            "table-data": "/tmp/table-data.png",
        },
        stats={},
    )
    selection = EvidenceSelectionCompiler(max_retrieved_objects=1).compile(
        task_id="task-object-rank",
        metric=metric,
        elements=elements,
        query=query,
        inventory=inventory,
        hits=[
            _hit("index-1", 0.09),
            _hit("index-2", 0.08),
            _hit("data-label", 0.03),
            _hit("data-value", 0.025),
        ],
        retrieval_complete=True,
    )

    assert selection.budget["selected_retrieval_objects"][0]["id"] == "table-data"
    assert selection.allowed_group_ids == ["data-row"]


def test_object_scope_excludes_other_medium_and_intensity_rows() -> None:
    _, metric, query, elements = _pipeline_inputs()
    rows = [
        ("air", "空气污染物二氧化硫排放量", "125.6吨"),
        ("water", "废水排放化学需氧量 COD", "200吨"),
        ("intensity", "空气污染物氮氧化物排放强度", "0.2吨/亿元营收"),
    ]
    spans = []
    hits = []
    for row_index, (name, label, value) in enumerate(rows):
        group_id = f"row-{name}"
        label_span = _table_cell(
            span_id=f"{name}-label",
            table_id="table-mixed",
            group_id=group_id,
            text=label,
            row=row_index,
            col=0,
        )
        value_span = _table_cell(
            span_id=f"{name}-value",
            table_id="table-mixed",
            group_id=group_id,
            text=value,
            row=row_index,
            col=1,
        )
        spans.extend([label_span, value_span])
        hits.extend([_hit(label_span.span_id, 0.05), _hit(value_span.span_id, 0.04)])
    inventory = EvidenceInventory(
        inventory_id="inventory-object-scope",
        document_id="doc-object-rank",
        ir_run_id="ir-object-rank",
        ir_revision=1,
        spans=spans,
        candidates=LiteralHarvester().harvest(spans),
        page_images={},
        visual_artifacts={"table-mixed": "/tmp/table-mixed.png"},
        stats={},
    )
    selection = EvidenceSelectionCompiler(max_retrieved_objects=1).compile(
        task_id="task-object-scope",
        metric=metric,
        elements=elements,
        query=query,
        inventory=inventory,
        hits=hits,
        retrieval_complete=True,
    )

    assert selection.allowed_group_ids == ["row-air"]
    selected = selection.budget["selected_retrieval_objects"][0]
    assert selected["excluded_group_count"] == 2


def test_air_scope_excludes_water_pollutant_row_without_broad_water_word() -> None:
    _, metric, query, elements = _pipeline_inputs()
    spans = [
        _table_cell(
            span_id="air-label",
            table_id="table-mixed",
            group_id="row-air",
            text="二氧化硫",
            row=0,
            col=0,
        ),
        _table_cell(
            span_id="air-value",
            table_id="table-mixed",
            group_id="row-air",
            text="125.6吨",
            row=0,
            col=1,
        ),
        _table_cell(
            span_id="cod-label",
            table_id="table-mixed",
            group_id="row-cod",
            text="COD",
            row=1,
            col=0,
        ),
        _table_cell(
            span_id="cod-value",
            table_id="table-mixed",
            group_id="row-cod",
            text="294.06吨",
            row=1,
            col=1,
        ),
    ]
    inventory = EvidenceInventory(
        inventory_id="inventory-medium-descendant-scope",
        document_id="doc-object-rank",
        ir_run_id="ir-object-rank",
        ir_revision=1,
        spans=spans,
        candidates=LiteralHarvester().harvest(spans),
        page_images={},
        visual_artifacts={"table-mixed": "/tmp/table-mixed.png"},
        stats={},
    )
    selection = EvidenceSelectionCompiler(max_retrieved_objects=1).compile(
        task_id="task-medium-descendant-scope",
        metric=metric,
        elements=elements,
        query=query,
        inventory=inventory,
        hits=[_hit(item.span_id, 0.05) for item in spans],
        retrieval_complete=True,
    )

    assert selection.allowed_group_ids == ["row-air"]
    assert "COD" in selection.model_context
    assert "excluded_scope_terms" in selection.model_context


@pytest.mark.parametrize("label,value", [
    ("环境计量指标", "125.6吨"),
    ("空气污染物排放量", "125.6"),  # Ranked object, but unit typography is absent.
])
def test_selection_uses_bounded_semantic_review_when_literal_topic_is_missing(label, value) -> None:
    _, metric, query, elements = _pipeline_inputs()
    spans = [
        _table_cell(
            span_id="semantic-label",
            table_id="table-semantic",
            group_id="semantic-row",
            text=label,
            row=0,
            col=0,
        ),
        _table_cell(
            span_id="semantic-value",
            table_id="table-semantic",
            group_id="semantic-row",
            text=value,
            row=0,
            col=1,
        ),
    ]
    inventory = EvidenceInventory(
        inventory_id="inventory-semantic-review",
        document_id="doc-object-rank",
        ir_run_id="ir-object-rank",
        ir_revision=1,
        spans=spans,
        candidates=LiteralHarvester().harvest(spans),
        page_images={},
        visual_artifacts={"table-semantic": "/tmp/table-semantic.png"},
        stats={},
    )
    hits = [
        RetrievalHit(
            span_id=item.span_id,
            semantic_rank=index,
            semantic_score=0.75 - index * 0.01,
            rrf_score=0.02,
            structural_boost=0.0,
            final_score=0.02,
        )
        for index, item in enumerate(spans, 1)
    ]

    selection = EvidenceSelectionCompiler(max_retrieved_objects=1).compile(
        task_id="task-semantic-review",
        metric=metric,
        elements=elements,
        query=query,
        inventory=inventory,
        hits=hits,
        retrieval_complete=True,
    )

    assert selection.allowed_group_ids == ["semantic-row"]
    assert selection.budget["semantic_model_review_fallback"] is True
    assert selection.budget["selected_retrieval_objects"][0]["scope_reasons"] == [
        "semantic_model_review_fallback"
    ]
