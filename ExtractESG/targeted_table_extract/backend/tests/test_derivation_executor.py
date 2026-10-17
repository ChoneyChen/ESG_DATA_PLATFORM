from __future__ import annotations

import hashlib
import pytest

from esg_targeted.results.derivation import DerivationExecutor
from esg_targeted.results.validation import ResultContractValidator
from esg_targeted.standards.catalog import StandardPackageCatalog
from tests.helpers import standard_dist_root


def _fixture_records(package):
    source_metrics = [
        "esrs.2023-set1.e1-6.dp07",
        "esrs.2023-set1.e1-6.dp09",
        "esrs.2023-set1.e1-6.dp11",
    ]
    target_metric = "esrs.2023-set1.e1-6.dp12"
    tasks = []
    observations = []
    dimensions = []
    evidence = []
    values = ["100", "200", "300"]
    elements_by_metric = {
        metric_id: [
            item for item in package.elements if item.metric_id == metric_id
        ]
        for metric_id in [*source_metrics, target_metric]
    }
    for metric_id in [*source_metrics, target_metric]:
        suffix = metric_id.rsplit("dp", 1)[-1]
        tasks.append(
            {
                "task_id": f"task-{suffix}",
                "report_id": "report",
                "document_id": "document",
                "ir_run_id": "ir",
                "ir_revision": 1,
                "package_id": package.manifest.package_id,
                "package_version": package.manifest.package_version,
                "metric_id": metric_id,
                "materiality_status": "not_assessed",
                "applicability_status": "not_assessed",
                "found_status": "reported" if metric_id in source_metrics else "uncertain",
                "result_count": 1 if metric_id in source_metrics else 0,
                "evidence_count": 1 if metric_id in source_metrics else 0,
                "uncertainty_reason": None,
                "review_status": "pending",
                "task_status": "completed" if metric_id in source_metrics else "partial",
            }
        )
    for metric_id, value in zip(source_metrics, values):
        suffix = metric_id.rsplit("dp", 1)[-1]
        observation_id = f"obs-{suffix}"
        evidence_set_id = f"evset-{suffix}"
        observations.append(
            {
                "observation_id": observation_id,
                "task_id": f"task-{suffix}",
                "metric_id": metric_id,
                "value_origin": "reported",
                "value_raw": value,
                "comparator": "exact",
                "value_numeric": value,
                "value_lower": None,
                "value_upper": None,
                "unit_raw": "tCO2e",
                "unit_id": "extractesg.core.unit.tonne-co2e",
                "reporting_period_raw": "2024",
                "reporting_year": 2024,
                "period_start": None,
                "period_end": None,
                "reporting_boundary": "集团",
                "evidence_set_id": evidence_set_id,
                "confidence": None,
                "review_status": "pending",
            }
        )
        entity = next(
            item
            for item in elements_by_metric[metric_id]
            if item.element_code == "reporting_entity"
        )
        dimensions.append(
            {
                "dimension_value_id": f"dim-{suffix}",
                "parent_record_type": "quantitative_observation",
                "parent_record_id": observation_id,
                "metric_id": metric_id,
                "element_id": entity.element_id,
                "dimension_id": entity.binding.target,
                "value_raw": "紫金矿业",
                "value_code": None,
                "value_level": None,
                "parent_dimension_value_id": None,
                "sequence": 1,
                "evidence_set_id": evidence_set_id,
                "review_status": "pending",
            }
        )
        excerpt = f"{metric_id} {value} tCO2e"
        evidence.append(
            {
                "evidence_id": f"evidence-{suffix}",
                "evidence_set_id": evidence_set_id,
                "source_record_type": "quantitative_observation",
                "source_record_id": observation_id,
                "document_id": "document",
                "ir_run_id": "ir",
                "ir_revision": 1,
                "ir_object_type": "cell",
                "ir_object_id": f"cell-{suffix}",
                "pdf_page_index": 5,
                "printed_page_label": None,
                "bbox_x0": None,
                "bbox_y0": None,
                "bbox_x1": None,
                "bbox_y1": None,
                "evidence_excerpt": excerpt,
                "quote_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                "evidence_role": "primary",
                "review_status": "pending",
            }
        )
    return {
        "reporting_tasks": tasks,
        "quantitative_observations": observations,
        "qualitative_assertions": [],
        "attribute_values": [],
        "dimension_values": dimensions,
        "evidence_references": evidence,
    }


def _outcomes(records):
    return [
        {
            "metric_id": task["metric_id"],
            "status": "found" if task["result_count"] else "partial",
            "found_status": task["found_status"],
            "review_status": task["review_status"],
            "semantic_decision_status": "decided",
            "display_readiness_status": "ready" if task["result_count"] else "not_applicable",
            "contract_validation_status": "passed",
            "uncertainty_reason": None,
            "result_counts": {
                "reporting_task": 1,
                "quantitative_observation": task["result_count"],
                "qualitative_assertion": 0,
                "attribute_value": 0,
                "dimension_value": 0,
                "evidence_reference": task["evidence_count"],
            },
        }
        for task in records["reporting_tasks"]
    ]


