from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FoundationMetric:
    code: str
    metric_id: str
    package_id: str
    group: str
    kind: str  # quantity | percentage | narrative
    dimension: str | None
    label: str
    definition: str
    # A component of another clause (renewable energy inside total energy,
    # Scope 1 inside total GHG). Set only where the child is a genuine part of
    # the parent's quantity; it drives the grouped display, never the value.
    parent_code: str | None = None


SCOPE2_UNSPECIFIED = "SCOPE2_METHOD_UNSTATED"


METRICS: tuple[FoundationMetric, ...] = (
    FoundationMetric(
        "E1-5_01", "esrs.2023-set1.e1-5.dp01", "esrs.2023-set1.e1-5", "energy", "quantity", "energy",
        "总能源消耗 Total energy consumption",
        "Absolute total energy consumed by own operations (能源消耗总量/综合能耗/能耗总量). "
        "Not one carrier or sub-total (direct/indirect only), not intensity, not energy generated or saved.",
    ),
    FoundationMetric(
        "E1-5_05", "esrs.2023-set1.e1-5.dp05", "esrs.2023-set1.e1-5", "energy", "quantity", "energy",
        "可再生能源消耗量 Renewable energy consumption",
        "Absolute total amount of renewable energy consumed (可再生能源消耗/使用总量). "
        "Not a share %, not installed capacity, not generation sold; one renewable carrier row (solar, hydro) is a component, not the total.",
        parent_code="E1-5_01",
    ),
    FoundationMetric(
        "E1-5_09", "esrs.2023-set1.e1-5.dp09", "esrs.2023-set1.e1-5", "energy", "percentage", "ratio",
        "可再生能源占比 Renewable share of energy consumption",
        "Percentage of total energy consumption that is renewable (可再生/非化石/清洁能源消费占比). "
        "Not renewable share of electricity only, not fossil share.",
    ),
    FoundationMetric(
        "E1-6_07", "esrs.2023-set1.e1-6.dp07", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "范围1排放 Scope 1 GHG emissions",
        "Absolute Scope 1 (direct) GHG emissions in CO2e. Not Scope 1+2 combined, not intensity, not reductions or targets.",
        parent_code="E1-6_12",
    ),
    FoundationMetric(
        "E1-6_09", "esrs.2023-set1.e1-6.dp09", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "范围2地点法排放 Scope 2 location-based",
        "Scope 2 (indirect energy) GHG emissions ONLY when the table or a note states the location-based method "
        "(地点法/基于位置/location-based/average grid emission factor).",
        parent_code="E1-6_12",
    ),
    FoundationMetric(
        "E1-6_10", "esrs.2023-set1.e1-6.dp10", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "范围2市场法排放 Scope 2 market-based",
        "Scope 2 GHG emissions ONLY when the table or a note states the market-based method "
        "(市场法/基于市场/market-based/contractual instruments or supplier-specific factors).",
        parent_code="E1-6_12",
    ),
    FoundationMetric(
        "E1-6_11", "esrs.2023-set1.e1-6.dp11", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "范围3排放 Scope 3 GHG emissions",
        "Absolute total Scope 3 (other indirect) GHG emissions. A single Scope 3 category is a component, not the total.",
        parent_code="E1-6_12",
    ),
    FoundationMetric(
        "E2-4_02", "esrs.2023-set1.e2-4.dp02", "esrs.2023-set1.e2-4", "air", "quantity", "mass",
        "向空气排放污染物 Pollutants emitted to air",
        "Mass of each pollutant emitted to air (NOx, SO2/SOx, particulate matter/dust/smoke, VOCs, etc.); map every pollutant row. "
        "Not GHG/CO2, not concentration, not intensity, not reduction amounts.",
    ),
    FoundationMetric(
        "E2-4_03", "esrs.2023-set1.e2-4.dp03", "esrs.2023-set1.e2-4", "water_pollutants", "quantity", "mass",
        "向水体排放污染物 Pollutants emitted to water",
        "Mass of each pollutant discharged to water (COD, ammonia nitrogen, total N/P, heavy metals such as Cu/Zn/Pb); map every pollutant row. "
        "Not wastewater volume, not concentration, not intensity.",
    ),
    FoundationMetric(
        "E2-4_09", "esrs.2023-set1.e2-4.dp09", "esrs.2023-set1.e2-4", "pollution_method", "narrative", None,
        "污染物测量方法 Pollutant measurement methodology",
        "Statement of how pollutant emission amounts are measured, monitored, calculated or estimated.",
    ),
    FoundationMetric(
        "E2-5_02", "esrs.2023-set1.e2-5.dp02", "esrs.2023-set1.e2-5", "substances", "quantity", "mass",
        "关注物质总量 Substances of concern",
        "Total amount of substances of concern / substances of very high concern (SVHC) generated, used or procured. "
        "Not hazardous waste, not pollutant emissions, not general chemicals use unless stated as substances of concern.",
    ),
    FoundationMetric(
        "E3-4_01", "esrs.2023-set1.e3-4.dp01", "esrs.2023-set1.e3-4", "water", "quantity", "volume",
        "耗水总量 Total water consumption",
        "Total water consumption (耗水量): water withdrawn and not discharged/returned. Not withdrawal, not discharge, not recycled water.",
    ),
    FoundationMetric(
        "E3-4_02", "esrs.2023-set1.e3-4.dp02", "esrs.2023-set1.e3-4", "water", "quantity", "volume",
        "水风险地区耗水量 Water consumption in areas at water risk",
        "Water CONSUMPTION in areas at water risk / high water stress. Withdrawal in water-risk areas is not this; a share % is not this.",
    ),
    FoundationMetric(
        "E3-4_03", "esrs.2023-set1.e3-4.dp03", "esrs.2023-set1.e3-4", "water", "quantity", "volume",
        "循环及重复利用水量 Water recycled and reused",
        "Total volume of water recycled and reused (循环水量, 重复利用水量, 回用水量, 中水/再生水使用量). A recycling or reuse RATE % is not this.",
    ),
    FoundationMetric(
        "E3-4_11", "esrs.2023-set1.e3-4.dp11", "esrs.2023-set1.e3-4", "water", "quantity", "volume",
        "取水总量 Total water withdrawal",
        "Total water withdrawn from all sources (取水总量/总取水量). One source (groundwater, municipal, surface) is a component, not the total.",
    ),
    FoundationMetric(
        "E3-4_12", "esrs.2023-set1.e3-4.dp12", "esrs.2023-set1.e3-4", "water", "quantity", "volume",
        "排水总量 Total water discharge",
        "Total water discharged (排水总量/废水排放总量). One destination or type of discharge is a component, not the total.",
    ),
    # ---- extended priority set: cross-sector indicators a reader looks for
    # first in almost every sustainability report --------------------------
    FoundationMetric(
        "E1-5_02", "esrs.2023-set1.e1-5.dp02", "esrs.2023-set1.e1-5", "energy", "quantity", "energy",
        "化石能源消耗量 Fossil energy consumption",
        "Total energy consumed from fossil sources (化石能源消耗/煤炭+石油+天然气合计). "
        "One fossil carrier (coal only, natural gas only) is a component, not the total.",
        parent_code="E1-5_01",
    ),
    FoundationMetric(
        "E1-6_12", "esrs.2023-set1.e1-6.dp12", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "温室气体排放总量（位置法） Total GHG emissions, location-based",
        "Total gross GHG emissions = Scope 1 + Scope 2 (location-based) + Scope 3. "
        "Not one scope alone, not market-based total, not intensity or reduction.",
    ),
    FoundationMetric(
        "E1-6_30", "esrs.2023-set1.e1-6.dp30", "esrs.2023-set1.e1-6", "ghg_intensity", "quantity", "ghg-intensity",
        "温室气体排放强度（位置法） GHG intensity, location-based",
        "Total location-based GHG emissions per unit of net revenue (吨CO2e/百万元营收). "
        "An absolute emission total, an energy intensity or a per-product intensity is not this.",
    ),
    FoundationMetric(
        "E5-5_07", "esrs.2023-set1.e5-5.dp07", "esrs.2023-set1.e5-5", "waste", "quantity", "mass",
        "废弃物产生总量 Total waste generated",
        "Total weight of waste generated (废弃物产生总量). A single stream (hazardous only, one facility, "
        "one treatment route) is a component; recycled or disposed amounts are destinations, not the total generated.",
    ),
    FoundationMetric(
        "E5-5_15", "esrs.2023-set1.e5-5.dp15", "esrs.2023-set1.e5-5", "waste", "quantity", "mass",
        "有害废弃物总量 Hazardous waste",
        "Total weight of hazardous waste (有害废弃物/危险废物总量). Non-hazardous waste or a treatment route is not this.",
        parent_code="E5-5_07",
    ),
    FoundationMetric(
        "E5-4_02", "esrs.2023-set1.e5-4.dp02", "esrs.2023-set1.e5-4", "materials", "quantity", "mass",
        "资源投入总重量 Materials used",
        "Overall total weight of products and technical/biological materials used (资源投入总量/原材料使用总量). "
        "A percentage share, packaging only, or one material is not this.",
    ),
    FoundationMetric(
        "S1-6_02", "esrs.2023-set1.s1-6.dp02", "esrs.2023-set1.s1-6", "workforce", "quantity", "count",
        "员工人数 Employees (head count)",
        "Number of employees at the end of the reporting period, head count (员工总数/期末人数). "
        "An average number, full-time-equivalent number, turnover or non-employees is a different clause.",
    ),
    FoundationMetric(
        "S1-6_12", "esrs.2023-set1.s1-6.dp12", "esrs.2023-set1.s1-6", "workforce", "percentage", "ratio",
        "员工流失率 Employee turnover rate",
        "Percentage of employees who left during the reporting period (员工流失率/离职率). "
        "A head count of leavers or a hiring rate is not this.",
    ),
    FoundationMetric(
        "S1-9_02", "esrs.2023-set1.s1-9.dp02", "esrs.2023-set1.s1-9", "workforce", "percentage", "ratio",
        "高管性别比例 Gender distribution at top management",
        "Percentage of top-management members by gender (管理层/高管性别比例, 女性管理者占比). "
        "Head-count numbers, board-only figures or overall workforce gender share are different scopes.",
    ),
    FoundationMetric(
        "S1-13_04", "esrs.2023-set1.s1-13.dp04", "esrs.2023-set1.s1-13", "workforce", "quantity", None,
        "人均培训时长 Average training hours per person",
        "Average training hours per employee (人均培训时长/人均培训小时数). "
        "Total training hours, a participation percentage or training spend is not this.",
    ),
    FoundationMetric(
        "S1-14_05", "esrs.2023-set1.s1-14.dp05", "esrs.2023-set1.s1-14", "workforce", "percentage", "ratio",
        "可记录工伤事故率 Recordable work-related accident rate",
        "Rate of recordable work-related accidents for own workforce (可记录工伤事故率, 每百万工时事故率). "
        "An accident count, fatalities count or lost days is a different clause.",
    ),
    FoundationMetric(
        "S1-16_01", "esrs.2023-set1.s1-16.dp01", "esrs.2023-set1.s1-16", "workforce", "percentage", "ratio",
        "性别薪酬差距 Gender pay gap",
        "Gender pay gap as a percentage (性别薪酬差距/男女薪酬差异). "
        "A remuneration ratio between the CEO and median employee is a different clause, not this.",
    ),
    FoundationMetric(
        "S1-17_02", "esrs.2023-set1.s1-17.dp02", "esrs.2023-set1.s1-17", "workforce", "quantity", "count",
        "歧视事件数量 Incidents of discrimination",
        "Number of incidents of discrimination, including harassment (歧视事件数量/歧视投诉件数). "
        "Complaints about other matters or fines amounts are different clauses.",
    ),
    FoundationMetric(
        "GOV-1_06", "esrs.2023-set1.gov-1.dp06", "esrs.2023-set1.gov-1", "governance", "percentage", "ratio",
        "董事会性别比例 Board gender diversity",
        "Share of board members by gender (董事会/治理机构性别比例, 女性董事占比). "
        "Head-count membership, independence ratio or management-team share is a different scope.",
    ),
    FoundationMetric(
        "G1-4_01", "esrs.2023-set1.g1-4.dp01", "esrs.2023-set1.g1-4", "governance", "quantity", "count",
        "腐败贿赂定罪数量 Convictions for corruption",
        "Number of convictions for violation of anti-corruption and anti-bribery laws (腐败/贿赂定罪数量). "
        "Confirmed incidents, dismissed employees or fines amounts are different clauses.",
    ),
    FoundationMetric(
        "G1-6_01", "esrs.2023-set1.g1-6.dp01", "esrs.2023-set1.g1-6", "governance", "quantity", None,
        "平均付款天数 Average payment days",
        "Average number of days to pay an invoice from the contractual or statutory due date (平均付款天数/平均账期). "
        "Standard payment terms or the share of on-time payments is not this.",
    ),
)

