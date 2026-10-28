"""Unified lifecycle for original standard clauses and project extensions.

One append-only ledger records what real extraction runs actually proved. An
acceptance is computed from a frozen result bundle, never declared by an
operator, and it binds to the clause's scientific contract digest. Editing the
clause therefore invalidates the old acceptance instead of silently inheriting a
verdict that no longer describes it.

States are derived, not stored:

``draft -> compiled -> trialed -> accepted -> publishable`` (plus ``retired``)

``publishable`` additionally requires the clause's business approval, which stays
with its own authority (extension approval receipts, package release registry).
This module only refuses to call a clause publishable when a real acceptance for
its current contract is missing.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .contracts import Sha256, SemanticVersion, StableId, StrictModel
from .errors import PackageIntegrityError
from .io import pretty_json_bytes

LEDGER_SCHEMA = "clause-lifecycle-ledger-1.0"

# Checks that must pass before a run can support a publishable clause. A bound
# fact additionally needs its physical anchor; established non-disclosure has no
# anchor to check. Optional checks add evidence but never replace these.
REQUIRED_CHECKS = ("task_executed", "facts_bound", "current_period")

EXPLAINED_ABSENCE = {"confirmed_not_disclosed"}
UNEXPLAINED_ABSENCE = {"not_covered", "unmapped", "insufficient_evidence"}

CONTRACT_FIELDS = (
    "template", "value_role", "unit_dimension", "canonical_unit_id", "normalization",
    "identity_axes", "comparison_axes", "comparison_mode", "aggregation",
    "semantic_contract_version", "positive_definition", "interpretation_guidance",
    "distinguish_from", "exclusions", "comparison_requirements", "semantic_type_note",
)


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()


class ClauseRef(StrictModel):
    """A clause either belongs to an immutable standard package or to a project extension."""
    kind: Literal["original", "extension"]
    package_id: StableId | None = None
    package_version: SemanticVersion | None = None
    source_datapoint_id: str | None = None
    extension_id: StableId | None = None
    definition_version: SemanticVersion | None = None

    @model_validator(mode="after")
    def one_identity(self):
        if self.kind == "original":
            if not self.package_id or not self.package_version or not self.source_datapoint_id:
                raise ValueError("An original clause requires its package identity and source datapoint")
            if self.extension_id or self.definition_version:
                raise ValueError("An original clause cannot carry extension identity")
        else:
            if not self.extension_id or not self.definition_version:
                raise ValueError("An extension clause requires its extension identity and version")
            if self.package_id or self.package_version or self.source_datapoint_id:
                raise ValueError("An extension clause cannot carry package identity")
        return self

    def key(self) -> str:
        if self.kind == "original":
            return f"original:{self.package_id}@{self.package_version}:{self.source_datapoint_id}"
        return f"extension:{self.extension_id}@{self.definition_version}"

    def label(self) -> str:
        return self.source_datapoint_id if self.kind == "original" else self.extension_id


class TrialReference(StrictModel):
    run_ref: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    ir_run_id: str | None = None
    reporting_year: int | None = None
    report_label: str | None = None


class AcceptanceCheck(StrictModel):
    check: Literal["task_executed", "facts_bound", "current_period", "evidence_anchored",
                   "unit_and_period", "gold_match", "single_interpretation"]
    outcome: Literal["pass", "fail", "not_applicable"]
    observed: int | None = None
    detail: str = Field(min_length=1)


class ClauseAcceptance(StrictModel):
    # 1.0 records predate the current-period check; they stay valid history.
    schema_version: Literal["clause-acceptance-1.0", "clause-acceptance-1.1"] = "clause-acceptance-1.0"
    acceptance_id: str = Field(min_length=1)
    clause: ClauseRef
    contract_digest: Sha256
    trial: TrialReference
    checks: list[AcceptanceCheck] = Field(min_length=1)
    outcome: Literal["accepted", "rejected"]
    computed_at: datetime
    recorded_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def verdict_follows_checks(self):
        if self.computed_at.tzinfo is None:
            raise ValueError("Acceptance time requires a timezone")
        names = [check.check for check in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("Acceptance checks must be unique")
        failed = [check for check in self.checks if check.outcome == "fail"]
        passed = {check.check for check in self.checks if check.outcome == "pass"}
        by_check = {check.check: check for check in self.checks}
        bound = (by_check.get("facts_bound").observed or 0) if "facts_bound" in by_check else 0
        anchored = "evidence_anchored" not in by_check or by_check["evidence_anchored"].outcome == "pass"
        # A required check may be not_applicable only when the run could not supply
        # the evidence it needs (for example an unknown report period).
        required = (REQUIRED_CHECKS if self.schema_version == "clause-acceptance-1.1"
                    else ("task_executed", "facts_bound"))
        required_ok = all(check in passed or by_check.get(check, AcceptanceCheck(
            check=check, outcome="fail", detail="missing")).outcome == "not_applicable"
            for check in required)
        if self.outcome == "accepted" and (failed or not required_ok or (bound and not anchored)):
            raise ValueError("An accepted clause requires every required check to pass, anchored facts, and no failure")
        if self.outcome == "rejected" and not failed:
            raise ValueError("A rejected clause requires at least one failing check")
        return self


class RetirementImpact(StrictModel):
    dependent_extensions: list[str] = Field(default_factory=list)
    published_observations: int = Field(default=0, ge=0)
    active_tasks: int = Field(default=0, ge=0)
    notes: str | None = None


class ClauseRetirement(StrictModel):
    schema_version: Literal["clause-retirement-1.0"] = "clause-retirement-1.0"
    retirement_id: str = Field(min_length=1)
    clause: ClauseRef
    contract_digest: Sha256
    reason: str = Field(min_length=1)
    retired_by: str = Field(min_length=1)
    retired_at: datetime
    impact: RetirementImpact
    forced: bool = False

    @model_validator(mode="after")
    def aware_time(self):
        if self.retired_at.tzinfo is None:
            raise ValueError("Retirement time requires a timezone")
        return self


class ClauseLifecycleLedger(StrictModel):
    schema_version: Literal["clause-lifecycle-ledger-1.0"] = LEDGER_SCHEMA
    acceptances: list[ClauseAcceptance] = Field(default_factory=list)
    retirements: list[ClauseRetirement] = Field(default_factory=list)

    def latest_acceptance(self, key: str) -> ClauseAcceptance | None:
        matches = [row for row in self.acceptances if row.clause.key() == key]
        return max(matches, key=lambda row: row.computed_at) if matches else None

    def retirement(self, key: str) -> ClauseRetirement | None:
        matches = [row for row in self.retirements if row.clause.key() == key]
        return max(matches, key=lambda row: row.retired_at) if matches else None

    def append_acceptance(self, acceptance: ClauseAcceptance) -> "ClauseLifecycleLedger":
        return self.model_copy(update={"acceptances": [*self.acceptances, acceptance]})

    def append_retirement(self, retirement: ClauseRetirement) -> "ClauseLifecycleLedger":
        return self.model_copy(update={"retirements": [*self.retirements, retirement]})


# --- contract identity -----------------------------------------------------

def original_contract_payload(metric: dict) -> dict:
    """Scientific identity of an original clause, excluding platform display policy."""
    contract = metric.get("measurement_contract") or {}
    payload = {field: contract.get(field) for field in CONTRACT_FIELDS if contract.get(field) is not None}
    payload["source_datapoint_id"] = metric.get("source_datapoint_id")
    payload["package_id"] = metric.get("package_id")
    return payload


def original_contract_digest(metric: dict) -> str:
    return _digest(original_contract_payload(metric))


def extension_contract_digest(definition: Any) -> str:
    body = definition.model_dump(mode="json") if hasattr(definition, "model_dump") else definition
    return _digest(body)


def contract_digest(clause: ClauseRef, payload: dict) -> str:
    """Acceptance binds to the clause's own contract, not to a report or a run."""
    if clause.kind == "original":
        return original_contract_digest(payload)
    return extension_contract_digest(payload)


