"""Typed business progress; logs and model budgets are never completion units."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


STAGE_LABELS = {
    "retrieving": "检索条款证据",
    "reading": "阅读条款证据",
    "extracting": "绑定条款事实",
    "clause_extraction": "逐条条款提取",
    "queued": "等待执行",
    "starting": "启动执行",
    "running": "正在执行",
    "stopping": "正在取消",
    "completed": "执行完成",
    "partial": "执行结束，有待处理成果",
    "failed": "执行失败",
    "cancelled": "已取消",
    "interrupted": "执行中断",
    "reconnected": "恢复观测已有任务",
    "resource_lifecycle": "资源与执行时间检查",
    "ocr": "文档识别",
    "document_ir": "结构与证据构建",
    "report_session": "报告定向提取",
    "targeted_extraction": "条款提取",
    "retrieval": "检索报告证据",
    "search": "检索报告证据",
    "source_understanding": "理解来源",
    "interpret_source": "理解来源",
    "mapping": "条款映射",
    "map_metrics": "条款映射",
    "repair_format": "修复输出协议",
    "materialization": "整理事实",
    "export": "保存阶段成果",
    "finalization": "检查并冻结成果",
    "planning": "规划证据动作",
    "archived": "已归档",
    "restored": "已恢复记录",
    "lifecycle": "记录与来源管理",
    "quality": "检查证据可用范围",
    "ir": "结构与证据构建",
    "finished": "提取执行结束",
}
IR_BUILD_LABELS = [
    "加载已固定的识别成果",
    "渲染报告页面",
    "检查原始页面与文本",
    "构建正文与表格结构",
    "整理阅读顺序与章节",
    "连接表格与跨页结构",
    "生成区域证据",
    "连接横向跨页证据",
    "按缺口复核与修复",
    "重建并核对修正结构",
    "验证并保存证据版本",
]
MODEL_ROLE_LABELS = {
    "reviewer": "原文复核",
    "verifier": "修订验证",
}
MODEL_ROLE_ACTIONS = {
    "reviewer": "复核原文并提出修订",
    "verifier": "验证修订与原文证据",
}


class BusinessProgress(BaseModel):
    schema_version: Literal["business-progress-v1"] = "business-progress-v1"
    stage_code: str
    label: str
    status: str
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=1)
    unit: Literal["pages", "steps", "requirements", "objects", "unknown"] = "unknown"
    determinate: bool = False
    percent: float | None = Field(default=None, ge=0, le=100)
    current_call: dict[str, Any] | None = None
    stages: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    last_business_progress_at: str | None = None

    @model_validator(mode="after")
    def current_display_labels(self):
        # Old progress snapshots retain their measured state. Read-time labels
        # use today's central vocabulary without rewriting persisted events.
        self.label = stage_label(self.stage_code)
        self.stages = [
            {**stage, "label": stage_label(stage["stage_code"])}
            if stage.get("stage_code")
            else dict(stage)
            for stage in self.stages
        ]
        if self.current_call:
            code = (
                self.current_call.get("tool")
                or self.current_call.get("route")
                or self.current_call.get("action")
            )
            role = str(self.current_call.get("role") or "")
            self.current_call = {
                **self.current_call,
                "role_label": MODEL_ROLE_LABELS.get(role, "模型角色待识别"),
            }
            if code:
                self.current_call = {
                    **self.current_call,
                    "label": stage_label(str(code)),
                }
            else:
                self.current_call = {
                    **self.current_call,
                    "label": MODEL_ROLE_ACTIONS.get(role, "模型调用动作待识别"),
                }
        return self


class ExecutionTiming(BaseModel):
    schema_version: Literal["execution-timing-v1"] = "execution-timing-v1"
    queue_wait_seconds: float = Field(default=0, ge=0)
    wall_elapsed_seconds: float = Field(default=0, ge=0)
    active_observed_seconds: float = Field(default=0, ge=0)
    unobserved_seconds: float = Field(default=0, ge=0)
    sleep_or_clock_adjustment_seconds: float = Field(default=0, ge=0)
    last_observed_at: str | None = None
    observation_complete: bool = False
    budget_policy: str = (
        "active_child_wall_time_excluding_observed_suspend_or_clock_adjustment"
    )


class TaskError(BaseModel):
    code: str
    category: Literal[
        "runtime",
        "protocol",
        "source",
        "identity",
        "mapping",
        "publication",
        "dependency",
    ]
    message: str
    technical_detail: str
    recoverable: bool
    next_action: str | None = None


def stage_label(code: str, *, task_type: str = "") -> str:
    if code in STAGE_LABELS:
        return STAGE_LABELS[code]
    if code.startswith("ocr_"):
        return "文档识别"
    if code.startswith("ir.build.stage."):
        try:
            index = int(code.rsplit(".", 1)[-1])
            return (
                IR_BUILD_LABELS[index - 1]
                if 1 <= index <= len(IR_BUILD_LABELS)
                else "结构与证据构建"
            )
        except (ValueError, IndexError):
            return "结构与证据构建"
    if code.startswith("ir."):
        return "证据定向修复阶段 " + code.rsplit(".", 1)[-1]
    # Unknown machine codes stay in technical details, never masquerade as a
    # translated business state. Chinese labels supplied by IR are usable.
    if any("\u4e00" <= char <= "\u9fff" for char in code):
        return code
    return "执行阶段待识别"


def classify_error(
    message: str, *, task_type: str = "", producer_error: dict | None = None
) -> TaskError:
    if producer_error and producer_error.get("category") in {
        "runtime",
        "protocol",
        "source",
        "identity",
        "mapping",
        "publication",
        "dependency",
    }:
        labels = {
            "runtime": "执行失败，已有成果保留",
            "protocol": "输出格式未满足合同，已有成果保留",
            "source": "来源证据存在问题，请检查受影响对象",
            "identity": "主体、期间或口径身份不完整",
            "mapping": "条款映射尚未满足要求",
            "publication": "正式发布条件尚未满足",
            "dependency": "执行依赖尚未就绪",
        }
        code = producer_error.get("code") or "producer_reported_error"
        budget_code = code == "model_budget_exceeded"
        return TaskError(
            code=code,
            category=producer_error["category"],
            message=producer_error.get("message")
            or (
                "模型调用达到预算限制，已有成果保留"
                if budget_code
                else labels[producer_error["category"]]
            ),
            technical_detail=producer_error.get("technical_detail") or message,
            recoverable=producer_error.get("recoverable", True),
            next_action=producer_error.get("next_action")
            or ("adjust_task_budget" if budget_code else None),
        )
    if producer_error and (
        producer_error.get("failure_category") == "budget"
        or producer_error.get("category") == "budget"
    ):
        boundary = producer_error.get("budget_boundary")
        label = (
            "该标准包的模型调用达到预算限制，已有成果保留"
            if boundary == "package"
            else "模型调用达到预算限制，已有成果保留"
        )
        return TaskError(
            code="model_budget_exceeded",
            category="runtime",
            message=label,
            technical_detail=producer_error.get("technical_detail") or message,
            recoverable=True,
            next_action="adjust_task_budget",
        )
    low = message.lower()
    # Compatibility only for the old producer's exact exception or known
    # budget phrases. "runtime budget" contains "time budget", and "input
    # token" is not a credential; these must not fall through to those guesses.
    legacy_model_budget = bool(re.search(r"(?:^|[\s.])budgetexceeded\s*:", low)) or any(
        phrase in low
        for phrase in (
            "report runtime budget exhausted",
            "report model call budget exhausted",
            "report input token budget exhausted",
            "report input token estimated admission budget exhausted",
            "report prepared input token budget exhausted",
            "report estimated input token budget exhausted",
            "report output token budget exhausted",
            "report format repair budget exhausted",
        )
    )
    if legacy_model_budget:
        category, code, label, action = (
            "runtime",
            "model_budget_exceeded",
            "模型调用达到预算限制，已有成果保留",
            "adjust_task_budget",
        )
    elif any(s in low for s in ("deadline", "timeout", "时限")) or re.search(
        r"\btime budget\b", low
    ):
        category, code, label, action = (
            "runtime",
            "execution_deadline",
            "执行达到时限，已有成果保留",
            "inspect",
        )
    elif any(
        s in low
        for s in ("protocol", "repair_format", "json", "schema", "validationerror")
    ):
        category, code, label, action = (
            "protocol",
            "output_contract_failed",
            "输出格式未满足合同，已有成果保留",
            "resume",
        )
    elif any(s in low for s in ("credential", "token", "secret", "凭据")):
        category, code, label, action = (
            "dependency",
            "credential_required",
            "执行凭据不可用，请配置后恢复",
            "configure_credentials",
        )
    elif any(s in low for s in ("compatib", "version", "版本")):
        category, code, label, action = (
            "dependency",
            "version_incompatible",
            "服务或输入版本不兼容，请查看兼容诊断",
            "compatibility",
        )
    elif any(
        s in low
        for s in (
            "subject_identity",
            "period_identity",
            "identity_axis",
            "主体身份",
            "期间身份",
        )
    ):
        category, code, label, action = (
            "identity",
            "identity_incomplete",
            "主体、期间或口径身份不完整",
            "inspect_identity",
        )
    elif any(s in low for s in ("mapping_rejected", "metric_mapping", "条款映射")):
        category, code, label, action = (
            "mapping",
            "mapping_failed",
            "条款映射尚未满足要求",
            "review_mapping",
        )
    elif any(s in low for s in ("publication", "publish_gate", "正式发布")):
        category, code, label, action = (
            "publication",
            "publication_blocked",
            "正式发布条件尚未满足",
            "review_publication",
        )
    elif task_type == "document_ir" or any(
        s in low for s in ("evidence", "ocr", "pdf")
    ):
        category, code, label, action = (
            "source",
            "source_processing_failed",
            "报告处理或证据使用失败，请查看受影响来源",
            "inspect_source",
        )
    else:
        category, code, label, action = (
            "runtime",
            "execution_failed",
            "执行失败，已有记录保留；请查看原因和可用操作",
            "inspect",
        )
    return TaskError(
        code=code,
        category=category,
        message=label,
        technical_detail=message,
        recoverable=True,
        next_action=action,
    )


def progress_from_event(task: Any, event: dict[str, Any]) -> BusinessProgress:
    if event.get("kind") == "log":
        # A log is diagnostic even when it contains stage/count-looking fields.
        # Only a producer's measured business event can move progress or status.
        return task.progress or BusinessProgress(
            stage_code=task.stage or task.task_type.value,
            label=stage_label(
                task.stage or task.task_type.value, task_type=task.task_type.value
            ),
            status=task.status.value,
        )
    stage = str(event.get("stage") or task.stage or task.task_type.value)
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    previous = task.progress.model_dump() if task.progress else {}
    status = str(event.get("status") or task.status.value)
    current, total, unit = None, None, "unknown"
    counts = event.get("counts") or (event.get("summary") or {}).get("counts") or {}
    call = event.get("current_call") or detail.get("model_calls", {}).get(
        "current_call"
    )
    stages = detail.get("stages") or previous.get("stages", [])
    if (
        task.task_type.value == "ocr"
        and event.get("kind") in {"telemetry", "progress"}
        and ("progress_current" in event or "progress_total" in event)
    ):
        current = max(0, int(event.get("progress_current") or 0))
        total = int(event.get("progress_total") or 0) or None
        unit = "pages"
    elif task.task_type.value == "document_ir" and isinstance(
        detail.get("current_stage"), dict
    ):
        active = detail["current_stage"]
        stage = "ir." + task.operation + ".stage." + str(active.get("index") or 0)
        total = int(active.get("total") or 0) or None
        current = sum(s.get("status") in {"done", "completed"} for s in stages)
        unit = "steps"
    elif task.task_type.value == "targeted_extraction":
        if counts.get("selected_clauses"):
            current = max(0, int(counts.get("processed_clauses") or 0))
            total = int(counts["selected_clauses"])
            unit = "requirements"
    if current is None and stage == previous.get("stage_code"):
        current, total, unit = (
            previous.get("current"),
            previous.get("total"),
            previous.get("unit", "unknown"),
        )
        counts = counts or previous.get("counts", {})
    if total is not None and (total <= 0 or current is None or current > total):
        total = None
    percent = round(current / total * 100, 1) if total and current is not None else None
    label = stage_label(stage, task_type=task.task_type.value)
    if task.task_type.value == "document_ir":
        stages = [
            {
                **s,
                "stage_code": "ir."
                + task.operation
                + ".stage."
                + str(s.get("index") or 0),
                "label": stage_label(
                    "ir." + task.operation + ".stage." + str(s.get("index") or 0)
                ),
            }
            for s in stages
        ]
    return BusinessProgress(
        stage_code=stage,
        label=label,
        status=status,
        current=current,
        total=total,
        unit=unit,
        determinate=percent is not None,
        percent=percent,
        current_call=call,
        stages=stages,
        counts=counts,
        last_business_progress_at=datetime.now(timezone.utc).isoformat(),
    )


def elapsed_seconds(start: str | None, finish: str | None = None) -> float:
    if not start:
        return 0.0
    try:
        left = datetime.fromisoformat(start)
        right = datetime.fromisoformat(finish) if finish else datetime.now(timezone.utc)
        if left.tzinfo is None:
            left = left.replace(tzinfo=timezone.utc)
        if right.tzinfo is None:
            right = right.replace(tzinfo=timezone.utc)
        return max(0.0, (right - left).total_seconds())
    except (TypeError, ValueError):
        return 0.0
