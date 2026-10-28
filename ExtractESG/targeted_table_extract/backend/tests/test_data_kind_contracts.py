"""The two data-kind contracts are the chain's output shape, independent of clauses."""
import pytest

from esg_targeted.data_contracts import (
    DATA_CONTRACT_VERSION,
    QualitativeAssertion,
    QuantitativeMeasurement,
    validate_records,
)


def test_quantitative_exact_value_requires_a_number():
    row = {"observation_id": "obs-1", "task_id": "task-1", "metric_id": "m", "value_raw": "12",
           "comparator": "exact"}
    with pytest.raises(ValueError):
        QuantitativeMeasurement.model_validate(row)
    row["value_numeric"] = "12"
    assert QuantitativeMeasurement.model_validate(row).value_numeric == "12"


def test_quantitative_interval_keeps_its_bounds_and_order():
    row = {"observation_id": "obs-1", "task_id": "task-1", "metric_id": "m", "value_raw": "≥ 5",
           "comparator": "at_least", "value_lower": "5"}
    assert QuantitativeMeasurement.model_validate(row).comparator == "at_least"
    bad = {**row, "comparator": "range", "value_lower": "9", "value_upper": "3"}
    with pytest.raises(ValueError):
        QuantitativeMeasurement.model_validate(bad)


def test_qualitative_statement_carries_text_not_numbers():
    row = {"assertion_id": "assert-1", "task_id": "task-1", "metric_id": "m",
           "statement_raw": "公司制定了反腐败政策。"}
    assert QualitativeAssertion.model_validate(row).statement_raw.startswith("公司")
    with pytest.raises(ValueError):
        QualitativeAssertion.model_validate({**row, "statement_raw": "  "})


def test_validate_records_reports_both_kinds():
    errors = validate_records({
        "quantitative_observations": [{"observation_id": "obs-1", "task_id": "t", "metric_id": "m",
                                       "value_raw": "1", "comparator": "range"}],
        "qualitative_assertions": [{"assertion_id": "a-1", "task_id": "t", "metric_id": "m",
                                    "statement_raw": ""}],
    })
    assert len(errors) == 2 and errors[0].startswith("quantitative_observations[1]")
    assert DATA_CONTRACT_VERSION == "extraction-data-kinds-v1"
