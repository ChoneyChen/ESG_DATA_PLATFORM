"""Record clause acceptance from frozen real result bundles.

Every verdict here is computed from a real extraction run, not declared. The
script only chooses which run proves which clause; it never supplies a value.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("ExtractESG/standards/src", "ExtractESG/extraction/src", "ExtractESG/contracts/src"):
    sys.path.insert(0, str(ROOT / package))

from esg_standard_packages.clause_lifecycle import (  # noqa: E402
    ClauseRef, evaluate_run, ledger_view, load_ledger, original_contract_digest, record_acceptance,
)
from esg_targeted.standards.catalog import StandardPackageCatalog  # noqa: E402

STANDARD_ROOT = ROOT / "ExtractESG/standards"


def bundle_requests(root: Path) -> list[dict]:
    """Newest bundle per report and package; earlier duplicates prove nothing new."""
    newest: dict[tuple[str, str, str], dict] = {}
    for path in sorted(root.glob("*/exports/extraction-result.json")):
        bundle = path.parent.parent
        request_path = bundle / "input/request.json"
        if not request_path.is_file():
            continue
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            result = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if request.get("extension_selections"):
            continue
        document_id = ((result.get("summary") or {}).get("document") or {}).get("document_id") or bundle.name
        key = (document_id, request.get("package_id"), request.get("package_version"))
        candidate = {"bundle": bundle, "request": request, "result": result, "document_id": document_id}
        previous = newest.get(key)
        if previous is None or bundle.name > previous["bundle"].name:
            newest[key] = candidate
    return sorted(newest.values(), key=lambda row: row["bundle"].name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", default=str(ROOT / "ExtractESG/targeted_table_extract/targeted_extract_output"))
    parser.add_argument("--ledger", default=str(STANDARD_ROOT / "lifecycle/ledger.json"))
    parser.add_argument("--actor", default="local-operator")
    parser.add_argument("--reason", default="真实报告试提取验收：冻结成果包自动核验")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    catalog = StandardPackageCatalog(STANDARD_ROOT / "releases")
    ledger_path = Path(args.ledger)
    runs = bundle_requests(Path(args.bundles))
    print(f"runs={len(runs)} ledger={ledger_path}")

    recorded, skipped, failures = 0, defaultdict(int), []
    per_clause: dict[str, dict] = {}
    for run in runs:
        request, result = run["request"], run["result"]
        package_id, package_version = request.get("package_id"), request.get("package_version")
        if not package_id or not package_version:
            skipped["no_package_identity"] += 1
            continue
        try:
            package = catalog.load(package_id, package_version)
        except (FileNotFoundError, ValueError) as error:
            failures.append({"bundle": run["bundle"].name, "reason": str(error)})
            continue
        by_metric = {metric.metric_id: metric for metric in package.metrics}
        tasks = (result.get("records") or {}).get("reporting_tasks") or []
        for metric_id in sorted({task.get("metric_id") for task in tasks if task.get("metric_id") in by_metric}):
            metric = by_metric[metric_id]
            body = metric.model_dump(mode="json")
            body["package_id"] = package_id
            clause = ClauseRef(kind="original", package_id=package_id, package_version=package_version,
                               source_datapoint_id=metric.source_datapoint_id)
            acceptance = evaluate_run(result, clause=clause, metric_ids=[metric_id],
                                      contract_digest_value=original_contract_digest(body),
                                      run_ref=run["bundle"].name, recorded_by=args.actor, reason=args.reason,
                                      report_label=((result.get("summary") or {}).get("document") or {}).get("document_label"))
            per_clause[clause.key()] = {"outcome": acceptance.outcome,
                                        "checks": {check.check: check.outcome for check in acceptance.checks},
                                        "report": acceptance.trial.report_label}
            if not args.dry_run:
                record_acceptance(ledger_path, acceptance)
            recorded += 1

    accepted = sum(1 for row in per_clause.values() if row["outcome"] == "accepted")
    rejected = len(per_clause) - accepted
    print(f"recorded={recorded} clauses={len(per_clause)} accepted={accepted} rejected={rejected}")
    for key, row in sorted(per_clause.items()):
        if row["outcome"] == "rejected":
            failed = [name for name, outcome in row["checks"].items() if outcome == "fail"]
            print(f"  rejected {key} {failed} {row['report']}")
    for failure in failures:
        print(f"  unreadable {failure['bundle']}: {failure['reason']}")
    if not args.dry_run:
        ledger = load_ledger(ledger_path)
        print(f"ledger acceptances={len(ledger.acceptances)} retirements={len(ledger.retirements)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
