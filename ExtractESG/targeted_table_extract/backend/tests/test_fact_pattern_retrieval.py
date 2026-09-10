from __future__ import annotations

import pytest

from esg_targeted.contracts import (
    EvidenceInventory,
    EvidenceSpan,
    RetrievalHit,
    SourceLocator,
)
from esg_targeted.evidence.harvester import LiteralHarvester
from esg_targeted.evidence.units import canonical_unit_id
from esg_targeted.retrieval.fact_pattern import contains_term, evaluate_fact_pattern
from esg_targeted.retrieval.hybrid import HybridRetriever
from esg_targeted.retrieval.sufficiency import EvidenceSufficiencyGate
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler

from tests.helpers import standard_dist_root


@pytest.mark.parametrize("label", ["範圍一", "范畴壹", "SCOPE 1", "Scope I", "Scope Ⅰ"])
def test_scope_number_variants_match_without_extra_wording(label) -> None:
    term = "scope1" if label.casefold().startswith("scope") else "范围1"
    assert contains_term(label, term)


def test_combined_scope_exposes_both_scopes_for_exclusion() -> None:
    assert contains_term("SCOPE1+2 合计", "scope2")
    assert contains_term("範圍1+2", "范围2")
    assert contains_term("Scope II", "scope2")
    assert not contains_term("Scope III", "scope1")


@pytest.mark.parametrize("text,unit", [
    ("34 kWh/m²", "kWh/m²"),
    ("0.2 tCO2e/GWh", "tCO2e/GWh"),
    ("0.4 百万 $tCO_{{2}}e$/GWh", "百万 $tCO_{{2}}e$/GWh"),
])
def test_harvester_retains_complete_physical_intensity_unit(text, unit) -> None:
    candidates = LiteralHarvester().harvest([_span(text, "physical-intensity")])
    quantities = [item for item in candidates if item.candidate_type == "quantity"]
    assert len(quantities) == 1
    assert quantities[0].unit_raw == unit
    assert canonical_unit_id(unit) is None  # Never mislabel as a pure energy/mass unit.


def _query(source_datapoint_id: str):
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    metric = next(
        item for item in package.metrics if item.source_datapoint_id == source_datapoint_id
    )
    return MetricQueryCompiler().compile(package, metric)


def _span(
    text: str,
    span_id: str,
    *,
    group_id: str | None = None,
    span_type: str = "block",
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=span_id,
        document_id="doc-pattern",
        ir_run_id="ir-pattern",
        ir_revision=1,
        span_type=span_type,
        text=text,
        context_text=text,
        page_index=0,
        object_ids=[span_id],
        locators=[
            SourceLocator(
                ir_object_type="cell" if span_type == "table_cell" else "block",
                ir_object_id=span_id,
                pdf_page_index=0,
            )
        ],
        context_group_id=group_id or f"group:{span_id}",
        has_table_structure=span_type == "table_cell",
    )


def _inventory(spans: list[EvidenceSpan]) -> EvidenceInventory:
    return EvidenceInventory(
        inventory_id="inventory-pattern",
        document_id="doc-pattern",
        ir_run_id="ir-pattern",
        ir_revision=1,
        spans=spans,
        candidates=LiteralHarvester().harvest(spans),
        page_images={},
        stats={},
    )


def test_compiler_separates_microplastic_topic_and_fixed_roles() -> None:
    combined = _query("E2-4_05")
    generated = _query("E2-4_06")
    used = _query("E2-4_07")

    assert any("微塑料" in group for group in combined.intent.topic_term_groups)
    assert any("产生和使用" in group for group in combined.intent.role_term_groups)
    assert any("产生" in group for group in generated.intent.role_term_groups)
    assert any("使用" in group for group in used.intent.role_term_groups)
    assert not any("使用" in group for group in generated.intent.role_term_groups)
    assert not any("产生" in group for group in used.intent.role_term_groups)
    assert "AR 20" not in used.lexical_text
    assert "IG 3" not in combined.lexical_text
    assert "05/06/07" not in combined.lexical_text


