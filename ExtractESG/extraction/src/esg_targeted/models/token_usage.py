"""Normalize provider usage without calling text bytes measured model tokens.

The engine's prompt count includes its rendered prompt and, for multimodal
providers, image tokens. A provider may also expose a text-only ``input_tokens``
estimate; prefer the engine count when both are available. Failed generation
uses exactly the same accounting path as successful generation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    input_token_basis: str
    output_token_basis: str

    def finish_kwargs(self) -> dict[str, Any]:
        return asdict(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _count(value: Any) -> int | None:
    """Ignore missing/invalid telemetry rather than silently inventing zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            return None
        return int(number)
    except (InvalidOperation, ValueError, TypeError):
        return None


def resolve_token_usage(record: Any, *, input_reservation: int,
                        output_reservation: int, raw_output: str | None = None,
                        input_reservation_basis: str = "utf8_text_reservation") -> TokenUsage:
    """Use valid telemetry; retain explicitly labelled estimates otherwise.

    A reservation is an admission estimate, not an observation. Keeping its
    basis makes partially observed failures and old ledgers auditable. Raw
    output bytes are a text estimate when the provider omits output usage.
    """
    lookup = record.get if isinstance(record, dict) else lambda key, default=None: getattr(record, key, default)
    if lookup("generation_started") is False:
        return TokenUsage(0, 0, "generation_not_started", "generation_not_started")
    input_count, input_basis = input_reservation, input_reservation_basis
    output_count, output_basis = output_reservation, "configured_output_reservation"
    for key, basis in (("prompt_tokens", "provider_prompt_tokens"), ("input_tokens", "provider_input_tokens")):
        count = _count(lookup(key))
        if count is not None:
            input_count, input_basis = count, lookup("input_token_basis") or basis
            break
    for key, basis in (("generation_tokens", "provider_generation_tokens"), ("output_tokens", "provider_output_tokens")):
        count = _count(lookup(key))
        if count is not None:
            output_count, output_basis = count, lookup("output_token_basis") or basis
            break
    else:
        raw = raw_output if raw_output is not None else lookup("raw_output")
        if isinstance(raw, str) and raw:
            output_count, output_basis = len(raw.encode("utf-8")), "utf8_output_estimate"
    if input_count < 0 or output_count < 0:
        raise ValueError("Token reservations must be non-negative")
    return TokenUsage(input_count, output_count, input_basis, output_basis)
