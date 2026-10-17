from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

import pytest

from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.mlx_runtime import MlxVlmRuntime
from esg_targeted.models.budgeted import BudgetedModel
from esg_targeted.contracts import SemanticDecision
from esg_targeted.runtime_budget import BudgetExceeded, ReportRuntimeBudget, RuntimeBudgetLimits, task_input_fingerprint
from esg_targeted.storage.power import IdleSleepAssertion, WatchdogClock


def test_budget_reservations_survive_restart_and_are_idempotent(tmp_path):
    path = tmp_path / "runtime.json"
    limits = RuntimeBudgetLimits(max_model_calls=2, max_input_tokens=20, max_output_tokens=30)
    budget = ReportRuntimeBudget(path, limits)
    first = budget.begin_call("source", input_tokens=5, max_output_tokens=10, call_id="one")
    assert budget.begin_call("source", input_tokens=5, max_output_tokens=10, call_id="one") == first
    restored = ReportRuntimeBudget(path, limits)
    restored.begin_call("mapping", input_tokens=5, max_output_tokens=10)
    with pytest.raises(BudgetExceeded, match="model call"):
        restored.begin_call("navigator", input_tokens=0, max_output_tokens=1)
    assert restored.snapshot()["usage"]["unfinished_calls"] == 2
    restored.finish_call("one", input_tokens=7, output_tokens=3, outcome="returned")
    restored.finish_call("one", input_tokens=99, output_tokens=99, outcome="returned")
    assert restored.snapshot()["usage"]["input_tokens"] == 12
    assert restored.snapshot()["usage"]["output_tokens"] == 13


def test_concurrent_budget_admission_cannot_overspend(tmp_path):
    limits = RuntimeBudgetLimits(max_model_calls=3)
    def attempt(index):
        budget = ReportRuntimeBudget(tmp_path / "runtime.json", limits)
        try:
            budget.begin_call("fake", input_tokens=1, max_output_tokens=1, call_id=str(index))
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 3


def test_generation_return_does_not_clear_protocol_failure_circuit(tmp_path):
    budget = ReportRuntimeBudget(tmp_path / "runtime.json", RuntimeBudgetLimits(max_consecutive_failures=2))
    context = {"object_id": "table-1", "group_ids": ["row-1"]}
    scope = task_input_fingerprint("understand", context)
    for index in range(2):
        call = budget.begin_call("source", input_tokens=1, max_output_tokens=1, scope=scope)
        budget.finish_call(call["call_id"], output_tokens=1, outcome="returned")
        budget.record_validation(call["call_id"], outcome="error", failure_signature="null-array" if index == 0 else "invented-reference")
        budget.record_validation(call["call_id"], outcome="error", failure_signature="null-array")
    with pytest.raises(BudgetExceeded, match="Identical task input") as rejected:
        budget.begin_call("source", input_tokens=1, max_output_tokens=1, scope=scope)
    assert rejected.value.boundary == "task", "A single failed input must not block independent report work"
    different_slice = task_input_fingerprint("understand", {**context, "group_ids": ["row-2"]})
    assert budget.begin_call("source", input_tokens=1, max_output_tokens=1, scope=different_slice)
    assert budget.snapshot()["failures"][scope]["consecutive"] == 2


@pytest.mark.parametrize("limit,kwargs,reason", [
    ({"max_format_repairs": 0}, {"is_format_repair": True}, "format repair"),
    ({"max_input_tokens": 1}, {"input_tokens": 2}, "input token"),
    ({"max_output_tokens": 1}, {"max_output_tokens": 2}, "output token"),
])
def test_budget_rejects_before_generation_without_consuming_call(tmp_path, limit, kwargs, reason):
    budget = ReportRuntimeBudget(tmp_path / "runtime.json", RuntimeBudgetLimits(**limit))
    arguments = {"input_tokens": 1, "max_output_tokens": 1, **kwargs}
    with pytest.raises(BudgetExceeded, match=reason):
        budget.begin_call("fake", **arguments)
    assert budget.snapshot()["usage"]["model_calls"] == 0
    assert "secret" not in json.dumps(budget.snapshot())


def test_power_assertion_is_nested_owner_scoped_and_cleanup_is_bounded(monkeypatch):
    from esg_targeted.storage import power
    monkeypatch.delenv("ESG_DISABLE_IDLE_SLEEP_ASSERTION", raising=False)
    monkeypatch.setattr(power, "sys", SimpleNamespace(platform="darwin"))
    events, commands = [], []
    class FakeProcess:
        pid = 900
        def __init__(self):
            self.terminated = self.killed = 0
        def poll(self):
            return None
        def terminate(self):
            self.terminated += 1
        def kill(self):
            self.killed += 1
        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("fake", timeout)
    process = FakeProcess()
    monkeypatch.setattr(power.subprocess, "Popen", lambda command, **_: commands.append(command) or process)
    with IdleSleepAssertion(events.append):
        with IdleSleepAssertion(events.append):
            assert len(commands) == 1
        assert process.terminated == 0
    assert commands[0][:3] == ["/usr/bin/caffeinate", "-i", "-w"]
    assert process.terminated == process.killed == 1
    assert [event["kind"] for event in events] == ["idle_sleep_assertion_acquired", "idle_sleep_assertion_released"]
    assert not power._owners


