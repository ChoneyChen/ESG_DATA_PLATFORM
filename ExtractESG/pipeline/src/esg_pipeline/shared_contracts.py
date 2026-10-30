from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceGap(StrictModel):
    gap_id: str
    kind: Literal[
        "source",
        "header",
        "row",
        "method",
        "mapping",
        "retrieval",
        "provider",
        "contract",
        "uninterpreted_row",
        "column_header",
        "source_reference",
        "transport",
        "budget",
        "context",
        "protocol",
        "generation_incomplete",
        "identity",
        "dependency",
        "not_covered",
        "planner_stalled",
        "planner_round_budget",
        "planner_action_budget",
    ]
    reason: str
    object_ids: list[str] = Field(default_factory=list)
    object_id: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    row_ids: list[str] = Field(default_factory=list)
    column_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    next_action: str | None = None
    topic_id: str | None = None
    attempt: int | None = None
    failure_stage: str | None = None
    status: Literal["open", "resolved"] = "open"


class AvailableAction(StrictModel):
    action: str
    label: str
    scope: dict[str, Any] = Field(default_factory=dict)
    endpoint: str
    method: Literal["GET", "POST", "DELETE"] = "POST"
    payload: dict[str, Any] = Field(default_factory=dict)
    calls_model: bool = False
    incurs_cost: bool = False
    requires_approval: bool = False
    requires_confirmation: bool = False
    confirmation_message: str | None = None
    requires_reason: bool = False
    requires_reference_check: bool = False
    preflight_endpoint: str | None = None
    enabled: bool = True


class StageOutcome(StrictModel):
    execution_status: Literal[
        "queued", "running", "interrupted", "failed", "cancelled", "finished"
    ]
    result_status: Literal[
        "usable",
        "partial",
        "needs_confirmation",
        "no_evidence_in_bound",
        "not_evaluated",
    ]
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    valid_scope: dict[str, Any] = Field(default_factory=dict)
    gaps: list[EvidenceGap] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    available_actions: list[AvailableAction] = Field(default_factory=list)
