"""Independent package limits with read-only report usage aggregation.

Shared source artifacts are not a shared execution allowance. Historical
report ledgers remain intact; proven call IDs carry their cost into their own
package when that package is explicitly executed again.
"""
from __future__ import annotations

from copy import deepcopy
import json
from esg_targeted.runtime_budget import ReportRuntimeBudget

POLICY_VERSION = "independent-standard-package-v1"


def _read(path):
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("calls"), dict):
        raise ValueError("Invalid model usage ledger; historical accounting must be inspected")
    return value


def _owned_call_ids(root):
    output = set()
    for path in (root / "agent" / "calls").glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("budget_call_id"), str):
            output.add(value["budget_call_id"])
    return output


def package_model_budget(artifacts, request, job_id):
    root = artifacts.job_dir(job_id)
    budget = ReportRuntimeBudget(root / "runtime-budget.json", request.call_budget.model_dump(), boundary="package")
    # Executing job only: report read APIs must not initialize or rewrite ledgers.
    with budget._ledger() as ledger:
        if ledger.get("scope_policy") != POLICY_VERSION:
            carried = []
            session = getattr(request, "report_session_id", None)
            if session:
                legacy = _read(artifacts.report_session_path(session) / "runtime-budget.json")
                if legacy:
                    for call_id in sorted(_owned_call_ids(root) & legacy["calls"].keys()):
                        ledger["calls"].setdefault(call_id, deepcopy(legacy["calls"][call_id]))
                        carried.append(call_id)
            ledger.update(scope_policy=POLICY_VERSION, job_id=job_id,
                package_id=request.package_id, legacy_carried_call_ids=carried)
    return budget


def report_model_usage(artifacts, session_id, projections):
    """Aggregate original call IDs exactly once without mutating usage files."""
    legacy = _read(artifacts.report_session_path(session_id) / "runtime-budget.json")
    combined = dict(legacy["calls"]) if legacy else {}
    packages, attributed = [], set()
    for projection in projections:
        root = artifacts._job_path(projection["job_id"])
        ledger = _read(root / "runtime-budget.json")
        if ledger:
            calls = ledger["calls"]
        else:
            ids = _owned_call_ids(root)
            calls = {key: value for key, value in (legacy or {}).get("calls", {}).items() if key in ids}
        attributed.update(calls)
        for key, value in calls.items():
            if key in combined and combined[key] != value:
                raise ValueError("Conflicting usage for the same model call; inspect accounting")
            combined[key] = value
        limits = (ledger or {}).get("limits") or projection.get("model_call_budget")
        request_path = root / "input/request.json"
        if limits is None and request_path.is_file():
            limits = json.loads(request_path.read_text(encoding="utf-8")).get("call_budget")
        packages.append({"job_id": projection["job_id"], "package_id": projection["package_id"],
            "scope": "standard_package", "limits": limits,
            "usage": ReportRuntimeBudget._usage({"calls": calls}),
            "accounting": ReportRuntimeBudget._accounting({"calls": calls}),
            "legacy_attributed": not bool(ledger) and bool(calls)})
    if not combined and not any(item["limits"] for item in packages):
        return None
    return {"scope": "report_summary", "policy": POLICY_VERSION, "enforces_report_limit": False,
        "usage": ReportRuntimeBudget._usage({"calls": combined}),
        "accounting": ReportRuntimeBudget._accounting({"calls": combined}), "packages": packages,
        "legacy_unattributed_usage": ReportRuntimeBudget._usage({"calls": {
            key: value for key, value in (legacy or {}).get("calls", {}).items() if key not in attributed}})}
