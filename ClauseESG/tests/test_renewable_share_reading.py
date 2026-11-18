"""Keep source-specific percentages distinct from an unproved energy denominator."""
import json
from pathlib import Path

import pytest

from esg_clauses.contract import ClauseContract
from esg_clauses.reading import check_reading


@pytest.fixture(scope="module")
def renewable_share():
    path = Path(__file__).resolve().parents[1] / "clauses/E1-5/E1-5_09/clause.json"
    return ClauseContract.model_validate(json.loads(path.read_text())).verify()


@pytest.mark.parametrize("label", [
    "Percentage of Solar Energy Consumption",
    "Renewable energy consumed onsite solar (%)",
    "水电消费占比",
])
def test_specific_source_share_keeps_value_with_unproved_denominator(renewable_share, label):
    result = check_reading(renewable_share, label, None, "%", "ratio")
    assert result.ok and result.status == "review"
    assert "renewable_component_share_denominator_unproven" in result.flags


def test_explicit_total_energy_denominator_confirms_source_component(renewable_share):
    result = check_reading(renewable_share, "Percentage of Solar Energy Consumption",
                           "Share of Sources in Total Energy Consumption", "%", "ratio")
    assert result.ok and result.status is None


def test_generic_renewable_share_retains_missing_scope_policy(renewable_share):
    result = check_reading(renewable_share, "Renewable Energy %", None, "%", "ratio")
    assert result.ok and result.status is None


def test_company_name_does_not_identify_a_solar_consumption_component(renewable_share):
    result = check_reading(renewable_share, "Renewable Energy %", "Canadian Solar", "%", "ratio")
    assert result.ok and result.status is None