# --- acceptance evaluation -------------------------------------------------

def _normalise_value(value: Any) -> str:
    text = str(value).strip().replace(",", "").replace("，", "")
    try:
        return format(float(text), ".12g")
    except (TypeError, ValueError):
        return text


def evaluate_run(
    result: dict,
    *,
    clause: ClauseRef,
    metric_ids: list[str],
    contract_digest_value: str,
    run_ref: str,
    recorded_by: str,
    reason: str,
    gold: dict | None = None,
    report_label: str | None = None,
) -> ClauseAcceptance:
    """Derive one acceptance verdict from one frozen result bundle.

    A clause only becomes acceptable when the run either bound a qualifying fact
    or established, over covered source objects, that the report does not
    disclose it. ``not_covered`` and ``insufficient_evidence`` stay failures: an
    unread page is not evidence of absence.
    """
    if not metric_ids:
        raise ValueError("Clause acceptance requires at least one executed metric ID")
    records = result.get("records") or {}
    tasks = [task for task in records.get("reporting_tasks") or [] if task.get("metric_id") in set(metric_ids)]
    task_ids = {task.get("task_id") for task in tasks}
    observations = [row for row in records.get("quantitative_observations") or [] if row.get("task_id") in task_ids]
    evidence_by_set: dict[str, list[dict]] = {}
    for row in records.get("evidence_references") or []:
        evidence_by_set.setdefault(row.get("evidence_set_id"), []).append(row)
    gaps = [gap for gap in (result.get("source_facts") or {}).get("gaps") or []
            if set(gap.get("metric_ids") or []) & set(metric_ids)]
    gap_kinds = {gap.get("kind") or gap.get("coverage_state") for gap in gaps}

    document = (result.get("summary") or {}).get("document") or {}
    ir_runs = sorted({task.get("ir_run_id") for task in tasks if task.get("ir_run_id")})
    trial = TrialReference(run_ref=run_ref, document_id=document.get("document_id") or (tasks[0].get("document_id") if tasks else "unknown"),
                           ir_run_id=ir_runs[0] if ir_runs else None,
                           reporting_year=_first_year(result, observations), report_label=report_label or document.get("document_label"))

    checks: list[AcceptanceCheck] = []

    # ``partial`` means the task ran and bound what it could; only a task that
    # never ran (or failed) is not execution evidence.
    finished = {"completed", "partial"}
    if not tasks:
        checks.append(AcceptanceCheck(check="task_executed", outcome="fail", observed=0,
                                      detail="本次运行没有该条款的报告任务"))
    elif all(str(task.get("task_status")) in finished for task in tasks):
        checks.append(AcceptanceCheck(check="task_executed", outcome="pass", observed=len(tasks),
                                      detail=f"{len(tasks)} 个条款任务已执行"))
    else:
        statuses = sorted({str(task.get("task_status")) for task in tasks
                           if str(task.get("task_status")) not in finished})
        checks.append(AcceptanceCheck(check="task_executed", outcome="fail", observed=len(tasks),
                                      detail=f"条款任务未完成：{', '.join(statuses)}"))

    # Facts count when the run bound a printed value, whatever status the task
    # ended with. A partial run that still bound the clause's total is evidence,
    # not an absence.
    bound = [row for row in observations
             if (row.get("value_raw") or "").strip() or (row.get("statement_raw") or "").strip()]
    reported = [task for task in tasks if task.get("found_status") == "reported"]
    if bound:
        checks.append(AcceptanceCheck(check="facts_bound", outcome="pass", observed=len(bound),
                                      detail=f"{len(bound)} 条事实已绑定印刷原值"))
    elif reported:
        checks.append(AcceptanceCheck(check="facts_bound", outcome="fail", observed=0,
                                      detail="任务判定为已披露，但没有绑定任何印刷原值"))
    elif gap_kinds & EXPLAINED_ABSENCE:
        checks.append(AcceptanceCheck(check="facts_bound", outcome="pass", observed=0,
                                      detail="已覆盖的来源对象确认按本条款口径未披露"))
    elif gap_kinds & UNEXPLAINED_ABSENCE:
        checks.append(AcceptanceCheck(check="facts_bound", outcome="fail", observed=0,
                                      detail=f"尚未建立缺失证据：{', '.join(sorted(gap_kinds & UNEXPLAINED_ABSENCE))}"))
    else:
        checks.append(AcceptanceCheck(check="facts_bound", outcome="fail", observed=0,
                                      detail="既无披露事实，也没有覆盖决策说明结果"))

    year = _first_year(result, observations)
    current = [row for row in bound if isinstance(row.get("reporting_year"), int) and row["reporting_year"] == year]
    if year is None:
        checks.append(AcceptanceCheck(check="current_period", outcome="not_applicable", observed=0,
                                      detail="报告期未识别，无法比对事实期间"))
    elif current:
        checks.append(AcceptanceCheck(check="current_period", outcome="pass", observed=len(current),
                                      detail=f"{len(current)} 条事实属于报告当期 {year}"))
    else:
        checks.append(AcceptanceCheck(check="current_period", outcome="fail", observed=0,
                                      detail=f"绑定事实均不属于报告当期 {year}"))

    unanchored = [row.get("observation_id") for row in observations
                  if not any(item.get("ir_object_id") and item.get("pdf_page_index") is not None
                                 for item in evidence_by_set.get(row.get("evidence_set_id"), []))]
    if observations and not unanchored:
        checks.append(AcceptanceCheck(check="evidence_anchored", outcome="pass", observed=len(observations),
                                      detail="每条事实都带 IR 对象与页码锚点"))
    elif observations:
        checks.append(AcceptanceCheck(check="evidence_anchored", outcome="fail", observed=len(unanchored),
                                      detail=f"缺少物理锚点的事实：{', '.join(map(str, unanchored[:5]))}"))
    else:
        checks.append(AcceptanceCheck(check="evidence_anchored",
                                      outcome="not_applicable" if not reported else "fail", observed=0,
                                      detail="没有需要锚点的事实" if not reported else "已披露事实缺少物理锚点"))

    incomplete = [row.get("observation_id") for row in observations
                  if not (row.get("unit_raw") or row.get("unit_id"))
                  or not (row.get("reporting_year") or row.get("period_start"))]
    if observations and not incomplete:
        checks.append(AcceptanceCheck(check="unit_and_period", outcome="pass", observed=len(observations),
                                      detail="每条事实都保留印刷单位与报告期间"))
    elif observations:
        checks.append(AcceptanceCheck(check="unit_and_period", outcome="fail", observed=len(incomplete),
                                      detail=f"缺少单位或期间的事实：{', '.join(map(str, incomplete[:5]))}"))
    else:
        checks.append(AcceptanceCheck(check="unit_and_period", outcome="not_applicable", observed=0,
                                      detail="没有需要检查的定量事实"))

    duplicates = {}
    for row in observations:
        duplicates.setdefault(_normalise_value(row.get("value_raw")), set()).add(row.get("reporting_boundary"))
    if observations and any(len(boundaries) > 1 for boundaries in duplicates.values()):
        checks.append(AcceptanceCheck(check="single_interpretation", outcome="fail", observed=len(duplicates),
                                      detail="同一印刷原值被映射到多个边界且未区分"))
    else:
        checks.append(AcceptanceCheck(check="single_interpretation", outcome="not_applicable", observed=len(observations),
                                      detail="同一印刷原值没有冲突解释"))

    if gold is None:
        checks.append(AcceptanceCheck(check="gold_match", outcome="not_applicable", observed=0,
                                      detail="本条款未提供独立对照期望"))
    else:
        checks.append(_gold_check(gold, observations))

    failed = [check for check in checks if check.outcome == "fail"]
    by_check = {check.check: check for check in checks}
    required_ok = all(by_check.get(name) is not None
                      and by_check[name].outcome in ("pass", "not_applicable")
                      for name in REQUIRED_CHECKS)
    outcome = "rejected" if failed or not required_ok else "accepted"
    return ClauseAcceptance(
        schema_version="clause-acceptance-1.1",
        acceptance_id=f"acceptance-{uuid4().hex}", clause=clause, contract_digest=contract_digest_value, trial=trial,
        checks=checks, outcome=outcome, computed_at=datetime.now(timezone.utc),
        recorded_by=recorded_by, reason=reason)


