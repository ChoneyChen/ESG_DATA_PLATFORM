"""Persistent dependency plans above the existing isolated-worker queue.

The coordinator never performs OCR/IR/extraction itself. Each child is one
existing queue task; dispatch keys recover the enqueue/checkpoint crash gap.
"""
from __future__ import annotations

from contextvars import ContextVar
import json
import threading
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, Field, model_validator

from esg_v2.control_plane.contracts import utc_now


dispatch_key = ContextVar("pipeline_plan_dispatch_key", default=None)


class PlanStandard(BaseModel):
    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    package_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    metric_ids: list[str] = Field(min_length=1)


class PipelinePlanRequest(BaseModel):
    name: str = Field(default="全链路提取", min_length=1, max_length=120)
    asset_ids: list[str] = Field(min_length=1, max_length=500)
    standards: list[PlanStandard] = Field(min_length=1, max_length=20)
    ocr_provider: Literal["local_first", "local_paddleocr", "paddle_api"] = "local_first"
    review_provider: Literal["local_nuextract", "qiniu"] = "local_nuextract"
    semantic_provider: Literal["local_nuextract", "qiniu_vlm"] = "local_nuextract"
    semantic_model: str | None = None
    retrieval_object_top_n: int = Field(default=2, ge=1, le=5)
    reuse_existing: bool = True
    allow_limited_evidence: bool = True
    qiniu_api_key: str | None = Field(default=None, exclude=True)
    ocr_token: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def unique_inputs(self):
        if len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("Duplicate report assets")
        keys = [s.package_id for s in self.standards]
        if len(set(keys)) != len(keys):
            raise ValueError("Select one version per standard package")
        for standard in self.standards:
            standard.metric_ids = list(dict.fromkeys(standard.metric_ids))
        return self


class PipelinePlanCoordinator:
    def __init__(self, store, dispatch, inspect_ir, cancel):
        self.store, self.dispatch, self.inspect_ir, self.cancel = store, dispatch, inspect_ir, cancel
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._thread = None
        self._secrets = {}
        with store._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS pipeline_plans (plan_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)")

    def list(self):
        with self.store._connect() as connection:
            return [json.loads(r[0]) for r in connection.execute("SELECT state_json FROM pipeline_plans ORDER BY rowid DESC")]

    def save(self, plan):
        plan["updated_at"] = utc_now()
        with self.store._connect() as connection:
            connection.execute("INSERT INTO pipeline_plans VALUES (?,?) ON CONFLICT(plan_id) DO UPDATE SET state_json=excluded.state_json", (plan["plan_id"], json.dumps(plan, ensure_ascii=False)))

    def create(self, request, documents):
        with self._lock:
            plan_id = f"plan-{uuid4().hex}"
            plan = {"schema_version": "pipeline-plan-v1", "plan_id": plan_id, "name": request.name,
                    "status": "active", "created_at": utc_now(), "config": request.model_dump(mode="json"),
                    "documents": [{**d, "status": "pending", "steps": {}} for d in documents]}
            self._secrets[plan_id] = {"qiniu_api_key": request.qiniu_api_key, "ocr_token": request.ocr_token}
            self.save(plan)
            return plan

    def action(self, plan_id, action):
        with self._lock:
            plan = next((p for p in self.list() if p["plan_id"] == plan_id), None)
            if plan is None:
                raise KeyError(plan_id)
            if action == "pause":
                plan["status"] = "paused"  # Running child finishes, no new child starts.
            elif action == "resume":
                if plan["status"] == "completed":
                    return plan
                plan["status"] = "active"
                for document in plan["documents"]:
                    if document["status"] != "completed":
                        document["status"] = "pending"
                        document.pop("error", None)
                    for step in document["steps"].values():
                        if step.get("status") in {"failed", "cancelled", "interrupted"}:
                            step.setdefault("history", []).append(step.pop("task_id"))
                            step["attempt"] = step.get("attempt", 0) + 1
                            step["status"] = "pending"
            elif action == "cancel":
                plan["status"] = "cancelled"
                for document in plan["documents"]:
                    for step in document["steps"].values():
                        if step.get("task_id"):
                            self.cancel(step["task_id"])
            else:
                raise ValueError("Unknown plan action")
            self.save(plan)
            return plan

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pipeline-plan-coordinator", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(2)

    def tick(self):
        with self._lock:
            for plan in self.list():
                if plan["status"] != "active":
                    continue
                for index, document in enumerate(plan["documents"]):
                    if document["status"] in {"completed", "failed"}:
                        continue
                    try:
                        self._advance(plan, index, document)
                    except Exception as exc:
                        document.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                    self.save(plan)
                statuses = {d["status"] for d in plan["documents"]}
                if statuses == {"completed"}:
                    plan["status"] = "completed"
                elif statuses <= {"completed", "failed", "waiting_review"}:
                    plan["status"] = "needs_attention"
                self.save(plan)

    def _step(self, plan, index, document, name):
        step = document["steps"].setdefault(name, {"attempt": 0, "status": "pending"})
        key = f"{plan['plan_id']}:{index}:{name}:{step['attempt']}"
        task = self.store.get(step["task_id"]) if step.get("task_id") else self.store.find_plan_step(key)
        if task is None:
            token = dispatch_key.set(key)
            try:
                task = self.dispatch(name, plan["config"], document, self._secrets.get(plan["plan_id"], {}))
            finally:
                dispatch_key.reset(token)
        step.update(task_id=task.task_id, run_id=task.native_job_id, status=task.status.value, error=task.error)
        if task.status.value in {"completed", "partial"}:
            completions = [e for e in self.store.events(task.task_id) if e.get("stage") in {"completed", "partial"} and e.get("detail")]
            if completions:
                step["result"] = completions[-1]["detail"]
        if task.status.value in {"failed", "cancelled", "interrupted", "partial"}:
            document.update(status="failed", error=f"{name}: {task.error or task.status.value}")
        else:
            document["status"] = "running"
        return task.native_job_id if task.status.value == "completed" else None

    def _advance(self, plan, index, document):
        if not document.get("ocr_run_id"):
            run_id = self._step(plan, index, document, "ocr")
            if not run_id:
                return
            document["ocr_run_id"] = run_id
        if not document.get("ir_run_id"):
            run_id = self._step(plan, index, document, "ir")
            if not run_id:
                return
            document["ir_run_id"] = run_id
        manifest = self.inspect_ir(document["ir_run_id"])
        if not manifest:
            raise ValueError("IR result is not available; inspect the source task")
        usable = manifest.get("can_build_evidence") or (plan["config"]["allow_limited_evidence"] and manifest.get("can_build_limited_evidence"))
        document["ir_run_id"] = manifest.get("run_id") or document["ir_run_id"]
        document["evidence_policy"] = manifest.get("evidence_policy", {})
        if not usable:
            document.update(status="waiting_review", error="IR requires review; repair it in 04, then resume this plan.")
            return
        complete = True
        for standard in plan["config"]["standards"]:
            name = f"targeted:{standard['package_id']}"
            complete = bool(self._step(plan, index, document, name)) and complete
        if complete:
            document["status"] = "completed"
        elif any(s.get("status") in {"failed", "cancelled", "interrupted"} for s in document["steps"].values()):
            document["status"] = "failed"
