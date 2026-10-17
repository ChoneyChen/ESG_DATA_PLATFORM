from __future__ import annotations

import json

import pytest

from esg_targeted.models.family_adjudication import (
    FamilyAdjudicationDecision,
    FamilyCaseDecision,
    FamilyModelRunResult,
    build_family_packet,
    parse_family_decision,
)
from esg_targeted.models.response_adapter import ModelResponseError
from esg_targeted.models.qiniu_vlm import QiniuVlmModel
from esg_targeted.results.family_resolution import FamilyConflictResolver
from esg_targeted.results.semantic_conflicts import SemanticConflictAuditor
from esg_targeted.standards.catalog import StandardPackageCatalog
from tests.helpers import standard_dist_root


def _records():
    location = "esrs.2023-set1.e1-6.dp09"
    market = "esrs.2023-set1.e1-6.dp10"
    observations = [
        {
            "observation_id": "obs-location",
            "task_id": "task-location",
            "metric_id": location,
            "value_raw": "100",
            "unit_raw": "tCO2e",
        },
        {
            "observation_id": "obs-market",
            "task_id": "task-market",
            "metric_id": market,
            "value_raw": "100",
            "unit_raw": "tCO2e",
        },
    ]
    evidence = [
        {
            "evidence_id": f"ev-{item['observation_id']}",
            "source_record_id": item["observation_id"],
            "evidence_role": "primary",
            "document_id": "doc",
            "ir_run_id": "ir",
            "ir_revision": 1,
            "pdf_page_index": 5,
            "ir_object_id": "cell-scope2",
            "evidence_excerpt": "Scope 2（位置法）100 tCO2e",
        }
        for item in observations
    ]
    tasks = [
        {
            "task_id": item["task_id"],
            "metric_id": item["metric_id"],
            "result_count": 1,
            "evidence_count": 1,
            "task_status": "completed",
            "found_status": "reported",
            "review_status": "pending",
            "uncertainty_reason": None,
        }
        for item in observations
    ]
    return {
        "reporting_tasks": tasks,
        "quantitative_observations": observations,
        "qualitative_assertions": [],
        "attribute_values": [],
        "dimension_values": [],
        "evidence_references": evidence,
    }


class LocationOnlyFamilyModel:
    provider_name = "fake_family"

    def readjudicate_family(self, packet, *, cancel_check=None):
        decision = FamilyAdjudicationDecision(
            packet_id=packet.packet_id,
            family_id=packet.family_id,
            cases=[
                FamilyCaseDecision(
                    case_id=case.case_id,
                    status="decided",
                    applicable_metric_ids=["esrs.2023-set1.e1-6.dp09"],
                    reason="The source row explicitly states location-based Scope 2.",
                )
                for case in packet.cases
            ],
        )
        return FamilyModelRunResult(
            decision=decision,
            raw_output=decision.model_dump_json(),
            cleaned_output=decision.model_dump_json(),
            provider=self.provider_name,
            model_id="fake",
        )


def test_family_parser_rejects_metric_outside_the_conflict_case():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.5.0"
    )
    records = _records()
    conflicts = SemanticConflictAuditor().audit(records, package)
    packet = build_family_packet(
        family_id="ghg-scope-method",
        conflicts=conflicts,
        records=records,
        package=package,
    )
    payload = {
        "packet_id": packet.packet_id,
        "family_id": packet.family_id,
        "cases": [
            {
                "case_id": packet.cases[0].case_id,
                "status": "decided",
                "applicable_metric_ids": ["esrs.2023-set1.e1-6.dp11"],
                "reason": "invented sibling",
            }
        ],
    }

    with pytest.raises(ModelResponseError, match="unsupplied metric"):
        parse_family_decision(json.dumps(payload), packet)


def test_joint_family_decision_removes_only_the_rejected_sibling_fact():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.5.0"
    )
    records = _records()
    conflicts = SemanticConflictAuditor().audit(records, package)
    outcomes = [
        {
            "metric_id": task["metric_id"],
            "status": "found",
            "found_status": "reported",
            "review_status": "pending",
            "semantic_decision_status": "decided",
            "display_readiness_status": "ready",
            "uncertainty_reason": None,
            "result_counts": {
                "reporting_task": 1,
                "quantitative_observation": 1,
                "qualitative_assertion": 0,
                "attribute_value": 0,
                "dimension_value": 0,
                "evidence_reference": 1,
            },
        }
        for task in records["reporting_tasks"]
    ]

    report = FamilyConflictResolver().resolve(
        conflicts=conflicts,
        records=records,
        outcomes=outcomes,
        package=package,
        model=LocationOnlyFamilyModel(),
    )

    assert report["attempted"] is True
    assert report["rejected_fact_ids"] == ["obs-market"]
    assert [
        item["observation_id"]
        for item in records["quantitative_observations"]
    ] == ["obs-location"]
    assert SemanticConflictAuditor().audit(records, package) == []
    market_task = next(
        item
        for item in records["reporting_tasks"]
        if item["metric_id"].endswith("dp10")
    )
    assert market_task["task_status"] == "partial"
    assert market_task["found_status"] == "uncertain"


def test_qiniu_family_adjudication_uses_one_strict_family_response():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.5.0"
    )
    records = _records()
    conflicts = SemanticConflictAuditor().audit(records, package)
    packet = build_family_packet(
        family_id="ghg-scope-method",
        conflicts=conflicts,
        records=records,
        package=package,
    )
    calls = []

    def requester(_url, payload, _headers, _timeout):
        calls.append(payload)
        decision = {
            "packet_id": packet.packet_id,
            "family_id": packet.family_id,
            "cases": [
                {
                    "case_id": packet.cases[0].case_id,
                    "status": "decided",
                    "applicable_metric_ids": ["esrs.2023-set1.e1-6.dp09"],
                    "reason": "Explicit location-based row label.",
                }
            ],
        }
        return {
            "model": "fake-vlm",
            "choices": [
                {
                    "message": {"content": json.dumps(decision)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }

    model = QiniuVlmModel(
        api_key="test",
        base_url="https://example.invalid/v1",
        model_id="fake-vlm",
        requester=requester,
    )

    result = model.readjudicate_family(packet)

    assert len(calls) == 1
    assert calls[0]["response_format"]["json_schema"]["name"] == (
        "targeted_family_adjudication"
    )
    assert result.decision.cases[0].applicable_metric_ids == [
        "esrs.2023-set1.e1-6.dp09"
    ]


def test_pairwise_conflicts_for_one_source_become_one_three_metric_case():
    package = StandardPackageCatalog(standard_dist_root()).load(
        "esrs.2023-set1.e1-6", "1.5.0"
    )
    records = _records()
    records["quantitative_observations"].append(
        {
            "observation_id": "obs-scope1",
            "task_id": "task-scope1",
            "metric_id": "esrs.2023-set1.e1-6.dp07",
            "value_raw": "100",
            "unit_raw": "tCO2e",
        }
    )
    records["evidence_references"].append(
        {
            **records["evidence_references"][0],
            "evidence_id": "ev-obs-scope1",
            "source_record_id": "obs-scope1",
        }
    )
    conflicts = SemanticConflictAuditor().audit(records, package)

    packet = build_family_packet(
        family_id="ghg-scope-method",
        conflicts=conflicts,
        records=records,
        package=package,
    )

    assert len(conflicts) == 3
    assert len(packet.cases) == 1
    assert {item.metric_id for item in packet.cases[0].facts} == {
        "esrs.2023-set1.e1-6.dp07",
        "esrs.2023-set1.e1-6.dp09",
        "esrs.2023-set1.e1-6.dp10",
    }