def _first_year(result: dict, observations: list[dict]) -> int | None:
    """The run's declared reporting period wins over whatever the facts carry."""
    period = ((result.get("summary") or {}).get("configuration") or {}).get("reporting_period") or {}
    if isinstance(period.get("year"), int):
        return period["year"]
    years = [row.get("reporting_year") for row in observations if isinstance(row.get("reporting_year"), int)]
    return max(years) if years else None


def _gold_check(gold: dict, observations: list[dict]) -> AcceptanceCheck:
    expected_present = bool(gold.get("present"))
    expected_value = gold.get("value_raw")
    if not expected_present:
        if not observations:
            return AcceptanceCheck(check="gold_match", outcome="pass", observed=0,
                                   detail="报告未披露本条款，与对照期望一致")
        return AcceptanceCheck(check="gold_match", outcome="fail", observed=len(observations),
                               detail="对照期望记录为未披露，但运行绑定了事实")
    if expected_value is None:
        return AcceptanceCheck(check="gold_match", outcome="fail", observed=len(observations),
                               detail="正向对照期望必须给出印刷原值")
    wanted = _normalise_value(expected_value)
    if any(_normalise_value(row.get("value_raw")) == wanted for row in observations):
        return AcceptanceCheck(check="gold_match", outcome="pass", observed=len(observations),
                               detail=f"绑定原值与对照期望 {expected_value} 一致")
    return AcceptanceCheck(check="gold_match", outcome="fail", observed=len(observations),
                           detail=f"期望 {expected_value}；实际绑定 {', '.join(sorted({str(row.get('value_raw')) for row in observations}))[:200]}")


