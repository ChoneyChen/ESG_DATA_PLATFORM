"""Clause lifecycle: acceptance derived from real runs, and its publication gate."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from esg_standard_packages.clause_lifecycle import (
    ClauseAcceptance, ClauseLifecycleLedger, ClauseRef, RetirementImpact, TrialReference,
    clause_version_diff, dependent_clauses, evaluate_run, lifecycle_state, load_ledger,
    original_contract_digest, record_acceptance, retire_and_save, retire_clause,
)
from esg_standard_packages.errors import PackageIntegrityError

METRIC = "esrs.2023-set1.e1-5.dp09"
CONTRACT = "a" * 64


def clause(**overrides):
    body = dict(kind="original", package_id="esrs.2023-set1.e1-5", package_version="2.3.0",
                source_datapoint_id="E1-5_09")
    body.update(overrides)
    return ClauseRef(**body)


def bundle(*, task_status="completed", found="reported", observations=None, gaps=None,
           evidence=None, metric_id=METRIC):
    observations = observations if observations is not None else [{
        "observation_id": "obs-1", "task_id": "task-1", "value_raw": "1,234.50", "value_numeric": "1234.5",
        "unit_raw": "兆瓦时", "reporting_year": 2025, "reporting_boundary": "group", "evidence_set_id": "evset-1"}]
    evidence = evidence if evidence is not None else [{
        "evidence_id": "evidence-1", "evidence_set_id": "evset-1", "ir_object_id": "cell-p0010-t0001-r0004-c0003",
        "pdf_page_index": 9}]
    return {
        "records": {
            "reporting_tasks": [{"task_id": "task-1", "metric_id": metric_id, "task_status": task_status,
                                 "found_status": found, "ir_run_id": "ir-20261028T000000Z-abcdef123456",
                                 "document_id": "doc-sha256-abc"}],
            "quantitative_observations": observations,
            "evidence_references": evidence,
        },
        "source_facts": {"gaps": gaps or []},
        "summary": {"document": {"document_id": "doc-sha256-abc", "document_label": "测试报告 2025"}},
    }


def accept(result=None, **overrides):
    body = dict(clause=clause(), metric_ids=[METRIC], contract_digest_value=CONTRACT,
                run_ref="tx-1", recorded_by="operator", reason="真实报告试提取验收")
    body.update(overrides)
    return evaluate_run(result if result is not None else bundle(), **body)


def test_clause_identity_is_explicit():
    assert clause().key() == "original:esrs.2023-set1.e1-5@2.3.0:E1-5_09"
    assert ClauseRef(kind="extension", extension_id="esrs.2023-set1.e1-6.extension.scope12",
                     definition_version="1.0.0").key() == \
        "extension:esrs.2023-set1.e1-6.extension.scope12@1.0.0"
    with pytest.raises(ValidationError):
        ClauseRef(kind="original", package_id="esrs.2023-set1.e1-5", package_version="2.3.0")
    with pytest.raises(ValidationError):
        ClauseRef(kind="extension", extension_id="esrs.2023-set1.e1-6.extension.scope12",
                  definition_version="1.0.0", source_datapoint_id="E1-5_09")


def test_bound_fact_from_a_real_run_is_accepted():
    record = accept()
    assert record.outcome == "accepted"
    checks = {check.check: check.outcome for check in record.checks}
    assert checks["task_executed"] == "pass" and checks["facts_bound"] == "pass"
    assert checks["evidence_anchored"] == "pass" and checks["unit_and_period"] == "pass"
    assert checks["gold_match"] == "not_applicable"


def test_missing_task_cannot_be_accepted():
    record = accept(result=bundle(metric_id="esrs.2023-set1.e1-5.dp01"))
    assert record.outcome == "rejected"
    assert {check.check: check.outcome for check in record.checks}["task_executed"] == "fail"


def test_uncovered_pages_are_not_evidence_of_absence():
    record = accept(result=bundle(found="not_found", observations=[], evidence=[], gaps=[{
        "gap_id": "gap-1", "kind": "not_covered", "metric_ids": [METRIC]}]))
    assert record.outcome == "rejected"
    assert "not_covered" in {check.check: check for check in record.checks}["facts_bound"].detail


def test_confirmed_non_disclosure_over_covered_objects_is_accepted():
    record = accept(result=bundle(found="not_found", observations=[], evidence=[], gaps=[{
        "gap_id": "gap-1", "kind": "confirmed_not_disclosed", "metric_ids": [METRIC]}]))
    assert record.outcome == "accepted"


def test_fact_without_a_physical_anchor_is_rejected():
    record = accept(result=bundle(evidence=[{"evidence_id": "e", "evidence_set_id": "evset-1",
                                             "ir_object_id": None, "pdf_page_index": None}]))
    assert record.outcome == "rejected"
    assert {check.check: check.outcome for check in record.checks}["evidence_anchored"] == "fail"


def test_gold_expectation_mismatch_is_rejected():
    record = accept(gold={"present": True, "value_raw": "9,999"})
    assert record.outcome == "rejected"
    matching = accept(gold={"present": True, "value_raw": "1234.50"})
    assert matching.outcome == "accepted"
    absent = accept(gold={"present": False})
    assert absent.outcome == "rejected"


def test_acceptance_cannot_be_declared_without_passing_checks():
    with pytest.raises(ValidationError):
        ClauseAcceptance(acceptance_id="a-1", clause=clause(), contract_digest=CONTRACT,
                         trial=TrialReference(run_ref="tx-1", document_id="doc-1"),
                         checks=[{"check": "task_executed", "outcome": "pass", "detail": "ok"}],
                         outcome="accepted", computed_at=datetime.now(timezone.utc),
                         recorded_by="operator", reason="x")


def test_state_requires_both_acceptance_and_business_approval():
    ledger = ClauseLifecycleLedger().append_acceptance(accept())
    none_yet = lifecycle_state(clause=clause(), contract_digest_value=CONTRACT,
                               ledger=ClauseLifecycleLedger(), business_approved=True)
    assert none_yet["state"] == "compiled" and not none_yet["publishable"]
    assert "no_real_acceptance" in none_yet["reasons"]

    unapproved = lifecycle_state(clause=clause(), contract_digest_value=CONTRACT, ledger=ledger)
    assert unapproved["state"] == "accepted" and not unapproved["publishable"]
    assert "business_approval_missing" in unapproved["reasons"]

    approved = lifecycle_state(clause=clause(), contract_digest_value=CONTRACT, ledger=ledger,
                               business_approved=True)
    assert approved["state"] == "publishable" and approved["publishable"]


def test_editing_the_clause_invalidates_its_acceptance():
    ledger = ClauseLifecycleLedger().append_acceptance(accept())
    stale = lifecycle_state(clause=clause(), contract_digest_value="b" * 64, ledger=ledger,
                            business_approved=True)
    assert stale["state"] == "compiled" and not stale["publishable"]
    assert stale["acceptance_stale"] and "contract_changed_since_acceptance" in stale["reasons"]


def test_rejected_acceptance_blocks_publication():
    rejected = accept(result=bundle(task_status="failed"))
    ledger = ClauseLifecycleLedger().append_acceptance(rejected)
    state = lifecycle_state(clause=clause(), contract_digest_value=CONTRACT, ledger=ledger,
                            business_approved=True)
    assert state["state"] == "trialed" and not state["publishable"]
    assert any(reason.startswith("acceptance_failed:") for reason in state["reasons"])


def test_retirement_keeps_history_and_blocks_running_tasks():
    ledger = ClauseLifecycleLedger()
    impact = RetirementImpact(published_observations=3, active_tasks=2)
    with pytest.raises(ValueError):
        retire_clause(ledger, clause=clause(), contract_digest_value=CONTRACT, reason="条款被替代",
                      retired_by="operator", impact=impact)
    retired = retire_clause(ledger, clause=clause(), contract_digest_value=CONTRACT, reason="条款被替代",
                            retired_by="operator", impact=impact, force=True)
    assert retired.retirement(clause().key()).impact.published_observations == 3
    state = lifecycle_state(clause=clause(), contract_digest_value=CONTRACT, ledger=retired,
                            business_approved=True)
    assert state["state"] == "retired" and not state["publishable"]


def test_version_diff_reports_each_changed_field():
    old = {"E1-5_09": {"source_datapoint_id": "E1-5_09", "labels": {"zh": "能耗"},
                       "measurement_contract": {"exclusions": ["不含车辆燃料"], "canonical_unit_id": "u1"}}}
    new = {"E1-5_09": {"source_datapoint_id": "E1-5_09", "labels": {"zh": "能耗"},
                       "measurement_contract": {"exclusions": ["不含车辆燃料", "不含原料用能"], "canonical_unit_id": "u1"}},
           "E1-5_10": {"source_datapoint_id": "E1-5_10", "labels": {"zh": "强度"}}}
    diff = clause_version_diff(old, new, old_version="2.2.0", new_version="2.3.0")
    assert [row["source_datapoint_id"] for row in diff["added"]] == ["E1-5_10"]
    assert diff["removed"] == []
    assert "exclusions" in diff["changed"]["E1-5_09"]
    assert diff["change_count"] == 2


def test_dependents_cover_relations_and_extensions():
    metrics = [{"metric_id": "m-total", "source_datapoint_id": "E1-5_01"},
               {"metric_id": "m-coal", "source_datapoint_id": "E1-5_03"}]
    relations = [{"relation_type": "decomposes", "source_metric_ids": ["m-coal"],
                  "target_metric_ids": ["m-total"], "description": "能耗总量由煤构成"}]
    extensions = [{"definition": {"extension_id": "esrs.2023-set1.e1-5.extension.fossil",
                                  "labels": {"zh": "化石能耗"}, "original_clause_links": [
                                      {"source_datapoint_id": "E1-5_03", "relationship": "aggregate_component"}],
                                  "derivation_rules": []}}]
    rows = dependent_clauses(target_source_datapoint_id="E1-5_03", metrics=metrics,
                             relations=relations, extensions=extensions)
    assert {row["relation_type"] for row in rows} == {"decomposes", "project_extension"}
    assert all(row["direction"] == "uses_this_clause" for row in rows)


def test_contract_digest_tracks_scientific_fields_only():
    base = {"source_datapoint_id": "E1-5_09", "package_id": "p1",
            "measurement_contract": {"exclusions": ["不含车辆"], "canonical_unit_id": "u1",
                                     "identity_axes": ["energy_source"]}}
    changed = {"source_datapoint_id": "E1-5_09", "package_id": "p1",
               "measurement_contract": {"exclusions": ["不含车辆", "不含原料"], "canonical_unit_id": "u1",
                                        "identity_axes": ["energy_source"]}}
    display = {"source_datapoint_id": "E1-5_09", "package_id": "p1", "display_unit": "万吨",
               "measurement_contract": {"exclusions": ["不含车辆"], "canonical_unit_id": "u1",
                                        "identity_axes": ["energy_source"]}}
    assert original_contract_digest(base) != original_contract_digest(changed)
    assert original_contract_digest(base) == original_contract_digest(display)


def test_ledger_is_append_only_for_a_run(tmp_path: Path):
    path = tmp_path / "ledger.json"
    first = accept()
    record_acceptance(path, first)
    assert load_ledger(path).acceptances[0].acceptance_id == first.acceptance_id
    record_acceptance(path, first)
    assert len(load_ledger(path).acceptances) == 1
    conflicting = first.model_copy(update={"acceptance_id": "acceptance-other", "outcome": "rejected",
                                           "checks": [*first.checks, {"check": "gold_match", "outcome": "fail",
                                                                      "detail": "later contradiction"}]})
    with pytest.raises(PackageIntegrityError):
        record_acceptance(path, conflicting)
    retire_and_save(path, clause=clause(), contract_digest_value=CONTRACT, reason="被替代",
                    retired_by="operator", impact=RetirementImpact())
    assert load_ledger(path).retirements[0].clause.key() == clause().key()