BY_CODE = {item.code: item for item in METRICS}


@dataclass(frozen=True)
class SourceGroup:
    key: str
    codes: tuple[str, ...]
    strong: tuple[str, ...]
    weak: tuple[str, ...]


GROUPS: tuple[SourceGroup, ...] = (
    SourceGroup(
        "energy", ("E1-5_01", "E1-5_02", "E1-5_05", "E1-5_09"),
        ("能源消耗", "能源消费", "能耗", "能源耗", "综合能源", "能源使用", "耗能", "用能", "用电", "可再生能源", "清洁能源",
         "非化石能源", "energy"),
        ("gwh", "mwh", "kwh", "千瓦时", "兆瓦时", "吨标准煤", "标准煤", "tce", "吉焦", "gj", "tj",
         "电力", "天然气", "柴油", "汽油", "煤炭", "electricity", "natural gas", "diesel", "fuel"),
    ),
    SourceGroup(
        "ghg", ("E1-6_07", "E1-6_09", "E1-6_10", "E1-6_11", "E1-6_12"),
        ("范围一", "范围1", "范围二", "范围2", "范围三", "范围3", "范畴一", "范畴二", "范畴三", "scope 1", "scope 2",
         "scope 3", "scope1", "scope2", "scope3", "温室气体", "greenhouse gas", "ghg", "直接排放", "间接排放"),
        ("co2e", "co₂e", "co_{{2}}e", "二氧化碳当量", "碳排放", "emissions", "排放总量"),
    ),
    SourceGroup(
        "air", ("E2-4_02",),
        ("氮氧化物", "二氧化硫", "硫氧化物", "颗粒物", "烟尘", "粉尘", "挥发性有机", "vocs", "nox", "sox", "so2",
         "particulate", "废气", "大气污染物", "air emissions", "air pollutant"),
        ("排放量", "排放", "吨", "tonnes", "emissions"),
    ),
    SourceGroup(
        "water_pollutants", ("E2-4_03",),
        ("化学需氧量", "cod", "氨氮", "总氮", "总磷", "生化需氧量", "bod", "水污染物", "废水污染物", "重金属",
         "总铜", "总锌", "总铅", "总镉", "总汞", "总砷", "ammonia nitrogen", "chemical oxygen demand"),
        ("废水", "排放量", "吨", "wastewater", "discharge"),
    ),
    SourceGroup(
        "water", ("E3-4_01", "E3-4_02", "E3-4_03", "E3-4_11", "E3-4_12"),
        ("取水", "耗水", "用水", "排水", "水资源", "循环水", "循环利用", "重复利用", "回用", "新鲜水", "新水", "淡水",
         "水风险", "缺水", "水压力", "water withdrawal", "water consumption", "water discharge", "water recycled",
         "recycled water", "water use", "water withdrawn", "water stress", "water management", "water resources",
         "water intake", "water sources", "total water", "ground water", "groundwater", "surface water"),
        ("立方米", "m3", "m³", "万吨", "百万吨", "吨", "megalitre", "ml", "地下水", "地表水", "市政", "groundwater",
         "surface water", "municipal"),
    ),
    SourceGroup(
        "substances", ("E2-5_02",),
        ("关注物质", "高度关注物质", "高关注物质", "svhc", "substances of concern", "substance of concern",
         "有毒有害物质", "有害物质", "危险化学品", "hazardous substances"),
        ("化学品", "chemicals", "使用量", "采购量"),
    ),
    SourceGroup(
        "ghg_intensity", ("E1-6_30",),
        ("温室气体排放强度", "排放强度", "碳强度", "ghg intensity", "emission intensity", "carbon intensity",
         "每百万元营收", "per million revenue", "per revenue"),
        ("tco2e", "co2e", "吨二氧化碳当量", "万元", "百万元"),
    ),
    SourceGroup(
        "waste", ("E5-5_07", "E5-5_15"),
        ("废弃物", "废物", "固废", "危险废物", "有害废弃物", "waste", "solid waste", "hazardous waste"),
        ("产生量", "总量", "处置", "回收", "有害", "无害", "吨", "tonnes", "generated", "disposed", "diverted"),
    ),
    SourceGroup(
        "materials", ("E5-4_02",),
        ("原材料", "资源投入", "物料使用", "材料使用", "materials used", "resource inflow", "material inflow"),
        ("吨", "重量", "采购量", "tonnes", "weight"),
    ),
    SourceGroup(
        "workforce", ("S1-6_02", "S1-6_12"),
        ("员工总数", "员工人数", "雇员人数", "在职员工", "员工流失率", "离职率", "employee turnover", "number of employees",
         "headcount", "head count"),
        ("人", "人数", "期末", "平均", "%", "turnover", "employees"),
    ),
    SourceGroup(
        "workforce_training", ("S1-13_04",),
        ("培训", "受训", "培训时长", "培训小时", "人均受训", "training", "training hours", "trained"),
        ("小时", "学时", "人均", "hours", "per employee"),
    ),
    SourceGroup(
        "workforce_safety", ("S1-14_05",),
        ("工伤", "事故", "可记录", "accident", "injury", "ltifr", "trifr", "recordable"),
        ("率", "每百万工时", "人数", "起", "rate"),
    ),
    SourceGroup(
        "workforce_pay", ("S1-16_01",),
        ("薪酬差距", "薪酬差异", "男女薪酬", "gender pay gap", "pay gap", "remuneration gap"),
        ("%", "比率", "ratio"),
    ),
    SourceGroup(
        "workforce_incidents", ("S1-17_02",),
        ("歧视", "骚扰", "discrimination", "harassment"),
        ("事件", "投诉", "件", "incident", "complaint"),
    ),
    SourceGroup(
        "workforce_gender", ("S1-9_02",),
        ("高管", "管理层", "女性管理者", "性别比例", "top management", "senior management", "gender distribution"),
        ("%", "女性", "男性", "female", "male"),
    ),
    SourceGroup(
        "governance", ("GOV-1_06", "G1-4_01", "G1-6_01"),
        ("董事会", "治理机构", "女性董事", "董事会多元化", "腐败", "贿赂", "定罪", "付款天数", "账期", "board", "corruption", "bribery",
         "conviction", "payment days"),
        ("%", "件", "天", "人", "女性", "days", "cases"),
    ),
)

