"""Deterministic concept guards applied after the model's row mapping.

The model proposes which row answers which datapoint; these explicit lexical
and unit rules reject confusable concepts (component vs total, withdrawal vs
consumption, ratio vs amount, fossil vs renewable) and mark partial scopes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from esg_targeted.foundation.textnorm import fold

TOTAL_RE = re.compile(r"(?:总|總|合计|合計|综合|綜合|全部|全集团|整体|total|overall|aggregate|all\s+sources)", re.I)
COMPONENT_PREFIX_RE = re.compile(r"^\s*(?:[-–—•·▪●]|其中|of\s+which|including|\(?[a-h]\)|[ⅰ-ⅹ])", re.I)
FRESHWATER_RE = re.compile(r"(?:淡水|fresh\s*water|freshwater)", re.I)
NON_FRESH_RE = re.compile(r"(?:非淡水|non[-\s]?fresh)", re.I)
WATER_SOURCE_RE = re.compile(r"(?:地下水|地表水|市政|自来水|第三方|海水|雨水|矿井|矿坑|外购水|河|湖|groundwater|surface\s+water|municipal|third[-\s]party|sea\s*water|rain)", re.I)
WATER_DEST_RE = re.compile(r"(?:地表|地下|外送|管网|海洋|市政|surface|ground|third[-\s]party|ocean|sea)", re.I)
GENERIC_TOTAL_RE = re.compile(r"(?:total|totals|sum|overall|合计|合計|总计|總計|小计|小計|总量|總量|全部)[\s:：]*(?:\d{1,2}|[¹²³])?", re.I)
RATIO_WORD_RE = re.compile(r"(?:%|％|率|占比|比例|比率|比重|percentage|proportion|share|ratio|rate)", re.I)
INTENSITY_RE = re.compile(r"(?:强度|密度|intensity|每(?:万元|百万元|亿元|单位|吨|人|平方)|单位(?:营收|产值|产品|面积|收入|增加值)|万元(?:工业)?(?:增加值|产值|营收|收入)|per\s+(?:unit|revenue|million|employee|tonne|m2|square))", re.I)
INTENSITY_UNIT_RE = re.compile(r"(?:/|／|\bper\b|每)", re.I)
CHANGE_RE = re.compile(r"(?:减少量|下降|降低|增加量|增长|变化|同比|节约|节省|节能量|减排|避免|抵消|抵銷|reduction|decrease|increase|change|saved|savings|avoided|offset|target|目标|基准|baseline)", re.I)
# A row that only names a method or a part ("基于市场", "Location-based") takes its
# subject from the section heading above it ("范围 2 排放量").
QUALIFIER_ONLY_RE = re.compile(r"^\s*(?:[-–—•·]\s*)?(?:基于市场|基于位置|基于地点|市场法|位置法|地点法|market[\s-]*based|location[\s-]*based)"
                               r"(?:\s*(?:法|方法|method|approach))?\s*(?:\d{1,2})?\s*$", re.I)


def _rx(*terms: str) -> re.Pattern:
    return re.compile("(?:" + "|".join(terms) + ")", re.I)


SCOPE1_RE = _rx(r"范围\s*[一1](?![0-9])", r"范畴\s*[一1]", r"scope\s*1(?![0-9])", r"scope\s*i\b", r"直接(?:温室气体|GHG|碳)?排放", r"direct\s+(?:ghg\s+)?emission")
SCOPE2_RE = _rx(r"范围\s*[二2](?![0-9])", r"范畴\s*[二2]", r"scope\s*2(?![0-9])", r"scope\s*ii\b", r"(?<!其他)间接(?:温室气体|GHG|碳)?排放", r"energy\s+indirect", r"(?<!other\s)indirect\s+(?:ghg\s+)?emission")
SCOPE3_RE = _rx(r"范围\s*[三3](?![0-9])", r"范畴\s*[三3]", r"scope\s*3(?![0-9])", r"scope\s*iii\b", r"其他间接", r"other\s+indirect", r"价值链排放", r"value\s+chain\s+emission")
COMBINED_SCOPE_RE = _rx(r"(?:范围|范畴|scope)\s*[一1]\s*(?:\+|＋|和|与|及|&|and|,|、)\s*(?:范围|范畴|scope)?\s*[二2]", r"1\s*\+\s*2", r"1\s*&\s*2")
SCOPE3_CATEGORY_RE = _rx(r"类别\s*\d", r"category\s*\d", r"\bcat\.?\s*\d", r"外购商品", r"上游", r"下游", r"upstream", r"downstream", r"商务差旅", r"雇员通勤", r"purchased\s+goods", r"business\s+travel", r"commuting")
NET_RE = _rx(r"净排放", r"淨排放", r"净额", r"\bnet\s+(?:ghg\s+)?emission", r"after\s+offset")
SCOPE3_NAMED_RE = _rx(r"纸张", r"差旅", r"通勤", r"废弃物", r"废物", r"运输", r"租赁", r"paper", r"travel", r"commut",
                      r"(?<![a-z])waste", r"transport", r"leased")
GHG_UNIT_RE = _rx(r"co\s*[_{$\\]*\s*\{*\s*[2₂]", r"二氧化碳", r"碳当量", r"co2e")
GHG_LABEL_RE = _rx(r"温室气体", r"ghg", r"greenhouse", r"碳排放", r"二氧化碳", r"co2", r"carbon\s+emission")

DIRECT_AND_INDIRECT_RE = _rx(r"直接(?:及|和|与|、|\+|＋)间接", r"direct\s+(?:and|&|\+)\s+indirect")
ENERGY_TOTAL_RE = _rx(r"能源消耗", r"能源消费", r"能耗", r"能源使用", r"耗能", r"用能", r"能量消耗", r"energy\s+(?:consumption|consumed|use)", r"total\s+energy")
ENERGY_SUBSET_RE = _rx(r"直接", r"间接", r"direct", r"indirect", r"电", r"煤", r"油", r"气", r"汽", r"热", r"柴", r"electric", r"gas", r"diesel", r"coal", r"steam", r"heat", r"fuel", r"petrol", r"gasoline", r"可再生", r"清洁", r"绿色", r"非化石", r"化石", r"renewable", r"clean", r"fossil", r"外购", r"purchased", r"自产", r"self[-\s]generated", r"数据中心", r"data\s+cent", r"办公", r"office")
RENEWABLE_RE = _rx(r"可再生", r"清洁能源", r"绿色电力", r"绿电", r"非化石", r"renewable", r"clean\s+energy", r"green\s+(?:power|electricity|energy)", r"non[-\s]?fossil")
ELECTRICITY_RE = _rx(r"电力", r"电量", r"用电", r"绿电", r"electricity", r"power")
FOSSIL_RE = _rx(r"(?<!非)化石", r"(?<!non-)(?<!non )fossil", r"电网", r"grid", r"灰电")
GENERATION_RE = _rx(r"发电量", r"装机", r"产生", r"生产", r"出售", r"售出", r"上网", r"generat", r"capacity", r"installed", r"sold", r"produced", r"exported")

AIR_POLLUTANT_RE = _rx(r"氮氧化", r"(?<![a-z0-9])nox(?![a-z0-9])", r"(?<![a-z0-9])no\s*x(?![a-z0-9])", r"二氧化硫", r"so\s*2", r"so₂", r"硫氧化", r"(?<![a-z0-9])sox(?![a-z0-9])", r"(?<![a-z0-9])so\s*x(?![a-z0-9])", r"颗粒", r"\bpm\s*(?:10|2\.?5)?\b", r"烟尘", r"粉尘", r"尘", r"挥发性有机", r"(?<![a-z0-9])vocs?(?![a-z0-9])", r"一氧化碳", r"(?<![a-z0-9])co(?![a-z0-9])(?!\s*2)", r"硫化氢", r"氨气", r"氯化氢", r"氯气", r"chlorine", r"氟化氢", r"硫酸雾", r"氟化物", r"二噁英", r"苯", r"甲醛", r"非甲烷", r"(?<![a-z0-9])nmhc(?![a-z0-9])", r"汞", r"铅", r"砷", r"镉", r"铬", r"镍", r"(?<![a-z0-9])hg(?![a-z0-9])", r"(?<![a-z0-9])pb(?![a-z0-9])", r"mercury", r"lead", r"arsenic", r"particulate", r"dust", r"sulph|sulfur", r"nitrogen\s+oxide", r"消耗臭氧", r"(?<![a-z0-9])ods(?![a-z0-9])", r"废气污染物", r"大气污染物", r"air\s+(?:pollutant|emission)")
WATER_POLLUTANT_RE = _rx(r"化学需氧量", r"(?<![a-z0-9])cod(?![a-z0-9])", r"生化需氧量", r"(?<![a-z0-9])bod(?![a-z0-9])", r"氨氮", r"nh\s*3\s*-?\s*n", r"总氮", r"总磷", r"石油类", r"悬浮物", r"(?<![a-z0-9])ss(?![a-z0-9])", r"氰化物", r"氟化物", r"硫化物", r"挥发酚", r"重金属", r"铜", r"锌", r"铅", r"镉", r"汞", r"砷", r"铬", r"镍", r"ammonia", r"chemical\s+oxygen", r"nitrogen", r"phosph", r"heavy\s+metal", r"水污染物", r"废水污染物")
AIR_ONLY_RE = _rx(r"氮氧化", r"(?<![a-z0-9])nox(?![a-z0-9])", r"二氧化硫", r"so\s*2", r"硫氧化", r"(?<![a-z0-9])sox(?![a-z0-9])", r"颗粒", r"烟尘", r"粉尘", r"挥发性有机", r"(?<![a-z0-9])vocs?(?![a-z0-9])", r"硫酸雾", r"一氧化碳", r"particulate", r"dust")
WATER_ONLY_RE = _rx(r"化学需氧量", r"(?<![a-z0-9])cod(?![a-z0-9])", r"生化需氧量", r"(?<![a-z0-9])bod(?![a-z0-9])", r"氨氮", r"总磷", r"总氮", r"悬浮物", r"石油类")
POLLUTANT_EXCLUDE_RE = _rx(r"浓度", r"concentration", r"mg\s*/", r"达标", r"compliance", r"去除", r"removal", r"减排", r"reduction", r"温室", r"ghg", r"二氧化碳(?!硫)", r"\bco\s*2\b", r"co₂", r"废水量", r"废水排放量", r"wastewater\s+(?:volume|discharge)")

SUBSTANCE_RE = _rx(r"关注物质", r"高度关注", r"高关注", r"svhc", r"substances?\s+of\s+(?:very\s+high\s+)?concern", r"有毒有害物质", r"有害物质", r"危险化学品", r"hazardous\s+substance")
WASTE_RE = _rx(r"废物", r"废弃物", r"waste")

CONSUMPTION_RE = _rx(r"耗水", r"(?:水|water)\S{0,4}消耗", r"消耗\S{0,4}水", r"水资源消耗", r"water\s+consum", r"consumption\s+of\s+water", r"consumed")
RISK_AREA_RE = _rx(r"风险", r"缺水", r"水压力", r"水资源压力", r"高水", r"stress", r"scarc", r"risk")
WITHDRAWAL_RE = _rx(r"取水", r"withdraw", r"abstract", r"新鲜水", r"新水", r"用水总量", r"总用水量", r"用水量", r"water\s+(?:intake|use)", r"water\s+withdrawn")
WATER_USE_ONLY_RE = _rx(r"用水", r"water\s+use")
RECYCLE_RE = _rx(r"循环", r"回用", r"重复利用", r"再利用", r"再生水", r"中水", r"recycl", r"reus", r"reclaim",
                 r"cycl(?:ing|ed)\s+(?:use|water)", r"recirculat")
DISCHARGE_RE = _rx(r"排水", r"废水排放", r"污水排放", r"排放(?:的)?废水", r"discharg", r"effluent")
WATER_WORD_RE = _rx(r"水", r"water", r"h2o", r"effluent")
# Things other than water that are also "reused" or "recycled" (equipment, waste, packaging).
NOT_WATER_RE = _rx(r"设备", r"废弃物", r"废物", r"固废", r"材料", r"物料", r"包装", r"电池", r"电子", r"产品", r"纸",
                   r"equipment", r"device", r"waste", r"material", r"packag", r"product", r"batter", r"paper")


@dataclass
class GuardResult:
    ok: bool
    status: str | None = None  # None keeps the bind status; "review" demotes
    flags: tuple[str, ...] = ()


def _reject(flag: str) -> GuardResult:
    return GuardResult(False, "rejected", (flag,))


MARKUP_RE = re.compile(r"[$\\{}^]|_(?=[{\w])|\\(?:mathrm|text|operatorname)")


OCR_SUBSCRIPT_LOSS_RE = re.compile(r"^(no|so)\s*[,，.]$")


def normalize(text: str | None) -> str:
    """Folded text with OCR math markup removed: `$ SO_{{2}} $` -> `so2`.

    A pollutant label printed as "NO," / "SO," lost its subscript x in OCR.
    """
    folded = fold(text)
    folded = re.sub(r"\$\s*([^$]{1,40}?)\s*\$", lambda m: MARKUP_RE.sub("", m.group(1)).replace(" ", ""), folded)
    parts = [OCR_SUBSCRIPT_LOSS_RE.sub(r"\1x", part.strip()) for part in MARKUP_RE.sub("", folded).split(" › ")]
    return " › ".join(parts)


def check(code: str, label: str, section: str | None, unit: str | None, dimension: str | None) -> GuardResult:
    """Apply concept rules for one datapoint to one mapped source row."""
    text = normalize(label)
    section = normalize(section)
    unit = normalize(unit) if unit else unit
    scope_text = f"{section or ''} {text}"
    flags: list[str] = []
    review = False
    last = text.split(" › ")[-1]
    # A bare total or method-only row takes its subject from the section and table title.
    subject = scope_text if GENERIC_TOTAL_RE.fullmatch(last.strip()) or QUALIFIER_ONLY_RE.match(last) else text
    component = bool(COMPONENT_PREFIX_RE.match(last))
    other_item = bool(re.search(r"(?:其他|其它|^other\b|others)", last, re.I))
    if unit and INTENSITY_UNIT_RE.search(unit) and code != "E1-5_09":
        return _reject("unit_is_intensity")

    if code == "E1-5_01":
        if not ENERGY_TOTAL_RE.search(scope_text) and not TOTAL_RE.search(text):
            return _reject("label_not_total_energy_consumption")
        # "direct and indirect" together is the whole; either alone is a part.
        whole = DIRECT_AND_INDIRECT_RE.sub(" ", text)
        if ENERGY_SUBSET_RE.search(whole) and not re.search(r"(?:总能|能源总|能耗总|总耗能|综合能耗|total\s+energy)", text, re.I):
            return _reject("label_is_energy_component_or_carrier")
        if RATIO_WORD_RE.search(text) or INTENSITY_RE.search(text) or CHANGE_RE.search(text):
            return _reject("label_is_ratio_intensity_or_change")
        if component or other_item:
            return _reject("component_row")
        if dimension not in (None, "energy"):
            return _reject(f"unit_dimension_{dimension}_not_energy")
    elif code == "E1-5_05":
        if not RENEWABLE_RE.search(subject):
            return _reject("label_not_renewable")
        if (component or other_item) and not TOTAL_RE.search(last):
            return _reject("component_row")
        if re.search(r"采购|购买|购入|purchas|现场|自发|自建|自产|on[-\s]?site|self[-\s]?generat", text) and not re.search(
                r"消耗|消费|使用|用量|consum|\buse", text):
            return _reject("renewable_source_component")
        if RATIO_WORD_RE.search(text) or GENERATION_RE.search(text) or INTENSITY_RE.search(text) or CHANGE_RE.search(text):
            return _reject("label_is_ratio_generation_or_change")
        if FOSSIL_RE.search(text):
            return _reject("label_mentions_fossil_or_grid")
        if dimension not in (None, "energy"):
            return _reject(f"unit_dimension_{dimension}_not_energy")
        if ELECTRICITY_RE.search(text):
            flags.append("renewable_electricity_only")
            review = True
        if re.search(r"清洁|clean", text) and not re.search(r"可再生|renewable", text):
            flags.append("clean_energy_may_include_non_renewable")
            review = True
    elif code == "E1-5_09":
        if not RENEWABLE_RE.search(subject):
            return _reject("label_not_renewable")
        if FOSSIL_RE.search(text):
            return _reject("label_mentions_fossil_or_grid")
        if GENERATION_RE.search(text) and not re.search(r"(?:消耗|消费|使用|用能|consum|use)", text, re.I):
            return _reject("label_is_generation_share")
        if dimension not in (None, "ratio") or not (dimension == "ratio" or RATIO_WORD_RE.search(text)):
            return _reject("not_a_ratio")
        if ELECTRICITY_RE.search(text) and not re.search(r"(?:能源|energy)", text, re.I):
            flags.append("renewable_share_of_electricity_only")
            review = True
        if re.search(r"清洁|clean", text) and not re.search(r"可再生|renewable", text):
            flags.append("clean_energy_may_include_non_renewable")
            review = True
    elif code in ("E1-6_07", "E1-6_09", "E1-6_10", "SCOPE2"):
        wanted = SCOPE1_RE if code == "E1-6_07" else SCOPE2_RE
        if not wanted.search(subject):
            return _reject("label_not_this_scope")
        if COMBINED_SCOPE_RE.search(subject):
            return _reject("label_is_combined_scopes")
        if code != "E1-6_07" and SCOPE3_RE.search(text):
            return _reject("label_is_scope3")
        if INTENSITY_RE.search(text) or CHANGE_RE.search(text) or RATIO_WORD_RE.search(text):
            return _reject("label_is_intensity_ratio_or_change")
        if NET_RE.search(text):
            return _reject("label_is_net_after_offsets")
        if unit and dimension != "ghg-emissions" and not GHG_UNIT_RE.search(unit):
            if dimension == "mass" and GHG_LABEL_RE.search(scope_text):
                flags.append("co2e_implied_by_label_mass_unit")
                review = True
            else:
                return _reject("unit_not_co2e")
    elif code == "E1-6_11":
        if not SCOPE3_RE.search(subject if subject is not text else scope_text):
            return _reject("label_not_scope3")
        if SCOPE3_CATEGORY_RE.search(text):
            return _reject("label_is_scope3_category")
        if COMBINED_SCOPE_RE.search(text) or INTENSITY_RE.search(text) or CHANGE_RE.search(text) or RATIO_WORD_RE.search(text):
            return _reject("label_is_combined_intensity_ratio_or_change")
        if NET_RE.search(text):
            return _reject("label_is_net_after_offsets")
        if SCOPE3_NAMED_RE.search(text) and not TOTAL_RE.search(text):
            flags.append("scope3_limited_to_named_categories")
            review = True
        if unit and dimension != "ghg-emissions" and not GHG_UNIT_RE.search(unit):
            if dimension == "mass" and GHG_LABEL_RE.search(scope_text):
                flags.append("co2e_implied_by_label_mass_unit")
                review = True
            else:
                return _reject("unit_not_co2e")
    elif code in ("E2-4_02", "E2-4_03"):
        allowed = AIR_POLLUTANT_RE if code == "E2-4_02" else WATER_POLLUTANT_RE
        other_only = WATER_ONLY_RE if code == "E2-4_02" else AIR_ONLY_RE
        if not allowed.search(subject):
            return _reject("label_not_a_pollutant_for_medium")
        if other_only.search(text) and not (AIR_ONLY_RE if code == "E2-4_02" else WATER_ONLY_RE).search(text):
            return _reject("pollutant_belongs_to_other_medium")
        if POLLUTANT_EXCLUDE_RE.search(text) or INTENSITY_RE.search(text) or CHANGE_RE.search(text):
            return _reject("label_is_concentration_intensity_ghg_or_change")
        if dimension == "ratio":
            flags.append("pollutant_unit_printed_as_percent")
            review = True
        elif dimension not in (None, "mass"):
            return _reject(f"unit_dimension_{dimension}_not_mass")
    elif code == "E2-5_02":
        if not SUBSTANCE_RE.search(scope_text):
            return _reject("label_not_substances_of_concern")
        if WASTE_RE.search(text):
            return _reject("label_is_waste")
        if RATIO_WORD_RE.search(text) or INTENSITY_RE.search(text):
            return _reject("label_is_ratio_or_intensity")
    elif code.startswith("E3-4"):
        if INTENSITY_RE.search(text) or RATIO_WORD_RE.search(text) or CHANGE_RE.search(text):
            return _reject("label_is_ratio_intensity_or_change")
        if NOT_WATER_RE.search(text) and not WATER_WORD_RE.search(text):
            return _reject("label_not_about_water")
        if dimension not in (None, "volume", "mass"):
            return _reject(f"unit_dimension_{dimension}_not_volume")
        if code == "E3-4_01":
            if not CONSUMPTION_RE.search(subject):
                return _reject("label_not_water_consumption")
            if WITHDRAWAL_RE.search(text) and not CONSUMPTION_RE.search(text):
                return _reject("label_is_withdrawal")
            if RISK_AREA_RE.search(text):
                return _reject("label_is_risk_area_subset")
            if DISCHARGE_RE.search(text) or RECYCLE_RE.search(text):
                return _reject("label_is_discharge_or_recycling")
        elif code == "E3-4_02":
            if not (CONSUMPTION_RE.search(scope_text) and RISK_AREA_RE.search(scope_text)):
                return _reject("label_not_water_consumption_in_risk_areas")
            if re.search(r"取水|withdraw", text, re.I):
                return _reject("label_is_withdrawal_in_risk_areas")
        elif code == "E3-4_03":
            if not RECYCLE_RE.search(subject):
                return _reject("label_not_recycled_or_reused_water")
        elif code == "E3-4_11":
            if not WITHDRAWAL_RE.search(subject):
                return _reject("label_not_water_withdrawal")
            if DISCHARGE_RE.search(text) or RECYCLE_RE.search(text) or RISK_AREA_RE.search(text) or CONSUMPTION_RE.search(text):
                return _reject("label_is_other_water_concept")
            if WATER_USE_ONLY_RE.search(text) and not re.search(r"取水|withdraw|abstract", text, re.I):
                flags.append("water_use_reported_as_withdrawal")
                review = True
        elif code == "E3-4_12":
            if not DISCHARGE_RE.search(scope_text):
                return _reject("label_not_water_discharge")
            if RISK_AREA_RE.search(text) or RECYCLE_RE.search(text):
                return _reject("label_is_other_water_concept")
        if code in ("E3-4_01", "E3-4_11", "E3-4_12", "E3-4_03"):
            qualifier = (FRESHWATER_RE.search(text) or NON_FRESH_RE.search(text) or WATER_SOURCE_RE.search(text)
                         or (code == "E3-4_12" and WATER_DEST_RE.search(text)))
            if component and not TOTAL_RE.search(text):
                return _reject("component_row")
            if qualifier:
                if TOTAL_RE.search(text) or CONSUMPTION_RE.search(text):
                    flags.append("subset_scope_" + ("freshwater" if FRESHWATER_RE.search(text) else "source_or_destination"))
                    review = True
                else:
                    return _reject("component_by_source_or_destination")
    else:
        # No bespoke branch for this code: fall back to its declared spec, so a
        # datapoint outside the sixteen still gets concept and unit checks
        # instead of passing unconditionally.
        from esg_targeted.foundation.specs import evaluate_spec
        declared = evaluate_spec(code, label, section, unit, dimension)
        if declared is not None:
            ok, reason = declared
            if not ok:
                return _reject(reason or "concept")
    return GuardResult(True, "review" if review else None, tuple(flags))


POLLUTANT_SYNONYMS = (
    ("NOx", ("氮氧化", "nox", "no x", "nitrogen oxide")),
    ("SOx", ("二氧化硫", "硫氧化", "so2", "so₂", "sox", "so x", "sulphur", "sulfur")),
    ("PM", ("颗粒", "烟尘", "粉尘", "particulate", "pm10", "pm2.5", "pm ", "dust")),
    ("VOCs", ("挥发性有机", "voc", "非甲烷")),
    ("CO", ("一氧化碳",)),
    ("H2S", ("硫化氢",)),
    ("NH3", ("氨气",)),
    ("HCl", ("氯化氢", "hydrogen chloride")),
    ("Cl2", ("氯气", "chlorine")),
    ("Fluoride", ("氟化物", "fluoride")),
    ("HF", ("氟化氢", "hydrogen fluoride")),
    ("H2SO4 mist", ("硫酸雾",)),
    ("COD", ("化学需氧量", "cod")),
    ("BOD", ("生化需氧量", "bod")),
    ("NH3-N", ("氨氮", "ammonia nitrogen")),
    ("TN", ("总氮", "total nitrogen")),
    ("TP", ("总磷", "total phosph")),
    ("Hg", ("汞", "mercury", "hg)")),
    ("Pb", ("铅", "lead", "pb)")),
    ("As", ("砷", "arsenic")),
    ("Cd", ("镉", "cadmium")),
    ("Cr", ("铬", "chromium")),
    ("Cu", ("总铜", "铜", "copper")),
    ("Zn", ("总锌", "锌", "zinc")),
    ("Ni", ("镍", "nickel")),
)


def pollutant_key(label: str | None) -> str:
    text = normalize((label or "").split(" › ")[-1])
    if re.fullmatch(r"\s*pm\s*(?:10|2\.5)?\s*", text):
        return "PM"
    for key, tokens in POLLUTANT_SYNONYMS:
        if any(token in text for token in tokens):
            return key
    return re.sub(r"[\s\-–—•·:：()（）\d.、]", "", text) or "unnamed"


ROW_SUBSET_RE = re.compile(
    r"(?:数据中心|生产基地|基地|工厂|厂区|园区|办公(?:楼|室|场所)|分公司|子公司|事业部|板块|门店|电站|自有|租赁|境内|境外|海外|"
    r"\bdata\s+cent|\bproduction\s+base|\bplants?\b|\bsites?\b|\boffices?\b|\bsubsidiar|\bsegments?\b|\bdomestic\b|"
    r"\boverseas\b|\bleased\b|\bowned\b)",
    re.I,
)
POLLUTANT_SPECIFIC_RE = re.compile(
    r"(?:氮氧化|二氧化硫|硫氧化|颗粒|烟尘|粉尘|挥发性有机|化学需氧量|氨氮|总磷|总氮|重金属|(?<![a-z0-9])(?:cod|nox|sox|so2|vocs?|pm10|pm2\.5|bod)(?![a-z0-9])|particulate|ammonia)",
    re.I,
)
POLLUTION_METHOD_RE = re.compile(
    r"(?:污染物|废气|废水|大气|排放物|pollutant|air\s+emission|effluent|wastewater"
    r"|排放量.{0,10}(?:公式|估算|测算))", re.I,
)
GHG_CONTEXT_RE = re.compile(r"(?:温室气体|二氧化碳|碳排放|greenhouse|(?<![a-z])co2|co₂|ghg|carbon)", re.I)


def row_is_subset(label: str | None) -> bool:
    return bool(ROW_SUBSET_RE.search(fold((label or "").split(" › ")[-1])))


METHOD_TERM_RE = re.compile(r"(?:监测|检测|在线|测量|测算|估算|计算|核算|排放因子|物料衡算|系数|浓度.{0,8}流量|monitor|measur|estimat|calculat|emission\s+factor|mass\s+balance)", re.I)


def is_method_text(text: str | None) -> bool:
    """Says how amounts are measured or estimated, and is not a GHG methodology note."""
    folded = normalize(text)
    return bool(METHOD_TERM_RE.search(folded)) and not GHG_CONTEXT_RE.search(folded)


def is_pollution_method_text(text: str | None) -> bool:
    folded = normalize(text)
    if not METHOD_TERM_RE.search(folded):
        return False
    if POLLUTANT_SPECIFIC_RE.search(folded):
        return True
    return bool(POLLUTION_METHOD_RE.search(folded)) and not GHG_CONTEXT_RE.search(folded)
