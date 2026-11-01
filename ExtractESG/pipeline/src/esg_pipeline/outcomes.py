"""Shared execution/result axes derived from durable stage artifacts."""

from __future__ import annotations
from typing import Any
from esg_pipeline.contracts import PipelineTask
from esg_pipeline.shared_contracts import AvailableAction, EvidenceGap, StageOutcome


def _errors(result: dict[str, Any]) -> list[str]:
    errors = []

    def visit(record):
        if not isinstance(record, dict):
            return
        if record.get("error"):
            errors.append(str(record["error"]))
        errors.extend(str(error) for error in record.get("errors", []) if error)
        visit(record.get("summary"))
        for child in record.get("jobs", []):
            visit(child)
        for stage in record.get("stages", []):
            visit(stage)

    visit(result)
    return list(dict.fromkeys(errors))


def execution_errors(result: dict[str, Any] | None) -> list[str]:
    """Put the stopped child's explicit cause before historical stage errors."""
    result = result or {}
    current = [
        str(job["error"])
        for job in result.get("jobs", [])
        if isinstance(job, dict)
        and job.get("status") in {"failed", "interrupted", "cancelled"}
        and job.get("error")
    ]
    return list(dict.fromkeys([*current, *_errors(result)]))


def stage_outcome(
    task: PipelineTask, result: dict[str, Any] | None = None
) -> StageOutcome:
    result = result or {}
    manifest = (
        result.get("manifest") if isinstance(result.get("manifest"), dict) else result
    )
    state = task.status.value
    policy = manifest.get("evidence_policy") or {}
    artifacts = list(result.get("artifacts") or [])
    actions = []
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    gaps = [
        EvidenceGap.model_validate(gap)
        for gap in (result.get("gaps") or summary.get("gaps") or [])
        if isinstance(gap, dict)
    ]
    if state in {"queued", "running"}:
        actions.append(
            AvailableAction(
                action="cancel",
                label="取消任务",
                endpoint=f"/api/pipeline/tasks/{task.task_id}/cancel",
                scope={"task_id": task.task_id},
            )
        )
    native_result_status = result.get("result_status") or summary.get("result_status")
    retained_result = native_result_status in {
        "usable",
        "partial",
        "needs_confirmation",
    }
    if state in {"completed", "partial"} or retained_result:
        artifact = {"kind": task.task_type.value, "run_id": task.native_job_id}
        if artifact not in artifacts:
            artifacts.append(artifact)
    usable = state in {"completed", "partial"} or retained_result
    result_status = (
        native_result_status
        if native_result_status
        in {
            "usable",
            "partial",
            "needs_confirmation",
            "no_evidence_in_bound",
            "not_evaluated",
        }
        else "partial"
        if state == "partial"
        else "usable"
        if usable
        else "not_evaluated"
    )
    if task.task_type.value == "document_ir" and usable:
        full = bool(manifest.get("can_build_evidence"))
        limited = bool(manifest.get("can_build_limited_evidence"))
        result_status = (
            "usable" if full else "partial" if limited else "needs_confirmation"
        )
        if not full:
            gaps.append(
                EvidenceGap(
                    gap_id=f"{task.task_id}:evidence",
                    kind="source",
                    reason=str(
                        manifest.get("readiness") or "Document evidence requires review"
                    ),
                    object_ids=policy.get("excluded_target_ids", []),
                    next_action="local_repair",
                )
            )
            targets = policy.get("excluded_target_ids", [])
            if targets:
                actions.append(
                    AvailableAction(
                        action="local_repair",
                        label="修复受影响证据",
                        scope={"ir_run_id": task.native_job_id, "target_ids": targets},
                        endpoint=f"/api/document-ir/jobs/{task.native_job_id}/repair",
                        calls_model=True,
                        incurs_cost=task.payload.get("review_provider") == "qiniu",
                        payload={
                            "parent_ir_run_id": task.native_job_id,
                            "target_ids": targets,
                            "reason_code": "report_evidence_gap",
                            "requested_by": "report-session-user",
                            "execute_vlm_reviews": True,
                            "review_provider": task.payload.get(
                                "review_provider", "local_qwen"
                            ),
                        },
                    )
                )
            else:
                actions.append(
                    AvailableAction(
                        action="review",
                        label="查看文档问题",
                        scope={"ir_run_id": task.native_job_id},
                        endpoint="/api/document-ir/review-worklist",
                        method="GET",
                    )
                )
            if limited:
                actions.append(
                    AvailableAction(
                        action="accept_limited",
                        label="使用可用证据继续",
                        scope=policy,
                        endpoint="/api/report-sessions/preview",
                    )
                )
    elif task.task_type.value == "targeted_extraction" and state in {"failed", "interrupted", "cancelled"}:
        session_id = task.payload.get("session_id")
        if session_id and not (result.get("delivery") or {}).get("export_id"):
            actions.append(AvailableAction(action="resume", label="继续提取",
                scope={"session_id":session_id,"run_id":task.native_job_id},
                endpoint=f"/api/report-sessions/{session_id}/resume"))
    errors = list(
        dict.fromkeys(
            [*execution_errors(result), *([task.error] if task.error else [])]
        )
    )
    input_request = task.payload.get("input") or {}
    ir_run_id = (
        task.payload.get("ir_run_id")
        or input_request.get("ir_run_id")
        or result.get("ir_run_id")
        or (summary.get("document") or {}).get("ir_run_id")
    )
    return StageOutcome(
        execution_status="finished" if state in {"completed", "partial"} else state,
        result_status=result_status,
        artifacts=artifacts,
        valid_scope={"ir_run_id": ir_run_id, **policy},
        gaps=gaps,
        errors=errors,
        available_actions=actions,
    )
