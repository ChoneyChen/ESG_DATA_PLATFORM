"""Pure request/command checks: no model calls or persisted business runs."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_v2.control_plane.contracts import (
    PipelineTask,
    PipelineTaskStatus,
    PipelineTaskType,
    TargetedExtractionBatchPipelineTaskRequest,
    TargetedExtractionPipelineTaskRequest,
)
from esg_v2.control_plane.hooks import PlatformTaskHooks
from esg_v2.control_plane.plans import PipelinePlanRequest


def targeted_request(**changes):
    return TargetedExtractionPipelineTaskRequest(
        **{
            "ir_run_id": "ir-fixture",
            "package_id": "esrs.e1-6",
            "package_version": "2.0.0",
            "metric_ids": ["e1-6.dp07"],
            "semantic_provider": "local_qwen",
            **changes,
        }
    )


def test_local_qwen_and_step_budget_reach_worker_command(tmp_path):
    request = targeted_request(agent_max_steps=12, semantic_model="ignored-local-id")
    assert request.semantic_model is None
    task = PipelineTask(
        task_id="pt-fixture", task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="run", title="fixture", status=PipelineTaskStatus.QUEUED,
        position=1, native_job_id="tx-fixture", payload=request.model_dump(mode="json"),
    )
    hooks = object.__new__(PlatformTaskHooks)
    hooks.settings = SimpleNamespace(
        targeted_runtime_python=Path(sys.executable), targeted_app_root=tmp_path,
    )
    argv = hooks.command(task, {}).argv
    assert argv[argv.index("--semantic-provider") + 1] == "local_qwen"
    assert argv[argv.index("--agent-max-steps") + 1] == "12"
    assert "--semantic-model" not in argv


def test_batch_requires_one_shared_step_budget():
    with pytest.raises(ValueError, match="share one IR"):
        TargetedExtractionBatchPipelineTaskRequest(requests=[
            targeted_request(agent_max_steps=8),
            targeted_request(package_id="esrs.e1-5", agent_max_steps=9),
        ])


def test_plan_preserves_agent_configuration_and_bounded_step_range():
    fields = dict(asset_ids=["pdf-fixture"], standards=[{
        "package_id": "esrs.e1-6", "package_version": "2.0.0", "metric_ids": ["dp07"],
    }], review_provider="local_qwen", semantic_provider="local_qwen", semantic_model="ignored-local-id")
    request = PipelinePlanRequest(**fields)
    assert request.agent_max_steps == 8
    assert request.semantic_model is None
    assert request.review_provider == "local_qwen"
    assert request.model_dump(mode="json")["semantic_provider"] == "local_qwen"
    for invalid in (0, 25):
        with pytest.raises(ValueError):
            PipelinePlanRequest(**fields, agent_max_steps=invalid)


def test_probe_preserves_native_terminal_status_for_queue_recovery(monkeypatch):
    record = {"status": "partial", "stage": "results", "error": "bounded gap",
              "summary": {"agent": {"state": "partial"}}, "progress_current": 1, "progress_total": 2}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: record)

    monkeypatch.setattr("esg_v2.control_plane.hooks.requests.Session", FakeSession)
    hooks = object.__new__(PlatformTaskHooks)
    hooks.settings = SimpleNamespace(targeted_backend_url="http://fake.invalid")
    task = PipelineTask(
        task_id="pt-fixture", task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="run", title="fixture", status=PipelineTaskStatus.RUNNING,
        position=1, native_job_id="tx-fixture", payload={},
    )
    probe = hooks.probe(task)
    assert probe["status"] == "partial"
    assert probe["error"] == "bounded gap"
    assert probe["summary"]["agent"]["state"] == "partial"
