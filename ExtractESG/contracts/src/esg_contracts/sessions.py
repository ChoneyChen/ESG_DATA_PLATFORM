"""Public extraction operation views. Scientific facts retain their own contract."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .facts import PipelineBudget, RunRequest
from .evidence import DocumentReference
from .results import RunSummary
from .exports import DeliveryStatus

RunStatus = Literal[
    "queued", "running", "completed", "partial", "failed", "cancelled", "interrupted"
]
TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}
PHASE_LABELS = {
    "queued": "等待执行",
    "preparing": "准备文档证据",
    "ocr": "识别报告页面",
    "ir": "构建结构与证据",
    "quality": "检查证据范围",
    "running": "正在执行",
    "retrieving": "检索条款证据",
    "reading": "阅读条款证据",
    "extracting": "绑定并保存原值",
    "finished": "执行结束",
    "failed": "执行失败",
    "cancelling": "正在取消",
    "cancelled": "已取消",
    "interrupted": "执行中断",
}


class View(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pdf", "document_ir"]
    asset_id: str | None = None
    ir_run_id: str | None = None

    @model_validator(mode="after")
    def selected(self):
        if self.kind == "pdf" and (not self.asset_id or self.ir_run_id):
            raise ValueError("PDF input needs only an asset identity")
        if self.kind == "document_ir" and (not self.ir_run_id or self.asset_id):
            raise ValueError("IR input needs only an IR identity")
        return self


class ExtractionPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: ExtractionInput
    clause_keys: list[str] = Field(default_factory=list)
    selection: str | None = None
    submission_key: str | None = Field(default=None, min_length=1, max_length=160)
    name: str = Field(default="条款提取", min_length=1, max_length=120)
    budget: PipelineBudget = Field(default_factory=PipelineBudget)
    expected_target_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_engine_fingerprint: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )

    @model_validator(mode="after")
    def targets(self):
        if bool(self.clause_keys) == bool(self.selection):
            raise ValueError("Select independent clauses or a named set")
        return self

    def native(self, ir_id):
        return RunRequest(
            ir_run_id=ir_id,
            clause_keys=self.clause_keys,
            selection=self.selection,
            name=self.name,
            budget=self.budget,
        )


class ReadingProgress(View):
    stage: str | None = None
    clause_key: str | None = None
    object_id: str | None = None
    current: int = 0
    total: int = 0
    model_calls: int = 0
    fact_count: int = 0


class ModelProgress(View):
    phase: str
    input_chars: int | None = None
    input_tokens: int | None = None
    generation_tokens: int | None = None
    output_token_budget: int | None = None
    elapsed_seconds: float | None = None


class NativeRun(View):
    run_id: str
    schema_version: Literal["extraction-run-1.0"] = "extraction-run-1.0"
    status: RunStatus
    name: str = "条款提取"
    request: RunRequest | None = None
    document: DocumentReference | None = None
    engine_fingerprint: str | None = None
    clause_keys: list[str] = Field(default_factory=list)
    target_digest: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    progress: ReadingProgress = Field(default_factory=ReadingProgress)
    current_call: ModelProgress | None = None
    last_call: ModelProgress | None = None
    cancel_requested: bool = False
    summary: RunSummary | None = None
    error: str | None = None
    delivery: DeliveryStatus | None = None
    export_download_url: str | None = None
    artifacts_url: str | None = None
    current_phase: str = "queued"
    phase_label: str = "等待执行"

    @model_validator(mode="after")
    def authoritative_phase(self):
        if self.status in TERMINAL:
            self.current_phase = (
                "finished" if self.status in {"completed", "partial"} else self.status
            )
            self.last_call = self.current_call or self.last_call
            self.current_call = None
        elif self.cancel_requested:
            self.current_phase = "cancelling"
            self.current_call = None
        else:
            self.current_phase = self.progress.stage or (
                "queued" if self.status == "queued" else "running"
            )
        self.phase_label = PHASE_LABELS.get(self.current_phase, "正在执行")
        return self


class PreparationStep(View):
    stage: str
    label: str
    status: str
    current: int | None = None
    total: int | None = None
    unit: str = "unknown"


class Preparation(View):
    processing_id: str
    status: str
    steps: list[PreparationStep] = Field(default_factory=list)
    ocr_run_id: str | None = None
    ir_run_id: str | None = None


class SessionAction(View):
    action: Literal["cancel"]
    label: str
    endpoint: str
    method: Literal["POST"] = "POST"


class ExtractionSession(View):
    session_id: str
    name: str
    status: RunStatus | Literal["cancelling"]
    stage: Literal["preparing", "extracting"]
    request: ExtractionPlanRequest
    source_sha256: str | None = None
    ir_run_id: str | None = None
    processing_id: str | None = None
    run_id: str | None = None
    queue_task_id: str | None = None
    run: NativeRun | None = None
    preparation: Preparation | None = None
    progress: ReadingProgress = Field(default_factory=ReadingProgress)
    clause_keys: list[str] = Field(default_factory=list)
    target_digest: str | None = None
    engine_fingerprint: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    execution_status: str
    result_status: Literal["usable", "empty", "partial", "not_evaluated"]
    current_phase: str = "queued"
    phase_label: str = "等待执行"
    observation: Literal["fresh", "unavailable"] = "fresh"
    observation_error: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    available_actions: list[SessionAction] = Field(default_factory=list)
