"""Compile product-facing scientific definitions from immutable standard packages.

The standard owns measurement semantics; product policy adds only comparison,
review and presentation concerns. Source report-reading axes are not implicitly
turned into scientific comparison dimensions.
"""
from __future__ import annotations

import json


UNIT_IDS = {
    "extractesg.core.unit.megawatt-hour": "MWh",
    "extractesg.core.unit.tonne-co2e": "tCO2e",
    "extractesg.core.unit.tonne": "tonne",
    "extractesg.core.unit.cubic-metre": "m3",
    "extractesg.core.unit.ratio": "ratio",
    "extractesg.core.unit.count": "count",
    "extractesg.core.unit.cny": "cny",
}


def unit_adapter(measurement: dict, *, policy: dict) -> tuple[str | None, str | None]:
    dimension = measurement["unit_dimension"]
    short = dimension.rsplit(".", 1)[-1] if dimension else None
    if dimension and short not in {"energy", "ghg-emissions", "mass", "volume", "monetary", "ratio", "count"}:
        if policy.get("canonical_unit") is None:
            raise ValueError(f"No display unit adapter for {dimension}")
        return policy["canonical_unit"], policy.get("unit_dimension")
    return UNIT_IDS.get(measurement["canonical_unit_id"]), dimension



def compile_scientific_contract(source_metric, *, policy: dict) -> dict:
    contract = source_metric.measurement_contract
    if contract is None:
        raise ValueError(f"Ready metric has no scientific contract: {source_metric.source_datapoint_id}")
    measurement = contract.model_dump(mode="json")
    canonical = UNIT_IDS.get(measurement["canonical_unit_id"])
    if measurement["canonical_unit_id"] and canonical is None:
        if policy.get("canonical_unit") is None:
            raise ValueError(f"No platform unit adapter for {measurement['canonical_unit_id']}")
        canonical = policy["canonical_unit"]
    if measurement["canonical_unit_id"] is None:
        canonical = policy.get("canonical_unit")
    dimension = measurement["unit_dimension"]
    if dimension and dimension.startswith("extractesg.core.unit-dimension."):
        dimension = dimension.rsplit(".", 1)[-1].replace("-", "_")
    if dimension == "ghg-emissions":
        dimension = "ghg_emissions"
    if dimension is None:
        dimension = policy.get("unit_dimension")
    value_type = policy["value_type"]
    if value_type == "numeric" and measurement["value_role"] != "actual":
        raise ValueError(f"Numeric publication requires actual role: {source_metric.source_datapoint_id}")
    if value_type == "statement" and measurement["value_role"] not in {"context", "structure"}:
        raise ValueError(f"Statement publication requires context role: {source_metric.source_datapoint_id}")
    return {
        "canonical_unit": policy.get("canonical_unit") if policy.get("canonical_unit") is not None else canonical,
        "unit_dimension": policy.get("unit_dimension") or dimension,
        "description": (policy.get("previous_definition") or {}).get("description", policy["description"]),
        "value_role": "actual" if value_type == "numeric" else "context",
        "aggregation": measurement["aggregation"] if measurement["aggregation"] != "none" else policy["aggregation"],
        "normalization": (policy.get("previous_definition") or {}).get("normalization", policy.get("normalization") or measurement["normalization"]),
        "comparison_mode": measurement["comparison_mode"],
        "exclusions": list(dict.fromkeys([
            *((policy.get("previous_definition") or {}).get("exclusions", measurement["exclusions"])),
            *policy["exclusions"]])),
        "comparison_conditions": list(dict.fromkeys([
            *((policy.get("previous_definition") or {}).get("comparison_conditions", measurement["comparison_requirements"])),
            *policy["comparison_conditions"]])),
        "positive_definition": measurement.get("positive_definition"),
        "interpretation_guidance": measurement.get("interpretation_guidance") or [],
        "distinguish_from": measurement.get("distinguish_from") or [],
        "semantic_contract_version": measurement.get("semantic_contract_version", "legacy-unpartitioned-v1"),
        "scientific_contract_source": {
            "package_metric_id": source_metric.metric_id,
            "measurement_contract": measurement,
            "adapter_version": "standard-scientific-contract-v1",
        },
    }


