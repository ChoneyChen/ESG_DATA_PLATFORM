"""Output-scope taxonomy and clause profile contracts."""
import pytest
from pydantic import ValidationError

from esg_standard_packages.clause_profile import (
    AcceptanceSample, ClauseExtractionProfile, compile_clause_profile, profile_digest, validate_profile,
)
from esg_standard_packages.clause_scope import (
    OutputScope, entity_code, fact_scope, make_scope, period_code, qualifier_codes, scope_coverage,
    scope_match, stable_scope_id,
)

METRIC = {
    "metric_id": "esrs.2023-set1.e1-5.dp01",
    "source_datapoint_id": "E1-5_01",
    "labels": {"zh": "能源消耗总量", "en": "Total energy consumption"},
    "description": "自身运营的能源消费总量",
    "data_class": "quantitative",
    "canonical_unit": "MWh",
    "unit_dimension": "energy",
    "allowed_value_origins": ["reported"],
    "measurement_contract": {
        "value_role": "actual", "unit_dimension": "extractesg.core.unit-dimension.energy",
        "canonical_unit_id": "extractesg.core.unit.megawatt-hour", "normalization": "compatible_unit_only",
        "identity_axes": ["reporting_entity", "reporting_period", "reporting_boundary", "energy_source"],
        "comparison_axes": ["reporting_boundary", "energy_source"],
        "comparison_mode": "conditional_numeric", "aggregation": "sum_disjoint_members",
        "positive_definition": "报告期内自身运营消耗的各种能源合计",
        "interpretation_guidance": [], "distinguish_from": ["能源生产量", "能源节约量"],
        "exclusions": ["标题、目录、空白不能作为带值事实"], "comparison_requirements": ["同一主体与期间"],
    },
}


def test_period_and_entity_normalization():
    assert period_code("2025年") == "annual"
    assert period_code("2024/2025财年") == "fiscal_year"
    assert period_code("2025年末") == "instant"
    assert period_code("近三年累计") == "cumulative"
    assert period_code("2030年目标") == "target_period"
    assert period_code("2024-01-01", start="2024-01-01", end="2024-12-31") == "annual"
    assert period_code(None) == "unknown"
    assert entity_code("subset", "subsidiary") == "subsidiary"
    assert entity_code("subset", "battery_production_bases") == "site"
    assert entity_code("subset", "refining") == "business_unit"
    assert entity_code("subset", "overseas") == "region"
    assert entity_code("subset", None) == "unclassified_subset"
    assert entity_code(None, None) == "unknown"
    assert entity_code("subset", "group") == "group"


def test_scope_identity_is_content_addressed():
    first = make_scope("集团年度总量")
    second = make_scope("集团年度总量", axes={})
    assert first.scope_id == second.scope_id == stable_scope_id(first.canonical())
    assert make_scope("集团年度总量", entity="subsidiary").scope_id != first.scope_id


def test_scope_declaration_rejects_free_text_codes():
    with pytest.raises(ValidationError, match="Unknown period code"):
        make_scope("x", period="last year")
    with pytest.raises(ValidationError, match="Unknown entity code"):
        make_scope("x", entity="some subsidiary")


def test_scope_match_distinguishes_conflict_from_unknown():
    scope = make_scope("集团年度总量")
    assert scope_match(scope, {"period": "annual", "entity": "group", "value_role": "actual", "aggregation": "total"}) == "match"
    assert scope_match(scope, {"period": "annual", "entity": "subsidiary", "value_role": "actual", "aggregation": "total"}) == "conflict"
    assert scope_match(scope, {"period": "annual", "entity": "unknown", "value_role": "actual", "aggregation": "total"}) == "unknown"
    with_method = make_scope("位置法范围二", period="annual", entity="group", method="location_based")
    assert scope_match(with_method, {"period": "annual", "entity": "group", "value_role": "actual", "aggregation": "total", "method": None}) == "unknown"
    assert scope_match(with_method, {"period": "annual", "entity": "group", "value_role": "actual", "aggregation": "total", "method": "market_based"}) == "conflict"