# --- lifecycle projection --------------------------------------------------

def lifecycle_state(*, clause: ClauseRef, contract_digest_value: str, ledger: ClauseLifecycleLedger,
                    compiled: bool = True, business_approved: bool = False) -> dict:
    key = clause.key()
    acceptance = ledger.latest_acceptance(key)
    retirement = ledger.retirement(key)
    stale = bool(acceptance and acceptance.contract_digest != contract_digest_value)
    accepted = bool(acceptance and not stale and acceptance.outcome == "accepted")
    reasons: list[str] = []
    if retirement:
        reasons.append("retired")
    if acceptance is None:
        reasons.append("no_real_acceptance")
    elif stale:
        reasons.append("contract_changed_since_acceptance")
    elif acceptance.outcome == "rejected":
        reasons.extend(f"acceptance_failed:{check.check}" for check in acceptance.checks if check.outcome == "fail")
    if not business_approved:
        reasons.append("business_approval_missing")
    publishable = accepted and business_approved and retirement is None
    if retirement:
        state = "retired"
    elif publishable:
        state = "publishable"
    elif accepted:
        state = "accepted"
    elif acceptance and not stale:
        state = "trialed"
    elif compiled:
        state = "compiled"
    else:
        state = "draft"
    return {"clause": clause.key(), "kind": clause.kind, "label": clause.label(), "state": state,
            "publishable": publishable, "reasons": reasons,
            "acceptance": acceptance.model_dump(mode="json") if acceptance else None,
            "acceptance_stale": stale,
            "retirement": retirement.model_dump(mode="json") if retirement else None}


