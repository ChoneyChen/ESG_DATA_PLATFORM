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
    ),
    FoundationMetric(
        "E1-6_09", "esrs.2023-set1.e1-6.dp09", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "范围2地点法排放 Scope 2 location-based",
        "Scope 2 (indirect energy) GHG emissions ONLY when the table or a note states the location-based method "
        "(地点法/基于位置/location-based/average grid emission factor).",
    ),
    FoundationMetric(
        "E1-6_10", "esrs.2023-set1.e1-6.dp10", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "范围2市场法排放 Scope 2 market-based",
        "Scope 2 GHG emissions ONLY when the table or a note states the market-based method "
        "(市场法/基于市场/market-based/contractual instruments or supplier-specific factors).",
    ),
    FoundationMetric(
        "E1-6_11", "esrs.2023-set1.e1-6.dp11", "esrs.2023-set1.e1-6", "ghg", "quantity", "ghg-emissions",
        "范围3排放 Scope 3 GHG emissions",
        "Absolute total Scope 3 (other indirect) GHG emissions. A single Scope 3 category is a component, not the total.",
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
        "energy", ("E1-5_01", "E1-5_05", "E1-5_09"),
        ("能源消耗", "能源消费", "能耗", "能源耗", "综合能源", "能源使用", "耗能", "用能", "用电", "可再生能源", "清洁能源",
         "非化石能源", "energy"),
        ("gwh", "mwh", "kwh", "千瓦时", "兆瓦时", "吨标准煤", "标准煤", "tce", "吉焦", "gj", "tj",
         "电力", "天然气", "柴油", "汽油", "煤炭", "electricity", "natural gas", "diesel", "fuel"),
    ),
    SourceGroup(
        "ghg", ("E1-6_07", "E1-6_09", "E1-6_10", "E1-6_11"),
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
)

GROUP_BY_KEY = {group.key: group for group in GROUPS}

METHOD_GROUP_KEYS = ("air", "water_pollutants")
METHOD_TERMS = (
    "监测", "检测", "在线", "测量", "测算", "估算", "计算", "核算", "排放因子", "物料衡算", "系数", "统计口径",
    "monitor", "measur", "estimat", "calculat", "emission factor", "mass balance", "methodolog",
)
