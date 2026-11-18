"""Total-energy semantics from the ordinary-PDF release batch and boundary controls."""

import json
from pathlib import Path

import pytest

from esg_clauses.contract import ClauseContract
from esg_clauses.reading import check_reading


@pytest.fixture(scope="module")
def energy_total():
    path = Path(__file__).resolve().parents[1] / "clauses/E1-5/E1-5_01/clause.json"
    return ClauseContract.model_validate(json.loads(path.read_text(encoding="utf-8"))).verify()


@pytest.mark.parametrize(
    "label,section",
    [
        ("Total Energy Consumption", None),
        ("TotalEnergyConsumption", None),
        ("1 Comprehensive Energy Consumption", None),
        ("综合能源消耗量", None),
        ("Total", "Energy Consumption"),
        ("Total", "EnergyConsumption"),
        ("Total Energy Consumption (including renewable energy, natural gas and electricity)", None),
        ("Total Energy Consumption (Renewable Energy + Non-renewable Energy)", None),
        ("TotalEnergyConsumption (including renewable energy, natural gas and electricity)", None),
        ("Total Energy Consumption (including solar energy and purchased electricity)", None),
        (
            "e-STORAGE, our utility-scale battery energy storage product subsidiary which "
            "provides customers with competitive turnkey, integrated, utility-scale battery "
            "energy storage solutions, consumed a total of 9,434 MWh of energy at its "
            "production sites in 2023.",
            None,
        ),
    ],
)
@pytest.mark.parametrize("unit,dimension", [("MWh", "energy"), (None, None)])
def test_explicit_energy_measurement_keeps_total_and_missing_unit(
    energy_total, label, section, unit, dimension
):
    assert check_reading(energy_total, label, section, unit, dimension).ok


@pytest.mark.parametrize(
    "label,section",
    [
        ("Total", None),
        ("Total", "GHG Management"),
        ("Total Modules Produced (Millions)", None),
        ("Total Number of Associates", None),
        ("Total Waste Generation (Million Kilograms)", None),
        ("Total Energy Consumption (Non-renewable Energy)", None),
        ("Total Energy Consumption (Renewable Energy)", None),
        ("TotalEnergyConsumption(Non-renewableEnergy)", None),
        ("TotalEnergyConsumption(RenewableEnergy)", None),
        ("Solar energy consumption", None),
        ("Wind energy consumption", None),
        ("Hydropower consumption", None),
        ("Total Energy Consumption (Clean Energy)", None),
        ("Renewable Energy › Total Energy Consumption", None),
        ("能源消耗总量（可再生能源）", None),
        ("能源消耗总量（非可再生能源）", None),
        (
            "On a life cycle basis, modules use up to 400 times less water per kWh "
            "than conventional energy and up to 24 times less water than other PV technologies.",
            None,
        ),
    ],
)
def test_total_does_not_replace_energy_meaning_or_erase_source_qualifier(
    energy_total, label, section
):
    assert not check_reading(energy_total, label, section, None, None).ok


@pytest.mark.parametrize('key',['E1-5_01','E1-5_05','E1-6_07'])
def test_boundary_quotes_name_coverage_instead_of_a_column_year(key):
    path=Path(__file__).resolve().parents[1]/'clauses'/key.rsplit('_',1)[0]/key/'clause.json'
    clause=ClauseContract.model_validate(json.loads(path.read_text())).verify()
    definition=clause.attributes['reporting_boundary']
    for value in ['2023','2023年','FY 2023']:
        assert not definition.accepts_value(value)
    assert definition.accepts_value('Manufacturing sites in China')
