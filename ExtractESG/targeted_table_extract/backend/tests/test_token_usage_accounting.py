from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_targeted.models.budgeted import BudgetedModel
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.mlx_runtime import MlxVlmRuntime
from esg_targeted.models.token_usage import resolve_token_usage
from esg_targeted.runtime_budget import ReportRuntimeBudget, RuntimeBudgetLimits


@pytest.mark.parametrize("record", [
    {"prompt_tokens": 110, "input_tokens": 10, "generation_tokens": 9, "output_tokens": 2},
    SimpleNamespace(prompt_tokens=110, input_tokens=10, generation_tokens=9, output_tokens=2),
])
def test_engine_usage_including_image_tokens_precedes_text_count(record):
    usage = resolve_token_usage(record, input_reservation=300, output_reservation=128)
    assert usage.to_dict() == {"input_tokens": 110, "output_tokens": 9,
        "input_token_basis": "provider_prompt_tokens", "output_token_basis": "provider_generation_tokens"}


@pytest.mark.parametrize("invalid", [None, True, -1, "nan", "inf", "2.5", "invalid"])
def test_missing_or_invalid_preferred_count_does_not_mask_valid_secondary(invalid):
    usage = resolve_token_usage({"prompt_tokens": invalid, "input_tokens": 12,
        "generation_tokens": invalid, "output_tokens": 4}, input_reservation=300, output_reservation=128)
    assert (usage.input_tokens, usage.output_tokens) == (12, 4)
    assert usage.input_token_basis == "provider_input_tokens"


def test_explicit_zero_is_observed_but_absent_telemetry_keeps_reservation():
    observed = resolve_token_usage({"prompt_tokens": 0, "generation_tokens": 0}, input_reservation=300, output_reservation=128)
    assert (observed.input_tokens, observed.output_tokens) == (0, 0)
    absent = resolve_token_usage({}, input_reservation=300, output_reservation=128)
    assert (absent.input_tokens, absent.output_tokens) == (300, 128)
    assert absent.output_token_basis == "configured_output_reservation"
    partial = resolve_token_usage({}, input_reservation=300, output_reservation=128, raw_output="中")
    assert partial.output_tokens == 3 and partial.output_token_basis == "utf8_output_estimate"


def test_only_instrumented_no_generation_releases_reservation():
    usage = resolve_token_usage({"generation_started": False, "input_tokens": 150},
        input_reservation=300, output_reservation=128)
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)
    assert usage.input_token_basis == "generation_not_started"
    # A category alone proves neither whether prefill started nor usage.
    unknown = resolve_token_usage({"failure_category": "request_config"}, input_reservation=300, output_reservation=128)
    assert unknown.input_tokens == 300


def test_failed_generation_shared_adapter_charges_measured_prompt_and_partial_output(tmp_path):
    class FailedModel:
        max_tokens = 4096
        def decide(self, *_args, **_kwargs):
            raise ModelRunFailure("truncated", category="generation_incomplete", raw_output='{"rows":[',
                telemetry={"input_tokens": None, "prompt_tokens": 101, "output_tokens": None,
                    "generation_tokens": 4096, "finish_reason": "length"})
    budget = ReportRuntimeBudget(tmp_path / "usage.json")
    with pytest.raises(ModelRunFailure):
        BudgetedModel(FailedModel(), budget).decide({"long_source": "中" * 200})
    snapshot = budget.snapshot()
    assert snapshot["usage"]["input_tokens"] == 101
    assert snapshot["usage"]["output_tokens"] == 4096
    assert snapshot["accounting"]["input"] == {"provider_prompt_tokens": 101}
    assert snapshot["accounting"]["output"] == {"provider_generation_tokens": 4096}


def test_unknown_failure_retains_estimates_with_separate_basis(tmp_path):
    class FailedModel:
        max_tokens = 100
        def decide(self, *_args, **_kwargs):
            raise OSError("provider disappeared without usage")
    budget = ReportRuntimeBudget(tmp_path / "usage.json")
    with pytest.raises(OSError):
        BudgetedModel(FailedModel(), budget).decide({"source": "x"})
    snapshot = budget.snapshot()
    assert snapshot["usage"]["input_tokens"] > 0
    assert snapshot["usage"]["output_tokens"] == 100
    assert set(snapshot["accounting"]["input"]) == {"estimated_prompt_tokens"}
    assert snapshot["accounting"]["output"] == {"configured_output_reservation": 100}


