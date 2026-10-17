from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from esg_targeted.ids import stable_id
from esg_targeted.models.response_adapter import (
    ModelResponseError,
    decode_first_json_object,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FamilyMetricContract(_StrictModel):
    metric_id: str
    label: str | None = None
    description: str
    fixed_scope: list[dict[str, Any]] = Field(default_factory=list)


class FamilyConflictFact(_StrictModel):
    metric_id: str
    fact_id: str
    value_raw: str
    unit_raw: str | None = None
    evidence_excerpts: list[str] = Field(default_factory=list)


class FamilyConflictCase(_StrictModel):
    case_id: str
    source: dict[str, Any]
    facts: list[FamilyConflictFact] = Field(min_length=2)


class FamilyAdjudicationPacket(_StrictModel):
    packet_id: str
    family_id: str
    metrics: list[FamilyMetricContract] = Field(min_length=2)
    cases: list[FamilyConflictCase] = Field(min_length=1)


class FamilyCaseDecision(_StrictModel):
    case_id: str
    status: Literal["decided", "uncertain"]
    applicable_metric_ids: list[str] = Field(default_factory=list)
    reason: str


class FamilyAdjudicationDecision(_StrictModel):
    packet_id: str
    family_id: str
    cases: list[FamilyCaseDecision]

    @model_validator(mode="after")
    def unique_case_ids(self) -> "FamilyAdjudicationDecision":
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("family adjudication contains duplicate case_id values")
        return self


class FamilyModelRunResult(_StrictModel):
    decision: FamilyAdjudicationDecision
    raw_output: str
    cleaned_output: str
    provider: str | None = None
    model_id: str | None = None
    prompt_tokens: int | None = None
    generation_tokens: int | None = None
    finish_reason: str | None = None
    timings: dict[str, float] = Field(default_factory=dict)


def build_family_packet(
    *,
    family_id: str,
    conflicts: list[dict[str, Any]],
    records: dict[str, list[dict[str, Any]]],
    package,
) -> FamilyAdjudicationPacket:
    observations = {
        item["observation_id"]: item
        for item in records.get("quantitative_observations", [])
    }
    evidence_by_fact: dict[str, list[str]] = {}
    for item in records.get("evidence_references", []):
        fact_id = item.get("source_record_id")
        excerpt = str(item.get("evidence_excerpt") or "").strip()
        if fact_id and excerpt:
            evidence_by_fact.setdefault(fact_id, []).append(excerpt)

    metric_ids = sorted(
        {
            metric_id
            for conflict in conflicts
            for metric_id in conflict.get("metric_ids", [])
        }
    )
    metrics_by_id = {item.metric_id: item for item in package.metrics}
    fixed_by_metric: dict[str, list[dict[str, Any]]] = {}
    for metric_id in metric_ids:
        fixed_by_metric[metric_id] = [
            {
                "element_code": element.element_code,
                "value": element.value_contract.fixed_value,
            }
            for element in package.elements
            if element.metric_id == metric_id
            and element.value_contract.fixed_value is not None
        ]

    conflicts_by_source: dict[str, dict[str, Any]] = {}
    for conflict in conflicts:
        source = dict(conflict.get("source") or {})
        source_key = json.dumps(
            source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        grouped = conflicts_by_source.setdefault(
            source_key,
            {"source": source, "fact_ids": set()},
        )
        grouped["fact_ids"].update(conflict.get("fact_ids") or [])

    cases = []
    for grouped in conflicts_by_source.values():
        fact_ids = sorted(grouped["fact_ids"])
        facts = []
        for fact_id in fact_ids:
            observation = observations.get(fact_id)
            if observation is None:
                continue
            facts.append(
                FamilyConflictFact(
                    metric_id=observation["metric_id"],
                    fact_id=fact_id,
                    value_raw=str(observation.get("value_raw") or ""),
                    unit_raw=observation.get("unit_raw"),
                    evidence_excerpts=list(
                        dict.fromkeys(evidence_by_fact.get(fact_id, []))
                    )[:4],
                )
            )
        if len(facts) < 2:
            continue
        source = grouped["source"]
        cases.append(
            FamilyConflictCase(
                case_id=stable_id(
                    "family-case", family_id, source, sorted(fact_ids)
                ),
                source=source,
                facts=facts,
            )
        )

    metrics = []
    for metric_id in metric_ids:
        metric = metrics_by_id[metric_id]
        metrics.append(
            FamilyMetricContract(
                metric_id=metric_id,
                label=metric.labels.get("zh") or metric.labels.get("en"),
                description=metric.description,
                fixed_scope=fixed_by_metric[metric_id],
            )
        )
    return FamilyAdjudicationPacket(
        packet_id=stable_id(
            "family-adjudication",
            package.manifest.package_id,
            package.manifest.package_version,
            family_id,
            [item.case_id for item in cases],
        ),
        family_id=family_id,
        metrics=metrics,
        cases=cases,
    )


def family_template(packet: FamilyAdjudicationPacket) -> dict[str, Any]:
    return {
        "packet_id": packet.packet_id,
        "family_id": packet.family_id,
        "cases": [
            {
                "case_id": item.case_id,
                "status": ["decided", "uncertain"],
                "applicable_metric_ids": [],
                "reason": "short evidence-grounded semantic reason",
            }
            for item in packet.cases
        ],
    }


def family_instructions() -> str:
    return "\n".join(
        [
            "Jointly adjudicate one confusable ESG metric family.",
            "Each case is the same physical source value previously assigned to "
            "incompatible sibling metrics.",
            "Compare every metric description and fixed_scope with the evidence "
            "excerpts and source context.",
            "Return status=decided only when the evidence supports a specific "
            "applicable set. An empty set means the source supports none of these metrics.",
            "More than one metric is allowed only when the report explicitly "
            "establishes every meaning; equal numbers alone are not dual support.",
            "Use status=uncertain when the supplied evidence cannot distinguish the "
            "siblings. Do not guess, calculate, or introduce an unsupplied metric id.",
            "Return exactly one compact JSON object and preserve packet_id, family_id, "
            "and every case_id.",
        ]
    )


def parse_family_decision(
    raw_output: str,
    packet: FamilyAdjudicationPacket,
) -> FamilyAdjudicationDecision:
    try:
        payload = decode_first_json_object(raw_output)
        decision = FamilyAdjudicationDecision.model_validate(payload)
    except Exception as exc:
        raise ModelResponseError(
            f"family adjudication violates output contract: {exc}"
        ) from exc
    if decision.packet_id != packet.packet_id or decision.family_id != packet.family_id:
        raise ModelResponseError("family adjudication returned the wrong packet or family id")
    cases_by_id = {item.case_id: item for item in packet.cases}
    returned = {item.case_id for item in decision.cases}
    if returned != set(cases_by_id):
        raise ModelResponseError(
            "family adjudication must return every supplied case exactly once"
        )
    for item in decision.cases:
        allowed = {fact.metric_id for fact in cases_by_id[item.case_id].facts}
        if not set(item.applicable_metric_ids) <= allowed:
            raise ModelResponseError(
                f"case {item.case_id} returned an unsupplied metric id"
            )
        if item.status == "uncertain" and item.applicable_metric_ids:
            raise ModelResponseError(
                f"case {item.case_id} cannot assign metrics while uncertain"
            )
    return decision


def family_response_schema(packet: FamilyAdjudicationPacket) -> dict[str, Any]:
    metric_ids = sorted(
        {fact.metric_id for case in packet.cases for fact in case.facts}
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "targeted_family_adjudication",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "packet_id": {"type": "string", "const": packet.packet_id},
                    "family_id": {"type": "string", "const": packet.family_id},
                    "cases": {
                        "type": "array",
                        "minItems": len(packet.cases),
                        "maxItems": len(packet.cases),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "case_id": {
                                    "type": "string",
                                    "enum": [item.case_id for item in packet.cases],
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["decided", "uncertain"],
                                },
                                "applicable_metric_ids": {
                                    "type": "array",
                                    "uniqueItems": True,
                                    "items": {"type": "string", "enum": metric_ids},
                                },
                                "reason": {"type": "string", "minLength": 1},
                            },
                            "required": [
                                "case_id",
                                "status",
                                "applicable_metric_ids",
                                "reason",
                            ],
                        },
                    },
                },
                "required": ["packet_id", "family_id", "cases"],
            },
        },
    }


def family_context_json(packet: FamilyAdjudicationPacket) -> str:
    return json.dumps(
        packet.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