def retire_clause(ledger: ClauseLifecycleLedger, *, clause: ClauseRef, contract_digest_value: str,
                  reason: str, retired_by: str, impact: RetirementImpact, force: bool = False) -> ClauseLifecycleLedger:
    """Retire one clause version; frozen tasks and publications keep their meaning."""
    if not reason.strip() or not retired_by.strip():
        raise ValueError("Retirement requires an identified actor and a substantive reason")
    existing = ledger.retirement(clause.key())
    if existing and existing.contract_digest == contract_digest_value:
        raise ValueError("This clause version is already retired")
    if impact.active_tasks and not force:
        raise ValueError("Retirement would strand running extraction tasks; stop or finish them first")
    record = ClauseRetirement(retirement_id=f"retirement-{uuid4().hex}", clause=clause,
                              contract_digest=contract_digest_value, reason=reason, retired_by=retired_by,
                              retired_at=datetime.now(timezone.utc), impact=impact, forced=force)
    return ledger.append_retirement(record)


# --- version difference and dependencies -----------------------------------

DIFF_FIELDS = ("canonical_unit_id", "unit_dimension", "value_role", "aggregation", "comparison_mode",
               "identity_axes", "comparison_axes", "required_dimensions", "comparison_dimensions",
               "fixed_dimensions", "exclusions", "positive_definition", "interpretation_guidance",
               "distinguish_from", "comparison_requirements", "normalization", "value_type")


def _flat_metric(metric: dict) -> dict:
    contract = dict(metric.get("measurement_contract") or {})
    flat = {field: metric.get(field) for field in ("value_type", "description", "labels") if field in metric}
    flat.update({field: contract.get(field) for field in CONTRACT_FIELDS if field in contract})
    for field in ("required_dimensions", "comparison_dimensions", "fixed_dimensions"):
        flat[field] = metric.get(field)
    return flat


def clause_version_diff(old: dict, new: dict, *, old_version: str | None = None,
                        new_version: str | None = None) -> dict:
    """Per-clause change set between two package versions, keyed by source datapoint."""
    added, removed, changed = [], [], {}
    for source_id in sorted(set(new) - set(old)):
        added.append({"source_datapoint_id": source_id, "label": (new[source_id].get("labels") or {}).get("zh")})
    for source_id in sorted(set(old) - set(new)):
        removed.append({"source_datapoint_id": source_id, "label": (old[source_id].get("labels") or {}).get("zh")})
    for source_id in sorted(set(old) & set(new)):
        before, after = _flat_metric(old[source_id]), _flat_metric(new[source_id])
        delta = {field: {"before": before.get(field), "after": after.get(field)}
                 for field in DIFF_FIELDS if before.get(field) != after.get(field)}
        if delta:
            changed[source_id] = delta
    return {"from_version": old_version, "to_version": new_version, "added": added, "removed": removed,
            "changed": changed, "change_count": len(added) + len(removed) + len(changed)}


