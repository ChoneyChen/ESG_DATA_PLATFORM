"""PDF preparation → independent clause run. Orchestration owns neither reader nor clauses."""

import json, threading
from uuid import uuid4
import requests
from esg_contracts.sessions import (
    ExtractionPlanRequest,
    ExtractionSession,
    NativeRun,
    Preparation,
    PreparationStep,
    TERMINAL,
    PHASE_LABELS,
)
from datetime import datetime, timezone
from esg_contracts.search import PageSearchIntent
from esg_clauses.repository import ClauseRepository
from esg_clauses.search import compile_search_plan
from .report_processing import (
    ProcessingRequest,
    ProcessingConfig,
    ProcessingActionRequest,
    STAGE_LABELS,
)
from .context import dispatch_key


class ExtractionServiceUnavailable(RuntimeError):
    pass


class ExtractionCoordinator:
    def __init__(self, store, processing, queue, cancel_task, base_url):
        self.store, self.processing, self.queue, self.cancel_task = (
            store,
            processing,
            queue,
            cancel_task,
        )
        self.base_url = base_url.rstrip("/")
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        with store._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS clause_extraction_plans (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    def remote(self, method, path, payload=None):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.request(
                    method,
                    self.base_url + path,
                    json=payload,
                    timeout=5 if method == "GET" else 30,
                )
                if response.status_code >= 500 or (
                    method == "GET" and response.status_code >= 400
                ):
                    raise ExtractionServiceUnavailable(
                        "提取服务暂时不可用，执行状态尚未确认。"
                    )
                if response.status_code >= 400:
                    raise ValueError(
                        f"Extraction service rejected request ({response.status_code}): "
                        + response.text[:1000]
                    )
                return response.json()
        except requests.RequestException as exc:
            raise ExtractionServiceUnavailable(
                "提取服务暂时不可用，执行状态尚未确认。"
            ) from exc

    def get(self, key):
        with self.store._connect() as db:
            row = db.execute(
                "SELECT body FROM clause_extraction_plans WHERE id=?", (key,)
            ).fetchone()
        if not row:
            raise KeyError(key)
        return json.loads(row[0])

    def save(self, state):
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO clause_extraction_plans VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (state["session_id"], json.dumps(state, ensure_ascii=False)),
            )

    def list(self, terminal_limit=None):
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT body FROM clause_extraction_plans ORDER BY rowid DESC"
            ).fetchall()
        states = [json.loads(row[0]) for row in rows]
        if terminal_limit is not None:
            active = [state for state in states if state["status"] not in TERMINAL]
            recent = [state for state in states if state["status"] in TERMINAL][:terminal_limit]
            states = [*active, *recent]
        return [self.detail(state) for state in states]

    def preview(self, request):
        native = request.native(
            request.input.ir_run_id or "awaiting-document-preparation"
        )
        snapshot = self.remote(
            "POST", "/api/targets/freeze", native.model_dump(mode="json")
        )
        engine = self.remote("GET", "/api/engine")
        if not engine["ready"]:
            raise ExtractionServiceUnavailable("提取服务代码已变化，需要重启。")
        if (
            request.expected_engine_fingerprint
            and request.expected_engine_fingerprint != engine["fingerprint"]
        ):
            raise ValueError(
                "Extraction engine changed after preview; reload the target selection"
            )
        if (
            request.expected_target_digest
            and request.expected_target_digest != snapshot["digest"]
        ):
            raise ValueError(
                "Selected clauses changed after preview; reload the target selection"
            )
        asset = (
            self.processing.assets.get(request.input.asset_id)
            if request.input.kind == "pdf"
            else None
        )
        if request.input.kind == "document_ir":
            self.remote("POST", "/api/runs/preview", native.model_dump(mode="json"))
        return {
            "targets": snapshot,
            "clause_count": len(snapshot["clauses"]),
            "output_count": len(snapshot["clauses"]),
            "source_sha256": asset["sha256"] if asset else None,
            "calls_model": False,
            "budget": request.budget.model_dump(),
            "engine_fingerprint": engine["fingerprint"],
            "engine_version": engine["version"],
        }

    def create(self, request):
        # One local coordinator process owns plan submission; retries cannot dispatch twice.
        with self._lock:
            if request.submission_key:
                with self.store._connect() as db:
                    row = db.execute(
                        "SELECT body FROM clause_extraction_plans WHERE json_extract(body,'$.request.submission_key')=?",
                        (request.submission_key,),
                    ).fetchone()
                if row:
                    previous = json.loads(row[0])
                    if previous["request"] != request.model_dump(mode="json"):
                        raise ValueError(
                            "Submission key input changed; use a new submission"
                        )
                    return self.detail(previous)
            return self._create(request)

    def _create(self, request):
        preview = self.preview(request)
        key = "extract-plan-" + uuid4().hex
        state = {
            "session_id": key,
            "name": request.name,
            "status": "queued",
            "stage": "preparing" if request.input.kind == "pdf" else "extracting",
            "request": request.model_dump(mode="json"),
            "target_snapshot": preview["targets"],
            "source_sha256": preview["source_sha256"],
            "ir_run_id": request.input.ir_run_id,
            "processing_id": None,
            "run_id": None,
            "queue_task_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine_fingerprint": preview["engine_fingerprint"],
        }
        self.save(state)
        return self.detail(state)

    def _observed_step(self, step):
        if not step.get("task_id"):
            return step
        try:
            child = self.store.get(step["task_id"])
        except KeyError:
            return step
        status = child.status.value
        progress = getattr(child, "progress", None)
        progress = (
            progress.model_dump(mode="json")
            if hasattr(progress, "model_dump")
            else progress
        )
        return {
            **step,
            "status": "cancelling"
            if child.cancel_requested and status in {"queued", "running"}
            else status,
            "progress": progress or step.get("progress") or {},
        }

    def _active_preparation(self, process):
        observed = [
            self._observed_step(step) for step in process.get("steps", {}).values()
        ]
        return [
            step
            for step in observed
            if step.get("status") in {"queued", "running", "cancelling"}
        ]

    def detail(self, state):
        state = {**state}
        snapshot = state.pop("target_snapshot", {})
        state["clause_keys"] = list(snapshot.get("clauses", {}))
        state["target_digest"] = snapshot.get("digest")
        state["observation"] = (
            "unavailable" if state.get("observation_error") else "fresh"
        )
        if state.get("processing_id"):
            try:
                prepared = self.processing.get(state["processing_id"])
                steps = []
                for stage, label in STAGE_LABELS.items():
                    step = self._observed_step(prepared["steps"].get(stage, {}))
                    progress = step.get("progress") or {}
                    steps.append(
                        PreparationStep(
                            stage=stage,
                            label=label,
                            status=step.get("status", "pending"),
                            current=progress.get("current"),
                            total=progress.get("total"),
                            unit=progress.get("unit", "unknown"),
                        )
                    )
                state["preparation"] = Preparation(
                    processing_id=state["processing_id"],
                    status=prepared["status"],
                    steps=steps,
                    ocr_run_id=prepared.get("ocr_run_id"),
                    ir_run_id=prepared.get("ir_run_id"),
                )
            except KeyError:
                state["observation"] = "unavailable"
                state["observation_error"] = "文档处理记录暂不可用。"
        if (
            state.get("cancel_requested")
            and not state.get("run_id")
            and state.get("preparation")
        ):
            state["status"] = (
                "cancelling" if self._active_preparation(prepared) else "cancelled"
            )
        if state.get("run_id"):
            try:
                run = NativeRun.model_validate(
                    self.remote("GET", f"/api/runs/{state['run_id']}")
                )
                state["observation"] = "fresh"
                state["observation_error"] = None
                state["run"] = run
                state["status"] = (
                    "cancelling"
                    if state.get("cancel_requested") and run.status not in TERMINAL
                    else "cancelled"
                    if state.get("cancel_requested") and run.status == "interrupted"
                    else run.status
                )
                state["progress"] = run.progress
                state["current_phase"] = run.current_phase
            except (ExtractionServiceUnavailable, ValueError) as exc:
                state["observation"] = "unavailable"
                state["observation_error"] = str(exc)
        if "current_phase" not in state:
            state["current_phase"] = (
                "queued" if state["status"] == "queued" else state["stage"]
            )
            preparation = state.get("preparation")
            if preparation and state["stage"] == "preparing":
                active = next(
                    (
                        step
                        for step in preparation.steps
                        if step.status in {"running", "queued"}
                    ),
                    None,
                )
                if active:
                    state["current_phase"] = active.stage
        if state["status"] == "cancelling":
            state["current_phase"] = "cancelling"
        if state["status"] in TERMINAL:
            state["current_phase"] = (
                "finished"
                if state["status"] in {"completed", "partial"}
                else state["status"]
            )
        state["phase_label"] = PHASE_LABELS.get(state["current_phase"], "正在执行")
        state["execution_status"] = (
            "finished"
            if state["status"] in {"completed", "partial"}
            else state["status"]
        )
        summary = state["run"].summary if state.get("run") else None
        assessment = summary.assessment if summary else None
        state["result_status"] = (
            assessment.capture_result if assessment
            else "usable" if state["status"] == "completed"
            else "partial" if state["status"] == "partial" else "not_evaluated"
        )
        state["available_actions"] = []
        if state["status"] in {"queued", "running"}:
            state["available_actions"] = [
                {
                    "action": "cancel",
                    "label": "取消",
                    "endpoint": f"/api/report-sessions/{state['session_id']}/cancel",
                    "method": "POST",
                }
            ]
        return ExtractionSession.model_validate(state).model_dump(mode="json")

    def cancel(self, key):
        with self._lock:
            state = self.get(key)
            current = self.detail(state)
            if current["status"] in TERMINAL:
                raise ValueError("任务已经结束，不能再次取消。")
            state.update(cancel_requested=True, status="cancelling")
            self.save(state)
            self._cancel_children(state)
            return self.detail(state)

    def _cancel_children(self, state):
        if state.get("run_id"):
            try:
                try:
                    self.remote("POST", f"/api/runs/{state['run_id']}/cancel")
                except ValueError:
                    run = self.remote("GET", f"/api/runs/{state['run_id']}")
                    if run["status"] not in TERMINAL:
                        raise
            except ExtractionServiceUnavailable as exc:
                state["observation_error"] = str(exc)
            finally:
                if state.get("queue_task_id"):
                    try:
                        self.cancel_task(state["queue_task_id"])
                    except ValueError:
                        pass  # A finished queue child needs no second stop.
        elif state.get("processing_id"):
            process = self.processing.get(state["processing_id"])
            if process["status"] in {"queued", "running", "cancelling"}:
                self.processing.action(
                    state["processing_id"], "cancel", ProcessingActionRequest()
                )
                process = self.processing.get(state["processing_id"])
            active = self._active_preparation(process)
            for step in active:
                if step.get("task_id"):
                    try:
                        self.cancel_task(step["task_id"])
                    except KeyError:
                        state["observation_error"] = (
                            "子任务记录暂不可用，尚未确认停止。"
                        )
                    except ValueError:
                        pass
            state["status"] = "cancelling" if active else "cancelled"
        else:
            state["status"] = "cancelled"
        self.save(state)

    def tick(self, state):
        if state["status"] == "cancelling":
            self._cancel_children(state)
            observed = self.detail(state)
            if observed["status"] in TERMINAL:
                state["status"] = observed["status"]
                self.save(state)
            return
        if state["status"] not in {"queued", "running"}:
            return
        state.pop("observation_error", None)
        request = ExtractionPlanRequest.model_validate(state["request"])
        if not state["ir_run_id"]:
            if not state["processing_id"]:
                clauses = ClauseRepository.thaw(state["target_snapshot"])
                targets = [
                    PageSearchIntent.model_validate(compile_search_plan(c)["page"])
                    for k, c in clauses.items()
                ]
                process = self.processing.create(
                    ProcessingRequest(
                        asset_id=request.input.asset_id,
                        mode="auto",
                        source_sha256=state["source_sha256"],
                        submission_key=state["session_id"],
                        config=ProcessingConfig(
                            page_selection="clauses",
                            page_search_targets=targets,
                            ocr_provider="local_paddleocr",
                            allow_cloud_ocr=False,
                            execute_required_reviews=False,
                            execute_optional_reviews=False,
                            review_provider="local_qwen",
                        ),
                    )
                )
                state["processing_id"] = process["processing_id"]
                state["status"] = "running"
                self.save(state)
            process = self.processing.get(state["processing_id"])
            if process["status"] in {
                "failed",
                "cancelled",
                "interrupted",
                "needs_attention",
            }:
                state.update(
                    status="failed",
                    error="Document preparation did not produce admissible evidence",
                    processing_status=process["status"],
                )
                self.save(state)
                return
            if process["status"] not in {"ready", "ready_with_limits"}:
                return
            state["ir_run_id"] = process["ir_run_id"]
            self.save(state)
        if not state["run_id"]:
            self.store.pin_ir(state["session_id"], state["ir_run_id"])
            native = request.native(state["ir_run_id"]).model_copy(
                update={
                    "clause_keys": list(state["target_snapshot"]["clauses"]),
                    "selection": None,
                }
            )
            run = self.remote(
                "POST",
                "/api/runs",
                {
                    "request": native.model_dump(mode="json"),
                    "target_snapshot": state["target_snapshot"],
                    "submission_key": state["session_id"],
                    "expected_engine_fingerprint": state.get("engine_fingerprint"),
                },
            )
            state.update(run_id=run["run_id"], stage="extracting")
            self.save(state)
        if not state["queue_task_id"]:
            token = dispatch_key.set(state["session_id"])
            try:
                task = self.queue(
                    state["run_id"], request.name, request.budget.timeout_seconds
                )
            finally:
                dispatch_key.reset(token)
            state["queue_task_id"] = task.task_id
            state["status"] = "running"
            self.save(state)
        run = self.remote("GET", f"/api/runs/{state['run_id']}")
        if run["status"] in TERMINAL:
            state["status"] = run["status"]
            self.save(state)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                with self.store._connect() as db:
                    rows = db.execute(
                        "SELECT body FROM clause_extraction_plans WHERE json_extract(body,'$.status') IN ('queued','running','cancelling')"
                    ).fetchall()
                for row in rows:
                    with self._lock:
                        state = json.loads(row[0])
                        try:
                            self.tick(state)
                        except ExtractionServiceUnavailable as exc:
                            state["observation_error"] = str(exc)
                            self.save(state)
                        except Exception as exc:
                            state.update(
                                status="failed",
                                error=f"{type(exc).__name__}: {str(exc)[:1000]}",
                            )
                            self.save(state)
                self._stop.wait(1)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
