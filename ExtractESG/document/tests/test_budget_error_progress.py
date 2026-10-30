from __future__ import annotations

import pytest

from esg_runtime.progress import classify_error


def test_typed_package_budget_is_neither_deadline_nor_credentials():
    error = classify_error("runtime budget / input token / JSON", producer_error={
        "failure_category": "budget", "budget_boundary": "package"})
    assert (error.category, error.code, error.next_action) == (
        "runtime", "model_budget_exceeded", "adjust_task_budget")
    assert "该标准包" in error.message and "时限" not in error.message


def test_explicit_producer_code_and_category_outrank_incidental_text():
    error = classify_error("BudgetExceeded: token timeout", producer_error={
        "category": "source", "code": "input_unreadable", "message": "证据不可读",
        "failure_category": "budget", "budget_boundary": "package"})
    assert (error.category, error.code, error.message) == ("source", "input_unreadable", "证据不可读")


def test_typed_budget_code_has_default_label_and_action_without_string_guess():
    error = classify_error("an incidental timeout description", producer_error={
        "category": "runtime", "code": "model_budget_exceeded"})
    assert error.next_action == "adjust_task_budget"
    assert "预算限制" in error.message and error.code == "model_budget_exceeded"


@pytest.mark.parametrize("message", [
    "BudgetExceeded: Report runtime budget exhausted; retained stages available",
    "esg_extraction.runtime_budget.BudgetExceeded: request rejected",
    "Report input token budget exhausted (102 > 100)",
    "Report prepared input token budget exhausted (102 > 100)",
    "Report input token estimated admission budget exhausted (102 > 100)",
])
def test_explicit_legacy_model_budget_fallback(message):
    error = classify_error(message)
    assert error.code == "model_budget_exceeded" and error.next_action == "adjust_task_budget"


@pytest.mark.parametrize("message", [
    "Model call 'read' exceeded its external active hard deadline",
    "Report session total execution time budget exhausted",
    "TimeoutError: Report session total execution budget exhausted",
])
def test_real_deadlines_keep_their_own_code(message):
    assert classify_error(message).code == "execution_deadline"


def test_plain_credentials_error_keeps_credential_path():
    assert classify_error("Provider token credential invalid").code == "credential_required"


def test_runtime_word_alone_does_not_match_time_budget_substring():
    # Unknown future runtime errors should not get a fabricated deadline.
    assert classify_error("New runtime budget policy rejected configuration").code == "execution_failed"
