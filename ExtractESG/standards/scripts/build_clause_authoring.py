#!/usr/bin/env python3
"""Generate the per-clause authoring declarations from the legacy foundation rules.

This is a one-time migration tool. After it runs, ``clause-authoring/<id>.json``
and ``profiles/esrs/2023-set1/clause-lexicon.json`` are the authored source;
the extraction runtime contains only generic operators.

    PYTHONPATH=... python standards/scripts/build_clause_authoring.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rx(value) -> str:
    return "re:" + value.pattern


def terms(*values: str) -> list[str]:
    return list(values)


def standards_clauses() -> dict[str, dict]:
    """Current compiled clause profiles, used only as the migration source."""
    root = ROOT
    registry = json.loads((root / "registry/esrs/2023-set1/registry.json").read_text(encoding="utf-8"))
    current = {(module["package_id"], module["package_version"]): module
               for module in registry.get("modules", []) if module.get("executable")}
    index = json.loads((root / "clauses/catalog.json").read_text(encoding="utf-8"))
    major = set(json.loads((root / "profiles/esrs/2023-set1/platform-major.json").read_text(
        encoding="utf-8"))["source_datapoint_ids"])
    result = {}
    for package in index.get("packages", []):
        module = current.get((package["package_id"], package["package_version"]))
        if module is None:
            continue
        for row in package.get("clauses", []):
            body = json.loads((root / "clauses" / row["path"] / "clause.json").read_text(encoding="utf-8"))
            if body["source_datapoint_id"] in major:
                result[body["source_datapoint_id"]] = body
    return result


# --- shared vocabulary (standard content, not extraction code) -------------

def lexicon(data: dict[str, dict] | None = None) -> dict[str, list[str]]:
    from esg_targeted.foundation import guards as G
    data = data or standards_clauses()

    entries: dict[str, list[str]] = {
        "total": [rx(G.TOTAL_RE)],
        "explicit_total": terms("总能", "能源总", "能耗总", "总耗能", "综合能耗", "total energy"),
        "ratio_word": [rx(G.RATIO_WORD_RE)],
        "intensity": [rx(G.INTENSITY_RE)],
        "intensity_unit": [rx(G.INTENSITY_UNIT_RE)],
        "change": [rx(G.CHANGE_RE)],
        "energy_total": [rx(G.ENERGY_TOTAL_RE)],
        "energy_subset": [rx(G.ENERGY_SUBSET_RE)],
        "direct_and_indirect": [rx(G.DIRECT_AND_INDIRECT_RE)],
        "renewable": [rx(G.RENEWABLE_RE)],
        "electricity": [rx(G.ELECTRICITY_RE)],
        "fossil": [rx(G.FOSSIL_RE)],
        "generation": [rx(G.GENERATION_RE)],
        "renewable_component": [rx(G.RENEWABLE_COMPONENT_RE)],
        "purchase_terms": terms("采购", "购买", "购入", "purchas", "现场", "自发", "自建", "自产",
                                "on-site", "on site", "self-generat", "self generat"),
        "consumption_terms": terms("消耗", "消费", "使用", "用量", "consum", "use"),
        "clean": terms("清洁", "clean"),
        "scope1": [rx(G.SCOPE1_RE)],
        "scope2": [rx(G.SCOPE2_RE)],
        "scope3": [rx(G.SCOPE3_RE)],
        "combined_scope": [rx(G.COMBINED_SCOPE_RE)],
        "scope3_category": [rx(G.SCOPE3_CATEGORY_RE)],
        "scope3_named": [rx(G.SCOPE3_NAMED_RE)],
        "net": [rx(G.NET_RE)],
        "ghg_unit": [rx(G.GHG_UNIT_RE)],
        "ghg_label": [rx(G.GHG_LABEL_RE)],
        "market_method": [rx(G.MARKET_METHOD_RE)],
        "location_method": [rx(G.LOCATION_METHOD_RE)],
        "revenue": [rx(G.REVENUE_RE)],
        "air_pollutant": [rx(G.AIR_POLLUTANT_RE)],
        "water_pollutant": [rx(G.WATER_POLLUTANT_RE)],
        "air_only": [rx(G.AIR_ONLY_RE)],
        "water_only": [rx(G.WATER_ONLY_RE)],
        "pollutant_exclude": [rx(G.POLLUTANT_EXCLUDE_RE)],
        "pollutant_specific": [rx(G.POLLUTANT_SPECIFIC_RE)],
        "fossil_fuel": [rx(G.FOSSIL_FUEL_RE)],
        "single_fuel": [rx(G.SINGLE_FUEL_RE)],
        "hazardous": [rx(G.HAZARDOUS_RE)],
        "waste_dest": [rx(G.WASTE_DEST_RE)],
        "material": [rx(G.MATERIAL_RE)],
        "employee": [rx(G.EMPLOYEE_RE)],
        "turnover": [rx(G.TURNOVER_RE)],
        "average": [rx(G.AVERAGE_RE)],
        "training": [rx(G.TRAINING_RE)],
        "topic_training": [rx(G.TOPIC_TRAINING_RE)],
        "whole_workforce": [rx(G.WHOLE_WORKFORCE_RE)],
        "consolidation_boundary": [rx(G.CONSOLIDATION_BOUNDARY_RE)],
        "hours": [rx(G.HOURS_RE)],
        "safety": [rx(G.SAFETY_RE)],
        "paygap": [rx(G.PAYGAP_RE)],
        "remun_ratio": [rx(G.REMUN_RATIO_RE)],
        "discrim": [rx(G.DISCRIM_RE)],
        "gender": [rx(G.GENDER_RE)],
        "topmgmt": [rx(G.TOPMGMT_RE)],
        "board": [rx(G.BOARD_RE)],
        "corruption": [rx(G.CORRUPTION_RE)],
        "conviction": [rx(G.CONVICTION_RE)],
        "rate_unit": [rx(G.RATE_UNIT_RE)],
        "paydays": [rx(G.PAYDAYS_RE)],
        "days": [rx(G.DAYS_RE)],
        "substance": [rx(G.SUBSTANCE_RE)],
        "waste": [rx(G.WASTE_RE)],
        "water_consumption": [rx(G.CONSUMPTION_RE)],
        "risk_area": [rx(G.RISK_AREA_RE)],
        "withdrawal": [rx(G.WITHDRAWAL_RE)],
        "water_use_only": [rx(G.WATER_USE_ONLY_RE)],
        "recycle": [rx(G.RECYCLE_RE)],
        "discharge": [rx(G.DISCHARGE_RE)],
        "water_word": [rx(G.WATER_WORD_RE)],
        "not_water": [rx(G.NOT_WATER_RE)],
        "freshwater": [rx(G.FRESHWATER_RE)],
        "non_fresh": [rx(G.NON_FRESH_RE)],
        "water_source": [rx(G.WATER_SOURCE_RE)],
        "water_dest": [rx(G.WATER_DEST_RE)],
        "lower_management": [rx(G.LOWER_MANAGEMENT_RE)],
        "top_management": [rx(G.TOP_MANAGEMENT_RE)],
        "per_hundred_workers": [rx(G.PER_HUNDRED_WORKERS_RE)],
        "non_hazardous": terms("无害", "非有害", "一般工业", "non-hazardous"),
        "waste_category": terms("一般", "无害", "無害", "生活", "建筑", "建築", "危险", "危險", "有害"),
        "waste_solid": terms("固废", "固体废弃物", "固体废物"),
        "generated": terms("产生", "生成", "generated", "generation"),
        "non_employees": terms("非员工", "非雇员", "劳务派遣", "外包", "non-employee", "contractor"),
        "population_subset": terms("少数民族", "少数族裔", "残疾", "殘疾", "女性员工", "男性员工", "外籍",
                                   "外派", "本地员工", "退伍", "复员"),
        "turnover_or_hiring": terms("入职率", "新入职", "新进员工", "招聘人数", "离职", "解雇", "解僱",
                                    "hiring", "new hires", "new employees", "leavers", "dismiss"),
        "participation": terms("人次", "参与", "参加", "演练", "演練", "活动人数", "person-times"),
        "incident": terms("事故", "事件", "工伤", "工傷", "伤亡", "傷亡", "accident", "incident"),
        "training_subset": terms("其中", "男性", "女性", "按性别", "按年龄", "岁以下", "岁以上",
                                "管理者", "管理层", "职级", "岗位", "地区", "区域", "基层", "中层",
                                "高层", "普通员工", "劳务", "派遣", "by gender", "by age",
                                "岁", "年龄", "以上", "以下", "基层员工", "中层管理", "高层管理"),
        "material_inflow": terms("资源投入", "原材料使用", "原材料投入", "材料投入使用", "materials used",
                                 "material inflow", "resource inflow"),
        "recycle_content": terms("废旧", "回收量", "回收率", "再生量", "recovered", "recovery",
                                 "recycled amount"),
        "geography": terms("数据中心", "生产基地", "基地", "工厂", "厂区", "园区", "办公", "分公司", "子公司",
                           "事业部", "板块", "门店", "电站", "自有", "租赁", "境内", "境外", "海外",
                           "data cent", "production base", "plant", "site", "office", "subsidiar",
                           "segment", "domestic", "overseas", "leased", "owned"),
        "regulatory_limit": [rx(G.REGULATORY_LIMIT_RE)],
        "index_entry": [rx(G.INDEX_ENTRY_RE)],
        "narrative_question": [rx(G.NARRATIVE_QUESTION_RE)],
        "change_or_target": [rx(G.RELATIVE_CHANGE_OR_TARGET_RE)],
        "qualifier_only": [rx(G.QUALIFIER_ONLY_RE)],
        "recycled_water": terms("循环水", "回用水", "重复利用水", "再生水", "中水", "recycled water", "reused water"),
        "alternative_source": terms("替代水源", "替代水", "alternative source"),
        "method_terms": terms("监测", "检测", "在线", "测量", "测算", "估算", "计算", "核算", "排放因子",
                              "物料衡算", "系数", "统计口径", "monitor", "measur", "estimat", "calculat",
                              "emission factor", "mass balance", "methodolog"),
        "fine": terms("罚款", "罚金", "fine", "penalt"),
        "coverage": terms("覆盖", "coverage"),
        "standard_terms": terms("标准账期", "标准付款期限", "standard terms", "on-time", "按时付款"),
    }
    # Group term sets and child vocabularies already declared by the clause
    # contracts; the migration copies them into the shared lexicon once.
    for body in data.values():
        group = body.get("group")
        if not group:
            continue
        if body.get("group_terms"):
            entries.setdefault(f"group:{group}:strong", list(body["group_terms"]))
        if body.get("group_unit_terms"):
            entries.setdefault(f"group:{group}:weak", list(body["group_unit_terms"]))
        children = (body.get("relationships") or {}).get("child_terms") or []
        if children:
            entries.setdefault(f"group:{group}:children", list(children))
    return entries


OPERATOR_USES = {
    "numeric_label": ["ratio_word"],
    "ghg_scope": ["scope1", "scope2", "scope3", "combined_scope", "scope3_category", "scope3_named",
                  "net", "ghg_label", "ghg_unit", "market_method", "location_method",
                  "intensity", "ratio_word", "change", "total", "consolidation_boundary"],
    "pollutant": ["air_pollutant", "water_pollutant", "air_only", "water_only", "pollutant_exclude",
                  "intensity", "change", "ratio_word"],
    "water": ["water_consumption", "withdrawal", "discharge", "recycle", "risk_area", "water_use_only",
              "freshwater", "non_fresh", "water_source", "water_dest", "water_word", "not_water",
              "total", "intensity", "ratio_word", "change", "geography", "waste_solid",
              "recycled_water", "alternative_source", "consumption_terms"],
    "waste": ["waste", "hazardous", "non_hazardous", "waste_category", "waste_solid", "waste_dest",
              "generated", "total", "ratio_word", "intensity"],
    "statement": ["method_terms"],
}


def spec_rules(spec) -> list[dict]:
    rules = []
    if spec.require_terms:
        rules.append({"rule": "require_any", "terms": list(spec.require_terms), "scope": "scope_text",
                      "reason": "label_not_this_concept"})
    if spec.exclude_terms:
        rules.append({"rule": "forbid_any", "terms": list(spec.exclude_terms), "reason": "label_is_other_concept"})
    if spec.scope_terms:
        rules.append({"rule": "require_any", "terms": list(spec.scope_terms), "scope": "scope_text",
                      "reason": "label_not_group_scope"})
    if spec.total and spec.component_terms:
        rules.append({"rule": "reject_component", "terms": list(spec.component_terms),
                      "params": {"prefix": False}, "reason": "component_row"})
    if spec.dimension:
        rules.append({"rule": "unit_dimension", "allowed": [spec.dimension],
                      "params": {"allow_missing": True}})
    return rules


# --- clauses with bespoke guards: translated rule for rule ------------------

def clause_rules(data: dict[str, dict] | None = None) -> dict[str, dict]:
    from esg_targeted.foundation import guards as G
    from legacy_foundation_specs import SPECS as LEGACY_SPECS
    data = data or standards_clauses()

    U = {"rule": "unit_dimension"}
    def unit(*allowed, **params):
        return {"rule": "unit_dimension", "allowed": list(allowed), "params": params}
    def req_all(*refs, scope="scope_text", reason=None):
        return {"rule": "require_all", "terms": list(refs), "scope": scope, "reason": reason}
    def req(*refs, scope="scope_text", reason=None, unless=None):
        rule = {"rule": "require_any", "terms": list(refs), "scope": scope}
        if reason: rule["reason"] = reason
        if unless: rule["unless_terms"] = list(unless)
        return rule
    def forbid(*refs, reason, unless=None, scope="text"):
        rule = {"rule": "forbid_any", "terms": list(refs), "scope": scope, "reason": reason}
        if unless: rule["unless_terms"] = list(unless)
        return rule
    def flag(*refs, flag, unless=None):
        rule = {"rule": "flag_review", "terms": list(refs), "flag": flag}
        if unless: rule["unless_terms"] = list(unless)
        return rule

    clauses: dict[str, dict] = {
        "E1-5_01": dict(group="energy", operator="numeric_label", rules=[
            req("@energy_total", "@total", reason="label_not_total_energy_consumption"),
            forbid("@energy_subset", reason="label_is_energy_component_or_carrier", unless=["@explicit_total"]),
            forbid("@ratio_word", "@intensity", "@change", reason="label_is_ratio_intensity_or_change"),
            {"rule": "reject_component", "reason": "component_row"},
            unit("energy"),
        ]),
        "E1-5_05": dict(group="energy", operator="numeric_label", rules=[
            req("@renewable", scope="subject", reason="label_not_renewable"),
            {"rule": "reject_component", "unless_terms": ["@total"], "reason": "component_row"},
            forbid("@purchase_terms", reason="renewable_source_component", unless=["@consumption_terms"]),
            flag("@renewable_component", flag="renewable_component_not_total", unless=["@total"]),
            forbid("@ratio_word", "@generation", "@intensity", "@change",
                   reason="label_is_ratio_generation_or_change"),
            forbid("@fossil", reason="label_mentions_fossil_or_grid"),
            unit("energy"),
            flag("@electricity", flag="renewable_electricity_only"),
            flag("@clean", flag="clean_energy_may_include_non_renewable", unless=["@renewable"]),
        ]),
        "E1-5_09": dict(group="energy", operator="numeric_label", rules=[
            req("@renewable", scope="subject", reason="label_not_renewable"),
            forbid("@fossil", reason="label_mentions_fossil_or_grid"),
            forbid("@generation", reason="label_is_generation_share", unless=["@consumption_terms"]),
            {"rule": "ratio_required", "reason": "not_a_ratio"},
            unit("ratio"),
            flag("@electricity", flag="renewable_share_of_electricity_only"),
            flag("@clean", flag="clean_energy_may_include_non_renewable", unless=["@renewable"]),
        ]),
        "E1-6_07": dict(group="ghg", operator="ghg_scope", rules=[
            {"rule": "ghg_scope", "params": {"scope": "scope_1"}},
            forbid("@intensity", "@ratio_word", "@change", reason="label_is_intensity_ratio_or_change"),
            forbid("@net", reason="label_is_net_after_offsets"),
            {"rule": "ghg_unit"},
        ]),
        "E1-6_09": dict(group="ghg", operator="ghg_scope", rules=[
            {"rule": "ghg_scope", "params": {"scope": "scope_2", "method": "location_based"}},
            forbid("@intensity", "@ratio_word", "@change", reason="label_is_intensity_ratio_or_change"),
            forbid("@net", reason="label_is_net_after_offsets"),
            {"rule": "ghg_unit"},
        ]),
        "E1-6_10": dict(group="ghg", operator="ghg_scope", rules=[
            {"rule": "ghg_scope", "params": {"scope": "scope_2", "method": "market_based"}},
            forbid("@intensity", "@ratio_word", "@change", reason="label_is_intensity_ratio_or_change"),
            forbid("@net", reason="label_is_net_after_offsets"),
            {"rule": "ghg_unit"},
        ]),
        "E1-6_11": dict(group="ghg", operator="ghg_scope", rules=[
            {"rule": "ghg_scope", "params": {"scope": "scope_3"}},
            forbid("@scope3_category", reason="label_is_scope3_category"),
            forbid("@combined_scope", "@intensity", "@ratio_word", "@change",
                   reason="label_is_combined_intensity_ratio_or_change"),
            forbid("@net", reason="label_is_net_after_offsets"),
            forbid("@scope3_named", reason="label_is_scope3_category", unless=["@total"]),
            {"rule": "ghg_unit"},
        ]),
        "E1-6_12": dict(group="ghg", operator="ghg_scope", rules=[
            {"rule": "ghg_scope_composition", "params": {"scopes": ["scope1", "scope2", "scope3"],
                                                          "method": "location_based"}},
            forbid("@intensity", "@ratio_word", "@change", reason="label_is_ratio_intensity_or_change"),
            flag("@consolidation_boundary", flag="consolidation_boundary_differs"),
            {"rule": "ghg_unit"},
        ]),
        "E1-6_30": dict(group="ghg_intensity", operator="numeric_label", rules=[
            req("@intensity", "@intensity_unit", scope="all", reason="label_not_intensity"),
            req("@revenue", scope="all", reason="label_not_revenue_intensity"),
            {"rule": "ghg_scope", "params": {"scope": "any", "single_scope_forbidden": True,
                                              "method": "location_based", "method_check": True}},
            unit("ghg-intensity"),
        ]),
        "E1-5_02": dict(group="energy", operator="numeric_label", rules=[
            forbid("@ratio_word", "@intensity", reason="label_is_ratio_or_intensity"),
            req("@fossil_fuel", scope="subject", reason="label_not_fossil_energy"),
            forbid("@single_fuel", reason="label_is_fossil_carrier", unless=["@total"]),
            forbid("@recycle", "@change", reason="label_is_change_or_offset"),
            unit("energy"),
        ]),
        "E2-4_02": dict(group="air", operator="pollutant", rules=[
            {"rule": "pollutant_medium", "params": {"medium": "air"}},
            forbid("@pollutant_exclude", "@intensity", "@change",
                   reason="label_is_concentration_intensity_ghg_or_change"),
            unit("mass", ratio_review="pollutant_unit_printed_as_percent"),
        ]),
        "E2-4_03": dict(group="water_pollutants", operator="pollutant", rules=[
            {"rule": "pollutant_medium", "params": {"medium": "water"}},
            forbid("@pollutant_exclude", "@intensity", "@change",
                   reason="label_is_concentration_intensity_ghg_or_change"),
            unit("mass", ratio_review="pollutant_unit_printed_as_percent"),
        ]),
        "E2-5_02": dict(group="substances", operator="numeric_label", rules=[
            req("@substance", reason="label_not_substances_of_concern"),
            forbid("@waste", reason="label_is_waste"),
            forbid("@ratio_word", "@intensity", reason="label_is_ratio_or_intensity"),
        ]),
        "E3-4_01": dict(group="water", operator="water", rules=[
            {"rule": "water_concept", "params": {"concept": "consumption"}},
            unit("volume", "mass"),
        ]),
        "E3-4_02": dict(group="water", operator="water", rules=[
            {"rule": "water_concept", "params": {"concept": "risk_area"}},
            unit("volume", "mass"),
        ]),
        "E3-4_03": dict(group="water", operator="water", rules=[
            {"rule": "water_concept", "params": {"concept": "recycled"}},
            unit("volume", "mass"),
        ]),
        "E3-4_11": dict(group="water", operator="water", rules=[
            {"rule": "water_concept", "params": {"concept": "withdrawal"}},
            unit("volume", "mass"),
        ]),
        "E3-4_12": dict(group="water", operator="water", rules=[
            {"rule": "water_concept", "params": {"concept": "discharge"}},
            unit("volume", "mass"),
        ]),
        "E5-5_07": dict(group="waste", operator="waste", rules=[
            {"rule": "waste_concept", "params": {"hazardous": None}},
            unit("mass"),
        ]),
        "E5-5_15": dict(group="waste", operator="waste", rules=[
            {"rule": "waste_concept", "params": {"hazardous": True}},
            unit("mass"),
        ]),
        "E5-4_02": dict(group="materials", operator="numeric_label", rules=[
            forbid("@waste", reason="label_is_waste_not_inflow", scope="text"),
            req("@material", reason="label_not_materials_used"),
            forbid("@recycle_content", reason="label_is_recycled_material_not_inflow", unless=["@material_inflow"]),
            forbid("@ratio_word", "@intensity", reason="label_is_ratio_or_intensity"),
            forbid("@hazardous", reason="label_is_hazardous_materials_subset"),
            unit("mass"),
        ]),
        "S1-6_02": dict(group="workforce", operator="numeric_label", rules=[
            forbid("@non_employees", reason="label_is_non_employees"),
            forbid("@population_subset", reason="label_is_population_subset"),
            forbid("@turnover_or_hiring", reason="label_is_turnover_or_hiring"),
            forbid("@average", reason="label_is_average_not_period_end"),
            forbid("@participation", reason="label_is_participation_count_not_headcount"),
            forbid("@incident", reason="label_is_incident_count_not_headcount"),
            req("@employee", reason="label_not_employees"),
            unit("count"),
        ]),
        "S1-6_12": dict(group="workforce", operator="numeric_label", rules=[
            req("@turnover", scope="subject", reason="label_not_turnover_rate"),
            forbid("@training_subset", reason="label_is_population_subset"),
            {"rule": "reject_component", "reason": "label_is_population_subset"},
            {"rule": "ratio_required", "reason": "unit_dimension_not_ratio"},
            unit("ratio"),
        ]),
        "S1-9_02": dict(group="workforce_gender", operator="numeric_label", rules=[
            req_all("@gender", "@topmgmt", reason="label_not_top_management_gender"),
            forbid("@lower_management", reason="label_not_top_management_level", unless=["@top_management"]),
            forbid("@board", reason="label_is_board_scope", unless=["@topmgmt"]),
            {"rule": "ratio_required", "reason": "unit_dimension_not_ratio"},
            unit("ratio"),
        ]),
        "S1-13_04": dict(group="workforce_training", operator="numeric_label", rules=[
            req_all("@training", "@hours", reason="label_not_training_hours"),
            forbid("@topic_training", reason="label_is_topic_specific_training", unless=["@whole_workforce"]),
            forbid("@training_subset", reason="label_is_population_subset", unless=["@whole_workforce"]),
            forbid("@ratio_word", reason="label_is_ratio"),
        ]),
        "S1-14_05": dict(group="workforce_safety", operator="numeric_label", rules=[
            req("@safety", scope="subject", reason="label_not_work_related_accident"),
            forbid("@coverage", reason="label_is_coverage_not_rate"),
            forbid("@per_hundred_workers", reason="label_is_per_employee_rate_not_work_hours"),
            {"rule": "ratio_required", "reason": "unit_dimension_not_ratio"},
            unit("ratio"),
        ]),
        "S1-16_01": dict(group="workforce_pay", operator="numeric_label", rules=[
            req("@paygap", scope="subject", reason="label_not_gender_pay_gap"),
            forbid("@remun_ratio", reason="label_is_remuneration_ratio"),
            {"rule": "ratio_required", "reason": "unit_dimension_not_ratio"},
            unit("ratio"),
        ]),
        "S1-17_02": dict(group="workforce_incidents", operator="numeric_label", rules=[
            req("@discrim", scope="subject", reason="label_not_discrimination_incident"),
            forbid("@fine", reason="label_is_fine_not_incident_count"),
            unit("count"),
        ]),
        "GOV-1_06": dict(group="governance", operator="numeric_label", rules=[
            req_all("@board", "@gender", reason="label_not_board_gender"),
            {"rule": "ratio_required", "reason": "unit_dimension_not_ratio"},
            unit("ratio"),
        ]),
        "G1-4_01": dict(group="governance", operator="numeric_label", rules=[
            req_all("@corruption", "@conviction", reason="label_not_corruption_conviction"),
            forbid("@fine", reason="label_is_fine_not_conviction", unless=["@conviction"]),
            unit("count"),
        ]),
        "G1-6_01": dict(group="governance", operator="numeric_label", rules=[
            req_all("@paydays", "@days", reason="label_not_payment_days"),
            forbid("@standard_terms", reason="label_is_terms_or_share_not_average_days"),
        ]),
        "E2-4_09": dict(group="pollution_method", operator="statement", rules=[
            {"rule": "require_any", "terms": ["@method_terms"], "scope": "text",
             "reason": "label_not_a_measurement_method"},
            {"rule": "require_any", "terms": ["@air_pollutant", "@water_pollutant"], "scope": "all",
             "reason": "label_not_about_pollutants"},
        ]),
    }
    # Specs-only platform clauses: the declarative spec is already the shape we
    # want; copy it into the clause authoring source.
    for code, spec in LEGACY_SPECS.items():
        if code in clauses:
            continue
        clauses[code] = dict(group=group_for_spec(code), operator="numeric_label",
                             rules=spec_rules(spec))
    # Codes with no guard and no spec still get their authored group/operator
    # from the clause contract, so the runtime never guesses from the code.
    for code, body in data.items():
        entry = clauses.setdefault(code, dict(group=body.get("group"), operator="numeric_label", rules=[]))
        relationships = body.get("relationships") or {}
        entry.setdefault("relationships", relationships)
        if body.get("exact_labels"):
            entry["exact_labels"] = list(body["exact_labels"])
    return clauses


def text_group_for(group: str) -> str:
    if group in {"air", "water_pollutants", "pollution_method"}:
        return "pollution"
    if group.startswith("workforce"):
        return "workforce"
    return group


def group_for_spec(code: str) -> str:
    if code.startswith("E4"):
        return "biodiversity"
    if code.startswith("S2"):
        return "value_chain"
    if code.startswith("S3"):
        return "communities"
    if code.startswith("S4"):
        return "consumer"
    if code.startswith("G1") or code.startswith("GOV"):
        return "governance"
    if code.startswith("E5-4"):
        return "materials"
    if code.startswith("E5-5"):
        return "waste"
    if code.startswith("S1-13"):
        return "workforce_training"
    if code.startswith("S1-14"):
        return "workforce_safety"
    if code.startswith("S1-16"):
        return "workforce_pay"
    if code.startswith("S1-17"):
        return "workforce_incidents"
    if code.startswith("S1-9"):
        return "workforce_gender"
    if code.startswith("S1-6"):
        return "workforce"
    return "general"


def main() -> int:
    standards = ROOT
    out = standards / "clause-authoring"
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.json"):
        stale.unlink()
    data = standards_clauses()
    vocab = lexicon(data)
    (standards / "profiles/esrs/2023-set1").mkdir(parents=True, exist_ok=True)
    (standards / "profiles/esrs/2023-set1/clause-lexicon.json").write_text(
        json.dumps({"schema_version": "clause-lexicon-1.0",
                    "description": "Shared clause vocabulary authored once in the standard tree; "
                                   "compiled clause files embed the terms they use.",
                    "terms": vocab}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    clauses = clause_rules(data)
    written = 0
    for code, entry in sorted(clauses.items()):
        uses = set(OPERATOR_USES.get(entry["operator"], []))
        for rule in entry.get("rules") or []:
            for key in ("terms", "unless_terms", "if_terms"):
                for term in rule.get(key) or []:
                    if isinstance(term, str) and term.startswith("@"):
                        uses.add(term[1:])
            for key, value in (rule.get("params") or {}).items():
                if key.endswith("terms") and isinstance(value, list):
                    for term in value:
                        if isinstance(term, str) and term.startswith("@"):
                            uses.add(term[1:])
        display = data.get(code)
        body = {
            "schema_version": "clause-reading-authoring-1.0",
            "clause_key": code,
            "group": entry.get("group"),
            "text_group": text_group_for(entry.get("group") or "general"),
            "operator": entry.get("operator", "numeric_label"),
            "relationships": entry.get("relationships") or {},
            "exact_labels": entry.get("exact_labels") or [],
            "uses": sorted(uses),
            "rules": entry.get("rules") or [],
        }
        if display is not None:
            body["display_label"] = display.get("display_label")
            body["display_definition"] = display.get("display_definition")
        if entry.get("data_kind"):
            body["data_kind"] = entry["data_kind"]
        (out / f"{code}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
        written += 1
    # Selection profiles are data, not extraction branches.
    profiles = json.loads((standards / "profiles/esrs/2023-set1/metric-profiles.json").read_text(
        encoding="utf-8"))["profiles"]
    (standards / "profiles/esrs/2023-set1/metric-profiles.json").write_text(
        json.dumps({"schema_version": "metric-profiles-1.0",
                    "description": "Named clause selections; a set is only a list of independent clauses.",
                    "profiles": profiles}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"clauses": written, "lexicon_terms": len(vocab),
                      "profiles": list(profiles)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