def test_fact_scope_keeps_printed_qualifiers():
    identity = {"source_row_label": "其中：太阳能", "reporting_period": "2025年", "entity_basis": "subset",
                "reporting_boundary": "battery_production_bases", "energy_carrier": "solar",
                "value_qualifier": ">="}
    scope = fact_scope(identity)
    assert scope["entity"] == "site" and scope["axes"]["energy_carrier"] == "solar"
    assert scope["qualifier"] == ["at_least", "of_which"]
    assert qualifier_codes("approx") == ["approx"]


def test_scope_coverage_reports_each_declared_scope():
    scopes = [make_scope("集团年度总量"),
              make_scope("基地年度总量", entity="site", required=False)]
    facts = [{"source_fact_id": "f1", "identity_raw": {"reporting_period": "2025年", "entity_basis": "subset",
                                                        "reporting_boundary": "battery_production_bases"}}]
    rows = {row["scope_id"]: row for row in scope_coverage(scopes, facts)}
    assert rows[scopes[0].scope_id]["state"] == "out_of_scope"
    assert rows[scopes[1].scope_id]["state"] == "covered"
    assert rows[scopes[1].scope_id]["fact_refs"] == ["f1"]


def test_compile_profile_from_a_real_contract():
    profile = compile_clause_profile(METRIC, package_id="esrs.2023-set1.e1-5", package_version="2.1.2",
                                     retrieval_terms=["能源消耗总量", "综合能源消费"])
    assert profile.clause_id == "esrs.2023-set1.e1-5@2.1.2:E1-5_01"
    assert profile.value_type == "numeric" and profile.canonical_unit == "MWh"
    assert [scope.required for scope in profile.output_scopes] == [True]
    assert profile.output_scopes[0].aggregation == "total"
    assert profile.retrieval.positive_terms[0] == "能源消耗总量"
    assert {item.field for item in profile.evidence} >= {"value", "source_row_label", "reporting_period", "unit"}
    validate_profile(profile)
    assert profile.contract_digest == profile_digest(profile)


def test_scope_two_methods_become_explicit_optional_scopes():
    metric = {**METRIC, "source_datapoint_id": "E1-6_09",
              "measurement_contract": {**METRIC["measurement_contract"],
                                       "comparison_axes": ["reporting_boundary", "scope_2_method"]}}
    profile = compile_clause_profile(metric, package_id="esrs.2023-set1.e1-6", package_version="2.2.0",
                                     retrieval_terms=["范围二排放"])
    methods = {scope.method for scope in profile.output_scopes if scope.method}
    assert methods == {"location_based", "market_based"}
    assert all(not scope.required for scope in profile.output_scopes if scope.method)


def test_fixed_axes_become_part_of_the_declared_scope():
    profile = compile_clause_profile(METRIC, package_id="esrs.2023-set1.e1-5", package_version="2.1.2",
                                     retrieval_terms=["可再生能源消耗"], fixed_axes={"energy_carrier": "renewable"})
    assert profile.output_scopes[0].axes == {"energy_carrier": "renewable"}
    assert profile.publication.fixed_axes == {"energy_carrier": "renewable"}


def test_profile_rejects_incoherent_declarations():
    profile = compile_clause_profile(METRIC, package_id="esrs.2023-set1.e1-5", package_version="2.1.2",
                                     retrieval_terms=["能源消耗总量"])
    with pytest.raises(ValidationError, match="at least one required output scope"):
        ClauseExtractionProfile(**{**profile.model_dump(mode="json"),
                                   "output_scopes": [{**profile.output_scopes[0].model_dump(mode="json"), "required": False}]})
    with pytest.raises(ValidationError, match="numeric clause requires its canonical unit"):
        ClauseExtractionProfile(**{**profile.model_dump(mode="json"), "canonical_unit": None})
    tampered = profile.model_copy(update={"exclusions": ["不同定义"]})
    with pytest.raises(ValueError, match="digest"):
        validate_profile(tampered)


def test_acceptance_sample_needs_exactly_one_expectation():
    with pytest.raises(ValidationError):
        AcceptanceSample(run_ref="tx-1")
    with pytest.raises(ValidationError):
        AcceptanceSample(run_ref="tx-1", expected_value_raw="1", expected_absent=True)
    assert AcceptanceSample(run_ref="tx-1", expected_absent=True).expected_absent
