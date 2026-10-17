"""Durable, model-free limits for one explicitly owned extraction execution.

The ledger path determines task ownership; independent package tasks use
independent paths. Reservations survive process death. Raw prompts, responses and credentials are
never copied to this ledger. A scope must fingerprint the complete task input,
including its row-group selection, rather than only an object identifier.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeBudgetLimits:
    max_model_calls: int = 128
    max_format_repairs: int = 16
    max_input_tokens: int = 750_000
    max_output_tokens: int = 150_000
    max_consecutive_failures: int = 3

    def __post_init__(self):
        if self.max_format_repairs < 0 or any(value <= 0 for key, value in asdict(self).items() if key != "max_format_repairs"):
            raise ValueError("Report runtime budget limits must be positive")


class BudgetExceeded(RuntimeError):
    category = "budget"

    def __init__(self, reason: str, usage: dict[str, Any], *, scope: str = "", boundary: str = "report"):
        super().__init__(reason)
        self.reason, self.usage, self.scope, self.boundary = reason, usage, scope, boundary


def task_input_fingerprint(task: str, context: Any) -> str:
    """Stable failure scope; normal disjoint object slices remain independent."""
    serialized = json.dumps({"task": task, "context": context}, sort_keys=True,
                            ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ReportRuntimeBudget:
    def __init__(self, path: Path, limits: RuntimeBudgetLimits | dict[str, Any] | None = None, *, boundary: str = "report"):
        if boundary not in {"report", "package"}:
            raise ValueError("Runtime budget boundary must be report or package")
        self.path = Path(path)
        self.boundary = boundary
        if isinstance(limits, dict):
            fields = RuntimeBudgetLimits.__dataclass_fields__
            limits = RuntimeBudgetLimits(**{key: value for key, value in limits.items() if key in fields and value is not None})
        self.limits = limits or RuntimeBudgetLimits()

    @contextlib.contextmanager
    def _ledger(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                ledger = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {
                    "schema_version": "report-runtime-budget/1", "calls": {}, "failures": {}}
                ledger["limits"] = asdict(self.limits)
                yield ledger
                temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                try:
                    with temporary.open("w", encoding="utf-8") as stream:
                        json.dump(ledger, stream, ensure_ascii=False, indent=2)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.chmod(0o600)
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _usage(ledger: dict) -> dict[str, int]:
        calls = list(ledger["calls"].values())
        return {"model_calls": len(calls), "format_repairs": sum(bool(call["is_format_repair"]) for call in calls),
                "input_tokens": sum(call["input_tokens"] for call in calls),
                "output_tokens": sum(call["output_tokens"] if call["state"] == "finished" else call["max_output_tokens"] for call in calls),
                "unfinished_calls": sum(call["state"] != "finished" for call in calls)}

    def snapshot(self) -> dict[str, Any]:
        with self._ledger() as ledger:
            return {"boundary": self.boundary, "limits": ledger["limits"], "usage": self._usage(ledger),
                    "accounting": self._accounting(ledger), "failures": dict(ledger["failures"])}

    @staticmethod
    def _accounting(ledger: dict) -> dict[str, Any]:
        # Do not label pre-existing caller values as measured usage. Existing
        # call records remain unchanged and contribute to the legacy bucket.
        result = {"schema_version": "token-accounting/1", "input": {}, "output": {}}
        for record in ledger["calls"].values():
            for axis in ("input", "output"):
                basis = record.get(f"{axis}_token_basis", "legacy_unclassified")
                count = record[f"{axis}_tokens"]
                if axis == "output" and record["state"] != "finished":
                    count, basis = record["max_output_tokens"], "configured_output_reservation"
                result[axis][basis] = result[axis].get(basis, 0) + count
        return result

    def begin_call(self, label: str, *, input_tokens: int, max_output_tokens: int,
                   scope: str = "", is_format_repair: bool = False, call_id: str | None = None,
                   input_token_basis: str = "caller_reservation", input_admission: dict | None = None) -> dict:
        if input_tokens < 0 or max_output_tokens <= 0:
            raise ValueError("Call token reservation must have non-negative input and positive output")
        call_id = call_id or uuid.uuid4().hex
        with self._ledger() as ledger:
            if call_id in ledger["calls"]:
                return {"call_id": call_id, **ledger["calls"][call_id]}
            usage = self._usage(ledger)
            failures = ledger["failures"].get(scope, {}) if scope else {}
            checks = [(usage["model_calls"] + 1, self.limits.max_model_calls, "model call"),
                      (usage["format_repairs"] + int(is_format_repair), self.limits.max_format_repairs, "format repair"),
                      (usage["input_tokens"] + input_tokens, self.limits.max_input_tokens, "input token"),
                      (usage["output_tokens"] + max_output_tokens, self.limits.max_output_tokens, "output token")]
            for requested, limit, name in checks:
                if name == "input token" and input_admission and not input_admission.get("exact") and input_admission.get("runtime_refinement"):
                    # A capable local runtime must replace this cold estimate
                    # with prepared engine tokens before its first forward.
                    continue
                if requested > limit:
                    estimation = " estimated admission" if name == "input token" and input_admission and not input_admission.get("exact") else ""
                    raise BudgetExceeded(f"{self.boundary.title()} {name}{estimation} budget exhausted ({requested} > {limit}); retained stages remain available", usage, scope=scope, boundary=self.boundary)
            if failures.get("consecutive", 0) >= self.limits.max_consecutive_failures:
                raise BudgetExceeded("Identical task input repeatedly failed without progress; split or revise this task before retrying", usage, scope=scope, boundary="task")
            record = {"label": label, "scope": scope, "is_format_repair": is_format_repair,
                      "input_tokens": input_tokens, "max_output_tokens": max_output_tokens,
                      "input_token_basis": input_token_basis,
                      "output_tokens": 0, "state": "reserved"}
            if input_admission is not None:
                record["input_admission"] = input_admission
            ledger["calls"][call_id] = record
            return {"call_id": call_id, **record}

    def revise_input_reservation(self, call_id: str, *, input_tokens: int,
                                 input_token_basis: str, input_admission: dict | None = None) -> dict:
        """Replace an estimate with prepared engine tokens before generation.

        This updates the existing call reservation; it never adds a model call
        or charges both the estimate and prepared request. Historical finished
        provider usage is immutable.
        """
        if input_tokens < 0:
            raise ValueError("Prepared input tokens must be non-negative")
        with self._ledger() as ledger:
            record = ledger["calls"][call_id]
            if record["state"] == "finished":
                raise ValueError("Cannot revise a finished model call")
            usage = self._usage(ledger)
            requested = usage["input_tokens"] - record["input_tokens"] + input_tokens
            if requested > self.limits.max_input_tokens:
                kind = "prepared" if not input_admission or input_admission.get("exact") else "estimated"
                raise BudgetExceeded(f"{self.boundary.title()} {kind} input token budget exhausted ({requested} > {self.limits.max_input_tokens}); retained stages remain available",
                                     usage, scope=record["scope"], boundary=self.boundary)
            record.update(input_tokens=input_tokens, input_token_basis=input_token_basis)
            if input_admission is not None:
                record["input_admission"] = input_admission
            return self._usage(ledger)

    def finish_call(self, call_id: str, *, input_tokens: int | None = None,
                    output_tokens: int = 0, outcome: str = "success", failure_signature: str | None = None,
                    input_token_basis: str | None = None, output_token_basis: str | None = None) -> dict:
        if output_tokens < 0 or (input_tokens is not None and input_tokens < 0):
            raise ValueError("Actual token usage must be non-negative")
        with self._ledger() as ledger:
            record = ledger["calls"][call_id]
            if record["state"] == "finished":
                return self._usage(ledger)
            record.update(state="finished", outcome=outcome, output_tokens=output_tokens)
            if input_tokens is not None:
                record["input_tokens"] = input_tokens
                record["input_token_basis"] = input_token_basis or "caller_reported"
            record["output_token_basis"] = output_token_basis or "caller_reported"
            if outcome != "returned":
                self._record_validation(ledger, record, outcome, failure_signature)
            return self._usage(ledger)

    @staticmethod
    def _record_validation(ledger, record, outcome, failure_signature):
        if record.get("validation_recorded"):
            return
        record["validation_recorded"] = True
        record["validation_outcome"] = outcome
        scope = record["scope"]
        if not scope:
            return
        prior = ledger["failures"].get(scope, {})
        if outcome == "success":
            ledger["failures"].pop(scope, None)
        elif failure_signature:
            signature = hashlib.sha256(failure_signature.encode("utf-8")).hexdigest()
            ledger["failures"][scope] = {"signature": signature,
                "consecutive": prior.get("consecutive", 0) + 1}

    def record_validation(self, call_id: str, *, outcome: str,
                          failure_signature: str | None = None) -> dict:
        if outcome not in {"success", "error"}:
            raise ValueError("Validation outcome must be success or error")
        with self._ledger() as ledger:
            record = ledger["calls"][call_id]
            if record["state"] != "finished":
                raise ValueError("Generation must finish before protocol validation")
            self._record_validation(ledger, record, outcome, failure_signature)
            return self._usage(ledger)
