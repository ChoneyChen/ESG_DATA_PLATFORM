"""Build immutable v2 semantic graphs from the last v1 source packages.

Topics express coherent disclosure subjects, not prompts or execution order.
Publishing is deliberately separate from generating these source packages.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASES = {"e1-6": "1.5.0", "e1-5": "1.2.0", "e2-4": "1.0.0"}
TOPICS = {
    "e1-6": {
        "gross-emissions": [7, 9, 10, 11, 12, 13],
        "emissions-structure": [1, 2, 3],
        "scope3-classification": [4, 5, 6, 26, 27],
        "emissions-methodology": [14, 15, 16, 29],
        "scope1-ets": [8], "biogenic-co2": [17, 24, 28],
        "contractual-instruments": [18, 19, 20, 21, 22, 23],
        "scope3-primary-data": [25], "emissions-intensity": [30, 31],
        "revenue-reconciliation": [32, 33, 34, 35],
    },
    "e1-5": {
        "energy-mix": [1, 2, 3, 4, 5, 9, 15],
        "renewable-consumption": [6, 7, 8],
        "fossil-carriers": [10, 11, 12, 13, 14],
        "energy-production": [16, 17],
        "energy-intensity": [18, 19, 20], "energy-revenue": [21, 22, 23],
    },
    "e2-4": {
        # Each medium retains its independent proven extraction route.
        "pollution-structure": [1], "air-emissions": [2],
        "water-emissions": [3], "soil-emissions": [4],
        "microplastics": [5, 6, 7], "pollution-methodology": [8, 9, 10, 15],
        "water-risk-slices": [11, 12, 13, 14], "industrial-emissions": [16, 17, 18, 19, 20],
    },
}
# Direction: source is the main measurement/input; target is detail/output.
RELATIONS = {
    "e1-6": [
        ("separate_disclosure", [7], [8, 17]),
        ("method_variant", [9], [10]),
        ("separate_disclosure", [9, 10], [18, 19, 20, 21, 22, 23, 24]),
        ("separate_disclosure", [11], [25, 26, 27, 28]),
        ("explains", [14, 15, 16], [7, 9, 10, 11, 12, 13]),
        ("explains", [29], [11, 25, 26, 27]),
        ("calculation_dependency", [7, 9, 11], [12]),
        ("calculation_dependency", [7, 10, 11], [13]),
        ("calculation_dependency", [12, 34], [30]),
        ("calculation_dependency", [13, 34], [31]),
        ("explains", [32, 33, 35], [34]),
        ("presentation_of", [7, 9, 10, 11, 12, 13], [1, 3]),
        ("presentation_of", [11], [4, 5, 6]),
        ("explains", [2], [7, 9, 10, 11]),
    ],
    "e1-5": [
        ("decomposes", [1], [2, 3, 5]), ("decomposes", [5], [6, 7, 8]),
        ("decomposes", [2], [10, 11, 12, 13, 14]),
        ("separate_disclosure", [1], [16, 17]),
        ("explains", [19, 20, 21, 22, 23], [18]),
        ("calculation_dependency", [1, 3], [4]),
        ("calculation_dependency", [1, 5], [9]),
        ("calculation_dependency", [1, 2], [15]),
    ],
    "e2-4": [
        ("presentation_of", [2, 3, 4], [1]),
        ("decomposes", [5], [6, 7]),
        ("explains", [8, 9, 10, 15], [2, 3, 4]),
        ("separate_disclosure", [3], [11, 12]), ("separate_disclosure", [4], [13, 14]),
        ("separate_disclosure", [2, 3, 4], [16, 17, 18, 19, 20]),
    ],
}


def build(module: str) -> Path:
    parent = ROOT / "packages/esrs/2023-set1" / module
    source, destination = parent / BASES[module], parent / "2.0.0"
    files = {p.name: json.loads(p.read_text()) for p in source.glob("*.json")}
    manifest = files["manifest.json"]
    manifest.update(format_version="2.0", package_version="2.0.0", status="draft")
    prefix = manifest["package_id"]
    mid = lambda number: f"{prefix}.dp{number:02d}"
    topics = {number: topic for topic, numbers in TOPICS[module].items() for number in numbers}
    anchor_numbers = {"e1-6": {7, 9, 10, 11, 33}, "e1-5": {1, 2, 3, 5, 16, 17}, "e2-4": {2, 3, 4, 5}}[module]
    for metric in files["metrics.json"]:
        number = int(metric["source_datapoint_id"].split("_")[-1])
        metric["disclosure_topic"] = f"{prefix}.topic.{topics[number]}"
        metric["disclosure_role"] = (
            "anchor" if number in anchor_numbers else
            "presentation" if metric["data_class"] == "structure" else
            "context" if metric["data_class"] == "qualitative" else
            "derived" if metric.get("depends_on") else "detail"
        )
    # Replace broad context links, keep conditional/alternative standard rules.
    relations = [r for r in files["relations.json"] if r["relation_type"] != "contextualizes"]
    for index, (kind, sources, targets) in enumerate(RELATIONS[module], 1):
        relations.append({
            "relation_id": f"{prefix}.relation.v2-{index:02d}", "relation_type": kind,
            "source_metric_ids": list(map(mid, sources)), "target_metric_ids": list(map(mid, targets)),
            "policy": "semantic_evidence_link", "description": {
                "decomposes": "成员分解；总量与组成项分别保留，不重复汇总。",
                "method_variant": "并列核算口径；只能依据实际方法证据映射，不因同值互相替代。",
                "separate_disclosure": "独立延伸披露；主指标缺失不表示该分支缺失，不能复制主量。",
                "calculation_dependency": "计算依赖；直接披露优先，只有身份与单位一致时允许既定公式派生。",
                "explains": "方法或对账证据关联；说明不能替代测量数值。",
                "presentation_of": "披露结构投影；引用源事实，不将数值再次伪装成结构断言。",
            }[kind],
            "identity_axes": ["period", "reporting_entity", "reporting_boundary", "aggregation_role"],
            "source_refs": files["metrics.json"][0]["source_refs"],
        })
    files["relations.json"] = relations
    # E2 structure assertions gain a fact owner; the three medium anchors are unchanged.
    for element in files["elements.json"]:
        metric = next(m for m in files["metrics.json"] if m["metric_id"] == element["metric_id"])
        if metric["data_class"] == "structure" and element["binding"]["record_type"] == "reporting_task" and element["cardinality"]["maximum"] is None:
            element["binding"]["record_type"] = "qualitative_assertion"
    for metric in files["metrics.json"]:
        metric_elements = [e for e in files["elements.json"] if e["metric_id"] == metric["metric_id"]]
        record_type = "quantitative_observation" if metric["data_class"] == "quantitative" else "qualitative_assertion"
        if metric["data_class"] == "structure" and not any(e["binding"]["target"] == "statement_raw" for e in metric_elements):
            files["elements.json"].append({
                "element_id": metric["metric_id"] + ".element.disclosure-statement", "metric_id": metric["metric_id"],
                "element_code": "disclosure_statement", "labels": {"en": "Disclosed structure member", "zh": "实际披露的结构成员"},
                "description": "有原文证据的结构成员或说明，不从数值合成陈述。", "semantic_role": "value",
                "requirement_level": "optional", "cardinality": {"minimum": 0, "maximum": 1}, "null_allowed": True,
                "value_contract": {"primary_type": "text"},
                "binding": {"record_type": "qualitative_assertion", "storage": "core_field", "target": "statement_raw"},
                "source_refs": metric["source_refs"],
            })
        if module != "e2-4" and not any(e["element_code"] == "source_row_label" for e in metric_elements):
            files["elements.json"].append({
                "element_id": metric["metric_id"] + ".element.source-row-label", "metric_id": metric["metric_id"],
                "element_code": "source_row_label", "labels": {"en": "Source row topic", "zh": "源行主题"},
                "description": "报告表格原始行标题或段落主题；用于辨识事实，不替代指标适用性判断。", "semantic_role": "subject",
                "requirement_level": "optional", "cardinality": {"minimum": 0, "maximum": 1}, "null_allowed": True,
                "value_contract": {"primary_type": "text"},
                "binding": {"record_type": record_type, "storage": "attribute", "target": "source_row_label"},
                "source_refs": metric["source_refs"],
            })
    if module == "e1-6":
        aggregation_codes = next(c for c in files["code_sets.json"] if c["code_set_id"].endswith(".aggregation-role"))
        for value in aggregation_codes["values"]:
            if value["code"] == "total":
                value["labels"].update({"zh-short": "合计", "zh-traditional": "合計", "zh-total": "总计", "en-short": "Total"})
            elif value["code"] == "subtotal":
                value["labels"].update({"zh-short": "小计", "zh-traditional": "小計", "en-short": "Subtotal"})
        metric = next(m for m in files["metrics.json"] if m["metric_id"] == mid(11))
        metric["description"] = "范围3温室气体绝对排放总量及重大类别分解。保留总量、小计和类别各自的源行身份，不能把合计当类别，也不能将总量与成员重复相加。各期间和实体分别成事实。"
        metric["reporting_requirement"] = metric["description"]
        metric["fact_grain"] = "source_row_period_entity"
        metric["required_semantic_discriminators"] = [x for x in metric.get("required_semantic_discriminators", []) if x != "scope3_category"]
        category_dimensions = [x for x in metric["required_dimension_ids"] if "category" in x]
        metric["required_dimension_ids"] = [x for x in metric["required_dimension_ids"] if x not in category_dimensions]
        metric["optional_dimension_ids"] = list(dict.fromkeys(metric["optional_dimension_ids"] + category_dimensions))
        for element in files["elements.json"]:
            if element["metric_id"] == mid(11) and element["element_code"] in {"aggregation_role", "scope3_category"}:
                element["value_contract"]["fixed_value"] = None
                element.update(requirement_level="optional", null_allowed=True)
                element["cardinality"]["minimum"] = 0
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (destination / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return destination


if __name__ == "__main__":
    for module in BASES:
        print(build(module))
