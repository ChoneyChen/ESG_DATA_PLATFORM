from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from esg_targeted.models.mlx_runtime import MlxVlmRuntime
from esg_targeted.models.token_admission import (
    InputAdmission, admit_model_input, bind_runtime_admission, estimate_input_tokens,
)
from esg_targeted.models.token_usage import resolve_token_usage
from esg_targeted.runtime_budget import BudgetExceeded, ReportRuntimeBudget, RuntimeBudgetLimits


def _engine(monkeypatch, *, token_count=17):
    generated = []
    vlm = ModuleType("mlx_vlm")
    utils = ModuleType("mlx_vlm.utils")
    generate = ModuleType("mlx_vlm.generate")
    utils.should_add_special_tokens = lambda *_: False
    utils.prepare_inputs = lambda *_args, **kwargs: {
        "input_ids": SimpleNamespace(size=token_count), "pixel_values": "prepared pixels",
        "attention_mask": "prepared mask", "image_grid_thw": "prepared grid",
    }
    def stream(*_args, **kwargs):
        generated.append(kwargs)
        yield SimpleNamespace(text="{}", prompt_tokens=token_count, generation_tokens=2,
                              finish_reason="stop")
    generate.stream_generate = stream
    monkeypatch.setitem(sys.modules, "mlx_vlm", vlm)
    monkeypatch.setitem(sys.modules, "mlx_vlm.utils", utils)
    monkeypatch.setitem(sys.modules, "mlx_vlm.generate", generate)
    runtime = MlxVlmRuntime()
    runtime._model = SimpleNamespace(config=SimpleNamespace(model_type="fake", image_token_index=2))
    runtime._processor = SimpleNamespace(encode=lambda prompt: list(prompt))
    runtime._render_prompt = lambda **_: "actual formatted prompt"
    runtime.max_input_chars = runtime.max_input_tokens = 10000
    runtime.timeout_seconds = 10
    return runtime, generated


def test_cold_admission_does_not_load_weights_or_claim_visual_precision():
    runtime = MlxVlmRuntime()
    runtime._model = runtime._processor = None
    runtime._ensure_loaded = lambda: pytest.fail("admission must never load weights")
    admission = admit_model_input(runtime, context={"value": "中" * 50}, instructions="",
                                   template={}, image_path="unopened.png")
    assert not admission.exact and admission.runtime_refinement
    assert admission.basis == "estimated_prompt_tokens"
    assert admission.visual_tokens is None
    assert "visual_token_expansion_unknown" in admission.warnings


def test_ready_admission_counts_prepared_image_expansion(monkeypatch):
    runtime, generated = _engine(monkeypatch, token_count=517)
    runtime._ensure_loaded = lambda: pytest.fail("already ready count must not load")
    admission = admit_model_input(runtime, context={"row": 1}, instructions="read", template={}, image_path="fake.png")
    assert admission.tokens == 517 and admission.exact
    assert admission.basis == "prepared_engine_input_tokens"
    assert admission.visual_tokens is None  # No fabricated text/image split.
    assert not generated


def test_mixed_script_estimate_is_not_utf8_byte_usage():
    text = "中文" * 100 + "plain JSON keys " * 100
    admission = estimate_input_tokens(text)
    assert admission.tokens != len(text.encode("utf-8"))
    assert admission.basis == "estimated_prompt_tokens" and not admission.exact


def test_cold_estimate_can_refine_down_without_false_boundary(tmp_path, monkeypatch):
    runtime, generated = _engine(monkeypatch, token_count=17)
    budget = ReportRuntimeBudget(tmp_path / "budget.json", RuntimeBudgetLimits(max_input_tokens=100))
    estimate = InputAdmission(1000, "estimated_prompt_tokens", runtime_refinement=True)
    call = budget.begin_call("read", input_tokens=estimate.tokens, max_output_tokens=20,
                            input_token_basis=estimate.basis, input_admission=estimate.to_dict())
    with bind_runtime_admission(runtime, budget, call["call_id"]):
        _, telemetry = runtime._generate(context="{}", template="{}", instructions="", images=["fake.png"],
            output_token_budget=20, cancel_check=None, preflight={})
    usage = resolve_token_usage(telemetry, input_reservation=estimate.tokens, output_reservation=20,
                                input_reservation_basis=estimate.basis)
    budget.finish_call(call["call_id"], **usage.finish_kwargs())
    assert budget.snapshot()["usage"]["input_tokens"] == 17
    assert budget.snapshot()["usage"]["model_calls"] == 1
    assert generated[0]["input_ids"].size == 17
    assert generated[0]["mask"] == "prepared mask"
    assert generated[0]["image_grid_thw"] == "prepared grid"
    assert not hasattr(runtime, "_input_admission_callback")


