from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


baseline = module("phase0_baseline")
comparison = module("phase0_model_comparison")


def result(conclusions):
    by_code = {}
    for conclusion in conclusions:
        current = by_code.setdefault(conclusion["code"], conclusion)
        if current is not conclusion:
            current.setdefault("members", []).extend(conclusion.get("members") or [])
            current.setdefault("candidates", []).extend(conclusion.get("candidates") or [])
    return {"conclusions": list(by_code.values())}


def member(code, value, *, unit="吨", year=2025, pollutant=None, boundary="group", status="accepted"):
    return {"code": code, "status": "found", "members": [{
        "member": pollutant, "status": status, "value_raw": value,
        "source": {"value_raw": value, "unit_raw": unit, "period_year": year, "entity_basis": boundary,
                   "page_number": 90, "row_id": "r2", "attributes": {"boundary_label": boundary}},
        "agreeing_sources": [], "conflicting_sources": [], "related_subset_sources": [], "component_sources": [],
    }]}


def test_legacy_scoring_uses_atomic_values_and_does_not_hide_extra_dimensions():
    gold = {"E1-6_07": "100", "E2-4_02": {"NOx": "2.5"}, "E2-5_02": None}
    actual = result([member("E1-6_07", "100"), member("E2-4_02", "2.5", pollutant="NOx"),
                     member("E2-4_02", "3.5", pollutant="CO"), {"code": "E2-5_02", "status": "not_found_in_selected_sources"}])
    rows = list(baseline.legacy_rows(gold, actual, "sample"))
    assert [row["outcome"] for row in rows] == ["correct", "correct", "correct"]
    metrics = baseline.summarize(rows)["metrics"]
    assert metrics["legacy_expected_value_recall"] == {"correct": 2, "total": 2}
    assert metrics["legacy_negative_specificity"] == {"correct": 1, "total": 1}


def test_legacy_scoring_does_not_merge_two_pollutant_members():
    gold = {"E2-4_02": {"NOx": "2.5"}}
    rows = list(baseline.legacy_rows(gold, result([member("E2-4_02", "2.5", pollutant="NOx"),
                                                member("E2-4_02", "3.5", pollutant="CO")]), "sample"))
    assert rows[0]["outcome"] == "correct"
    # Extra members are not silently discarded: their coverage is unlabelled,
    # not counted as a false positive against a different expected member.


def test_legacy_scoring_distinguishes_numeric_mismatch_from_absence():
    gold = {"E1-6_07": "100", "E2-4_09": None, "E2-5_02": "present"}
    actual = result([member("E1-6_07", "1"), {"code": "E2-4_09", "status": "found"},
                     {"code": "E2-5_02", "status": "not_found_in_selected_sources"}])
    rows = list(baseline.legacy_rows(gold, actual, "sample"))
    assert [row["outcome"] for row in rows] == ["wrong", "false_positive", "missed"]


def test_full_field_gold_scores_boundary_and_unit_independently():
    fact = {"gold_fact_id": "source-1", "source_concept": "pollution.voc.air.refining", "column_year": "2025",
            "allowed_standard_datapoint_ids": ["E2-4_02"], "forbidden_standard_datapoint_ids": [],
            "raw_value": "0.40", "raw_unit": "万吨", "report_boundary": "公司全系统",
            "measurement_boundary": "company_report_scope",
            "evidence": {"pdf_page_number": 90}}
    rows = list(baseline.full_field_rows({"facts": [fact]}, result([member("E2-4_02", "0.40", unit="万吨", pollutant="VOCs", boundary="炼化")])))
    assert rows[0]["outcome"] == "matched"
    assert rows[0]["fields"]["dimension"]["correct"] is True
    assert rows[0]["fields"]["boundary"]["correct"] is True
    assert rows[0]["fields"]["subject"]["correct"] is None


def test_full_field_negative_requires_an_actual_rejection():
    fact = {"gold_fact_id": "negative-1", "source_concept": "ghg.scope1-plus-scope2.combined", "column_year": "2025",
            "allowed_standard_datapoint_ids": [], "forbidden_standard_datapoint_ids": ["E1-6_07"],
            "raw_value": "1.79", "raw_unit": "亿吨二氧化碳当量", "report_boundary": "公司全系统",
            "evidence": {"pdf_page_number": 90}}
    assert list(baseline.full_field_rows({"facts": [fact]}, result([])))[0]["outcome"] == "not_covered"
    rejected = member("E1-6_07", "1.79", unit="亿吨二氧化碳当量", status="rejected")
    rejected["members"] = []
    rejected["candidates"] = [rejected.pop("source", None) or {
        "status": "rejected", "value_raw": "1.79", "unit_raw": "亿吨二氧化碳当量", "page_number": 90}]
    assert list(baseline.full_field_rows({"facts": [fact]}, result([rejected])))[0]["outcome"] == "rejected"


def test_model_comparison_scores_real_source_shape_and_negative_coverage():
    case = {"case_id": "one", "facts": [{"code": "E1-6_07", "row": "r2", "member": None,
                                       "value": "19104", "unit": "tonnes CO2 e", "period": "2025", "boundary": "group"}],
            "negative_rows": [{"code": "E1-6_07", "row": "r28", "reason": "combined_scopes"}]}
    actual = result([member("E1-6_07", "19,104", unit="tonnes CO2 e")])
    scored = comparison.score(case, actual)
    assert scored[0]["recall"] is True
    assert scored[0]["boundary"] is True
    assert scored[1]["outcome"] == "not_covered"
    assert comparison.metrics(scored)["negative_rejection"] == {"correct": 0, "total": 1}


def test_model_fixture_has_stable_cell_targets_and_explicit_negatives():
    gold = json.loads((ROOT / "ExtractESG/tests/fixtures/business/phase0-model-comparison-gold.v1.json").read_text())
    assert len(gold["cases"]) == 3
    assert sum(len(case["facts"]) for case in gold["cases"]) == 16
    assert sum(len(case["negative_rows"]) for case in gold["cases"]) == 9
    for case in gold["cases"]:
        assert case["ir_run_id"] and case["table_id"] and case["pdf_page"]
        assert {fact["code"] for fact in case["facts"]}