def test_legacy_accounting_does_not_relabel_or_rewrite_old_call_records(tmp_path):
    path = tmp_path / "usage.json"
    original = {"old": {"label": "understand", "scope": "", "is_format_repair": False,
        "input_tokens": 100, "output_tokens": 20, "max_output_tokens": 30, "state": "finished"}}
    path.write_text(json.dumps({"schema_version": "report-runtime-budget/1", "calls": original, "failures": {}}))
    budget = ReportRuntimeBudget(path)
    snapshot = budget.snapshot()
    assert snapshot["accounting"]["input"] == {"legacy_unclassified": 100}
    assert json.loads(path.read_text())["calls"] == original
    call = budget.begin_call("new", input_tokens=10, max_output_tokens=20, input_token_basis="utf8_text_reservation")
    usage = resolve_token_usage({"prompt_tokens": 12, "generation_tokens": 4}, input_reservation=10, output_reservation=20)
    budget.finish_call(call["call_id"], **usage.finish_kwargs(), outcome="returned")
    budget.finish_call(call["call_id"], input_tokens=999, output_tokens=999)
    assert budget.snapshot()["usage"]["input_tokens"] == 112
    assert json.loads(path.read_text())["calls"]["old"] == original["old"]


def test_real_v4_truncated_calls_replay_engine_counts_not_old_byte_reservations(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/real_provider_usage_v4.json").read_text())
    calls = [row for row in fixture["calls"] if row["telemetry"]["failure_category"] == "generation_incomplete"]
    assert len(calls) == 12
    budget = ReportRuntimeBudget(tmp_path / "replay.json")
    for row in calls:
        assert len(row["source_sha256"]) == 64
        call = budget.begin_call("source_understanding", input_tokens=row["ledger_input_charged"],
            max_output_tokens=row["output_reservation"], input_token_basis="utf8_text_reservation")
        usage = resolve_token_usage(row["telemetry"], input_reservation=row["ledger_input_charged"],
            output_reservation=row["output_reservation"])
        budget.finish_call(call["call_id"], **usage.finish_kwargs(), outcome="error", failure_signature="length")
    snapshot = budget.snapshot()
    assert snapshot["usage"]["input_tokens"] == sum(row["telemetry"]["prompt_tokens"] for row in calls)
    assert snapshot["usage"]["output_tokens"] == 12 * 4096
    assert snapshot["usage"]["input_tokens"] < sum(row["ledger_input_charged"] for row in calls)
    assert set(snapshot["accounting"]["input"]) == {"provider_prompt_tokens"}


def test_constraints_failure_before_inference_is_explicitly_instrumented(monkeypatch):
    import sys
    from types import ModuleType
    module = ModuleType("mlx_vlm.generate")
    module.stream_generate = lambda *_args, **_kwargs: pytest.fail("inference must not start")
    monkeypatch.setitem(sys.modules, "mlx_vlm", ModuleType("mlx_vlm"))
    monkeypatch.setitem(sys.modules, "mlx_vlm.generate", module)
    runtime = MlxVlmRuntime()
    runtime._model = object()
    runtime._processor = SimpleNamespace(encode=lambda prompt: list(prompt))
    runtime.max_input_chars = runtime.max_input_tokens = 100
    runtime._render_prompt = lambda **_: "prompt"
    def reject(**_kwargs):
        raise ModelRunFailure("schema rejected", category="request_config")
    runtime._generation_constraints = reject
    with pytest.raises(ModelRunFailure) as failure:
        runtime._generate(context="{}", template="{}", instructions="", images=[], output_token_budget=128,
            cancel_check=None, preflight={})
    assert failure.value.telemetry["generation_started"] is False
    usage = resolve_token_usage(failure.value.telemetry, input_reservation=100, output_reservation=128)
    assert usage.input_tokens == usage.output_tokens == 0
