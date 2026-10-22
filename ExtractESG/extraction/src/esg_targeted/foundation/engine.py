"""Foundation-16 engine: plan bounded calls over a fixed IR, execute, conclude."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from esg_targeted.foundation.catalog import METRICS, metrics_for
from esg_targeted.foundation.consolidate import NOT_FOUND_STATUSES, consolidate
from esg_targeted.foundation.kernel import TableTask, bind, plan_table_tasks, run_task, task_digest
from esg_targeted.foundation.period import detect_reporting_period
from esg_targeted.foundation.retrieval import select_tables
from esg_targeted.foundation.tables import build_table_view, composed_tables
from esg_targeted.foundation.textlayer import PageText, load_native_text, reconcile_view
from esg_targeted.foundation.text_pass import (
    TEXT_GROUPS, TextBlock, bind_text, page_scope2_methods, run_text_task, text_task_digest,
)
from esg_targeted.ir.loader import DocumentIrLoader, LoadedDocumentIr

# Bump whenever the binding, guard or consolidation rules change meaning. A
# saved result from an older rule set is never presented as a current one.
ENGINE_VERSION = "foundation16-engine-v2"


SCOPE2_UNSTATED_FLAGS = ("scope2_method_unstated", "scope2_method_not_stated_in_source")


def needs_text_pass(conclusion: dict | None) -> bool:
    """Not found in tables, or a Scope 2 value whose method the tables never state."""
    if conclusion is None or conclusion["status"] in NOT_FOUND_STATUSES:
        return True
    return conclusion["code"] in ("E1-6_09", "E1-6_10") and all(
        any(flag in member["flags"] for flag in SCOPE2_UNSTATED_FLAGS) for member in conclusion.get("members") or [])


class FoundationEngine:
    """One compact model call per candidate table, then bounded text follow-up."""

    # Class-level defaults keep engines built without __init__ (tests, replay)
    # on the base set instead of failing on a missing attribute.
    profile = "base16"
    metrics = METRICS
    selected_codes = tuple(metric.code for metric in METRICS)

    def __init__(self, ir: LoadedDocumentIr, *, profile: str = "base16",
                 only_tables: list[str] | None = None) -> None:
        started = time.perf_counter()
        self.ir = ir
        pages = {payload["page"]["page_index"]: payload for payload in ir.pages}
        views = [v for v in (build_table_view(t, pages.get(t.get("page_index"))) for t in composed_tables(ir.tables)) if v]
        candidates = select_tables(views)
        native = load_native_text(ir.root)
        for candidate in candidates:
            if native.get(candidate.view.page_index):
                reconcile_view(candidate.view, PageText(native[candidate.view.page_index]))
        header_years = [y for c in candidates for y in c.view.header_years()]
        self.period = detect_reporting_period(ir.manifest, ir.pages, header_years=header_years)
        # The selected metric set is an input, not a constant of the link.
        self.profile = profile
        self.metrics = metrics_for(profile)
        self.selected_codes = tuple(metric.code for metric in self.metrics)
        allowed = set(self.selected_codes)
        self.tasks: list[TableTask] = [task for c in candidates
                                       for task in plan_table_tasks(c.view, sorted(c.groups), allowed)]
        if only_tables:
            self.tasks = [t for t in self.tasks if t.view.table_id in only_tables]
        self.prepare_seconds = round(time.perf_counter() - started, 2)

    @classmethod
    def from_path(cls, ir_root: Path, **kwargs) -> "FoundationEngine":
        return cls(DocumentIrLoader().load(ir_root), **kwargs)

    def plan(self) -> dict:
        return {"engine_version": ENGINE_VERSION, "metric_profile": self.profile,
                "selected_metric_count": len(self.metrics),
                "document_label": self.ir.manifest.get("document_label"),
                "ir_run_id": self.ir.run_id, "ir_revision": self.ir.ir_revision, "document_id": self.ir.document_id,
                "reporting_period": self.period.__dict__,
                "tasks": [{"task_id": t.task_id, "table_id": t.view.table_id, "page_number": t.view.page_number,
                           "groups": t.groups, "lines": len(t.lines), "text_layer_agreement": t.view.text_layer_agreement,
                           "headers_rebuilt": t.view.headers_rebuilt} for t in self.tasks],
                "prepare_seconds": self.prepare_seconds}

    def execute(self, model, *, on_start: Callable[[dict], None] | None = None,
                on_record: Callable[[dict], None] | None = None,
                cancel_check: Callable[[], bool] | None = None,
                reuse: dict[str, dict] | None = None, strict_reuse: bool = True,
                cache: dict[str, dict] | None = None) -> list[dict]:
        """Run table calls then text follow-up.

        `reuse` re-binds finished calls by task id; with `strict_reuse` only when
        the saved call answered exactly the request the current code would send.
        `cache` holds earlier answers by request digest (greedy decoding returns
        the same answer to the same request), e.g. from other sessions on this IR.
        """
        records: list[dict] = []
        reuse = reuse or {}
        cache = cache or {}
        total = len(self.tasks)

        def saved_answer(task_id: str, digest) -> tuple[dict | None, str | None]:
            saved = reuse.get(task_id)
            if saved is not None and saved.get("status") == "ok":
                if not strict_reuse:
                    return saved, "session"
                sha = digest()
                if saved.get("request_sha") == sha:
                    return saved, "session"
                return (cache[sha], "cache") if sha in cache else (None, None)
            if cache:
                sha = digest()
                if sha and sha in cache:
                    return cache[sha], "cache"
            return None, None

        for index, task in enumerate(self.tasks, 1):
            if cancel_check and cancel_check():
                return records
            saved, origin = saved_answer(task.task_id, lambda: task_digest(task, self.period.year))
            if saved is not None:
                observations, extras = bind(task, saved["payload"], self.period.year)
                record = {**saved, "task_id": task.task_id, "observations": [o.to_dict() for o in observations],
                          **extras, "reused": origin}
            else:
                if on_start:
                    on_start({"kind": "table", "index": index, "total": total, "task_id": task.task_id,
                              "table_id": task.view.table_id, "page_number": task.view.page_number, "groups": task.groups})
                record = run_task(model, task, self.period.year)
            records.append(record)
            if on_record:
                on_record(record)
        interim = {c["code"]: c for c in consolidate(records, self.period.year, self.metrics)}
        allowed = set(self.selected_codes)
        for group, codes in TEXT_GROUPS.items():
            codes = tuple(code for code in codes if code in allowed)
            if not codes:
                continue
            missing = tuple(c for c in codes if needs_text_pass(interim.get(c)))
            if not missing:
                continue
            if cancel_check and cancel_check():
                return records
            saved, origin = saved_answer(f"text:{group}", lambda: text_task_digest(group, missing, self.ir.pages, self.period.year))
            if saved is not None and tuple(saved.get("codes") or ()) == missing:
                record = self.replay([saved])[0]
                record["reused"] = origin
                records.append(record)
                if on_record:
                    on_record(record)
                continue
            if on_start:
                on_start({"kind": "text", "group": group, "codes": list(missing), "index": total, "total": total})
            record = run_text_task(model, group, missing, self.ir.pages, self.period.year)
            records.append(record)
            if on_record:
                on_record(record)
        return records

    def replay(self, saved: list[dict]) -> list[dict]:
        """Re-bind saved model payloads with current rules; no model call."""
        by_id = {record["task_id"]: record for record in saved}
        records = []
        for task in self.tasks:
            record = by_id.get(task.task_id)
            if record is None:
                continue
            if record.get("status") == "ok":
                observations, extras = bind(task, record["payload"], self.period.year)
                record = {**record, "observations": [o.to_dict() for o in observations], **extras}
            records.append(record)
        for task_id, record in by_id.items():
            if not task_id.startswith("text:"):
                continue
            if record.get("status") == "ok":
                blocks = [TextBlock(**b) for b in record.get("blocks") or []]
                observations = bind_text(record["payload"], blocks, self.period.year, page_scope2_methods(self.ir.pages))
                record = {**record, "observations": [o.to_dict() for o in observations]}
                record.pop("method_notes", None)
                notes = [{"block_id": o.note_block_id, "text": o.row_label, "page_number": o.page_number}
                         for o in observations if o.code == "E2-4_09" and o.status != "rejected"]
                if notes:
                    record["method_notes"] = notes
            records.append(record)
        return records

    def conclude(self, records: list[dict], *, elapsed_seconds: float) -> dict:
        answered = [r for r in records if r.get("status") not in ("no_candidates",)]
        fresh = [r for r in answered if not r.get("reused")]
        return {
            **self.plan(),
            "elapsed_seconds": round(elapsed_seconds, 2),
            "model_calls": len(fresh),
            "reused_answers": len(answered) - len(fresh),
            "input_tokens": sum(r.get("input_tokens") or 0 for r in fresh),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in fresh),
            "failed_calls": [r["task_id"] for r in records if r["status"] not in ("ok", "no_candidates")],
            "conclusions": consolidate(records, self.period.year, self.metrics),
        }


def read_calls(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
