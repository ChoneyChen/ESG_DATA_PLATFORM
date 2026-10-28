"""The extraction chain's two data-kind contracts.

Every fact is either a quantitative measurement or a qualitative assertion.
The contract is about the shape of that fact, independent of the report, the
route and the clause: which value fields may coexist, which comparator needs
which bounds, and that a statement never carries a number/unit. Clause
contracts decide *what* to read; this module decides *what a produced record
of each kind must look like*.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATA_CONTRACT_VERSION = "extraction-data-kinds-v1"
COMPARATORS = {"exact", "range", "at_least", "at_most", "approx"}


class _Strict(BaseModel):
    # Core records carry additional schema fields (value_origin, confidence,
    # evidence references); the kind contract judges the value-bearing fields.
    model_config = ConfigDict(extra="ignore")


def _decimal(value: str | None) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"not a decimal: {value!r}") from error


class QuantitativeMeasurement(_Strict):
    """A printed number, an explicit interval, or a bounded estimate."""

    observation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    value_raw: str = Field(min_length=1)
    comparator: Literal["exact", "range", "at_least", "at_most", "approx"] = "exact"
    value_numeric: str | None = None
    value_lower: str | None = None
    value_upper: str | None = None
    unit_raw: str | None = None
    unit_id: str | None = None
    reporting_period_raw: str | None = None
    reporting_year: int | None = None
    period_start: str | None = None
    period_end: str | None = None
    reporting_boundary: str | None = None
    evidence_set_id: str | None = None
    review_status: str | None = None

    @model_validator(mode="after")
    def value_shape_matches_comparator(self):
        number = _decimal(self.value_numeric)
        lower = _decimal(self.value_lower)
        upper = _decimal(self.value_upper)
        if self.comparator in {"exact", "approx"}:
            if number is None:
                raise ValueError(f"{self.comparator} measurement requires value_numeric")
        elif self.comparator == "range":
            if lower is None and upper is None:
                raise ValueError("range measurement requires a lower or upper bound")
        elif self.comparator == "at_least" and lower is None:
            raise ValueError("at_least measurement requires value_lower")
        elif self.comparator == "at_most" and upper is None:
            raise ValueError("at_most measurement requires value_upper")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("value_lower is greater than value_upper")
        if number is not None and lower is not None and number < lower:
            raise ValueError("value_numeric is below value_lower")
        if number is not None and upper is not None and number > upper:
            raise ValueError("value_numeric is above value_upper")
        return self


class QualitativeAssertion(_Strict):
    """Reported text; never a number or a unit."""

    assertion_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    statement_raw: str = Field(min_length=1)
    statement_summary: str | None = None
    reporting_period_raw: str | None = None
    reporting_year: int | None = None
    period_start: str | None = None
    period_end: str | None = None
    reporting_boundary: str | None = None
    evidence_set_id: str | None = None
    review_status: str | None = None

    @model_validator(mode="after")
    def statement_is_text(self):
        if not self.statement_raw.strip():
            raise ValueError("statement_raw cannot be blank")
        return self


CONTRACT_BY_KIND = {"quantitative": QuantitativeMeasurement, "qualitative": QualitativeAssertion}


def validate_records(records: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Return contract violations for both kinds; an empty list means valid."""
    errors: list[str] = []
    for index, row in enumerate(records.get("quantitative_observations", []), 1):
        try:
            QuantitativeMeasurement.model_validate(row)
        except ValueError as error:
            errors.append(f"quantitative_observations[{index}]: {error}")
    for index, row in enumerate(records.get("qualitative_assertions", []), 1):
        try:
            QualitativeAssertion.model_validate(row)
        except ValueError as error:
            errors.append(f"qualitative_assertions[{index}]: {error}")
    return errors