def dependent_clauses(*, target_source_datapoint_id: str, metrics: list[dict], relations: list[dict],
                      extensions: list[dict] | None = None) -> list[dict]:
    """Clauses whose meaning or calculation depends on the given original clause."""
    by_metric_id = {metric.get("metric_id"): metric.get("source_datapoint_id") for metric in metrics}
    dependents: list[dict] = []
    for relation in relations:
        sources = {by_metric_id.get(item, item) for item in relation.get("source_metric_ids") or []}
        targets = {by_metric_id.get(item, item) for item in
                   (relation.get("target_metric_ids") or relation.get("member_metric_ids") or [])}
        if target_source_datapoint_id in sources and targets:
            dependents.append({"direction": "uses_this_clause", "relation_type": relation.get("relation_type"),
                               "clauses": sorted(targets), "description": relation.get("description")})
        if target_source_datapoint_id in targets and sources:
            dependents.append({"direction": "this_clause_uses", "relation_type": relation.get("relation_type"),
                               "clauses": sorted(sources), "description": relation.get("description")})
    for definition in extensions or []:
        body = definition.get("definition") or definition
        links = {link.get("source_datapoint_id") for link in body.get("original_clause_links") or []}
        operands = {operand.get("source_datapoint_id") for rule in body.get("derivation_rules") or []
                    for operand in rule.get("operands") or []}
        if target_source_datapoint_id in links or target_source_datapoint_id in operands:
            dependents.append({"direction": "uses_this_clause", "relation_type": "project_extension",
                               "clauses": [body.get("extension_id")],
                               "description": body.get("labels", {}).get("zh"),
                               "calculation_operand": target_source_datapoint_id in operands})
    return dependents


# --- ledger persistence ----------------------------------------------------

def load_ledger(path: Path) -> ClauseLifecycleLedger:
    path = Path(path)
    if not path.exists():
        return ClauseLifecycleLedger()
    return ClauseLifecycleLedger.model_validate_json(path.read_bytes())


def _write_ledger(path: Path, ledger: ClauseLifecycleLedger) -> None:
    payload = pretty_json_bytes(ledger.model_dump(mode="json"))
    fd, name = tempfile.mkstemp(prefix=".ledger-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_ledger(path: Path, ledger: ClauseLifecycleLedger) -> None:
    """Atomic replacement under an exclusive lock; records are only ever appended."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".ledger.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _write_ledger(path, ledger)


def record_acceptance(path: Path, acceptance: ClauseAcceptance) -> ClauseLifecycleLedger:
    """Refuse a second acceptance with a different verdict for the same run and contract."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".ledger.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = load_ledger(path)
        for existing in ledger.acceptances:
            if (existing.clause.key(), existing.contract_digest, existing.trial.run_ref) == (
                    acceptance.clause.key(), acceptance.contract_digest, acceptance.trial.run_ref):
                if existing.outcome != acceptance.outcome:
                    raise PackageIntegrityError(
                        "A recorded acceptance cannot be overwritten by a different verdict; run a new trial")
                return ledger
        ledger = ledger.append_acceptance(acceptance)
        _write_ledger(path, ledger)
        return ledger


def retire_and_save(path: Path, *, clause: ClauseRef, contract_digest_value: str, reason: str,
                    retired_by: str, impact: RetirementImpact, force: bool = False) -> ClauseLifecycleLedger:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".ledger.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = retire_clause(load_ledger(path), clause=clause, contract_digest_value=contract_digest_value,
                               reason=reason, retired_by=retired_by, impact=impact, force=force)
        _write_ledger(path, ledger)
        return ledger


def ledger_view(ledger: ClauseLifecycleLedger, *, states: list[dict] | None = None) -> dict:
    return {"schema_version": ledger.schema_version,
            "acceptance_count": len(ledger.acceptances), "retirement_count": len(ledger.retirements),
            "acceptances": [row.model_dump(mode="json") for row in ledger.acceptances],
            "retirements": [row.model_dump(mode="json") for row in ledger.retirements],
            "states": states or []}
