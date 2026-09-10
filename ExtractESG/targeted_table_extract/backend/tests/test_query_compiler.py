from __future__ import annotations

from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler

from tests.helpers import standard_dist_root


def _queries():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    compiler = MetricQueryCompiler()
    metrics = {item.source_datapoint_id: item for item in package.metrics}
    return {
        datapoint: compiler.compile(package, metrics[datapoint])
        for datapoint in ("E2-4_02", "E2-4_03", "E2-4_04")
    }


def _topic_terms(query) -> set[str]:
    return {term.casefold() for group in query.intent.topic_term_groups for term in group}


def test_pollutant_descendants_are_filtered_by_pollution_medium() -> None:
    queries = _queries()
    air = _topic_terms(queries["E2-4_02"])
    water = _topic_terms(queries["E2-4_03"])
    soil = _topic_terms(queries["E2-4_04"])

    assert {"二氧化硫", "so2", "氮氧化物", "nox", "颗粒物", "voc"} <= air
    assert {"cod", "bod", "化学需氧量", "生化需氧量"}.isdisjoint(air)
    assert {"cod", "bod", "化学需氧量", "生化需氧量"} <= water
    assert {"so2", "nox", "pm2.5", "voc"}.isdisjoint(water)
    assert {"so2", "nox", "cod", "bod"}.isdisjoint(soil)


def test_medium_conflicts_remain_explicit_negative_terms() -> None:
    queries = _queries()
    air_not = {item.casefold() for item in queries["E2-4_02"].intent.must_not_terms}
    water_not = {item.casefold() for item in queries["E2-4_03"].intent.must_not_terms}

    assert {"水污染", "土壤污染", "cod", "bod", "化学需氧量", "生化需氧量"} <= air_not
    assert {"空气污染", "土壤污染", "so2", "nox", "pm2.5", "voc"} <= water_not


def test_exclusive_medium_descendants_are_not_both_positive_and_negative() -> None:
    queries = _queries()
    for query in queries.values():
        positive = _topic_terms(query)
        negative = {item.casefold() for item in query.intent.must_not_terms}
        assert positive.isdisjoint(negative)


def test_explicit_multi_context_metric_does_not_exclude_its_active_contexts() -> None:
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E2-4_02")
    metric = metric.model_copy(
        update={
            "subject_concept_groups": [
                ["esrs.2023-set1.e2-4.concept.air-pollution"],
                ["esrs.2023-set1.e2-4.concept.water-pollution"],
                ["esrs.2023-set1.e2-4.concept.emission-amount"],
                ["esrs.2023-set1.e2-4.concept.pollutant"],
            ]
        },
        deep=True,
    )
    query = MetricQueryCompiler().compile(package, metric)
    positive = _topic_terms(query)
    negative = {item.casefold() for item in query.intent.must_not_terms}

    assert {"空气污染", "水污染", "so2", "cod"} <= positive
    assert {"空气污染", "水污染", "so2", "cod"}.isdisjoint(negative)
