from __future__ import annotations

from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler

from tests.helpers import standard_dist_root


def _query(package_id: str, datapoint: str):
    package = StandardPackageCatalog(standard_dist_root()).load(package_id, "1.0.0")
    metric = next(item for item in package.metrics if item.source_datapoint_id == datapoint)
    return MetricQueryCompiler().compile(package, metric)


def _query_version(package_id: str, version: str, datapoint: str):
    package = StandardPackageCatalog(standard_dist_root()).load(package_id, version)
    metric = next(item for item in package.metrics if item.source_datapoint_id == datapoint)
    return package, MetricQueryCompiler().compile(package, metric)


def test_e1_5_energy_query_is_compiled_from_package_concepts() -> None:
    query = _query("esrs.2023-set1.e1-5", "E1-5_12")
    positive = {term.casefold() for group in query.intent.topic_term_groups for term in group}
    negative = {term.casefold() for term in query.intent.must_not_terms}

    assert {"能源消费", "天然气能源", "natural gas fuel"} <= positive
    assert {"可再生能源", "核能"} <= negative
    assert "quantity" in query.intent.expected_candidate_types


def test_e1_6_scope_query_uses_scope_and_gross_emission_exclusions() -> None:
    query = _query("esrs.2023-set1.e1-6", "E1-6_07")
    positive = {term.casefold() for group in query.intent.topic_term_groups for term in group}
    negative = {term.casefold() for term in query.intent.must_not_terms}

    assert {"范围1排放", "direct ghg emissions", "温室气体排放总量"} <= positive
    assert {"范围2排放", "范围3排放", "碳信用", "碳移除"} <= negative
    assert positive.isdisjoint(negative)


def test_e1_6_percentage_and_intensity_candidate_types_are_generic() -> None:
    percentage = _query("esrs.2023-set1.e1-6", "E1-6_25")
    intensity = _query("esrs.2023-set1.e1-6", "E1-6_30")

    assert percentage.intent.expected_candidate_types == ["percentage"]
    assert intensity.intent.expected_candidate_types == ["quantity", "number"]


def test_e1_6_v110_scope1_query_does_not_require_gross_wording() -> None:
    _, query = _query_version("esrs.2023-set1.e1-6", "1.1.0", "E1-6_07")
    positive_groups = [set(group) for group in query.intent.topic_term_groups]

    assert any("範圍1 溫室氣體排放量" in group for group in positive_groups)
    assert not any("温室气体排放总量" in group for group in positive_groups)
    assert len(query.intent.must_term_groups) == 2


def test_e1_v110_exposes_typed_entity_and_aggregation_dimensions() -> None:
    package, _ = _query_version("esrs.2023-set1.e1-5", "1.1.0", "E1-5_12")
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E1-5_12")
    elements_by_id = {item.element_id: item for item in package.elements}
    elements = [elements_by_id[item] for item in package.elements_by_metric[metric.metric_id]]

    assert any(item.element_code == "reporting_entity" for item in elements)
    aggregation = next(item for item in elements if item.element_code == "aggregation_role")
    assert aggregation.value_contract.fixed_value == "component"