def test_sum_derivation_requires_and_preserves_complete_identity():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.5.0"
    )
    records = _fixture_records(package)
    outcomes = _outcomes(records)

    report = DerivationExecutor().execute(
        records=records,
        outcomes=outcomes,
        package=package,
    )

    derived = [
        item
        for item in records["quantitative_observations"]
        if item["metric_id"].endswith("dp12")
    ]
    assert len(derived) == 1
    assert derived[0]["value_origin"] == "derived"
    assert derived[0]["value_numeric"] == "600"
    assert derived[0]["unit_raw"] == "tCO2e"
    target_dimensions = [
        item
        for item in records["dimension_values"]
        if item["parent_record_id"] == derived[0]["observation_id"]
    ]
    assert {
        (item["dimension_id"], item["value_raw"])
        for item in target_dimensions
    } >= {
        ("esrs.2023-set1.e1-6.dimension.reporting-entity", "紫金矿业"),
        ("esrs.2023-set1.e1-6.dimension.scope2-method", "location_based"),
    }
    assert derived[0]["observation_id"] in report["derived_observation_ids"]
    target_outcome = next(item for item in outcomes if item["metric_id"].endswith("dp12"))
    assert target_outcome["dependency_status"] == "ready"
    assert target_outcome["dependency_blocks"] == []
    ResultContractValidator(package.core).validate(records)


def test_derivation_does_not_infer_a_missing_reporting_boundary():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.5.0"
    )
    records = _fixture_records(package)
    records["quantitative_observations"][0]["reporting_boundary"] = None

    report = DerivationExecutor().execute(
        records=records,
        outcomes=_outcomes(records),
        package=package,
    )

    assert not [
        item
        for item in records["quantitative_observations"]
        if item["metric_id"].endswith("dp12")
    ]
    dp12 = next(
        item for item in report["rules"] if item["target_metric_id"].endswith("dp12")
    )
    assert "dependency_observations_incomplete" in dp12["skipped"]


def test_reported_target_has_priority_over_derivation():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.5.0"
    )
    records = _fixture_records(package)
    reported = dict(records["quantitative_observations"][0])
    reported.update(
        observation_id="obs-12-reported",
        task_id="task-12",
        metric_id="esrs.2023-set1.e1-6.dp12",
        value_raw="610",
        value_numeric="610",
        evidence_set_id="evset-12-reported",
    )
    records["quantitative_observations"].append(reported)
    source_entity = records["dimension_values"][0]
    target_entity = next(
        item
        for item in package.elements
        if item.metric_id == reported["metric_id"]
        and item.element_code == "reporting_entity"
    )
    records["dimension_values"].append(
        {
            **source_entity,
            "dimension_value_id": "dim-12-reported",
            "parent_record_id": reported["observation_id"],
            "metric_id": reported["metric_id"],
            "element_id": target_entity.element_id,
            "dimension_id": target_entity.binding.target,
            "evidence_set_id": reported["evidence_set_id"],
        }
    )

    report = DerivationExecutor().execute(
        records=records,
        outcomes=_outcomes(records),
        package=package,
    )

    target = [
        item
        for item in records["quantitative_observations"]
        if item["metric_id"].endswith("dp12")
    ]
    assert [(item["value_origin"], item["value_raw"]) for item in target] == [
        ("reported", "610")
    ]
    dp12 = next(
        item for item in report["rules"] if item["target_metric_id"].endswith("dp12")
    )
    assert dp12["skipped"][0]["reason"] == "reported_target_priority"


def test_legacy_e2_derivations_are_not_implicitly_activated():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    records = {
        "reporting_tasks": [],
        "quantitative_observations": [],
        "qualitative_assertions": [],
        "attribute_values": [],
        "dimension_values": [],
        "evidence_references": [],
    }

    report = DerivationExecutor().execute(
        records=records,
        outcomes=[],
        package=package,
    )

    assert report["derived_observation_ids"] == []
    assert all(
        item["skipped"] == ["package_derivation_execution_not_enabled"]
        for item in report["rules"]
    )


