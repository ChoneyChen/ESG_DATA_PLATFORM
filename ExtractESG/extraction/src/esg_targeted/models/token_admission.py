"""Input admission in model-token units, separate from packet byte limits.

This module never loads weights or runs inference. Ready adapters can prepare
their exact engine inputs; cold/remote adapters explicitly retain an estimate.
MLX refines that reservation after normal runtime loading, before inference.
"""
from __future__ import annotations

import contextlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True)
class InputAdmission:
    tokens: int
    basis: str
    exact: bool = False
    text_tokens: int | None = None
    visual_tokens: int | None = None
    warnings: tuple[str, ...] = ()
    runtime_refinement: bool = False

    def __post_init__(self):
        if isinstance(self.tokens, bool) or not isinstance(self.tokens, int) or self.tokens < 0:
            raise ValueError("Input token admission must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _delegate(model):
    # Budgeted task roles can share the same runtime. Never call close/load.
    return getattr(model, "_delegate", model)


def estimate_input_tokens(text: str, *, image_count: int = 0) -> InputAdmission:
    """An explicitly approximate cold/remote reservation, never byte usage.

    The mixed-script heuristic allocates one token per non-ASCII character and
    one per three ASCII characters, plus chat framing. It is not a tokenizer
    count or an upper bound. Image expansion is unknown until processor prep.
    """
    non_ascii = sum(ord(char) > 127 for char in text)
    text_tokens = non_ascii + math.ceil((len(text) - non_ascii) / 3) + 64
    # A placeholder allowance must never be described as measured image usage.
    visual_tokens = 2048 * image_count if image_count else 0
    return InputAdmission(text_tokens + visual_tokens, "estimated_prompt_tokens", False,
        text_tokens, None if image_count else 0,
        ("visual_token_expansion_unknown",) if image_count else ("tokenizer_not_prepared",))


def admit_model_input(model, *, context, instructions, template, image_path=None) -> InputAdmission:
    """Count a ready provider's full request or return a labelled estimate."""
    adapter = _delegate(model)
    prepare = getattr(adapter, "input_admission", None)
    if callable(prepare):
        admission = prepare(context=context, instructions=instructions,
                            template=template, image_path=image_path)
        if admission is not None:
            if not isinstance(admission, InputAdmission):
                raise TypeError("Model input admission must return InputAdmission")
            return admission
    text = json.dumps({"context": context, "instructions": instructions, "template": template},
                      ensure_ascii=False, separators=(",", ":"), default=str)
    return replace(estimate_input_tokens(text, image_count=int(bool(image_path))),
                   runtime_refinement=callable(getattr(adapter, "_prepare_runtime_inputs", None)))


@contextlib.contextmanager
def bind_runtime_admission(model, budget, call_id: str):
    """Refine only this active call after runtime preparation, before inference.

    No model state is prepared on entry. Single-worker execution prevents two
    simultaneous calls on one runtime; nested bindings restore their parent.
    """
    adapter = _delegate(model)
    sentinel = object()
    previous = getattr(adapter, "_input_admission_callback", sentinel)
    prepared = []
    def refine(admission: InputAdmission):
        budget.revise_input_reservation(call_id, input_tokens=admission.tokens,
                                       input_token_basis=admission.basis,
                                       input_admission=admission.to_dict())
        prepared[:] = [admission]
    adapter._input_admission_callback = refine
    try:
        yield
    except Exception as exc:
        telemetry = getattr(exc, "telemetry", {}) or {}
        if (prepared and telemetry.get("generation_started") is not False
                and telemetry.get("prompt_tokens") is None and telemetry.get("input_tokens") is None):
            # A native failure may omit provider usage. Retain the refined
            # prepared reservation rather than restoring the cold heuristic;
            # its basis still makes clear this is not observed consumption.
            try:
                exc.telemetry = {**telemetry, "input_tokens": prepared[0].tokens,
                                 "input_token_basis": prepared[0].basis}
            except (AttributeError, TypeError):
                pass
        raise
    finally:
        if previous is sentinel:
            delattr(adapter, "_input_admission_callback")
        else:
            adapter._input_admission_callback = previous
