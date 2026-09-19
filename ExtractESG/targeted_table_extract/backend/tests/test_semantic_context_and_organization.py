import json
from copy import deepcopy

from esg_targeted.evidence.context import EvidenceContextCompiler
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.target_cells import target_cell_contracts
from esg_targeted.models.response_adapter import DirectFillResponseAdapter
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.contracts import (
    GuardResult,
    SemanticAssignment,
    SemanticDecision,
    SemanticFactGroup,
)
from esg_targeted.results.materializer import CoreResultMaterializer
from esg_targeted.results.organization import FactOrganizer
from esg_targeted.results.semantic_conflicts import SemanticConflictAuditor
from esg_targeted.results.validation import ResultContractValidator
from esg_targeted.standards.catalog import StandardPackageCatalog
from tests.helpers import standard_dist_root
from tests.test_inventory_and_guard import build_pipeline, complete_row


def test_notes_are_linked_without_becoming_quantity_targets(tmp_path):
    inventory, packet, _, package, _ = build_pipeline(tmp_path)
    original = next(s for s in inventory.spans if s.span_type == "block")
    note = original.model_copy(update={"span_id":"method-note", "context_group_id":"block:method-note", "page_index":2,
                                       "text":"注：空气污染物核算使用报告边界内运营单位，采用排放因子方法。"})
    inventory = inventory.model_copy(update={"spans":[*inventory.spans, note]})
    enriched = EvidenceContextCompiler().attach(packet, inventory, package)
    assert "method-note" in enriched.context_only_span_ids
    assert enriched.allowed_group_ids == packet.allowed_group_ids
    regions = EvidenceRegionCompiler(max_context_chars=24000, max_groups_per_region=8, max_regions=32, max_output_rows=8, max_span_chars=900).split(enriched)
    assert all(any(c["text"] == note.text for c in json.loads(r.model_context)["linked_context"]) for r in regions)
    assert all("method-note" != t.span_id for r in regions for t in target_cell_contracts(r))


def test_uncertain_grounded_measurement_is_retained_but_not_relabelled(tmp_path):
    _, _, regions, package, metric = build_pipeline(tmp_path)
    packet = next(r for r in regions if target_cell_contracts(r))
    row = complete_row(packet, "氮氧化物", "125.6吨", "2024年", "中国境内运营")
    row.update(metric_match="uncertain", interpretation_note="方法未明，不能按请求标签推定。", context_refs=[])
    decision, _ = DirectFillResponseAdapter().parse(json.dumps({"task_id":packet.task_id, "status":"partial", "rows":[row], "uncertainty_code":"scope_ambiguous", "skipped_targets":[], "missing_context":["核算方法"]}), packet=packet)
    assert decision.fact_groups[0].metric_match == "uncertain"
    guard = GroundingGuard().validate(packet, decision)
    assert guard.accepted, guard.feedback
    result = CoreResultMaterializer().materialize(package=package, metric=metric, packet=packet, decision=decision, guard=guard, attempts=1)
    assert result.quantitative_observations == []
    assert result.outcome.status == "partial"
    assert decision.fact_groups[0].interpretation_note


def test_organization_links_units_preserves_identity_and_every_raw_record():
    package = StandardPackageCatalog(standard_dist_root()).load("esrs.2023-set1.e1-5", "1.2.0")
    metric = "esrs.2023-set1.e1-5.dp12"
    entity = next(e.element_id for e in package.elements if e.metric_id == metric and e.element_code == "reporting_entity")
    def fact(fid, value, unit, year="2024年"):
        return {"observation_id":fid, "metric_id":metric, "task_id":"t", "value_raw":value, "unit_raw":unit,
                "unit_id":"extractesg.core.unit." + {"GWh":"gigawatt-hour","TJ":"terajoule"}[unit], "reporting_period_raw":year}
    facts = [fact("a","10","GWh"), fact("b","36","TJ"), fact("c","36","TJ"), fact("d","36","TJ","2023年")]
    dimensions = [{"parent_record_id":f["observation_id"],"element_id":entity,"value_raw":"紫金" if f["observation_id"] != "c" else "藏格"} for f in facts]
    records = {"quantitative_observations":facts,"dimension_values":dimensions}
    before = deepcopy(records)
    ordered, view = FactOrganizer().organize(records, package)
    assert records == before
    assert len(ordered["quantitative_observations"]) == 4
    assert view["logical_measurement_count"] == 3
    assert view["facts"]["a"]["logical_measurement_id"] == view["facts"]["b"]["logical_measurement_id"]
    assert view["facts"]["c"]["logical_measurement_id"] != view["facts"]["b"]["logical_measurement_id"]
    assert view == FactOrganizer().organize(ordered, package)[1]


