"""Queue independent clause reads of registered PDFs or existing IR references."""

import json, threading, shutil
from uuid import uuid4
import requests
from esg_contracts.model_providers import ModelSelection
from esg_contracts.sessions import (
    ExtractionInput,
    ExtractionPlanRequest,
    ExtractionSession,
    NativeRun,
    Preparation,
    PreparationStep,
    TERMINAL,
    PHASE_LABELS,
)
from datetime import datetime, timezone
from .report_processing import ProcessingActionRequest, STAGE_LABELS
from .report_batches import ACTIVE_STATUSES, WAITING_FOR_WINDOW, ReportBatches
from .context import dispatch_key


class ExtractionServiceUnavailable(ConnectionError):
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
        self.batches = ReportBatches(store)

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
                if response.status_code >= 500:
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

    def save(self, state, *, release_provider=False):
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO clause_extraction_plans VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (state["session_id"], json.dumps(state, ensure_ascii=False)),
            )
            if release_provider and state.get("batch_id"):
                self.batches.release_provider(db, state["batch_id"], state["session_id"])
            if state.get("batch_id") and (
                state["status"] in TERMINAL or state["status"] == WAITING_FOR_WINDOW or release_provider
            ):
                self.batches.refill(db, state["batch_id"], state["updated_at"])

    def list(self, terminal_limit=None, archived=False, *, include_waiting=True):
        with self.store._connect() as db:
            rows = db.execute(
                """SELECT json_set(json_remove(body,'$.target_snapshot'),
                    '$.clause_keys',json((SELECT json_group_array(key) FROM json_each(body,'$.target_snapshot.clauses'))),
                    '$.target_digest',json_extract(body,'$.target_snapshot.digest'))
                    FROM clause_extraction_plans
                    WHERE (? OR json_extract(body,'$.status')<>?)
                    ORDER BY rowid DESC""",
                (include_waiting, WAITING_FOR_WINDOW),
            ).fetchall()
        states = [json.loads(row[0]) for row in rows]
        states = [state for state in states if bool(state.get("archived_at")) == archived]
        if terminal_limit is not None:
            active = [state for state in states if state["status"] not in TERMINAL]
            recent = [state for state in states if state["status"] in TERMINAL][:terminal_limit]
            states = [*active, *recent]
        runs = self.run_statuses(states)
        with self.store._connect() as db:
            replacements = self.batches.replacements(db, [state["session_id"] for state in states])
        return [self.detail(state, runs.get(state.get("run_id")),
                            replacement_ids=replacements.get(state["session_id"], [])) for state in states]

    def batch_waiting_count(self):
        with self.store._connect() as db:
            return db.execute(
                "SELECT count(*) FROM clause_extraction_plans WHERE json_extract(body,'$.status')=?",
                (WAITING_FOR_WINDOW,),
            ).fetchone()[0]

    def run_statuses(self, states):
        ids = list(dict.fromkeys(s["run_id"] for s in states if s.get("run_id") and not s.get("resume_requested")))
        return {
            run["run_id"]: run
            for offset in range(0, len(ids), 1000)
            for run in self.remote("POST", "/api/runs/status", {"run_ids": ids[offset:offset + 1000]})["runs"]
        }

    def archive_many(self, keys, archived=True):
        with self._lock:
            states = [self.get(key) for key in dict.fromkeys(keys)]
            if archived and any(self.detail(state)["status"] not in TERMINAL for state in states):
                raise ValueError("执行中的任务请先取消，结束后再删除记录。")
            now = datetime.now(timezone.utc).isoformat()
            with self.store._connect() as db:
                for state in states:
                    if archived:
                        state["archived_at"] = now
                    else:
                        state.pop("archived_at",None)
                    state["updated_at"] = now
                    db.execute("UPDATE clause_extraction_plans SET body=? WHERE id=?",
                        (json.dumps(state,ensure_ascii=False),state["session_id"]))
            return {"session_ids":[state["session_id"] for state in states],"archived":archived}

    def archive(self, key):
        self.archive_many([key])
        return {"session_id": key, "archived": True, "facts_retained": True}

    def restore(self, key):
        self.archive_many([key],False)
        return {"session_id": key, "archived": False, "facts_retained": True}

    def purge_many(self, keys):
        with self._lock, self.store._lock:
            states = [self.get(key) for key in dict.fromkeys(keys)]
            if any(not state.get("archived_at") for state in states):
                raise ValueError("请先将任务移入已删除列表。")
            if any(self.detail(state)["status"] not in TERMINAL for state in states):
                raise ValueError("执行中的任务不能彻底删除。")
            run_ids = [state["run_id"] for state in states if state.get("run_id")]
            with self.store._connect() as db:
                task_ids = []
                for run_id in run_ids:
                    for row in db.execute("SELECT task_id,status FROM pipeline_tasks WHERE task_type='targeted_extraction' AND native_job_id=?", (run_id,)):
                        if row["status"] in {"queued", "running"}:
                            raise ValueError("任务正在收尾，请结束后再彻底删除。")
                        task_ids.append(row["task_id"])
            if run_ids:
                self.remote("POST", "/api/runs/purge", {"run_ids": run_ids})
            with self.store._connect() as db:
                for owner in [*keys, *run_ids, *task_ids]:
                    db.execute("DELETE FROM ir_input_pins WHERE owner_id=?", (owner,))
                for task_id in task_ids:
                    logs = self.store.path.parent / "tasks" / task_id
                    if logs.exists():
                        shutil.rmtree(logs)
                    db.execute("DELETE FROM pipeline_tasks WHERE task_id=?", (task_id,))
                for state in states:
                    db.execute("DELETE FROM clause_extraction_plans WHERE id=?", (state["session_id"],))
                for state in states:
                    batch_id = state.get("batch_id")
                    if batch_id and self.batches.release_provider(db, batch_id, state["session_id"]):
                        self.batches.refill(db, batch_id, datetime.now(timezone.utc).isoformat())
            return {"session_ids": [state["session_id"] for state in states], "purged": True}

    def preview(self, request):
        return self._preview(request, engine=None, snapshots={})

    def _preview(self, request, *, engine, snapshots):
        native = request.native()
        selection = (tuple(request.clause_keys), request.selection,
                     request.model_selection or ModelSelection(), request.document_model_selection)
        if selection not in snapshots:
            snapshots[selection] = self.remote(
                "POST", "/api/targets/freeze", native.model_dump(mode="json")
            )
        snapshot = snapshots[selection]
        if engine is None:
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
        source_sha256 = asset["sha256"] if asset else None
        if request.input.kind == "document_ir":
            ir_preview = self.remote("POST", "/api/runs/preview", native.model_dump(mode="json"))
            source_sha256 = (ir_preview.get("document") or {}).get("source_sha256")
        if request.replaces_session_id:
            self._validate_replacement(request, source_sha256)
        return {
            "targets": snapshot,
            "clause_count": len(snapshot["clauses"]),
            "output_count": len(snapshot["clauses"]),
            "source_sha256": source_sha256,
            "calls_model": False,
            "budget": request.budget.model_dump(),
            "model_selection": (request.model_selection or ModelSelection()).model_dump(mode="json"),
            "document_model_selection": request.document_model_selection.model_dump(mode="json") if request.document_model_selection else None,
            "engine_fingerprint": engine["fingerprint"],
            "engine_version": engine["version"],
        }

    def _validate_replacement(self, request, source_sha256):
        try:
            previous = self.get(request.replaces_session_id)
        except KeyError:
            raise ValueError("找不到本次重提对应的原任务。") from None
        if ExtractionInput.model_validate(previous["request"]["input"]) == request.input:
            return
        previous_sha256 = previous.get("source_sha256")
        if not previous_sha256 and previous.get("run_id") and source_sha256:
            run = self.remote("GET", f"/api/runs/{previous['run_id']}")
            previous_sha256 = (run.get("document") or {}).get("source_sha256")
        if not source_sha256 or source_sha256 != previous_sha256:
            raise ValueError("重提必须使用原任务对应的同一份报告。")

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
        state = self._new_session(request, preview)
        self.save(state)
        return self.detail(state)

    def create_batch(self, request):
        with self._lock:
            batch_id = self.batches.submitted(request)
            if not batch_id:
                engine = self.remote("GET", "/api/engine")
                snapshots = {}
                states = [
                    self._new_session(member, self._preview(member, engine=engine, snapshots=snapshots))
                    for member in request.sessions
                ]
                batch_id = self.batches.create(request, states)
            return self.batches.get(batch_id)

    @staticmethod
    def _new_session(request, preview):
        key = "extract-plan-" + uuid4().hex
        return {
            "session_id": key,
            "name": request.name,
            "status": "queued",
            "stage": "extracting",
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

    def detail(self, state, run_record=None, *, replacement_ids=None):
        state = {**state}
        if replacement_ids is None:
            session_id = state["session_id"]
            with self.store._connect() as db:
                replacement_ids = self.batches.replacements(db, [session_id]).get(session_id, [])
        state["replacement_session_ids"] = replacement_ids
        snapshot = state.pop("target_snapshot", {})
        if snapshot:
            state["clause_keys"] = list(snapshot["clauses"])
            state["target_digest"] = snapshot["digest"]
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
        if state.get("run_id") and not state.get("resume_requested"):
            try:
                run = NativeRun.model_validate(
                    run_record if run_record is not None else self.remote("GET", f"/api/runs/{state['run_id']}")
                )
                state["observation"] = "fresh"
                state["observation_error"] = None
                state["run"] = run
                if run.error:
                    state["error"] = run.error
                state["status"] = (
                    "cancelling"
                    if state.get("cancel_requested") and run.status not in TERMINAL
                    else "cancelled"
                    if state.get("cancel_requested")
                    and (run.status == "interrupted" or state["status"] == "cancelled")
                    else run.status
                )
                state["progress"] = run.progress
                state["current_phase"] = run.current_phase
            except (ExtractionServiceUnavailable, ValueError) as exc:
                state["observation"] = "unavailable"
                state["observation_error"] = str(exc)
        if "current_phase" not in state:
            state["current_phase"] = (
                state["status"] if state["status"] in {"queued", WAITING_FOR_WINDOW} else state["stage"]
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
        if state["status"] in {"queued", "running", WAITING_FOR_WINDOW}:
            state["available_actions"] = [
                {
                    "action": "cancel",
                    "label": "取消",
                    "endpoint": f"/api/report-sessions/{state['session_id']}/cancel",
                    "method": "POST",
                }
            ]
        elif state.get("run") and state["run"].resumable:
            state["available_actions"] = [{"action":"resume", "label":"继续提取",
                "endpoint":f"/api/report-sessions/{state['session_id']}/resume", "method":"POST"}]
        return ExtractionSession.model_validate(state).model_dump(mode="json")

    def resume(self, key):
        with self._lock:
            state = self.get(key)
            if not state.get("run_id"):
                raise ValueError("The extraction has not started")
            if state.get("batch_id"):
                if state.get("resume_requested"):
                    return self.detail(state)
                run = self.detail(state).get("run") or {}
                if not run.get("resumable"):
                    raise ValueError("This extraction is not ready to resume")
                state.update(status=WAITING_FOR_WINDOW, resume_requested=True, admitted_at=None)
            else:
                self.remote("POST", f"/api/runs/{state['run_id']}/resume", {})
                state["status"] = "queued"
            state.update(queue_task_id=None, cancel_requested=False, error=None,
                         execution_index=state.get("execution_index",0)+1)
            self.save(state)
            return self.detail(self.get(key))

    def cancel(self, key):
        with self._lock:
            state = self.get(key)
            current = self.detail(state)
            if current["status"] in TERMINAL:
                raise ValueError("任务已经结束，不能再次取消。")
            if state["status"] == WAITING_FOR_WINDOW:
                state.update(cancel_requested=True, status="cancelled")
                state.pop("resume_requested", None)
                self.save(state)
                return self.detail(state)
            state.pop("resume_requested", None)
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
        if state.get("processing_id"):
            active = self._cancel_preparation(state)
            state["status"] = "cancelling" if active else "cancelled"
        elif not state.get("run_id"):
            state["status"] = "cancelled"
        self.save(state)

    def _cancel_preparation(self, state):
        process = self.processing.get(state["processing_id"])
        if process["status"] in {"queued", "running", "cancelling"}:
            self.processing.action(state["processing_id"], "cancel", ProcessingActionRequest())
            process = self.processing.get(state["processing_id"])
        active = self._active_preparation(process)
        for step in active:
            if step.get("task_id"):
                try:
                    self.cancel_task(step["task_id"])
                except ValueError:
                    pass  # The child may have returned while cancellation propagated.
        return active

    def tick(self, state, run_record=None):
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
        providers = tuple(selection.provider for selection in
                          (request.model_selection, request.document_model_selection) if selection)
        batch_id = state.get("batch_id")
        resuming = state["session_id"] if state.get("resume_requested") else None
        if (batch_id and (not run_record or run_record["status"] not in TERMINAL)
                and self.batches.provider_paused(batch_id, providers, resuming_session=resuming)):
            return
        if state.get("resume_requested"):
            try:
                run_record = self.remote("POST", f"/api/runs/{state['run_id']}/resume", {})
            except ValueError as exc:
                state.pop("resume_requested")
                state.update(status="interrupted", error=str(exc))
                self.save(state)
                return
            state.pop("resume_requested")
            self.save(state, release_provider=True)
        if not state["run_id"]:
            if state["ir_run_id"]:
                self.store.pin_ir(state["session_id"], state["ir_run_id"])
            native = request.native().model_copy(
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
        run = run_record if run_record is not None else self.remote("GET", f"/api/runs/{state['run_id']}")
        previous = self.store.get(state["queue_task_id"]) if state.get("queue_task_id") else None
        if run_record is not None and run["status"] == "queued" and previous is not None and previous.status.value not in {"queued", "running"}:
            # Bulk observations can precede a child's terminal transition.
            # Confirm the native run still requests another slice before
            # dispatching it from a snapshot taken before that child exited.
            run = self.remote("GET", f"/api/runs/{state['run_id']}")
        if run["status"] == "queued" and (previous is None or previous.status.value not in {"queued", "running"}):
            if previous is not None:
                state['execution_index'] = state.get('execution_index',0)+1
            token = dispatch_key.set(f"{state['session_id']}:extract:{state.get('execution_index',0)}")
            try:
                batch_context = {"batch_id": batch_id,
                                 "model_provider": request.model_selection.provider if request.model_selection else None,
                                 "document_model_provider": request.document_model_selection.provider if request.document_model_selection else None} if batch_id else {}
                task = self.queue(state["run_id"], request.name, request.budget.timeout_seconds,
                                  session_id=state["session_id"], **batch_context)
            finally:
                dispatch_key.reset(token)
            state["queue_task_id"] = task.task_id
            state["status"] = "running"
            self.save(state)
        if run["status"] in TERMINAL:
            if run["status"] == "cancelled":
                state.update(cancel_requested=True, status="cancelling")
                self._cancel_children(state)
                return
            state["status"] = run["status"]
            self.save(state)

    def reconcile_cancelled(self):
        """Stop dangling children without changing already cancelled reports."""
        with self.store._connect() as db:
            rows = db.execute("SELECT body FROM clause_extraction_plans WHERE json_extract(body,'$.status')='cancelled'").fetchall()
        stopped = []
        for row in rows:
            state = json.loads(row[0])
            child = self.store.active_native_task(state['run_id']) if state.get('run_id') else None
            if child:
                self.cancel_task(child.task_id)
                stopped.append(child.task_id)
            if state.get('processing_id'):
                process = self.processing.get(state['processing_id'])
                if self._active_preparation(process):
                    stopped.extend(step['task_id'] for step in self._cancel_preparation(state) if step.get('task_id'))
        return stopped

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                with self.store._connect() as db:
                    rows = db.execute(
                        """SELECT body FROM clause_extraction_plans
                           WHERE json_extract(body,'$.status') IN (?,?,?) ORDER BY rowid""",
                        ACTIVE_STATUSES,
                    ).fetchall()
                states = [json.loads(row[0]) for row in rows]
                try:
                    runs = self.run_statuses(states)
                except ExtractionServiceUnavailable:
                    self._stop.wait(1)
                    continue
                for snapshot in states:
                    with self._lock:
                        try:
                            state = self.get(snapshot["session_id"])
                        except KeyError:
                            continue
                        if state["status"] not in ACTIVE_STATUSES:
                            continue
                        try:
                            observed = runs.get(state.get("run_id")) if state.get("run_id") == snapshot.get("run_id") else None
                            self.tick(state, observed)
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
