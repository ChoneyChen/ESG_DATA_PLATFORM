from copy import deepcopy

from esg_v2.control_plane.contracts import PipelineTaskStatus, PipelineTaskType
from esg_v2.control_plane.outcomes import stage_outcome
from esg_v2.control_plane.scheduler import snapshot_fingerprint
from esg_v2.control_plane.store import PipelineQueueStore


def test_heartbeat_does_not_duplicate_progress_but_real_artifact_changes_do():
    snapshot = {"execution_status": "running", "revision": "one", "updated_at": "one",
                "jobs": [{"status": "running", "progress_current": 1, "heartbeat_at": "one",
                          "summary": {"counts": {"candidate_source_facts": 3}, "current_call": {
                              "phase": "generation", "generation_tokens": 64, "elapsed_seconds": 10}}}], "gaps": []}
    polling = deepcopy(snapshot)
    polling.update(revision="two", updated_at="two")
    polling["jobs"][0]["heartbeat_at"] = "two"
    polling["jobs"][0]["summary"]["current_call"].update(generation_tokens=70, elapsed_seconds=11)
    assert snapshot_fingerprint(snapshot) == snapshot_fingerprint(polling)
    for field, value in [("progress_current", 2), ("status", "interrupted")]:
        changed = deepcopy(polling)
        changed["jobs"][0][field] = value
        assert snapshot_fingerprint(snapshot) != snapshot_fingerprint(changed)
    changed = deepcopy(polling)
    changed["jobs"][0]["summary"]["counts"]["candidate_source_facts"] = 4
    assert snapshot_fingerprint(snapshot) != snapshot_fingerprint(changed)
    changed["jobs"][0]["summary"]["current_call"]["generation_tokens"] = 96
    assert snapshot_fingerprint(snapshot) != snapshot_fingerprint(changed)


def test_interrupted_report_propagates_candidate_maturity_scope_and_child_error(tmp_path):
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(task_id="pt-fake", task_type=PipelineTaskType.TARGETED_EXTRACTION,
                        operation="report_session", title="Report", native_job_id="report-fake",
                        payload={"input": {"kind": "document_ir", "ir_run_id": "ir-fake"}})
    task = store.update(task.task_id, status=PipelineTaskStatus.INTERRUPTED)
    result = {"result_status": "needs_confirmation", "artifacts": [{"kind": "source_candidates", "count": 261}],
              "jobs": [{"status": "interrupted", "error": "generation hard deadline", "summary": {"errors": ["protocol failure"]}}]}
    outcome = stage_outcome(task, result)
    assert outcome.execution_status == "interrupted"
    assert outcome.result_status == "needs_confirmation"
    assert outcome.valid_scope["ir_run_id"] == "ir-fake"
    assert outcome.artifacts[0]["kind"] == "source_candidates"
    assert outcome.errors == ["generation hard deadline", "protocol failure"]


def test_finished_limited_projections_never_offer_empty_resume(tmp_path):
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(task_id="pt-fake", task_type=PipelineTaskType.TARGETED_EXTRACTION,
                        operation="report_session", title="Report", native_job_id="report-fake", payload={})
    task = store.update(task.task_id, status=PipelineTaskStatus.PARTIAL)
    outcome = stage_outcome(task, {"result_status": "partial", "jobs": [{"status": "completed"}]})
    assert all(action.action != "resume" for action in outcome.available_actions)


def test_stopped_child_cause_precedes_outer_and_historical_errors():
    from esg_v2.control_plane.outcomes import execution_errors

    result = {"error": "RuntimeError: session projection stopped", "errors": ["earlier protocol failure"],
        "jobs": [{"status": "completed", "error": "historical repaired failure"},
                 {"status": "interrupted", "error": "GenerationTimeout: hard call deadline"}]}
    errors = execution_errors(result)
    assert errors[0] == "GenerationTimeout: hard call deadline"
    assert set(errors[1:]) == {"RuntimeError: session projection stopped", "earlier protocol failure", "historical repaired failure"}


def test_event_fingerprint_preserves_partition_evidence_errors_and_actions():
    snapshot = {"execution_status": "running", "counts": {"processing_metrics": 1, "unfinished_metrics": 0},
        "source_facts": [{"source_fact_id": "candidate-one", "complete": False}], "gaps": [], "errors": [],
        "available_actions": [{"action": "cancel", "scope": {"session_id": "report-fixture"}}]}
    changes = [
        ("counts", {"processing_metrics": 0, "unfinished_metrics": 1}),
        ("source_facts", [{"source_fact_id": "candidate-one", "complete": True}]),
        ("gaps", [{"gap_id": "protocol-one", "kind": "protocol", "reason": "required field absent"}]),
        ("errors", ["GenerationTimeout: hard call deadline"]),
        ("available_actions", [{"action": "resume", "scope": {"session_id": "report-fixture"}}]),
    ]
    for field, value in changes:
        changed = {**deepcopy(snapshot), field: value}
        assert snapshot_fingerprint(snapshot) != snapshot_fingerprint(changed), field


def test_suspended_scheduler_records_wall_overdue_without_claiming_inference(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from esg_targeted.storage.power import WatchdogClock
    from esg_v2.control_plane.scheduler import PipelineScheduler, PipelineTaskHooks
    from esg_v2.control_plane.secrets import RuntimeSecretVault

    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(task_id="pt-fake", task_type=PipelineTaskType.OCR,
        operation="ocr", title="Report", native_job_id="ocr-fake", payload={"total_timeout_seconds": 600})
    task = store.update(task.task_id, started_at=datetime.fromtimestamp(100, timezone.utc).isoformat())
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), PipelineTaskHooks())
    monkeypatch.setattr("esg_v2.control_plane.scheduler.time.time", lambda: 1500)
    monkeypatch.setattr("esg_v2.control_plane.scheduler.time.monotonic", lambda: 20)
    scheduler._observe_clock(task, WatchdogClock(100, 10))
    assert scheduler._deadline_expired(task)
    event = store.events(task.task_id)[-1]
    assert event["detail"]["wall_deadline"] == 700
    assert event["detail"]["deadline_overdue_seconds"] == 800
    assert event["detail"]["classification"] == "suspend_or_wall_clock_change"