def test_compiler_does_not_turn_supporting_standard_context_into_hard_topics() -> None:
    soil = _query("E2-4_04")
    bat = _query("E2-4_18")

    assert len(soil.intent.topic_term_groups) == 3
    assert any("土壤排放" in group for group in soil.intent.topic_term_groups)
    assert len(bat.intent.topic_term_groups) == 2
    assert any("BAT-AEL" in group for group in bat.intent.topic_term_groups)


def test_package_concepts_expand_specific_pollutant_aliases_without_custom_query_code() -> None:
    air = _query("E2-4_02")
    topic_terms = {term for group in air.intent.topic_term_groups for term in group}

    assert {"二氧化硫", "SO2", "SO₂", "Sulfur dioxide"} <= topic_terms
    assert {"大气污染物", "排放量", "污染物"} <= topic_terms
    assert "温室气体" in air.intent.must_not_terms
    assert "二氧化硫" in air.lexical_text


def test_compiler_inherits_mutually_exclusive_subject_concepts() -> None:
    air = _query("E2-4_02")
    water = _query("E2-4_03")
    soil = _query("E2-4_04")

    assert {"废水排放", "土壤排放"} <= set(air.intent.must_not_terms)
    assert {"大气污染物", "土壤排放"} <= set(water.intent.must_not_terms)
    assert {"大气污染物", "废水排放"} <= set(soil.intent.must_not_terms)

    wrong_medium = _span("大气污染物排放量：二氧化硫125.6吨。", "span-air")
    inventory = _inventory([wrong_medium])
    match = evaluate_fact_pattern(water, [wrong_medium], inventory.candidates)
    assert match.conflict_terms
    assert not match.full_pattern


def test_specific_pollutant_fact_pattern_is_complete_with_heading_and_quantity() -> None:
    query = _query("E2-4_02")
    span = _span("大气污染物排放量：二氧化硫125.6吨。", "span-so2")
    inventory = _inventory([span])
    match = evaluate_fact_pattern(query, [span], inventory.candidates)

    assert match.topic_complete
    assert match.compatible_candidate
    assert match.full_pattern


def test_fact_pattern_requires_topic_role_and_typed_quantity() -> None:
    query = _query("E2-4_07")
    direct = _span("本年度使用微塑料8.2吨。", "span-direct")
    direct_inventory = _inventory([direct])
    match = evaluate_fact_pattern(query, [direct], direct_inventory.candidates)
    assert match.full_pattern

    year_only = _span("2024年开展微塑料管理。", "span-year")
    year_inventory = _inventory([year_only])
    match = evaluate_fact_pattern(query, [year_only], year_inventory.candidates)
    assert match.topic_complete
    assert not match.compatible_candidate
    assert not match.full_pattern


def test_fact_pattern_combines_cells_only_inside_same_table_row() -> None:
    query = _query("E2-4_07")
    spans = [
        _span("使用的微塑料", "cell-label", group_id="row:1", span_type="table_cell"),
        _span("8.2吨", "cell-value", group_id="row:1", span_type="table_cell"),
    ]
    inventory = _inventory(spans)
    match = evaluate_fact_pattern(query, spans, inventory.candidates)
    assert match.full_pattern


def test_literal_harvester_recognises_energy_ghg_currency_and_intensity_units() -> None:
    spans = [
        _span("能源消费 125.6 MWh，范围1排放 82.4 tCO2e。", "span-measurements"),
        _span("温室气体强度为 0.42 tCO2e/百万元。", "span-intensity"),
        _span("净收入为 2.3 亿元。", "span-revenue"),
        _span("一手数据覆盖率为 45%。", "span-percentage"),
    ]
    candidates = LiteralHarvester().harvest(spans)
    quantities = {(item.normalized_value, item.unit_raw, item.unit_id) for item in candidates if item.candidate_type == "quantity"}

    assert ("125.6", "MWh", "extractesg.core.unit.megawatt-hour") in quantities
    assert ("82.4", "tCO2e", "extractesg.core.unit.tonne-co2e") in quantities
    assert any(value == "0.42" and unit == "tCO2e/百万元" for value, unit, _ in quantities)
    assert any(value == "2.3" and unit == "亿元" for value, unit, _ in quantities)
    assert not any(value == "45" and unit == "%" for value, unit, _ in quantities)


