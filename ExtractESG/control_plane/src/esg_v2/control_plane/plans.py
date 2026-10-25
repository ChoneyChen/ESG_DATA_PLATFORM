"""Persistent dependency plans above the existing isolated-worker queue.

The coordinator never performs OCR/IR/extraction itself. Each child is one
existing queue task; dispatch keys recover the enqueue/checkpoint crash gap.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import threading
import time
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, Field, model_validator

from esg_v2.control_plane.contracts import utc_now
from esg_v2.control_plane.shared_contracts import AvailableAction, ReportExtractionRequest
from esg_v2.control_plane.progress import elapsed_seconds
from esg_v2.control_plane.task_references import task_reference_ids, resolve_task_references
from esg_standard_packages.extensions import ExtensionSelection


dispatch_key = ContextVar("pipeline_plan_dispatch_key", default=None)
execution_budget_seconds = ContextVar("pipeline_plan_execution_budget", default=None)


def display_plan_name(plan: dict) -> str:
    """Human label; correlation keys stay in their own durable fields."""
    raw = str(plan.get("name") or (plan.get("report_request") or {}).get("name") or "报告提取")
    if raw.startswith("自动生产:"):
        document = (plan.get("documents") or [{}])[0]
        return "批量生产 · " + str(document.get("file_name") or "报告")
    return raw


class PlanStandard(BaseModel):
    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    package_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    metric_ids: list[str] = Field(default_factory=list)


class PipelinePlanRequest(BaseModel):
    name: str = Field(default="全链路提取", min_length=1, max_length=120)
    asset_ids: list[str] = Field(min_length=1, max_length=500)
    standards: list[PlanStandard] = Field(default_factory=list, max_length=128)
    extension_selections: list[ExtensionSelection] = Field(default_factory=list)
    ocr_provider: Literal["local_first", "local_paddleocr", "paddle_api"] = "local_first"
    review_provider: Literal["local_nuextract", "local_qwen", "qiniu"] = "local_qwen"
    semantic_provider: Literal["local_nuextract", "local_qwen", "qiniu_vlm"] = "local_qwen"
    semantic_model: str | None = None
    retrieval_object_top_n: int = Field(default=2, ge=1, le=5)
    extraction_mode: Literal["auto", "per_metric"] = "auto"
    shared_object_budget: int = Field(default=3, ge=1, le=8)
    agent_max_steps: int = Field(default=8, ge=1, le=24)
    execute_optional_reviews: bool = False
    execute_required_reviews: bool = True
    reuse_existing: bool = True
    allow_limited_evidence: bool = True
    qiniu_api_key: str | None = Field(default=None, exclude=True)
    ocr_token: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def unique_inputs(self):
        if not any(s.metric_ids for s in self.standards) and not self.extension_selections:
            raise ValueError('Select an original requirement or approved extension')
        if len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("Duplicate report assets")
        keys = [s.package_id for s in self.standards]
        if len(set(keys)) != len(keys):
            raise ValueError("Select one version per standard package")
        for standard in self.standards:
            standard.metric_ids = list(dict.fromkeys(standard.metric_ids))
        self.semantic_model = (
            self.semantic_model.strip() or None
            if self.semantic_provider == "qiniu_vlm" and self.semantic_model
            else None
        )
        return self


class PipelinePlanCoordinator:
    def __init__(self, store, dispatch, inspect_ir, cancel, record_processing=None, inspect_session=None):
        self.store, self.dispatch, self.inspect_ir, self.cancel = store, dispatch, inspect_ir, cancel
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._thread = None
        self.record_processing = record_processing
        # Reading a finished native session's detail lets a resumed plan adopt
        # its results instead of asking the service to re-run finished work.
        self.inspect_session = inspect_session
        self._secrets = {}
        # Set by the app: turns a terminal report plan into reviewable
        # candidates without waiting for an operator click.
        self.on_report_handoff = None
        self._list_cache: tuple[float, list] | None = None
        with store._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS pipeline_plans (plan_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)")

    def list(self):
        # The workbench polls plan state; re-parsing every plan for each read
        # made the session list take seconds. Two seconds of reuse is enough.
        now = time.monotonic()
        if self._list_cache and now - self._list_cache[0] < 2.0:
            return self._list_cache[1]
        with self.store._connect() as connection:
            plans = [json.loads(r[0]) for r in connection.execute("SELECT state_json FROM pipeline_plans ORDER BY rowid DESC")]
        self._list_cache = (now, plans)
        return plans

    def save(self, plan):
        plan["updated_at"] = utc_now()
        self._list_cache = None
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

    def create_report(self, request: ReportExtractionRequest, document):
        with self._lock:
            brain = request.role_config.semantic_brain
            preparation = document.get("processing_config") or {}
            if request.model_plan is not None and not preparation:
                from esg_v2.control_plane.model_plan import report_processing_config
                preparation = report_processing_config(request).model_dump(mode="json")
            budget = request.effective_budget()
            config = PipelinePlanRequest(asset_ids=[document.get("asset_id") or "existing-ir"],
                name=request.name, standards=[s.model_dump() for s in request.standards],
                extension_selections=request.extension_selections,
                semantic_provider=brain.provider, semantic_model=brain.model,
                review_provider=preparation.get("review_provider", "qiniu" if brain.provider == "qiniu_vlm" else "local_qwen"),
                ocr_provider=preparation.get("ocr_provider", "local_first" if request.role_config.cloud_authorization.allow_ocr_api else "local_paddleocr"),
                execute_optional_reviews=preparation.get("execute_optional_reviews", False),
                execute_required_reviews=preparation.get("execute_required_reviews", True),
                allow_limited_evidence=request.allow_limited_evidence,
                retrieval_object_top_n=budget.retrieval_object_top_n, shared_object_budget=budget.shared_object_budget,
                agent_max_steps=budget.agent_max_steps, qiniu_api_key=request.qiniu_api_key, ocr_token=request.ocr_token)
            plan = self.create(config, [document])
            if document.get("automation_key"):
                plan["automation_key"] = document["automation_key"]
            plan["budget_started_at"] = None
            plan["budget_attempt"] = 1
            plan["budget_baseline_task_ids"] = []
            plan["report_request"] = request.model_dump(mode="json")
            plan["config"]["report_request"] = plan["report_request"]
            self.save(plan)
            if document.get("ir_run_id"):
                self.store.pin_ir(plan["plan_id"], document["ir_run_id"])
            return plan

    def action(self, plan_id, action, secrets=None):
        with self._lock:
            plan = next((p for p in self.list() if p["plan_id"] == plan_id), None)
            if plan is None:
                raise KeyError(plan_id)
            if plan.get('lifecycle', {}).get('state', 'active') != 'active':
                raise ValueError('任务已归档或丢弃，请先恢复记录')
            if secrets:
                self._secrets.setdefault(plan_id, {}).update({k: v for k, v in secrets.items() if v})
            if action == "pause":
                plan["status"] = "paused"  # Running child finishes, no new child starts.
            elif action == "resume":
                if plan["status"] == "completed":
                    return plan
                references=resolve_task_references(self.store,plan)
                if references['missing_task_ids'] or references['invalid_reference_paths']:
                    raise ValueError('执行记录已删除或引用不完整，请从报告资产新建任务。')
                plan["status"] = "active"
                plan.setdefault("budget_history", []).append(plan.get("execution_budget", {}))
                plan["budget_attempt"] = plan.get("budget_attempt", 1) + 1
                plan["budget_baseline_task_ids"] = task_reference_ids(plan)
                plan["budget_started_at"] = None
                for document in plan["documents"]:
                    if document["status"] != "completed":
                        document["status"] = "pending"
                        document.pop("error", None)
                        document.pop("platform_error", None)
                        document.pop("termination_reason", None)
                    for step in document["steps"].values():
                        if step.get("status") in {"failed", "cancelled", "interrupted"} or (step.get("status") == "partial" and name_is_extraction(step)):
                            if name_is_extraction(step):
                                step["resume_run_id"] = step.get("run_id")
                            if step.get("task_id"):
                                step.setdefault("history", []).append(step.pop("task_id"))
                            step["attempt"] = step.get("attempt", 0) + 1
                            step["status"] = "pending"
            elif action == "cancel":
                plan["status"] = "cancelled"
                children=resolve_task_references(self.store,plan)['tasks']
                for task in children:
                    try:self.cancel(task.task_id)
                    except Exception as exc:
                        if not isinstance(exc,KeyError) and getattr(exc,'status_code',None)!=404:raise
                if any(task.status.value in {'queued', 'running'} or self.store.worker_alive(task.task_id) for task in children):
                    plan['status'] = 'cancelling'
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
                if plan['status'] == 'cancelling':
                    children = [self.store.get(step['task_id']) for document in plan['documents']
                        for step in document['steps'].values() if step.get('task_id')]
                    if not any(task.status.value in {'queued', 'running'} or self.store.worker_alive(task.task_id) for task in children):
                        plan['status'] = 'cancelled'
                        for document in plan['documents']:
                            if document['status'] not in {'completed', 'partial'}:
                                document['status'] = 'cancelled'
                        self.save(plan)
                    continue
                if plan["status"] != "active":
                    if plan["status"] in {"completed", "partial"}:
                        self._maybe_handoff(plan)
                    continue
                for index, document in enumerate(plan["documents"]):
                    if document["status"] in {"completed", "partial", "failed", "interrupted", "cancelled"}:
                        continue
                    try:
                        if plan["config"].get("report_request"):
                            budget = ReportExtractionRequest.model_validate(plan["config"]["report_request"]).effective_budget()
                            elapsed = self._execution_budget(plan, budget.total_timeout_seconds)["used_seconds"]
                            if elapsed >= budget.total_timeout_seconds:
                                for step in document["steps"].values():
                                    if step.get("task_id"):
                                        self.cancel(step["task_id"])
                                document.update(status="interrupted", error="报告已达到总执行时限；已保存的成果与检查点保留",
                                    platform_error="Report reached its total time budget; valid artifacts and checkpoints retained",
                                    termination_reason="total_timeout")
                                continue
                        self._advance(plan, index, document)
                    except Exception as exc:
                        document.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                    self.save(plan)
                statuses = {d["status"] for d in plan["documents"]}
                if statuses == {"completed"}:
                    plan["status"] = "completed"
                elif statuses <= {"completed", "partial"}:
                    plan["status"] = "partial"
                elif plan.get("report_request") and statuses in ({"interrupted"}, {"cancelled"}):
                    plan["status"] = next(iter(statuses))
                elif statuses <= {"completed", "partial", "failed", "waiting_review", "interrupted", "cancelled"}:
                    plan["status"] = "needs_attention"
                self.save(plan)
                if plan["status"] in {"completed", "partial"}:
                    self._maybe_handoff(plan)

    def _maybe_handoff(self, plan):
        """Deliver a finished report once; retries stay bounded and spaced.

        Ownership is decided by the hook: a live batch automation delivers its
        own items and reports ``deferred`` here.
        """
        if not self.on_report_handoff:
            return
        if not (plan.get("config") or {}).get("report_request"):
            return
        for document in plan["documents"]:
            if document.get("status") not in {"completed", "partial"}:
                continue
            record = document.get("auto_handoff") or {}
            if record.get("status") in {"imported", "needs_identity", "no_artifacts"}:
                continue
            attempts_before = int(record.get("attempts") or 0)
            if attempts_before >= 12:
                continue
            if record.get("attempted_at"):
                try:
                    last = datetime.fromisoformat(record["attempted_at"])
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    pause = 30 if attempts_before < 5 else 300
                    if (datetime.now(timezone.utc) - last).total_seconds() < pause:
                        continue
                except ValueError:
                    pass
            attempts = int(record.get("attempts") or 0) + 1
            document["auto_handoff"] = {"status": "running", "attempts": attempts,
                "attempted_at": datetime.now(timezone.utc).isoformat()}
            self.save(plan)
            try:
                result = self.on_report_handoff(plan, document) or {}
                status = result.get("status") or "unknown"
                deferred = status == "deferred"
                document["auto_handoff"] = {"status": status, "stage": result.get("stage"),
                    "message": result.get("message"), "report_id": result.get("report_id"),
                    "auto_review": result.get("auto_review"),
                    "attempts": attempts - 1 if deferred else attempts,
                    "updated_at": datetime.now(timezone.utc).isoformat()}
            except Exception as exc:
                document["auto_handoff"] = {"status": "failed", "message": str(exc),
                    "attempts": attempts, "updated_at": datetime.now(timezone.utc).isoformat()}
            self.save(plan)

    def _finished_session(self, plan, document):
        """A session that already finished keeps its result; never re-run it."""
        if not self.inspect_session or not document.get("native_session_id"):
            return None
        try:
            detail = self.inspect_session(plan, document)
        except Exception:
            return None
        if not isinstance(detail, dict) or detail.get("execution_status") != "finished":
            return None
        statuses = {job.get("status") for job in detail.get("jobs") or [] if isinstance(job, dict)}
        partial = detail.get("result_status") != "usable" or "partial" in statuses
        return {"session_id": detail.get("session_id") or document.get("native_session_id"),
                "result_status": "partial" if partial else "completed"}

    def _step(self, plan, index, document, name):
        step = document["steps"].setdefault(name, {"attempt": 0, "status": "pending"})
        if step.get("queue_record_deleted"):
            if step.get("status") in {"completed", "partial"} and isinstance(step.get("run_id"),str) and step['run_id']:
                return step['run_id']
            document.update(status='interrupted',error='此阶段的执行记录已删除，请从报告资产新建任务。')
            return None
        index = document.get("dispatch_document_index", index)
        if step.get('task_id') is not None and not isinstance(step.get('task_id'),str):
            document.update(status='interrupted',error='阶段任务引用不完整，请从报告资产新建任务。')
            return None
        key = f"{plan['plan_id']}:{index}:{name}:{step['attempt']}"
        try:
            task = self.store.get(step["task_id"]) if step.get("task_id") else self.store.find_plan_step(key)
        except KeyError:
            document.update(status='interrupted',error='关联执行记录缺失，请从报告资产新建任务。')
            return None
        if task is None:
            finished = self._finished_session(plan, document) if name == "report_session" else None
            if finished:
                step.update(status=finished.get("result_status") or "completed",
                            run_id=finished.get("session_id") or document.get("native_session_id"),
                            finished_at=utc_now(), message="复用已完成的报告会话")
                return step["run_id"]
            token = dispatch_key.set(key)
            budget = ReportExtractionRequest.model_validate(plan["config"]["report_request"]).effective_budget() if plan["config"].get("report_request") else None
            remaining = self._execution_budget(plan, budget.total_timeout_seconds)["remaining_seconds"] if budget else None
            budget_token = execution_budget_seconds.set(max(1, int(remaining)) if remaining is not None else None)
            try:
                config = {**plan["config"], "_resume_job_id": step.get("resume_run_id"), "_plan_id": plan["plan_id"], "_document_index": index, "_transition_queued": step.get("transition_queued", False)}
                try:
                    task = self.dispatch(name, config, document, self._secrets.get(plan["plan_id"], {}))
                except Exception:
                    # The dispatch key is the identity of this step's queue row.
                    # If a parallel tick already created it, adopt that row
                    # instead of failing the document.
                    adopted = self.store.find_plan_step(key)
                    if adopted is None:
                        raise
                    task = adopted
            finally:
                dispatch_key.reset(token)
                execution_budget_seconds.reset(budget_token)
            step.pop("transition_queued", None)
        step.update(task_id=task.task_id, run_id=task.native_job_id, operation=task.operation,
                    status=task.status.value, error=task.error)
        if task.outcome:
            step["outcome"] = task.outcome.model_dump(mode="json")
        else:
            step.pop("outcome", None)
        if name == "report_session":
            document["native_session_id"] = task.native_job_id
        if task.status.value in {"completed", "partial"}:
            completions = [e for e in self.store.events(task.task_id) if e.get("stage") in {"completed", "partial"} and e.get("detail")]
            if completions:
                step["result"] = completions[-1]["detail"]
        if task.status.value in {"failed", "cancelled", "interrupted"}:
            platform_error = f"{name}: {task.error or task.status.value}"
            errors = task.outcome.errors if task.outcome else []
            step["platform_error"] = platform_error
            document.update(status=task.status.value if name == "report_session" else "failed",
                error=next(iter(errors), None) or task.error or {
                    "failed": "报告执行失败；已保存的成果与检查点保留",
                    "cancelled": "报告执行已取消；已保存的成果与检查点保留",
                    "interrupted": "报告执行已中断；已保存的成果与检查点保留",
                }[task.status.value], platform_error=platform_error)
        else:
            document["status"] = "running"
        return task.native_job_id if task.status.value in {"completed", "partial"} else None

    def _execution_budget(self, plan, limit):
        """Charge started child execution, never queue or human-review waiting.

        Unknown observation gaps remain charged conservatively; only explicitly
        observed wall/monotonic differences are excluded and labelled as such.
        Explicit resume starts a new allowance with prior usage retained.
        """
        baseline = {identity for identity in plan.get("budget_baseline_task_ids", []) if isinstance(identity,str)}
        references=resolve_task_references(self.store,plan)
        task_ids = set(task_reference_ids(plan)) - baseline
        used = queue_wait = unobserved = adjustment = 0.0
        started = []
        for task_id in task_ids:
            try:
                task = self.store.get(task_id)
            except KeyError:
                continue
            queue_wait += task.timing.queue_wait_seconds
            if task.started_at:
                started.append(task.started_at)
                wall = elapsed_seconds(task.started_at, task.finished_at)
                adjustment += task.timing.sleep_or_clock_adjustment_seconds
                unobserved += task.timing.unobserved_seconds
                used += max(0.0, wall - task.timing.sleep_or_clock_adjustment_seconds)
        plan["budget_started_at"] = min(started) if started else None
        plan["execution_budget"] = {
            'observation_status':references['reference_integrity']['status'],
            'missing_task_ids':references['missing_task_ids'],
            "schema_version": "report-execution-budget-v1", "attempt": plan.get("budget_attempt", 1),
            "policy": "started_child_execution;queue_and_review_wait_excluded;explicit_resume_new_allowance",
            "limit_seconds": limit, "used_seconds": round(used, 3), "remaining_seconds": round(max(0.0, limit - used), 3),
            "queue_wait_seconds": round(queue_wait, 3), "unobserved_seconds": round(unobserved, 3),
            "sleep_or_clock_adjustment_seconds": round(adjustment, 3),
        }
        return plan["execution_budget"]

    def _advance(self, plan, index, document):
        if not document.get("ocr_run_id") and not document.get("ir_run_id"):
            run_id = self._step(plan, index, document, "ocr")
            if not run_id:
                return
            document["ocr_run_id"] = run_id
        if not document.get("ir_run_id"):
            run_id = self._step(plan, index, document, "ir")
            if not run_id:
                return
            document["ir_run_id"] = run_id
        self.store.pin_ir(plan["plan_id"], document["ir_run_id"])
        manifest = self.inspect_ir(document["ir_run_id"])
        if not manifest:
            raise ValueError("IR result is not available; inspect the source task")
        usable = manifest.get("can_build_evidence") or (plan["config"]["allow_limited_evidence"] and manifest.get("can_build_limited_evidence"))
        resolved_run = manifest.get("run_id") or document["ir_run_id"]
        if resolved_run != document["ir_run_id"]:
            document.setdefault("evidence_transitions", []).append({"from": document["ir_run_id"], "to": resolved_run,
                "reason": "Explicit evidence repair resolution", "at": utc_now()})
            document["ir_run_id"] = resolved_run
            self.store.pin_ir(plan["plan_id"], resolved_run)
        document["evidence_policy"] = manifest.get("evidence_policy", {})
        if not usable:
            policy = document.get("evidence_policy") or {}
            reason = policy.get("reason") or "no page or object retained a usable evidence capability"
            document.update(status="waiting_review", error=(
                f"IR {document['ir_run_id']} is not evidence-ready ({manifest.get('readiness') or 'unknown'}): {reason} "
                "Repair it in 04, then resume this plan."))
            return
        if self.record_processing:
            # Enrollment is reuse bookkeeping, not a precondition for the report:
            # the IR above already passed the usability check, so a provenance
            # mismatch must not stop extraction.
            try:
                self.record_processing(plan, document)
            except (OSError, ValueError, TypeError) as exc:
                document["processing_enrollment"] = {"status": "skipped", "reason": str(exc)}
        if plan["config"].get("report_request"):
            run_id = self._step(plan, index, document, "report_session")
            if run_id:
                document["status"] = "partial" if document["steps"]["report_session"]["status"] == "partial" else "completed"
            return
        complete = True
        for standard in plan["config"]["standards"]:
            name = f"targeted:{standard['package_id']}"
            complete = bool(self._step(plan, index, document, name)) and complete
        if complete:
            document["status"] = "partial" if any(s.get("status") == "partial" for s in document["steps"].values()) else "completed"
        elif any(s.get("status") in {"failed", "cancelled", "interrupted"} for s in document["steps"].values()):
            document["status"] = "failed"


def name_is_extraction(step):
    return step.get("operation") in {"run", "resume", "report_session", "report_session_resume"}