def test_display_readiness_does_not_reject_alternative_unit_presentations():
    facts = [
        {"observation_id":"a", "reporting_period_raw":"2024年"},
        {"observation_id":"b", "reporting_period_raw":"2024年"},
    ]
    dimensions = [
        {"parent_record_id":fid, "element_id":"entity", "value_raw":"紫金矿业"}
        for fid in ("a", "b")
    ]

    assert CoreResultMaterializer._display_readiness(
        quantitative=facts,
        qualitative=[],
        dimensions=dimensions,
        fixed_element_ids={"fixed-scope"},
    ) == "ready"


def test_display_readiness_ignores_package_fixed_scope_as_row_identity():
    facts = [
        {"observation_id":"a", "reporting_period_raw":"2024年"},
        {"observation_id":"b", "reporting_period_raw":"2024年"},
    ]
    dimensions = [
        {"parent_record_id":fid, "element_id":"fixed-scope", "value_code":"scope1"}
        for fid in ("a", "b")
    ]

    assert CoreResultMaterializer._display_readiness(
        quantitative=facts,
        qualitative=[],
        dimensions=dimensions,
        fixed_element_ids={"fixed-scope"},
    ) == "needs_semantic_completion"


def test_retired_versions_remain_loadable_but_not_selectable():
    catalog = StandardPackageCatalog(standard_dist_root())
    assert catalog.load("esrs.2023-set1.e1-6", "1.0.0").manifest.package_version == "1.0.0"
    assert {p.package_version for p in catalog.list() if p.package_id.endswith("e1-6")} == {"1.4.0"}


def _scope3_structure_decision(packet, category_element_id, statement_element_id=None):
    source_spans = packet.spans[:2]
    groups = []
    for index, (span, category) in enumerate(
        zip(source_spans, ["类别1：外购商品与服务", "类别3：燃料和能源相关活动"]),
        1,
    ):
        groups.append(
            SemanticFactGroup(
                group_ref_id=f"scope3-row-{index}",
                assignments=[
                    SemanticAssignment(
                        element_id=category_element_id,
                        source_ref_id=span.span_id,
                        evidence_span_ids=[span.span_id],
                        value_raw=category,
                        source_mode="span",
                        confidence=0.95,
                    ),
                    *(
                        [SemanticAssignment(
                            element_id=statement_element_id,
                            source_ref_id=span.span_id,
                            evidence_span_ids=[span.span_id],
                            value_raw=category,
                            source_mode="span",
                            confidence=0.95,
                        )]
                        if statement_element_id else []
                    ),
                ],
            )
        )
    return SemanticDecision(
        task_id=packet.task_id,
        status="found",
        fact_groups=groups,
        selected_evidence_span_ids=[item.span_id for item in source_spans],
        uncertainty_code="none",
    )


def _accepted_guard(packet, decision):
    return GuardResult(
        task_id=packet.task_id,
        accepted=True,
        recoverable=False,
        issues=[],
        accepted_group_count=len(decision.fact_groups),
        accepted_assignment_count=sum(len(item.assignments) for item in decision.fact_groups),
        feedback="accepted fixture",
    )


def test_legacy_task_dimensions_have_unique_ids_and_single_fixed_value(tmp_path):
    _, packet, _, _, _ = build_pipeline(tmp_path)
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.2.0"
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E1-6_04")
    category = next(
        item for item in package.elements
        if item.metric_id == metric.metric_id and item.element_code == "scope3_category"
    )
    decision = _scope3_structure_decision(packet, category.element_id)
    result = CoreResultMaterializer().materialize(
        package=package,
        metric=metric,
        packet=packet,
        decision=decision,
        guard=_accepted_guard(packet, decision),
        attempts=1,
    )

    categories = [
        item for item in result.dimension_values
        if item["element_id"].endswith("element.scope3_category")
    ]
    classifications = [
        item for item in result.dimension_values
        if item["element_id"].endswith("element.scope3_classification")
    ]
    assert [item["sequence"] for item in categories] == [1, 2]
    assert len({item["dimension_value_id"] for item in categories}) == 2
    assert len(classifications) == 1
    ResultContractValidator(package.core).validate(
        {
            "reporting_tasks": result.reporting_tasks,
            "quantitative_observations": result.quantitative_observations,
            "qualitative_assertions": result.qualitative_assertions,
            "attribute_values": result.attribute_values,
            "dimension_values": result.dimension_values,
            "evidence_references": result.evidence_references,
        }
    )


