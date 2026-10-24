"""Deterministic concept guards applied after the model's row mapping.

The model proposes which row answers which datapoint; these explicit lexical
and unit rules reject confusable concepts (component vs total, withdrawal vs
consumption, ratio vs amount, fossil vs renewable) and mark partial scopes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from esg_targeted.foundation.catalog import BY_CODE
from esg_targeted.foundation.textnorm import fold

TOTAL_RE = re.compile(r"(?:总|總|合计|合計|综合|綜合|全部|全集团|整体|total|overall|aggregate|all\s+sources)", re.I)
COMPONENT_PREFIX_RE = re.compile(r"^\s*(?:[-–—•·▪●]|其中|of\s+which|including|\(?[a-h]\)|[ⅰ-ⅹ])", re.I)
FRESHWATER_RE = re.compile(r"(?:淡水|新水|新鲜水|取新水|fresh\s*water|freshwater)", re.I)
NON_FRESH_RE = re.compile(r"(?:非淡水|non[-\s]?fresh)", re.I)
WATER_SOURCE_RE = re.compile(r"(?:地下水|地表水|市政|自来水|第三方|海水|雨水|矿井|矿坑|外购水|河|湖|groundwater|surface\s+water|municipal|third[-\s]party|sea\s*water|rain)", re.I)
WATER_DEST_RE = re.compile(r"(?:地表|地下|外送|管网|海洋|市政|surface|ground|third[-\s]party|ocean|sea|marine|sewage|sewer|river|public\s+sewer|municipal)", re.I)
GENERIC_TOTAL_RE = re.compile(r"(?:total|totals|sum|overall|合计|合計|总计|總計|小计|小計|总量|總量|全部)[\s:：]*(?:\d{1,2}|[¹²³])?", re.I)
RATIO_WORD_RE = re.compile(r"(?:%|％|率|占比|比例|比率|比重|percentage|proportion|share|ratio|rate)", re.I)
INTENSITY_RE = re.compile(r"(?:强度|密度|intensity|每(?:万元|百万元|亿元|单位|吨|人|平方)|单位(?:营收|产值|产品|面积|收入|增加值)|万元(?:工业)?(?:增加值|产值|营收|收入)|per\s+(?:unit|revenue|million|employee|tonne|m2|square))", re.I)
INTENSITY_UNIT_RE = re.compile(r"(?:/|／|\bper\b|每)", re.I)
CHANGE_RE = re.compile(r"(?:减少量|下降|降低|增加量|增长|变化|同比|节约|节省|节能量|减排|避免|抵消|抵銷|reduction|decrease|increase|change|saved|savings|avoided|offset|target|目标|基准|baseline)", re.I)
# Progress/target tables print "较 2020 年下降 52%" or "到 2030 年实现…" beside
# current-year columns. Such a row is a plan or a delta, never the amount the
# clause asks for, whichever clause it was mapped to.
RELATIVE_CHANGE_OR_TARGET_RE = re.compile(
    r"(?:相比|较|比|compared\s+(?:with|to))\s*(?:19|20)\d{2}\s*年?[^。；;]{0,24}?"
    r"(?:下降|降低|减少|增加|增长|提升)"
    r"|(?:下降|降低|减少|增加|增长|提升)\s*\d+(?:\.\d+)?\s*[%％]"
    r"|(?:下降率|降低率|减少率|增长率|变化率|降幅|增幅)"
    r"|(?:到|至|by)\s*(?:19|20)\d{2}\s*年?[^。；;]{0,20}?(?:目标|计划|规划|实现|达成|承诺)",
    re.I)
# A long interrogative label is a disclosure description beside a page number
# (content indexes print "how the board oversees climate…"), not a value row.
NARRATIVE_QUESTION_RE = re.compile(r"(?:如何|是否|怎样|為何|为什么|何时|how\s+|why\s+)", re.I)
# Management levels below top management. A workforce-gender row about them is
# a related measure, not the top-management distribution the clause asks for.
LOWER_MANAGEMENT_RE = re.compile(
    r"(?:初级管理|中级管理|基层管理|一线管理|非管理层|创收职能|"
    r"entry[-\s]?level|middle\s+management|first[-\s]?line|non[-\s]?management)",
    re.I)
TOP_MANAGEMENT_RE = re.compile(
    r"(?:高级管理|最高管理|执行管理|高管|top\s+management|senior\s+management|executive)", re.I)
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
# A regulatory limit, permit or quota is a different quantity from the actual
# amount, even when a report prints both in one table: Chinese permits print
# "核定的年度二氧化硫排放总量" next to "外排废气中二氧化硫量". Treating the
# permit as a rival value turns an otherwise correct extraction into a conflict.
# GRI/ESRS index rows point at a section; the number in them is a page or a
# clause number, never the disclosed figure.
INDEX_ENTRY_RE = _rx(r"gri\s*\d{1,3}", r"disclosure\s*\d{2,3}", r"指标\s*\d{3}", r"esrs\s+[a-z]\d",
                      # A content index points at a page; it is not a value.
                      # GRI ("GRI 3：实质性议题"), HKEX ("层面A1", "关键绩效指标
                      # A1.3") and "一般披露" rows all print a page number.
                      r"实质性议题", r"一般披露", r"关键绩效指标\s*[a-z]?\d", r"层面\s*[a-z]\d",
                      r"(?:^|[\s（(])\d{1,3}[-–]\d{1,2}(?![\d.%])"
                      r"(?!\s*(?:岁|年|月|日|吨|噸|人|小时|小時|万元|%|％))")
REGULATORY_LIMIT_RE = _rx(
    r"核定(?:的)?[^，。；]{0,20}(?:总量|排放量|量|配额|额度)", r"许可[^，。；]{0,16}排放",
    r"排污许可", r"排放许可证",
    r"排放(?:配额|额度|限额|上限)", r"允许排放", r"总量控制指标",
    r"(?:approved|permitted|allowed)\s+(?:annual\s+)?(?:emission|discharge)",
    r"(?:emission|discharge)\s+(?:limit|cap|allowance|quota|ceiling)",
    r"(?:regulatory|permitted)\s+limit",
)
POLLUTANT_EXCLUDE_RE = _rx(r"浓度", r"concentration", r"mg\s*/", r"达标", r"compliance", r"去除", r"removal", r"减排", r"reduction", r"温室", r"ghg", r"二氧化碳(?!硫)", r"\bco\s*2\b", r"co₂", r"废水量", r"废水排放量", r"wastewater\s+(?:volume|discharge)")

# ---- vocabulary for the extended priority set (concept families, not reports)
FOSSIL_FUEL_RE = _rx(r"化石", r"煤炭", r"原煤", r"煤", r"石油", r"原油", r"汽油", r"柴油", r"燃料油", r"天然气", r"液化石油气",
                     r"fossil", r"coal", r"petroleum", r"crude\s*oil", r"gasoline", r"diesel", r"natural\s+gas", r"lpg")
HAZARDOUS_RE = _rx(r"有害", r"危险废物", r"危险废弃物", r"hazardous")
WASTE_DEST_RE = _rx(r"处置", r"处理", r"回收", r"填埋", r"焚烧", r"利用", r"dispos", r"treat", r"divert", r"recycl", r"landfill", r"incinerat")
SINGLE_FUEL_RE = _rx(r"天然气", r"natural\s+gas", r"柴油", r"diesel", r"汽油", r"petrol", r"gasoline", r"煤油", r"kerosene",
                     r"液化石油气", r"lpg", r"合成天然气", r"synthetic\s+natural\s+gas", r"sng", r"填埋气", r"landfill\s+gas",
                     r"燃料油", r"fuel\s+oil", r"(?<![a-z])coal(?![a-z])", r"煤炭", r"原煤",
                     r"原油", r"石油", r"crude\s*oil", r"petroleum")
MATERIAL_RE = _rx(r"原材料", r"物料", r"材料", r"资源投入", r"materials?", r"resource\s+inflow")
EMPLOYEE_RE = _rx(r"员工", r"雇员", r"职工", r"从业人数", r"employee", r"staff")
TURNOVER_RE = _rx(r"流失率", r"离职率", r"turnover")
AVERAGE_RE = _rx(r"平均", r"average", r"mean")
TRAINING_RE = _rx(r"培训", r"受训", r"training", r"trained")
TOPIC_TRAINING_RE = _rx(r"商业道德", r"反腐败", r"合规", r"信息安全", r"数据安全", r"隐私", r"网络安全", r"ehs", r"安全培训", r"领导力",
                        r"管理层", r"新员工", r"供应商", r"ethics", r"compliance", r"leadership", r"contractor")
WHOLE_WORKFORCE_RE = _rx(r"全员", r"全体员工", r"全部员工", r"员工人均", r"人均受训", r"all\s+employees",
                         r"per\s+employee", r"workforce")
CONSOLIDATION_BOUNDARY_RE = _rx(r"运营范围", r"运营边界", r"财务控制", r"operational\s+control",
                                r"operational\s+boundary", r"financial\s+control")
HOURS_RE = _rx(r"小时", r"学时", r"课时", r"时长", r"时数", r"hours?")
SAFETY_RE = _rx(r"工伤", r"事故", r"accident", r"injur", r"ltifr", r"trifr")
PAYGAP_RE = _rx(r"薪酬差距", r"薪酬差异", r"男女薪酬", r"gender\s+pay\s+gap", r"pay\s+gap")
REMUN_RATIO_RE = _rx(r"薪酬比", r"薪酬倍数", r"总薪酬比", r"remuneration\s+ratio", r"pay\s+ratio")
DISCRIM_RE = _rx(r"歧视", r"骚扰", r"discrimination", r"harassment")
GENDER_RE = _rx(r"性别", r"女性", r"男性", r"female", r"male", r"gender")
TOPMGMT_RE = _rx(r"高管", r"高级管理", r"管理层", r"管理者", r"top\s+management", r"senior\s+management")
BOARD_RE = _rx(r"董事会", r"女性董事", r"董事", r"治理机构", r"board", r"supervisory")
CORRUPTION_RE = _rx(r"腐败", r"贿赂", r"corruption", r"bribery")
CONVICTION_RE = _rx(r"定罪", r"判决", r"判定有罪", r"conviction", r"convicted")
RATE_UNIT_RE = re.compile(r"[/／]|\bper\b|每|%|％|人次|work-hours", re.I)
PAYDAYS_RE = _rx(r"付款", r"支付", r"账期", r"payment")
DAYS_RE = _rx(r"天", r"日", r"days?")

SUBSTANCE_RE = _rx(r"关注物质", r"高度关注", r"高关注", r"svhc", r"substances?\s+of\s+(?:very\s+high\s+)?concern", r"有毒有害物质", r"有害物质", r"危险化学品", r"hazardous\s+substance")
WASTE_RE = _rx(r"废物", r"废弃物", r"固废", r"垃圾", r"waste")

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
    # Applied before every concept rule: no disclosure of an actual amount is a
    # permit, quota or regulatory ceiling, whatever metric it is mapped to.
    if not code.startswith("E1-8") and REGULATORY_LIMIT_RE.search(text):
        return _reject("label_is_regulatory_limit_not_actual")
    if INDEX_ENTRY_RE.search(text):
        return _reject("label_is_index_entry")
    if len(text) >= 24 and NARRATIVE_QUESTION_RE.search(text):
        return _reject("label_is_narrative_question")
    if RELATIVE_CHANGE_OR_TARGET_RE.search(text):
        return _reject("label_is_target_or_relative_change")
    review = False
    last = text.split(" › ")[-1]
    # A bare total or method-only row takes its subject from the section and table title.
    subject = scope_text if GENERIC_TOTAL_RE.fullmatch(last.strip()) or QUALIFIER_ONLY_RE.match(last) else text
    component = bool(COMPONENT_PREFIX_RE.match(last))
    other_item = bool(re.search(r"(?:其他|其它|^other\b|others)", last, re.I))
    # An intensity unit belongs only to a metric declared as an intensity; the
    # rule follows the catalog rather than naming codes here.
    declared = BY_CODE.get(code)
    intensity_metric = bool(declared and ((declared.dimension or "").endswith("intensity") or declared.dimension == "ratio"))
    # Judge by the resolved dimension: a unit like "m3 (m3 per employee)" prints
    # a conversion note, not a denominator of its own.
    if unit and (dimension or "").endswith("intensity") and code != "E1-5_09" and not intensity_metric:
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
        if SCOPE3_CATEGORY_RE.search(text) or re.search(r"other\s+categor|categor(?:y|ies)\b", text, re.I):
            return _reject("label_is_scope3_category")
        if COMBINED_SCOPE_RE.search(text) or INTENSITY_RE.search(text) or CHANGE_RE.search(text) or RATIO_WORD_RE.search(text):
            return _reject("label_is_combined_intensity_ratio_or_change")
        if NET_RE.search(text):
            return _reject("label_is_net_after_offsets")
        if SCOPE3_NAMED_RE.search(text) and not TOTAL_RE.search(text):
            # A named category (waste paper, business travel) is a component of
            # Scope 3, never the total.
            return _reject("label_is_scope3_category")
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
            # Reclaimed water taken in as an alternative source is a supply
            # choice; the clause asks for the volume the company recycled or
            # reused. Keep the value, but as a reviewed reading.
            if re.search(r"替代水源|替代水|alternative\s+(?:water\s+)?source", text, re.I) \
                    and re.search(r"使用|用量|consum|\buse", text, re.I):
                flags.append("reclaimed_water_as_alternative_source")
                review = True
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
    elif code == "E1-5_02":
        if RATIO_WORD_RE.search(text) or INTENSITY_RE.search(text):
            return _reject("label_is_ratio_or_intensity")
        if not FOSSIL_FUEL_RE.search(subject):
            return _reject("label_not_fossil_energy")
        # One fuel is a component of fossil energy, not the total.
        if SINGLE_FUEL_RE.search(text) and not TOTAL_RE.search(text):
            return _reject("label_is_fossil_carrier")
        if RECYCLE_RE.search(text) or CHANGE_RE.search(text):
            return _reject("label_is_change_or_offset")
        if dimension not in (None, "energy"):
            return _reject(f"unit_dimension_{dimension}_not_energy")
    elif code == "E1-6_12":
        if INTENSITY_RE.search(text) or RATIO_WORD_RE.search(text) or CHANGE_RE.search(text):
            return _reject("label_is_ratio_intensity_or_change")
        # The total must name emissions and be a total: an unrelated row with an
        # unrecognised unit can otherwise pass every other check.
        if not re.search(r"温室气体|温室氣體|碳排放|二氧化碳|排放|ghg|co2", text, re.I):
            return _reject("label_not_ghg_emissions")
        scopes = sum(bool(item.search(text)) for item in (SCOPE1_RE, SCOPE2_RE, SCOPE3_RE))
        if scopes == 1 and not TOTAL_RE.search(text):
            return _reject("label_is_single_scope_not_total")
        # The same total under another consolidation boundary (operational
        # control vs financial control) is an alternative view, not a rival value.
        if CONSOLIDATION_BOUNDARY_RE.search(text):
            flags.append("consolidation_boundary_differs")
            review = True
        if dimension not in (None, "ghg-emissions"):
            return _reject("unit_not_co2e")
    elif code == "E1-6_30":
        if not (INTENSITY_RE.search(text) or INTENSITY_UNIT_RE.search(unit or "")):
            return _reject("label_not_intensity")
        if dimension not in (None, "ghg-intensity"):
            return _reject(f"unit_dimension_{dimension}_not_ghg_intensity")
    elif code == "E5-5_07":
        if not WASTE_RE.search(subject):
            return _reject("label_not_waste")
        # A category (hazardous / non-hazardous / general industrial / construction)
        # is a component of the total, not the total itself.
        if re.search(r"一般|无害|無害|生活|建筑|建築|危险|危險|有害", text):
            return _reject("label_is_waste_category")
        # "固体废弃物量" without a total word is the solid-waste stream, not the
        # overall generated total (a report also prints hazardous and liquid).
        if re.search(r"固废|固体废弃物|固体废物", text) and not TOTAL_RE.search(text):
            return _reject("label_is_waste_category")
        if RATIO_WORD_RE.search(text) or INTENSITY_RE.search(text):
            return _reject("label_is_ratio_or_intensity")
        if HAZARDOUS_RE.search(text) and not TOTAL_RE.search(text):
            return _reject("label_is_hazardous_subset")
        if WASTE_DEST_RE.search(text) and not re.search(r"产生|生成|generated|generation", text, re.I):
            return _reject("label_is_treatment_route_not_generated")
        if dimension not in (None, "mass"):
            return _reject(f"unit_dimension_{dimension}_not_mass")
    elif code == "E5-5_15":
        if not (WASTE_RE.search(subject) and HAZARDOUS_RE.search(text)):
            return _reject("label_not_hazardous_waste")
        if re.search(r"无害|非有害|non-hazardous", text, re.I) and not re.search(r"有害|危险", text):
            return _reject("label_is_non_hazardous_waste")
        if WASTE_DEST_RE.search(text) and not re.search(r"产生|生成|generated|generation", text, re.I):
            return _reject("label_is_treatment_route_not_generated")
        if dimension not in (None, "mass"):
            return _reject(f"unit_dimension_{dimension}_not_mass")
    elif code == "E5-4_02":
        immediate = text.split("，")[-1] if "，" in text else text
        if WASTE_RE.search(immediate):
            return _reject("label_is_waste_not_inflow")
        if not MATERIAL_RE.search(subject):
            return _reject("label_not_materials_used")
        if RATIO_WORD_RE.search(text) or INTENSITY_RE.search(text):
            return _reject("label_is_ratio_or_intensity")
        # A hazardous/toxic portion of the material inflow is a subset of the
        # total weight, even when the report calls it a total.
        if re.search(r"有毒|有害|危險|危险|hazardous|toxic", text, re.I):
            return _reject("label_is_hazardous_materials_subset")
        if dimension not in (None, "mass"):
            return _reject(f"unit_dimension_{dimension}_not_mass")
    elif code == "S1-6_02":
        if re.search(r"非员工|非雇员|劳务派遣|外包|non-employee|contractor", text, re.I):
            return _reject("label_is_non_employees")
        # A subgroup count (minority, disabled, women, expatriates, local staff)
        # is a breakdown of the workforce, not the headcount.
        if re.search(r"少数民族|少数族裔|残疾|殘疾|女性员工|男性员工|外籍|外派|本地员工|退伍|复员", text, re.I):
            return _reject("label_is_population_subset")
        if TURNOVER_RE.search(text) or re.search(r"入职率|新入职|新进员工|招聘人数|离职|解雇|解僱|hiring|new hires|new employees|leavers?|dismiss", text, re.I):
            return _reject("label_is_turnover_or_hiring")
        if AVERAGE_RE.search(text):
            return _reject("label_is_average_not_period_end")
        # Person-times ("人次") count participation, never people.
        if "人次" in text or re.search(r"参与|参加|演练|演練|活动人数|person[-\s]?times", text, re.I):
            return _reject("label_is_participation_count_not_headcount")
        # An accident/incident count is a safety event, not a head count.
        if re.search(r"事故|事件|工伤|工傷|伤亡|傷亡|accident|incident", text, re.I):
            return _reject("label_is_incident_count_not_headcount")
        if not EMPLOYEE_RE.search(subject):
            return _reject("label_not_employees")
        if dimension not in (None, "count"):
            return _reject(f"unit_dimension_{dimension}_not_count")
    elif code == "S1-6_12":
        if not TURNOVER_RE.search(subject):
            return _reject("label_not_turnover_rate")
        # A rate for men, women or one age band is a population subset, not the
        # workforce-wide turnover the clause asks for.
        if component or re.search(
                r"其中|男性|女性|按性别|按年龄|岁以下|岁以上|\d+\s*[-–—~至]\s*\d+\s*岁|\d+\s*岁|"
                r"管理者|管理层|职级|岗位|地区|区域|基层|中层|高层|普通员工|劳务|派遣|"
                r"by\s+(?:gender|age|region|level|job)", text):
            return _reject("label_is_population_subset")
        if dimension != "ratio" and not RATE_UNIT_RE.search(unit or ""):
            return _reject(f"unit_dimension_{dimension}_not_ratio")
    elif code == "S1-9_02":
        if not (GENDER_RE.search(text) and TOPMGMT_RE.search(text)):
            return _reject("label_not_top_management_gender")
        if LOWER_MANAGEMENT_RE.search(text) and not TOP_MANAGEMENT_RE.search(text):
            return _reject("label_not_top_management_level")
        if BOARD_RE.search(text) and not TOPMGMT_RE.search(text):
            return _reject("label_is_board_scope")
        if dimension != "ratio" and not RATE_UNIT_RE.search(unit or ""):
            return _reject(f"unit_dimension_{dimension}_not_ratio")
    elif code == "S1-13_04":
        if not (TRAINING_RE.search(text) and HOURS_RE.search(text)):
            return _reject("label_not_training_hours")
        # Business-ethics, EHS or leadership training is a topic, not the
        # workforce-wide average this clause asks for.
        if TOPIC_TRAINING_RE.search(text) and not WHOLE_WORKFORCE_RE.search(text):
            return _reject("label_is_topic_specific_training")
        if re.search(r"总(?:培训)?(?:时长|小时|学时)", text) and not re.search(r"人均|每人|per\s+(?:employee|person)|average", text, re.I):
            return _reject("label_is_total_hours_not_per_person")
        # A breakdown by gender, category, level or region describes a
        # sub-population, not the workforce-wide average.
        if re.search(r"按(?:性别|雇员类别|职级|地区|年龄)|男性|女性", text) and not WHOLE_WORKFORCE_RE.search(text):
            return _reject("label_is_population_subset")
        if RATIO_WORD_RE.search(text):
            return _reject("label_is_ratio")
    elif code == "S1-14_05":
        if not SAFETY_RE.search(subject):
            return _reject("label_not_work_related_accident")
        # Insurance or training coverage of workers is not an accident rate.
        if re.search(r"覆盖|coverage", text, re.I):
            return _reject("label_is_coverage_not_rate")
        # A lost-hours or fatality count is a different clause; this one is a rate.
        if dimension != "ratio" and not RATE_UNIT_RE.search(unit or ""):
            return _reject(f"unit_dimension_{dimension}_not_ratio")
    elif code == "S1-16_01":
        if not PAYGAP_RE.search(subject):
            return _reject("label_not_gender_pay_gap")
        if REMUN_RATIO_RE.search(text):
            return _reject("label_is_remuneration_ratio")
        if dimension != "ratio" and not RATE_UNIT_RE.search(unit or ""):
            return _reject(f"unit_dimension_{dimension}_not_ratio")
    elif code == "S1-17_02":
        if not DISCRIM_RE.search(subject):
            return _reject("label_not_discrimination_incident")
        if re.search(r"罚款|赔偿|罚金|fines?|penalt", text, re.I):
            return _reject("label_is_fine_not_incident_count")
        if dimension not in (None, "count"):
            return _reject(f"unit_dimension_{dimension}_not_count")
    elif code == "GOV-1_06":
        if not (BOARD_RE.search(text) and GENDER_RE.search(text)):
            return _reject("label_not_board_gender")
        if dimension != "ratio" and not RATE_UNIT_RE.search(unit or ""):
            return _reject(f"unit_dimension_{dimension}_not_ratio")
    elif code == "G1-4_01":
        if not (CORRUPTION_RE.search(text) and CONVICTION_RE.search(text)):
            return _reject("label_not_corruption_conviction")
        if re.search(r"罚款|罚金|fines?|penalt", text, re.I) and not CONVICTION_RE.search(text):
            return _reject("label_is_fine_not_conviction")
        if dimension not in (None, "count"):
            return _reject(f"unit_dimension_{dimension}_not_count")
    elif code == "G1-6_01":
        if not (PAYDAYS_RE.search(text) and DAYS_RE.search(text)):
            return _reject("label_not_payment_days")
        if re.search(r"标准账期|标准付款期限|standard\s+terms|on-time|按时付款", text, re.I):
            return _reject("label_is_terms_or_share_not_average_days")
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
    r"\boverseas\b|\bleased\b|\bowned\b|hong\s+kong|macau|macao|taiwan)",
    re.I,
)
POLLUTANT_SPECIFIC_RE = re.compile(
    r"(?:氮氧化|二氧化硫|硫氧化|颗粒|烟尘|粉尘|挥发性有机|化学需氧量|氨氮|总磷|总氮|重金属|(?<![a-z0-9])(?:cod|nox|sox|so2|vocs?|pm10|pm2\.5|bod)(?![a-z0-9])|particulate|ammonia)",
    re.I,
)
POLLUTION_METHOD_RE = re.compile(
    # "废水" alone is about water use; it counts only beside a discharge,
    # concentration or monitoring statement.
    r"(?:污染物|废气|大气污染物|排放物|pollutant|air\s+emission"
    r"|(?:废水|污水|effluent|wastewater)[^。；]{0,12}(?:排放|浓度|监测|monitor)"
    r"|排放量[^。；]{0,12}(?:公式|估算|测算|监测|核算))", re.I,
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
