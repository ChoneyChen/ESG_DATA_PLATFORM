from __future__ import annotations

from esg_v2.document.review_response_adapter import ReviewResponseAdapter
from esg_v2.runtime_telemetry import RunTelemetryTracker


def test_verifier_aliases_are_normalized_before_contract_validation() -> None:
    payload = ReviewResponseAdapter.normalize_verifier(
        {
            "verdict": "approve",
            "confidence": 0.93,
            "transaction_decisions": [
                {
                    "transaction_id": "transaction-000001",
                    "decision": "accept",
                    "rationale": "The patch matches the visible evidence.",
                }
            ],
        }
    )

    assert payload["verdict"] == "accept"
    assert payload["transaction_decisions"] == [
        {
            "transaction_id": "transaction-000001",
            "decision": "accept",
            "rationale": "The patch matches the visible evidence.",
            "verdict": "accept",
            "confidence": 0.93,
            "disagreements": "The patch matches the visible evidence.",
        }
    ]


def test_runtime_telemetry_counts_logical_and_actual_model_attempts() -> None:
    snapshots = []
    tracker = RunTelemetryTracker(snapshots.append, started_at="2026-08-05T00:00:00+00:00")
    tracker.event({"event": "stage_started", "index": 9, "total": 11, "name": "Agent review"})
    tracker.event(
        {
            "event": "logical_model_call_started",
            "task_id": "review-000001",
            "role": "reviewer",
            "round_index": 1,
        }
    )
    tracker.event(
        {
            "event": "model_attempt_started",
            "task_id": "review-000001",
            "role": "reviewer",
            "round_index": 1,
            "retry_index": 1,
            "model_id": "test-model",
        }
    )
    tracker.event(
        {
            "event": "model_attempt_completed",
            "status": "succeeded",
            "failure_category": "none",
            "latency_ms": 1250,
            "usage": {"total_tokens": 400},
        }
    )
    final = tracker.finish("done")

    calls = final["model_calls"]
    assert calls["logical_started"] == 1
    assert calls["actual_attempts"] == 1
    assert calls["completed_attempts"] == 1
    assert calls["retries"] == 1
    assert calls["succeeded"] == 1
    assert calls["total_latency_ms"] == 1250
    assert calls["usage"]["total_tokens"] == 400
    assert final["stages"][0]["status"] == "done"
