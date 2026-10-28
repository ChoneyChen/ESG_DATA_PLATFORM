"""Clause lifecycle service over the real catalog; all runs are fixtures."""
import json
from pathlib import Path

import pytest

from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.clause_lifecycle_service import ClauseLifecycleService

EXTRACT = Path(__file__).resolve().parents[3]
STANDARDS = EXTRACT / "standards"
DIST = STANDARDS / "releases"
PACKAGE = "esrs.2023-set1.e1-5"


def service(tmp_path: Path | None = None) -> ClauseLifecycleService:
    catalog = StandardPackageCatalog(DIST)
    if tmp_path is None:
        return ClauseLifecycleService(catalog)
    svc = ClauseLifecycleService(catalog)
    svc.ledger_path = tmp_path / "ledger.json"
    return svc


def bundle(tmp_path: Path, *, metric_id: str, reported: bool = True) -> Path:
    root = tmp_path / "results" / "tx-fixture"
    (root / "exports").mkdir(parents=True)
    observations = [{"observation_id": "obs-1", "task_id": "task-1", "value_raw": "1234.5",
                     "value_numeric": "1234.5", "unit_raw": "兆瓦时", "reporting_year": 2025,
                     "reporting_boundary": "group", "evidence_set_id": "evset-1"}] if reported else []
    evidence = [{"evidence_id": "evidence-1", "evidence_set_id": "evset-1",
                 "ir_object_id": "cell-p0010-t0001-r0004-c0003", "pdf_page_index": 9}] if reported else []
    (root / "exports/extraction-result.json").write_text(json.dumps({
        "records": {"reporting_tasks": [{"task_id": "task-1", "metric_id": metric_id, "task_status": "completed",
                                         "found_status": "reported" if reported else "not_found",
                                         "ir_run_id": "ir-1", "document_id": "doc-1"}],
                    "quantitative_observations": observations, "evidence_references": evidence},
        "source_facts": {"gaps": [] if reported else [{"gap_id": "g", "kind": "not_covered",
                                                       "metric_ids": [metric_id]}]},
        "summary": {"document": {"document_id": "doc-1", "document_label": "测试报告"}},
    }, ensure_ascii=False), encoding="utf-8")
    return root


def version_of(svc: ClauseLifecycleService) -> str:
    return next(package.package_version for package in svc.catalog.list() if package.package_id == PACKAGE)


def test_unmeasured_clauses_are_not_publishable(tmp_path: Path):
    view = service(tmp_path).view()
    assert view["acceptance_count"] == 0
    assert view["states"] and all(row["state"] == "compiled" for row in view["states"])
    assert not any(row["publishable"] for row in view["states"])
    assert all("no_real_acceptance" in row["reasons"] for row in view["states"])


def test_real_run_records_an_acceptance_but_no_clause_is_publishable_without_approval(tmp_path: Path):
    svc = service(tmp_path)
    version = version_of(svc)
    root = bundle(tmp_path, metric_id="esrs.2023-set1.e1-5.dp01")
    result = svc.record(bundle_path=str(root), allowed_roots=[tmp_path / "results"], package_id=PACKAGE,
                        package_version=version, source_datapoint_ids=["E1-5_01"],
                        recorded_by="operator", reason="真实报告试提取")
    assert result["acceptance"]["outcome"] == "accepted"
    assert result["state"]["state"] == "accepted" and not result["state"]["publishable"]
    assert result["state"]["reasons"] == ["business_approval_missing"]
    rows = svc.package_lifecycle(PACKAGE, version)
    accepted = [row for row in rows if row["state"] == "accepted"]
    assert [row["label"] for row in accepted] == ["E1-5_01"]


def test_uncovered_pages_reject_an_otherwise_clean_run(tmp_path: Path):
    svc = service(tmp_path)
    version = version_of(svc)
    root = bundle(tmp_path, metric_id="esrs.2023-set1.e1-5.dp02", reported=False)
    result = svc.record(bundle_path=str(root), allowed_roots=[tmp_path / "results"], package_id=PACKAGE,
                        package_version=version, source_datapoint_ids=["E1-5_02"],
                        recorded_by="operator", reason="真实报告试提取")
    assert result["acceptance"]["outcome"] == "rejected"
    assert result["state"]["state"] == "trialed"


def test_bundle_must_stay_inside_the_result_root(tmp_path: Path):
    svc = service(tmp_path)
    version = version_of(svc)
    outside = bundle(tmp_path, metric_id="esrs.2023-set1.e1-5.dp01")
    with pytest.raises(ValueError, match="outside"):
        svc.record(bundle_path=str(outside), allowed_roots=[tmp_path / "allowed"], package_id=PACKAGE,
                   package_version=version, source_datapoint_ids=["E1-5_01"],
                   recorded_by="operator", reason="真实报告试提取")


def test_unknown_clause_is_refused(tmp_path: Path):
    svc = service(tmp_path)
    version = version_of(svc)
    root = bundle(tmp_path, metric_id="esrs.2023-set1.e1-5.dp01")
    with pytest.raises(ValueError, match="Unknown original clauses"):
        svc.record(bundle_path=str(root), allowed_roots=[tmp_path / "results"], package_id=PACKAGE,
                   package_version=version, source_datapoint_ids=["E9-9_99"],
                   recorded_by="operator", reason="真实报告试提取")


def test_retirement_is_recorded_and_blocks_publication(tmp_path: Path):
    svc = service(tmp_path)
    version = version_of(svc)
    result = svc.retire(package_id=PACKAGE, package_version=version, source_datapoint_id="E1-5_01",
                        reason="被新版本条款替代", retired_by="operator", published_observations=2)
    assert result["retirement"]["impact"]["published_observations"] == 2
    assert result["state"]["state"] == "retired" and not result["state"]["publishable"]


def test_version_diff_and_dependents_read_real_packages(tmp_path: Path):
    svc = service(tmp_path)
    newer = version_of(svc)
    older = "2.1.1"
    diff = svc.diff(PACKAGE, older, newer)
    assert {"from_version", "to_version", "added", "removed", "changed"} <= set(diff)
    assert diff["change_count"] == len(diff["added"]) + len(diff["removed"]) + len(diff["changed"])
    dependents = svc.dependents(PACKAGE, newer, "E1-5_03")
    assert dependents["source_datapoint_id"] == "E1-5_03"
    assert isinstance(dependents["dependents"], list)