GROUP_BY_KEY = {group.key: group for group in GROUPS}

METHOD_GROUP_KEYS = ("air", "water_pollutants")
METHOD_TERMS = (
    "监测", "检测", "在线", "测量", "测算", "估算", "计算", "核算", "排放因子", "物料衡算", "系数", "统计口径",
    "monitor", "measur", "estimat", "calculat", "emission factor", "mass balance", "methodolog",
)


# Named metric sets. The link runs whichever set is selected; a set never
# refers to a report, and no rule below refers to a set.
BASE16: tuple[str, ...] = ('E1-5_01', 'E1-5_05', 'E1-5_09', 'E1-6_07', 'E1-6_09', 'E1-6_10', 'E1-6_11', 'E2-4_02', 'E2-4_03', 'E2-4_09', 'E2-5_02', 'E3-4_01', 'E3-4_02', 'E3-4_03', 'E3-4_11', 'E3-4_12')
EXTENDED: tuple[str, ...] = ('E1-5_02', 'E1-6_12', 'E1-6_30', 'E5-5_07', 'E5-5_15', 'E5-4_02', 'S1-6_02', 'S1-6_12', 'S1-9_02', 'S1-13_04', 'S1-14_05', 'S1-16_01', 'S1-17_02', 'GOV-1_06', 'G1-4_01', 'G1-6_01')
PROFILES: dict[str, tuple[str, ...]] = {"base16": BASE16, "priority32": BASE16 + EXTENDED}


def metrics_for(profile: str) -> tuple[FoundationMetric, ...]:
    """The metrics of one set; an unknown set is an error, never a silent 16."""
    codes = PROFILES.get(profile)
    if codes is None:
        raise ValueError(f"Unknown metric profile: {profile}")
    return tuple(BY_CODE[code] for code in codes)
