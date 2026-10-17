"""Isolate incomplete historical records at the report read boundary.

Reading never repairs a record or creates execution. An unreadable item remains
visible with its diagnostic identity and real owner deletion command.
"""
import logging
import traceback
from uuid import uuid4

logger = logging.getLogger(__name__)


def read_report_session(plan, project, deletion_action):
    try:
        return project(plan)
    except Exception as exc:
        identity = 'error-' + uuid4().hex
        # Do not log a stored request or exception text containing validation inputs.
        logger.error('Report record read failed [%s] plan=%s exception=%s\n%s', identity,
                     plan.get('plan_id'), type(exc).__name__, ''.join(traceback.format_tb(exc.__traceback__)))
        error = {'code': 'report_record_read_failed', 'category': 'state_integrity',
                 'message': '此任务记录不完整，当前无法确认执行与成果状态。可删除记录，或从报告资产新建任务。',
                 'request_id': identity, 'layer': 'document', 'recoverable': False}
        lifecycle = plan.get('lifecycle') if isinstance(plan.get('lifecycle'),dict) else {}
        return {'schema_version': 'report-session-platform-v1', 'session_id': plan['plan_id'],
                'name': plan.get('name') or '待处理的历史提取任务',
                'request': plan.get('report_request') if isinstance(plan.get('report_request'), dict) else {},
                'created_at': plan.get('created_at'), 'updated_at': plan.get('updated_at'),
                'execution_status': 'unavailable', 'result_status': 'not_evaluated',
                'job_ids': [], 'preparation_stages': [], 'stages': [],
                'read_status': 'incomplete', 'read_error': error, 'error_detail': error,
                'error': error['message'], 'native_unavailable': error,
                'lifecycle': {'state': lifecycle.get('state', 'active'),
                              'revision': lifecycle.get('revision', 0)},
                'available_actions': [deletion_action('session', plan)]}


SUMMARY_KEYS = (
    "schema_version", "session_id", "native_session_id", "name", "execution_status", "native_execution_status",
    "result_status", "revision", "created_at", "updated_at", "error", "errors", "error_detail", "read_status",
    "read_error", "native_unavailable", "lifecycle", "state_notice", "status", "job_ids", "available_actions",
    "database_status", "counts", "source_asset_id", "asset_id", "deletion_pending",
)
STAGE_KEYS = ("name", "label", "operation", "status", "task_id", "run_id", "error", "error_detail", "started_at",
              "finished_at", "updated_at")


def summarize_report_session(detail: dict) -> dict:
    """List-row projection: identity, status, actions and progress, without
    job ledgers, evidence policies and frozen plans (fetched per session)."""
    request = detail.get("request") or {}
    summary = {key: detail[key] for key in SUMMARY_KEYS if key in detail}
    summary["request"] = {key: request[key] for key in ("input", "standards", "extension_selections", "extraction_route",
                                                       "selection_snapshot", "name") if key in request}
    snapshot = summary["request"].get("selection_snapshot")
    if isinstance(snapshot, dict):
        summary["request"]["selection_snapshot"] = {"source_datapoint_ids": snapshot.get("source_datapoint_ids", [])}
    policy = detail.get("evidence_policy") or {}
    summary["evidence_policy"] = {key: policy[key] for key in ("mode", "ir_run_id", "available_page_count") if key in policy}
    stages = []
    for stage in detail.get("preparation_stages") or []:
        progress = stage.get("progress") or {}
        compact = {key: stage[key] for key in STAGE_KEYS if key in stage}
        compact["progress"] = {key: progress[key] for key in ("label", "status", "current", "total", "unit", "percent",
                                                              "current_call") if key in progress}
        stages.append(compact)
    summary["preparation_stages"] = stages
    summary["view"] = "summary"
    return summary
