"""Execute a report session through the foundation-16 route.

All package projections share one bounded table pass over the frozen IR; each
package job then receives its own importable result bundle. Calls are appended
to the shared session folder as they finish, so a resumed session reuses them.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from esg_targeted.contracts import ExtractionRequest, JobStatus
from esg_targeted.foundation.bundle import BundleWriter
from esg_targeted.foundation.catalog import METRICS
from esg_targeted.foundation.engine import FoundationEngine, read_calls
from esg_targeted.ir.loader import DocumentIrLoader
from esg_targeted.results.validation import ResultContractError

from esg_targeted.foundation.catalog import metrics_for

FOUNDATION_METRIC_IDS = frozenset(m.metric_id for m in METRICS)


def foundation_metric_ids(profile: str = "base16") -> frozenset[str]:
    """The metric ids of one profile, so callers validate against the right set."""
    return frozenset(metric.metric_id for metric in metrics_for(profile))
STAGE_TABLES = "foundation_tables"
STAGE_TEXT = "foundation_text"
STAGE_BUNDLE = "foundation_bundle"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FoundationSessionExecutor:
    def __init__(self, service) -> None:
        self.service = service
        self.settings = service.settings
        self.jobs = service.jobs
        self.artifacts = service.artifacts

    def run(self, session_id: str, state: dict, ir_path) -> None:
        projections = state["projections"]
        shared = self.artifacts.report_session_dir(session_id) / "foundation16"
        shared.mkdir(parents=True, exist_ok=True)
        profile = str(state.get("request", {}).get("metric_profile") or "base16")
        engine = FoundationEngine(DocumentIrLoader().load(ir_path), profile=profile)
        (shared / "plan.json").write_text(json.dumps(engine.plan(), ensure_ascii=False, indent=2))
        calls_path = shared / "calls.jsonl"
        reusable = {r["task_id"]: r for r in read_calls(calls_path) if r.get("status") == "ok"} if calls_path.is_file() else {}
        cache = self._answers_for_ir(engine.ir.run_id, session_id)
        total = len(engine.tasks)
        started = time.perf_counter()
        lead = projections[0]["job_id"]
        for projection in projections:
            self.jobs.update(projection["job_id"], status=JobStatus.RUNNING, stage=STAGE_TABLES,
                             progress_current=0, progress_total=max(1, total),
                             summary=self._summary(projection, processed=0, current_call=None))

        def on_start(event: dict) -> None:
            stage = STAGE_TABLES if event["kind"] == "table" else STAGE_TEXT
            call = {"tool": stage, "route": "foundation16", "role": "specialist", "model_id": self.settings.qwen_model_path.name,
                    "object_id": event.get("table_id") or f"text:{event.get('group')}", "phase": "generation",
                    "page_number": event.get("page_number"), "index": event["index"], "total": event["total"],
                    "started_at": _now()}
            self.jobs.update(lead, stage=stage, progress_current=event["index"] - 1,
                             summary=self._summary(projections[0], processed=0, current_call=call))

        with calls_path.open("a") as sink:
            def on_record(record: dict) -> None:
                # Answers taken from another session are written here too, so
                # this session's calls file alone reproduces its result.
                if record.get("reused") != "session":
                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    sink.flush()

            model = self._model(state)
            try:
                records = engine.execute(model, on_start=on_start, on_record=on_record, reuse=reusable, cache=cache,
                                         cancel_check=lambda: self.service._state(session_id)["cancel_requested"])
            finally:
                model.close()
        if self.service._state(session_id)["cancel_requested"]:
            for projection in projections:
                self.jobs.update(projection["job_id"], status=JobStatus.CANCELLED, stage="cancelled",
                                 summary=self._summary(projection, processed=0, current_call=None))
            return
        summary = engine.conclude(records, elapsed_seconds=time.perf_counter() - started)
        (shared / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        writer = BundleWriter(self.settings, model_label=self.settings.qwen_model_path.name, source_tasks=engine.tasks)
        for projection in projections:
            job_id = projection["job_id"]
            self.jobs.update(job_id, stage=STAGE_BUNDLE, summary=self._summary(projection, processed=0, current_call=None))
            request = ExtractionRequest.model_validate(self.artifacts.read_json(job_id, "input/request.json"))
            conclusions = [c for c in summary["conclusions"] if c["metric_id"] in set(request.metric_ids)]
            try:
                result = writer.write_package(projection["package_id"], projection["package_version"], conclusions, summary,
                                              engine.ir, output_dir=self.artifacts.job_dir(job_id), job_id=job_id,
                                              session_id=session_id, request=request)
            except ResultContractError as exc:
                # Packages are separate deliverables: an invalid bundle fails
                # its own package and keeps every other package writable.
                self.jobs.update(job_id, status=JobStatus.FAILED, stage="failed", error=f"{type(exc).__name__}: {exc}",
                                 summary=self._summary(projection, processed=0, current_call=None))
                self.jobs.add_event(job_id, "bundle_contract_failed", "error",
                                    f"Package {projection['package_id']} produced an invalid result bundle",
                                    {"error": str(exc)})
                continue
            counts = result["record_counts"]
            self.jobs.update(job_id, status=JobStatus.COMPLETED, stage="finished", progress_current=max(1, total),
                             progress_total=max(1, total),
                             summary=self._summary(projection, processed=len(conclusions), current_call=None,
                                                   core_facts=counts["quantitative_observations"] + counts["qualitative_assertions"],
                                                   conclusions=conclusions, timing=summary))

    def _answers_for_ir(self, ir_run_id: str, session_id: str) -> dict[str, dict]:
        """Successful answers of earlier foundation16 sessions on the same IR, by request digest."""
        answers: dict[str, dict] = {}
        root = self.artifacts.report_session_dir(session_id).parent
        for plan_path in root.glob("*/foundation16/plan.json"):
            if plan_path.parent.parent.name == session_id:
                continue
            try:
                if json.loads(plan_path.read_text()).get("ir_run_id") != ir_run_id:
                    continue
                records = read_calls(plan_path.parent / "calls.jsonl")
            except (OSError, ValueError):
                continue
            for record in records:
                if record.get("status") == "ok" and record.get("request_sha"):
                    answers.setdefault(record["request_sha"], record)
        return answers

    def _model(self, state: dict):
        from esg_targeted.models.qwen import QwenMlxModel

        budget = state.get("plan", {}).get("budget", {})
        timeout = min(float(budget.get("call_timeout_seconds") or 300), 300.0)
        return QwenMlxModel(self.settings.qwen_model_path, max_tokens=4096, timeout_seconds=timeout)

    @staticmethod
    def _summary(projection: dict, *, processed: int, current_call: dict | None, core_facts: int = 0,
                 conclusions: list[dict] | None = None, timing: dict | None = None) -> dict:
        selected = projection.get("selected_metric_ids") or []
        summary = {
            "route": "foundation16",
            "counts": {"selected_metrics": len(selected), "processed_metrics": processed, "core_facts": core_facts,
                       "unfinished_metrics": max(0, len(selected) - processed) if processed else 0},
            "agent": {"current_call": current_call},
        }
        if conclusions is not None:
            from .bundle import conclusion_summary
            summary["foundation_conclusions"] = [conclusion_summary(c) for c in conclusions]
        if timing is not None:
            summary["model_execution"] = {key: timing.get(key) for key in
                                          ("model_calls", "input_tokens", "output_tokens", "elapsed_seconds")}
        return summary
