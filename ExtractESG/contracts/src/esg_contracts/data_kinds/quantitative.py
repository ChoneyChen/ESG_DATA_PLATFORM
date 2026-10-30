"""Quantitative value contract: printed value first, structured fields are optional."""

from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
    ValidationError,
)

from . import extensible_metadata


class QuantitativeValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value_raw: str = Field(min_length=1)
    parse_status: Literal["parsed", "unparsed", "ambiguous", "conflict"] = "unparsed"
    comparator: Literal[
        "exact",
        "range",
        "greater_than",
        "less_than",
        "at_least",
        "at_most",
        "approx",
        "unknown",
    ] = "unknown"
    value_numeric: str | None = None
    value_lower: str | None = None
    value_upper: str | None = None
    unit_raw: str | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_structured_fields_when_present(self):
        if not self.value_raw.strip():
            raise ValueError("Printed value cannot be blank")
        values = []
        for raw in (self.value_numeric, self.value_lower, self.value_upper):
            try:
                value = Decimal(raw) if raw is not None else None
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("Structured numbers must be decimal strings") from exc
            if value is not None and not value.is_finite():
                raise ValueError("Structured numbers must be finite")
            values.append(value)
        number, lower, upper = values
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("Contradictory interval bounds")
        if number is not None and (
            (lower is not None and number < lower)
            or (upper is not None and number > upper)
        ):
            raise ValueError("Structured number conflicts with its bounds")
        if self.parse_status == "parsed":
            if self.comparator in {"exact", "approx"} and number is None:
                raise ValueError("Parsed point values need their number")
            if self.comparator == "range" and (lower is None or upper is None):
                raise ValueError("Parsed intervals need both bounds")
            if self.comparator in {"at_least", "greater_than"} and lower is None:
                raise ValueError("Parsed lower bound missing")
            if self.comparator in {"at_most", "less_than"} and upper is None:
                raise ValueError("Parsed upper bound missing")
            if self.comparator == "unknown":
                raise ValueError("A parsed value needs a known comparator")
        return self

    @model_validator(mode="wrap")
    @classmethod
    def raw_first(cls, data, handler):
        prepared = extensible_metadata(cls, data, forbidden={"statement_raw"})
        try:
            return handler(prepared)
        except ValidationError as exc:
            if (
                not isinstance(prepared, dict)
                or not isinstance(prepared.get("value_raw"), str)
                or not prepared["value_raw"].strip()
            ):
                raise
            if any(
                error["loc"] and error["loc"][0] in {"value_raw", "kind"}
                for error in exc.errors()
            ):
                raise
            annotations = dict(prepared.get("extensions") or {})
            annotations["_unparsed_metadata"] = {
                k: v
                for k, v in prepared.items()
                if k not in {"value_raw", "kind", "extensions"}
            }
            annotations["_metadata_issues"] = [error["msg"] for error in exc.errors()]
            fallback = {
                "value_raw": prepared["value_raw"],
                "extensions": annotations,
                "parse_status": "unparsed",
                "comparator": "unknown",
            }
            if "kind" in prepared:
                fallback["kind"] = prepared["kind"]
            if isinstance(prepared.get("unit_raw"), str):
                fallback["unit_raw"] = prepared["unit_raw"]
            return handler(fallback)