def test_retrieval_normalizes_traditional_scope_and_latex_ghg_units() -> None:
    text = "範圍一 溫室氣體排放量為 3.37 百万 $ tCO_{{2}}e $"
    span = _span(text, "span-traditional-ghg")
    candidates = LiteralHarvester().harvest([span])

    assert contains_term(text, "范围1排放")
    assert contains_term(text, "温室气体排放")
    assert any(
        item.candidate_type == "quantity"
        and item.normalized_value == "3.37"
        and item.unit_id == "extractesg.core.unit.megatonne-co2e"
        for item in candidates
    )


def test_sufficiency_sends_ranked_structured_evidence_to_semantic_review() -> None:
    query = _query("E2-4_07")
    span = _span(
        "本项计量值 8.2吨。",
        "semantic-table-value",
        group_id="semantic-row",
        span_type="table_cell",
    )
    inventory = _inventory([span])
    hit = RetrievalHit(
        span_id=span.span_id,
        semantic_rank=1,
        semantic_score=0.72,
        rrf_score=0.02,
        structural_boost=0.0,
        final_score=0.02,
    )

    result = EvidenceSufficiencyGate().assess(
        query, inventory, [hit], retrieval_complete=True
    )

    assert result.status == "uncertain"
    assert not result.auto_not_found_allowed
    assert result.reason_codes == ["ranked_structured_evidence_requires_semantic_review"]


def test_hybrid_ranks_topic_role_quantity_above_year_and_unrelated_mass() -> None:
    query = _query("E2-4_07")
    spans = [
        _span("本年度使用微塑料8.2吨。", "span-best"),
        _span("2024年开展微塑料管理。", "span-year"),
        _span("本年度空气污染物排放量为100吨。", "span-unrelated"),
    ]
    inventory = _inventory(spans)
    hits = HybridRetriever(inventory).rank_all(query)
    assert hits[0].span_id == "span-best"
    assert hits[0].compatible_candidate
    assert hits[0].combination_boost > 0
    assert "topic_typed_value_combination" in hits[0].reasons
    year_hit = next(item for item in hits if item.span_id == "span-year")
    assert not year_hit.compatible_candidate
    assert year_hit.combination_boost == 0


def test_sufficiency_is_conservative_about_role_and_local_not_found() -> None:
    query = _query("E2-4_07")

    role_missing = _inventory([_span("微塑料总量为8.2吨。", "span-total")])
    hits = HybridRetriever(role_missing).search(query, 12)
    result = EvidenceSufficiencyGate().assess(
        query, role_missing, hits, retrieval_complete=True
    )
    assert result.status == "uncertain"
    assert not result.auto_not_found_allowed
    assert result.reason_codes == ["topic_and_typed_value_found_but_role_is_incomplete"]

    absent = _inventory([_span("本年度空气污染物排放量为100吨。", "span-air")])
    hits = HybridRetriever(absent).search(query, 12)
    result = EvidenceSufficiencyGate().assess(query, absent, hits, retrieval_complete=True)
    assert result.status == "insufficient"
    assert result.auto_not_found_allowed

    mixed_query = query.model_copy(update={"data_class": "mixed"})
    result = EvidenceSufficiencyGate().assess(
        mixed_query, absent, hits, retrieval_complete=True
    )
    assert result.status == "uncertain"
    assert not result.auto_not_found_allowed