def test_watchdog_observes_suspend_and_rechecks_wall_deadline():
    clock = WatchdogClock(100, 10)
    assert clock.observe(100.5, 10.5, deadline=700) is None
    event = clock.observe(1500, 20, deadline=700)
    assert event["classification"] == "suspend_or_wall_clock_change"
    assert event["deadline_overdue_seconds"] == 800
    assert event["sleep_or_clock_adjustment_seconds"] == 1390
    stalled = WatchdogClock(100, 10).observe(110, 20)
    assert stalled["classification"] == "scheduler_observation_gap"
    assert stalled["sleep_or_clock_adjustment_seconds"] == 0


@pytest.mark.parametrize("finish_reason,category", [("length", "generation_incomplete"), ("stop", None)])
def test_mlx_length_is_generation_failure_with_partial_output(monkeypatch, finish_reason, category):
    module = ModuleType("mlx_vlm.generate")
    closed = []
    def stream(*_, **__):
        try:
            yield SimpleNamespace(text='{"rows":[', generation_tokens=4, finish_reason=finish_reason)
        finally:
            closed.append(True)
    module.stream_generate = stream
    monkeypatch.setitem(sys.modules, "mlx_vlm", ModuleType("mlx_vlm"))
    monkeypatch.setitem(sys.modules, "mlx_vlm.generate", module)
    runtime = MlxVlmRuntime()
    runtime._model = object()
    runtime._processor = SimpleNamespace(encode=lambda prompt: list(prompt))
    runtime.max_input_chars = runtime.max_input_tokens = 100
    runtime.timeout_seconds = 600
    runtime._render_prompt = lambda **_: "short prompt"
    kwargs = dict(context="{}", template="{}", instructions="", images=[], output_token_budget=4, cancel_check=None, preflight={})
    if category:
        with pytest.raises(ModelRunFailure) as failure:
            runtime._generate(**kwargs)
        assert failure.value.category == category
        assert failure.value.raw_output == '{"rows":['
        assert failure.value.telemetry["finish_reason"] == "length"
    else:
        assert runtime._generate(**kwargs)[0] == '{"rows":['
    assert closed == [True]


def test_two_role_adapters_share_ledger_and_preserve_provider_capabilities(tmp_path):
    class FakeModel:
        max_tokens = 10
        provider_name = "fake"
        timeout_seconds = 600
        closed = False
        def decide(self, packet, **kwargs):
            return SimpleNamespace(decision=SemanticDecision(task_id="t", status="not_found"),
                prompt_tokens=5, generation_tokens=2, raw_output="{}", finish_reason="stop")
        def generate_structured(self, **kwargs):
            return "{}", {}
        def close(self):
            self.closed = True
    budget = ReportRuntimeBudget(tmp_path / "runtime.json", RuntimeBudgetLimits(max_model_calls=2))
    brain_raw, specialist_raw = FakeModel(), FakeModel()
    brain = BudgetedModel(brain_raw, budget, role="semantic_brain")
    specialist = BudgetedModel(specialist_raw, budget, role="specialist")
    assert brain.provider_name == "fake" and not hasattr(brain, "readjudicate_family")
    brain.timeout_seconds = 20
    assert brain_raw.timeout_seconds == 20
    brain.generate_structured(context={})  # disclosure owns this accounting
    assert budget.snapshot()["usage"]["model_calls"] == 0
    for model in (brain, specialist):
        assert model.decide(SimpleNamespace(model_context="small"), feedback="source evidence correction").decision.status == "not_found"
    assert budget.snapshot()["usage"] == {"model_calls": 2, "format_repairs": 0,
        "input_tokens": 10, "output_tokens": 4, "unfinished_calls": 0}
    with pytest.raises(BudgetExceeded):
        brain.decide(SimpleNamespace(model_context="small"))
    brain.close()
    assert brain_raw.closed


@pytest.mark.parametrize("failure", ["length", "timeout", "protocol", "invalid_decision"])
def test_budget_adapter_propagates_failure_and_never_resets_circuit(tmp_path, failure):
    class FakeModel:
        max_tokens = 10
        def decide(self, packet, **kwargs):
            if failure == "timeout":
                raise ModelRunFailure("hard timeout", category="timeout", telemetry={"prompt_tokens": 5, "generation_tokens": 2})
            decision = "not a protocol" if failure == "protocol" else SemanticDecision(
                task_id="t", status="ambiguous", uncertainty_code="model_output_invalid" if failure == "invalid_decision" else "none")
            return SimpleNamespace(decision=decision, prompt_tokens=5, generation_tokens=2,
                raw_output="partial", finish_reason="length" if failure == "length" else "stop")
    budget = ReportRuntimeBudget(tmp_path / "runtime.json", RuntimeBudgetLimits(max_consecutive_failures=1))
    model = BudgetedModel(FakeModel(), budget)
    packet = {"rows": ["slice-1"]}
    if failure == "invalid_decision":
        assert model.decide(packet).decision.uncertainty_code == "model_output_invalid"
    else:
        with pytest.raises(ModelRunFailure) as error:
            model.decide(packet)
        assert error.value.category == {"length": "generation_incomplete", "timeout": "timeout", "protocol": "protocol"}[failure]
    assert budget.snapshot()["usage"]["model_calls"] == 1
    assert budget.snapshot()["usage"]["input_tokens"] == 5
    assert budget.snapshot()["usage"]["output_tokens"] == 2
    with pytest.raises(BudgetExceeded, match="Identical task input"):
        model.decide(packet)
