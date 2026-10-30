"""Safe, versioned Console projection. No scheduler payload crosses this port."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal
from pydantic import BaseModel, Field


class MonitorRun(BaseModel):
    session_id: str
    report_id: str | None = None
    name: str
    status: str
    current_phase: str
    phase_label: str
    current: int = 0
    total: int = 0
    facts: int = 0
    model_calls: int = 0
    model_phase: str | None = None
    generated_tokens: int | None = None
    elapsed_seconds: float | None = None
    observation: Literal["fresh", "unavailable"] = "fresh"
    updated_at: str | None = None
    can_cancel: bool = False
    preparation: list[dict[str, str | int | None]] = Field(default_factory=list)


class MonitorSnapshot(BaseModel):
    schema_version: Literal["console-monitor-1.0"] = "console-monitor-1.0"
    revision: str
    observed_at: str
    active_count: int
    runs: list[MonitorRun] = Field(default_factory=list)


TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}


def monitor_snapshot(sessions):
    runs = []
    for state in sessions:
        native = state.get("run") or {}
        summary = native.get("summary") or {}
        progress = state.get("progress") or {}
        call = native.get("current_call") or {}
        runs.append(MonitorRun(
            session_id=state["session_id"],
            report_id=state.get("request", {}).get("input", {}).get("asset_id"),
            name=state["name"], status=state["status"],
            current_phase=state["current_phase"], phase_label=state["phase_label"],
            current=progress.get("current", 0), total=progress.get("total", 0) or len(state.get("clause_keys", [])),
            facts=summary.get("source_facts", progress.get("fact_count", 0)),
            model_calls=summary.get("model_calls", progress.get("model_calls", 0)),
            model_phase=call.get("phase"), generated_tokens=call.get("generation_tokens"),
            elapsed_seconds=call.get("elapsed_seconds"), observation=state.get("observation", "fresh"),
            updated_at=state.get("updated_at"),
            can_cancel=any(a.get("action") == "cancel" for a in state.get("available_actions", [])),
            preparation=[{key: step.get(key) for key in ("stage", "label", "status", "current", "total", "unit")}
                for step in (state.get("preparation") or {}).get("steps", [])],
        ))
    body = [run.model_dump(mode="json") for run in runs]
    return MonitorSnapshot(
        revision=sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
        observed_at=datetime.now(timezone.utc).isoformat(),
        active_count=sum(run.status not in TERMINAL for run in runs), runs=runs,
    )
