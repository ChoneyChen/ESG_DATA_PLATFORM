"""The standard package owns measurement identity; the platform may only add declared gaps."""
from types import SimpleNamespace

import pytest

from esg_standard_packages.scientific_contract import PLATFORM_IDENTITY_EXTENSIONS, check_identity_axes


def contract(*, identity=("reporting_entity", "energy_source"), comparison=("reporting_period",)):
    return SimpleNamespace(measurement_contract=SimpleNamespace(identity_axes=list(identity),
                                                                comparison_axes=list(comparison)))


def test_a_declared_axis_is_accepted():
    check_identity_axes("E1-5_01", contract(), {"required_dimensions": ["energy_source"], "fixed_dimensions": {}})


def test_a_comparison_axis_counts_as_declared():
    check_identity_axes("E1-5_01", contract(), {"required_dimensions": ["reporting_period"], "fixed_dimensions": {}})


def test_a_registered_gap_is_accepted_once():
    key = "worker_population"
    assert key in PLATFORM_IDENTITY_EXTENSIONS
    check_identity_axes("S2-1_05", contract(), {"required_dimensions": [key], "fixed_dimensions": {}})


def test_an_invented_identity_axis_fails_the_build():
    with pytest.raises(ValueError, match="未被标准包声明"):
        check_identity_axes("E1-5_01", contract(), {"required_dimensions": ["energy_carrier_v2"],
                                                    "fixed_dimensions": {}})


def test_a_fixed_value_on_an_undeclared_axis_fails_the_build():
    with pytest.raises(ValueError, match="energy_carrier_v2"):
        check_identity_axes("E1-5_01", contract(), {"required_dimensions": [],
                                                    "fixed_dimensions": {"energy_carrier_v2": "solar"}})