def test_e1_6_v1_4_materializes_each_scope3_member_as_evidenced_assertion(tmp_path):
    _, packet, _, _, _ = build_pipeline(tmp_path)
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.4.0"
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E1-6_04")
    category = next(
        item for item in package.elements
        if item.metric_id == metric.metric_id and item.element_code == "scope3_category"
    )
    statement = next(
        item for item in package.elements
        if item.metric_id == metric.metric_id and item.element_code == "statement"
    )
    decision = _scope3_structure_decision(
        packet, category.element_id, statement.element_id
    )
    result = CoreResultMaterializer().materialize(
        package=package,
        metric=metric,
        packet=packet,
        decision=decision,
        guard=_accepted_guard(packet, decision),
        attempts=1,
    )
    records = {
        "reporting_tasks": result.reporting_tasks,
        "quantitative_observations": result.quantitative_observations,
        "qualitative_assertions": result.qualitative_assertions,
        "attribute_values": result.attribute_values,
        "dimension_values": result.dimension_values,
        "evidence_references": result.evidence_references,
    }

    assert len(result.qualitative_assertions) == 2
    assert all(item["statement_raw"] for item in result.qualitative_assertions)
    assert {
        item["parent_record_id"] for item in result.dimension_values
        if item["element_id"].endswith("element.scope3_category")
    } == {item["assertion_id"] for item in result.qualitative_assertions}
    assert len(result.evidence_references) >= 2
    ResultContractValidator(package.core).validate(records)


def test_qualitative_period_without_statement_is_not_materialized_as_assertion(tmp_path):
    _, packet, _, _, _ = build_pipeline(tmp_path)
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.4.0"
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E1-6_14")
    period = next(
        item for item in package.elements
        if item.metric_id == metric.metric_id and item.element_code == "reporting_period"
    )
    span = packet.spans[0]
    decision = SemanticDecision(
        task_id=packet.task_id,
        status="found",
        fact_groups=[SemanticFactGroup(
            group_ref_id=span.context_group_id,
            assignments=[SemanticAssignment(
                element_id=period.element_id,
                source_ref_id=span.span_id,
                evidence_span_ids=[span.span_id],
                value_raw=span.text,
                source_mode="span",
            )],
        )],
    )
    result = CoreResultMaterializer().materialize(
        package=package, metric=metric, packet=packet, decision=decision,
        guard=_accepted_guard(packet, decision), attempts=1,
    )
    assert result.qualitative_assertions == []
    assert result.outcome.status == "partial"
    assert result.outcome.semantic_decision_status == "unresolved"


def test_same_source_cannot_silently_be_both_location_and_market_based():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.4.0"
    )
    observations = [
        {"observation_id": "obs-location", "metric_id": "esrs.2023-set1.e1-6.dp09", "value_raw": "100", "unit_raw": "tCO2e"},
        {"observation_id": "obs-market", "metric_id": "esrs.2023-set1.e1-6.dp10", "value_raw": "100", "unit_raw": "tCO2e"},
    ]
    evidence = [
        {"source_record_id": fact["observation_id"], "evidence_role": "primary", "document_id": "doc", "ir_run_id": "ir", "ir_revision": 1, "pdf_page_index": 5, "ir_object_id": "cell-scope2"}
        for fact in observations
    ]
    conflicts = SemanticConflictAuditor().audit(
        {"quantitative_observations": observations, "evidence_references": evidence},
        package,
    )
    assert len(conflicts) == 1
    assert conflicts[0]["requires_joint_readjudication"] is True
    assert conflicts[0]["incompatible_dimensions"]["esrs.2023-set1.e1-6.dimension.scope2-method"] == ["location_based", "market_based"]
