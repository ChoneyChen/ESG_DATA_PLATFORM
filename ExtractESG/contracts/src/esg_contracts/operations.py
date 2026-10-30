"""Operational observations expose no dispatch payload, local paths or runtime secrets."""

from pydantic import BaseModel, ConfigDict


class TaskObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    task_id: str
    task_type: str
    operation: str
    title: str
    status: str
    native_job_id: str | None = None
    stage: str
    message: str
    progress_current: int = 0
    progress_total: int = 0
    cancel_requested: bool = False
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