# Axes the platform must record to identify a measurement but the standard
# package does not declare yet. Every entry is a known gap in the package
# contract, listed once with its reason: a new clause may not invent identity
# silently, and closing a gap here means deleting the entry.
PLATFORM_IDENTITY_EXTENSIONS = {
    "aggregation_role": "总量与其组成项是不同测量",
    "board_definition": "董事会与管理层是不同人群",
    "channel_scope": "投诉渠道覆盖决定数量含义",
    "community_scope": "受影响社区范围决定数量含义",
    "complaint_definition": "投诉按显式定义计数",
    "consumer_population": "消费者条款点名人群",
    "denominator_scope": "占比必须声明分母范围",
    "gender": "性别多样性按人群测量",
    "impact_scope": "影响范围决定数量含义",
    "impact_type": "影响类型区分场址影响",
    "incident_scope": "事件范围决定数量含义",
    "measurement_method": "地点法与市场法范围2是不同测量",
    "policy_scope": "政策覆盖人群决定适用范围",
    "remedy_scope": "补救措施覆盖范围决定数量含义",
    "sensitivity_class": "敏感区域分级决定场址计数",
    "site": "场址是场址类条款的身份轴",
    "site_definition": "场址定义决定面积与数量",
    "value_chain_stage": "价值链阶段决定事件归属",
    "worker_population": "员工群体决定数量含义",
}


def check_identity_axes(original: str, source_metric, policy: dict) -> None:
    """The package owns identity; the platform may only add declared gaps."""
    contract = source_metric.measurement_contract
    declared = set(contract.identity_axes) | set(contract.comparison_axes)
    used = {*policy.get("required_dimensions", []), *(policy.get("fixed_dimensions") or {})}
    unknown = sorted(used - declared - set(PLATFORM_IDENTITY_EXTENSIONS))
    if unknown:
        raise ValueError(f"{original}: 平台身份轴未被标准包声明，也不是已登记的扩展: {unknown}")


def compile_dictionary_metrics(*, selected: list[str], registry, catalog, packages, policies: dict,
                               previous_metrics: dict | None = None) -> list[dict]:
    records = {record["id"]: record for record in catalog["records"] if record["record_kind"] == "独立编号条目"}
    modules = {original: module for module in registry["modules"] for original in module["source_datapoint_ids"]}
    result = []
    for original in selected:
        record, module = records[original], modules[original]
        ready = module["executable"] and module["core_analysis_state"] == "authored_draft"
        if original not in module["core_source_datapoint_ids"]:
            raise ValueError("A platform-major clause must be package-core: " + original)
        policy = json.loads(json.dumps(policies[original], ensure_ascii=False))
        if previous_metrics and original in previous_metrics:
            policy["previous_definition"] = previous_metrics[original]
        source_metric = None
        if ready:
            package = packages[module["disclosure_requirement"]]
            source_metric = next(metric for metric in package.metrics if metric.source_datapoint_id == original)
            check_identity_axes(original, source_metric, policy)
            policy.update(compile_scientific_contract(source_metric, policy=policy))
        result.append({
            **policy,
            "source_datapoint_id": original,
            "original_name": record["name"],
            "original_data_type": record["data_type"],
            "topic": record["standard"].strip(),
            "readiness": "ready" if ready else "planned",
            "definition_status": "authored_draft" if ready else "planned_proposal",
            "package_core": True,
            "platform_major": True,
            "standard_binding": {
                "package_id": module["package_id"],
                "package_version": module["package_version"] if ready else None,
                "metric_id": source_metric.metric_id if ready else None,
                "digest": module["source_digest"] if ready else None,
            },
            "source_measurement_contract": source_metric.measurement_contract.model_dump(mode="json") if source_metric else None,
            "source_metric": source_metric,
        })
    return result