@pytest.mark.parametrize("case,code,stage", [
    ("missing_source", "dependency_observations_missing", "source"),
    ("mapping_unresolved", "dependency_semantics_unresolved", "mapping"),
    ("missing_boundary", "operand_identity_incomplete", "identity"),
    ("mismatched_period", "dependency_identity_mismatch", "identity"),
    ("unknown_unit", "operand_unit_unknown", "calculation"),
    ("incompatible_units", "operand_units_incompatible", "calculation"),
    ("conflicting_unit_id", "operand_unit_id_conflict", "calculation"),
    ("unknown_raw_scale", "operand_units_incompatible", "calculation"),
    ("invalid_numeric", "operand_numeric_value_invalid", "calculation"),
    ("missing_evidence", "operand_evidence_missing", "source"),
    ("duplicate_occurrence", "operand_occurrence_ambiguous", "identity"),
])
def test_dependency_blocks_explain_the_actual_failure_without_claiming_nondisclosure(case, code, stage):
    package = StandardPackageCatalog(standard_dist_root()).load("esrs.2023-set1.e1-6", "1.5.0")
    records = _fixture_records(package)
    # Keep anonymous source identity in these failure fixtures.
    for dimension in records["dimension_values"]:
        dimension["value_raw"] = "Example reporting entity"
    outcomes = _outcomes(records)
    source = records["quantitative_observations"][0]
    target = next(outcome for outcome in outcomes if outcome["metric_id"].endswith("dp12"))
    target.update(status="not_found", found_status="not_found_after_complete_search",
                  search_resolution_status="not_found_after_complete_search")
    if case == "missing_source":
        records["quantitative_observations"].remove(source)
        outcomes = [outcome for outcome in outcomes if outcome["metric_id"] != source["metric_id"]]
    elif case == "mapping_unresolved":
        outcomes[0]["semantic_decision_status"] = "unresolved"
    elif case == "missing_boundary":
        source["reporting_boundary"] = None
    elif case == "mismatched_period":
        source["reporting_period_raw"] = "2023"
        source["reporting_year"] = 2023
    elif case == "unknown_unit":
        source.update(unit_id=None, unit_raw=None)
    elif case == "incompatible_units":
        source.update(unit_id=None, unit_raw="kgCO2e")
    elif case == "conflicting_unit_id":
        source["unit_raw"] = "MtCO2e"
    elif case == "unknown_raw_scale":
        source["unit_raw"] = "亿吨"
    elif case == "invalid_numeric":
        source["value_numeric"] = "NaN"
    elif case == "missing_evidence":
        records["evidence_references"] = records["evidence_references"][1:]
    elif case == "duplicate_occurrence":
        duplicate = {**source, "observation_id": "anonymous-duplicate", "evidence_set_id": "duplicate-evidence-set"}
        records["quantitative_observations"].append(duplicate)
        records["dimension_values"].append({**records["dimension_values"][0], "dimension_value_id": "duplicate-dimension",
            "parent_record_id": duplicate["observation_id"]})
    report = DerivationExecutor().execute(records=records, outcomes=outcomes, package=package)
    rule = next(item for item in report["rules"] if item["target_metric_id"] == target["metric_id"])
    assert rule["derived_observation_ids"] == []
    assert rule["dependency_status"] == target["dependency_status"] == "blocked"
    block = next(block for block in target["dependency_blocks"] if block["code"] == code)
    assert block["stage"] == stage and block["reason"]
    assert target["status"] == "ambiguous" and target["found_status"] == "uncertain"
    assert target["search_resolution_status"] == "not_found_after_complete_search"
    if case == "missing_source":
        assert block["dependency_result_status"] == "not_executed"


def test_blocked_optional_derivation_preserves_an_available_direct_reported_fact():
    package = StandardPackageCatalog(standard_dist_root()).load("esrs.2023-set1.e1-6", "1.5.0")
    records = _fixture_records(package)
    target_task = next(task for task in records["reporting_tasks"] if task["metric_id"].endswith("dp12"))
    target_task.update(result_count=1, evidence_count=1, found_status="reported", task_status="completed")
    reported = {**records["quantitative_observations"][0], "observation_id": "anonymous-reported-target",
        "task_id": target_task["task_id"], "metric_id": target_task["metric_id"]}
    records["quantitative_observations"].append(reported)
    records["quantitative_observations"][0].update(unit_id=None, unit_raw=None)
    outcomes = _outcomes(records)
    target = next(outcome for outcome in outcomes if outcome["metric_id"] == target_task["metric_id"])
    DerivationExecutor().execute(records=records, outcomes=outcomes, package=package)
    assert target["dependency_status"] == "blocked"
    assert target["status"] == "found" and target["found_status"] == "reported"
    assert reported in records["quantitative_observations"]
    assert target_task["task_status"] == "completed"


def test_durable_dependency_overlay_is_idempotent_and_cannot_create_derived_core():
    from copy import deepcopy
    package = StandardPackageCatalog(standard_dist_root()).load("esrs.2023-set1.e1-6", "1.5.0")
    records = _fixture_records(package)
    checkpoint_records = deepcopy(records)
    checkpoint_outcomes = _outcomes(checkpoint_records)
    report = DerivationExecutor().execute(records=records, outcomes=_outcomes(records), package=package)
    assert report["derived_observation_ids"]
    before = deepcopy(checkpoint_records)
    DerivationExecutor.apply_dependency_state(records=checkpoint_records, outcomes=checkpoint_outcomes, report=report)
    first = deepcopy(checkpoint_outcomes)
    DerivationExecutor.apply_dependency_state(records=checkpoint_records, outcomes=checkpoint_outcomes, report=report)
    assert checkpoint_outcomes == first
    assert checkpoint_records == before
    assert all(item["value_origin"] == "reported" for item in checkpoint_records["quantitative_observations"])
    assert next(item for item in checkpoint_outcomes if item["metric_id"].endswith("dp12"))["dependency_status"] == "ready"