def test_prepared_over_limit_stops_before_any_forward(tmp_path, monkeypatch):
    runtime, generated = _engine(monkeypatch, token_count=101)
    budget = ReportRuntimeBudget(tmp_path / "budget.json", RuntimeBudgetLimits(max_input_tokens=100))
    admission = InputAdmission(10, "estimated_prompt_tokens", runtime_refinement=True)
    call = budget.begin_call("read", input_tokens=10, max_output_tokens=20,
                             input_admission=admission.to_dict())
    with pytest.raises(BudgetExceeded, match="prepared input") as failure:
        with bind_runtime_admission(runtime, budget, call["call_id"]):
            runtime._generate(context="{}", template="{}", instructions="", images=["fake.png"],
                              output_token_budget=20, cancel_check=None, preflight={})
    assert not generated and failure.value.telemetry["generation_started"] is False
    usage = resolve_token_usage(failure.value.telemetry, input_reservation=10, output_reservation=20)
    budget.finish_call(call["call_id"], **usage.finish_kwargs(), outcome="error")
    assert budget.snapshot()["usage"]["input_tokens"] == 0


def test_unknown_remote_estimate_cannot_defer_a_hard_limit(tmp_path):
    budget = ReportRuntimeBudget(tmp_path / "budget.json", RuntimeBudgetLimits(max_input_tokens=100))
    estimate = estimate_input_tokens("x" * 1000)
    with pytest.raises(BudgetExceeded, match="estimated admission"):
        budget.begin_call("cloud", input_tokens=estimate.tokens, max_output_tokens=10,
                          input_token_basis=estimate.basis, input_admission=estimate.to_dict())
    assert budget.snapshot()["usage"]["model_calls"] == 0


def test_refinement_preserves_historical_provider_usage(tmp_path):
    path = tmp_path / "budget.json"
    budget = ReportRuntimeBudget(path)
    first = budget.begin_call("first", input_tokens=5, max_output_tokens=10)
    budget.finish_call(first["call_id"], input_tokens=12, output_tokens=2,
                       input_token_basis="provider_prompt_tokens")
    original = json.loads(path.read_text())["calls"][first["call_id"]]
    next_call = budget.begin_call("second", input_tokens=20, max_output_tokens=10)
    budget.revise_input_reservation(next_call["call_id"], input_tokens=13,
                                    input_token_basis="prepared_engine_input_tokens")
    assert budget.snapshot()["usage"]["input_tokens"] == 25
    assert budget.snapshot()["usage"]["model_calls"] == 2
    assert json.loads(path.read_text())["calls"][first["call_id"]] == original
    with pytest.raises(ValueError, match="finished"):
        budget.revise_input_reservation(first["call_id"], input_tokens=0, input_token_basis="fake")


def test_failed_unknown_usage_keeps_estimation_basis():
    usage = resolve_token_usage({}, input_reservation=50, output_reservation=20,
                                input_reservation_basis="estimated_prompt_tokens")
    assert usage.input_token_basis == "estimated_prompt_tokens"


def test_native_failure_keeps_refined_reservation_instead_of_cold_estimate(tmp_path, monkeypatch):
    runtime, _ = _engine(monkeypatch, token_count=17)
    def fail(*_args, **_kwargs):
        raise OSError("native evaluation ended without usage")
        yield  # A generator which fails on its first iteration.
    monkeypatch.setattr(sys.modules["mlx_vlm.generate"], "stream_generate", fail)
    budget = ReportRuntimeBudget(tmp_path / "budget.json", RuntimeBudgetLimits(max_input_tokens=100))
    estimate = InputAdmission(1000, "estimated_prompt_tokens", runtime_refinement=True)
    call = budget.begin_call("read", input_tokens=1000, max_output_tokens=20,
                             input_admission=estimate.to_dict())
    with pytest.raises(OSError) as failure:
        with bind_runtime_admission(runtime, budget, call["call_id"]):
            runtime._generate(context="{}", template="{}", instructions="", images=["fake.png"],
                              output_token_budget=20, cancel_check=None, preflight={})
    usage = resolve_token_usage(failure.value.telemetry, input_reservation=estimate.tokens,
                                input_reservation_basis=estimate.basis, output_reservation=20)
    assert usage.input_tokens == 17 and usage.input_token_basis == "prepared_engine_input_tokens"
