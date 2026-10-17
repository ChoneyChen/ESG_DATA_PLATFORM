"""Synthetic task-scope acceptance. No models or report fixtures are used."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from esg_targeted.budget_scope import POLICY_VERSION, package_model_budget, report_model_usage
from esg_targeted.runtime_budget import BudgetExceeded, ReportRuntimeBudget
from esg_targeted.storage.artifacts import ArtifactStore


def request(package_id="package-a", session_id="session", **overrides):
    limits = dict(max_model_calls=10, max_format_repairs=2, max_input_tokens=100,
                  max_output_tokens=100, max_consecutive_failures=3)
    limits.update(overrides)
    return SimpleNamespace(package_id=package_id, report_session_id=session_id,
                           call_budget=SimpleNamespace(model_dump=lambda: dict(limits)))


def charge(budget, call_id, amount, label="semantic_brain"):
    budget.begin_call(label, call_id=call_id, input_tokens=amount, max_output_tokens=1)
    budget.finish_call(call_id, input_tokens=amount, output_tokens=1,
                       input_token_basis="provider_prompt_tokens",
                       output_token_basis="provider_generation_tokens")


def trace(store, job_id, call_id):
    store.write_json(job_id, f"agent/calls/{call_id}.json", {"budget_call_id": call_id})


def projections():
    return [{"job_id": "job-a", "package_id": "package-a"},
            {"job_id": "job-b", "package_id": "package-b"}]


def disk_state(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
            for path in root.rglob("*")}


def test_each_package_receives_full_allowance_and_exhaustion_is_independent(tmp_path):
    store = ArtifactStore(tmp_path)
    first = package_model_budget(store, request(), "job-a")
    second = package_model_budget(store, request("package-b"), "job-b")
    charge(first, "a", 95)
    with pytest.raises(BudgetExceeded):
        first.begin_call("specialist", input_tokens=6, max_output_tokens=1)
    charge(second, "b", 95, "specialist")
    aggregate = report_model_usage(store, "session", projections())
    assert aggregate["usage"]["input_tokens"] == 190
    assert aggregate["enforces_report_limit"] is False
    assert [p["limits"]["max_input_tokens"] for p in aggregate["packages"]] == [100, 100]
    assert [p["usage"]["input_tokens"] for p in aggregate["packages"]] == [95, 95]


def test_roles_in_one_package_share_budget_and_reopen_does_not_reset_usage(tmp_path):
    store = ArtifactStore(tmp_path)
    brain = package_model_budget(store, request(max_model_calls=1), "job-a")
    charge(brain, "brain-call", 60)
    specialist = package_model_budget(store, request(max_model_calls=1), "job-a")
    with pytest.raises(BudgetExceeded, match="model call"):
        specialist.begin_call("specialist", input_tokens=10, max_output_tokens=1)
    assert specialist.snapshot()["usage"]["input_tokens"] == 60
    assert specialist.snapshot()["usage"]["model_calls"] == 1


def test_legacy_cost_is_carried_only_to_proven_owner_exactly_once(tmp_path):
    store = ArtifactStore(tmp_path)
    legacy = ReportRuntimeBudget(store.report_session_path("session") / "runtime-budget.json")
    charge(legacy, "a", 20)
    charge(legacy, "b", 30)
    charge(legacy, "unknown", 40)
    trace(store, "job-a", "a")
    trace(store, "job-b", "b")
    legacy_before = legacy.path.read_bytes()
    owned = package_model_budget(store, request(), "job-a")
    assert owned.snapshot()["usage"]["input_tokens"] == 20
    package_model_budget(store, request(), "job-a")
    assert owned.snapshot()["usage"]["model_calls"] == 1
    record = json.loads(owned.path.read_text())
    assert record["scope_policy"] == POLICY_VERSION
    assert record["legacy_carried_call_ids"] == ["a"]
    aggregate = report_model_usage(store, "session", projections())
    assert aggregate["usage"]["input_tokens"] == 90
    assert aggregate["usage"]["model_calls"] == 3
    assert [p["usage"]["input_tokens"] for p in aggregate["packages"]] == [20, 30]
    assert aggregate["packages"][1]["legacy_attributed"] is True
    assert aggregate["legacy_unattributed_usage"]["input_tokens"] == 40
    assert legacy.path.read_bytes() == legacy_before


def test_report_usage_read_does_not_initialize_or_rewrite_artifacts(tmp_path):
    store = ArtifactStore(tmp_path)
    assert report_model_usage(store, "not-created", projections()) is None
    assert disk_state(tmp_path) == {}
    first = package_model_budget(store, request(), "job-a")
    charge(first, "a", 15)
    before = disk_state(tmp_path)
    one = report_model_usage(store, "session", projections())
    two = report_model_usage(store, "session", projections())
    assert one == two
    assert one["packages"][1]["usage"]["model_calls"] == 0
    assert one["packages"][1]["limits"] is None
    assert disk_state(tmp_path) == before
    assert not store._job_path("job-b").exists()
    assert not store.report_session_path("session").exists()


def test_aggregate_preserves_reserved_cost_and_accounting_basis(tmp_path):
    store = ArtifactStore(tmp_path)
    budget = package_model_budget(store, request(), "job-a")
    budget.begin_call("semantic_brain", call_id="unfinished", input_tokens=20,
                      input_token_basis="estimated_prompt_tokens", max_output_tokens=35)
    aggregate = report_model_usage(store, "session", projections())
    assert aggregate["usage"]["unfinished_calls"] == 1
    assert aggregate["usage"]["output_tokens"] == 35
    assert aggregate["accounting"]["input"] == {"estimated_prompt_tokens": 20}
    assert aggregate["accounting"]["output"] == {"configured_output_reservation": 35}


def test_conflicting_duplicate_call_identity_is_rejected_without_mutation(tmp_path):
    store = ArtifactStore(tmp_path)
    first = package_model_budget(store, request(), "job-a")
    second = package_model_budget(store, request("package-b"), "job-b")
    charge(first, "same", 15)
    first_record = json.loads(first.path.read_text())
    second_record = json.loads(second.path.read_text())
    second_record["calls"]["same"] = deepcopy(first_record["calls"]["same"])
    second_record["calls"]["same"]["input_tokens"] += 1
    second.path.write_text(json.dumps(second_record))
    before = disk_state(tmp_path)
    with pytest.raises(ValueError, match="Conflicting usage"):
        report_model_usage(store, "session", projections())
    assert disk_state(tmp_path) == before
