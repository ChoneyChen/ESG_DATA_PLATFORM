from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_100 = ROOT / "core/1.0.0/core.json"
CORE_110 = ROOT / "core/1.1.0/core.json"
SOURCE_URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32023R2772"
IG3_URL = (
    "https://www.efrag.org/sites/default/files/media/document/2025-06/"
    "EFRAG%20IG%203%20List%20of%20ESRS%20Data%20Points%20%281%29%20%281%29.xlsx"
)
E1_5_PACKAGE_VERSION = "1.2.0"
E1_6_PACKAGE_VERSION = "1.4.0"

# Semantic definitions are standard data, not runtime rules or lexical gates.
SEMANTIC_DEFINITIONS = {
    "E1-6_01": "范围1、范围2、范围3及温室气体总量披露的结构。识别报告实际采用的行主题、期间列、实体列、单位和总计层级；输出结构成员或说明，不把表内所有数字复制成结构事实。",
    "E1-6_02": "企业说明温室气体盘查采用的组织边界，包括财务控制、运营控制或其他明确方法。每种实际采用的方法形成可独立举证的断言；不能把表中公司名称、合计列或数值当作边界方法。",
    "E1-6_03": "企业对温室气体排放采用的分解维度，例如国家、经营分部、经济活动、子公司、设施或排放源。输出实际披露的分解轴，不输出这些轴下的每个数值单元格。",
    "E1-6_04": "企业按照 GHG Protocol 披露的范围3类别清单。每个实际披露的类别形成独立清单事实，并分别保留证据；总计、合计、小计、标题和注释不是类别成员。只有报告正文、表头或关联方法说明明确建立 GHG Protocol 口径时才匹配本指标，不能仅凭出现范围3类别推定分类体系。",
    "E1-6_05": "企业按照 ISO 14064-1 披露的范围3类别清单。每个实际披露的类别形成独立清单事实，并分别保留证据；总计、合计、小计、标题和注释不是类别成员。只有报告正文、表头或关联方法说明明确建立 ISO 14064-1 口径时才匹配本指标，不能把普通 GHG Protocol 类别表改贴为 ISO 分类。",
    "E1-6_06": "企业对范围3排放所作的价值链阶段分解。上游和下游等实际披露阶段分别形成独立清单事实并保留证据；合计、标题和注释不是价值链阶段。",
    "E1-6_07": "企业在报告期间因拥有或控制的排放源产生的范围1温室气体绝对排放量，以报告原有二氧化碳当量单位披露。直接排放、范围一、范畴一可表达同一范围；不要求出现‘总量’字样。范围1与2合计、强度、减排量、抵消或目标不属于此测量。逐一保留报告的实体和期间。",
    "E1-6_08": "范围1温室气体排放中受受监管排放交易体系覆盖的比例；分母应为范围1排放。碳价情景、碳敞口、另一公司的 ETS 项目或一般排放限制比例不能仅因出现百分号而等同此指标。结合报告对交易机制和覆盖口径的说明判断。",
    "E1-6_09": "基于位置法核算的范围2温室气体绝对排放量。位置法依据能源消费所在地电网的平均排放因子；方法可能写在表注、核算政策或另一页。‘间接排放/范围2’本身没有声明方法；未说明时方法未知，不能同时认作位置法和市场法。与另一方法数值相同只有在报告确实声明两种方法时才可分别归属。",
    "E1-6_10": "基于市场法核算的范围2温室气体绝对排放量。市场法体现能源采购合同工具、供应商/产品特定排放属性及适用的剩余组合；方法可由报告核算说明支持，不要求逐字标签。仅披露用电、绿电比例或电网平均因子并不能独自证明市场法。方法未明的范围2测量应保留待确认，明确为位置法的不能贴上市方法标签。",
    "E1-6_11": "按重大范围3类别披露的温室气体绝对排放量。每个类别、期间和实体单元格形成独立事实；总计、合计、小计、标题及注释不作为类别成员。相同数值出现在成员实体与合计列时仍是不同事实。",
    "E1-6_12": "采用位置法范围2口径的温室气体总量，即范围1、位置法范围2及范围3的合计。优先提取报告明确披露的该总量；未直接披露时仅在数据处理阶段按相同期间、实体和边界派生。不能把单独的范围2位置法排放误当作总量。",
    "E1-6_13": "采用市场法范围2口径的温室气体总量，即范围1、市场法范围2及范围3的合计。优先提取明确披露值；缺少市场法范围2时不得借用位置法数值或形成派生总量。",
    "E1-6_14": "报告边界、核算方法、数据来源或排放因子的实际变更及其可比性影响。每个变更形成可读、可举证的陈述；年份、百分比或排放量本身不是变更说明。",
    "E1-6_15": "温室气体核算采用的方法、关键假设、排放因子来源和计算工具。分别保留有语义的原文陈述；ISO、GHG Protocol、IPCC、电网因子或供应商参数只有在证据明确适用于本报告时才写入。",
    "E1-6_16": "本报告期与上一个报告日期之间对排放具有显著影响的事件或变化。目录、披露索引和仅指向其他页码的导航信息不是事件证据。",
    "E1-6_17": "从范围1排放中排除并单独披露的生物源二氧化碳绝对量。必须有明确的生物源或生物质 CO2 语义；普通范围1、其他温室气体或污染物排放不能替代。",
    "E1-6_18": "与合同工具相关的范围2排放比例。分子、分母或文字说明必须明确连接范围2排放与合同工具；绿电或绿证的存在本身不等于该排放比例。",
    "E1-6_19": "企业实际使用的合同工具类型清单，例如购电协议、能源属性证书或供应商特定工具。每类工具为独立清单成员；比例和范围2排放量不是工具类型。",
    "E1-6_20": "基于市场法范围2排放中与捆绑合同工具相关的比例。需同时支持市场法、范围2、捆绑工具和比例语义；不能用一般绿电比例替代。",
    "E1-6_21": "范围2排放中关联捆绑能源属性声明的比例。必须区分捆绑属性声明、非捆绑证书及一般合同工具。",
    "E1-6_22": "范围2排放中关联非捆绑能源属性声明的比例。必须有非捆绑属性或证书的明确证据，不能从绿证名称推断比例。",
    "E1-6_23": "合同工具的类型和组合方式说明。输出可读的工具组合陈述，不把范围2表中的数字、年份或单位转成定性断言。",
    "E1-6_24": "从范围2排放中排除并单独披露的生物源二氧化碳绝对量。没有明确生物源语义时应为未找到，不能复制普通范围2排放。",
    "E1-6_25": "使用供应商或其他价值链伙伴一手数据计算的范围3排放比例。需明确连接一手数据、范围3计算及比例口径。",
    "E1-6_26": "企业排除某一范围3类别的具体理由。每条事实必须包含被排除类别及可读理由；类别排放数值或总计不是排除理由。",
    "E1-6_27": "纳入温室气体盘查的范围3类别清单。每个类别形成独立列表事实并保留证据；总计、年份、单位和类别排放数字不是清单成员。",
    "E1-6_28": "从范围3排放中排除并单独披露的生物源二氧化碳绝对量。没有明确生物源证据时不得从范围3类别表复制数值。",
    "E1-6_29": "范围3盘查边界、计算方法、关键假设、数据来源和工具。输出实际方法陈述并分别举证，不能以类别数字或单位代替。",
    "E1-6_30": "以采用位置法范围2的温室气体总量为分子、净收入为分母的排放强度。报告使用工业增加值、产量、面积或能源量作分母的强度不是本指标；派生时必须匹配期间、实体、边界和货币量级。",
    "E1-6_31": "以采用市场法范围2的温室气体总量为分子、净收入为分母的排放强度。没有市场法总量或净收入分母时不得借用位置法或其他强度。",
    "E1-6_32": "用于温室气体强度计算的净收入与财务报表净收入之间的对账说明。需有明确的对账关系、差异或一致性陈述；整张经济绩效表不是对账说明。",
    "E1-6_33": "财务报表口径的净收入总额。只提取直接表示净收入或营业收入总额的期间/实体值；利润、资产、产量、资源量及其他经济指标不属于本指标。",
    "E1-6_34": "企业实际用于温室气体强度分母的净收入。必须由强度计算说明或对账表明确支持；工业增加值、利润或一般营业收入不能在缺少关联证据时自动采用。",
    "E1-6_35": "净收入对账中的其他净收入或调整项。只提取明确标记为对账其他项的货币值；利润、资产、产量和资源量不得纳入。",
    "E1-5_02": "企业自身运营消耗的化石来源能源总量，包括适用的煤炭、石油、天然气和其他化石来源及化石来源购入能源。载体分项或直接能源子总计不自动等同完整化石能源总量；区分总量、子总计、组成项、消费和生产，以及数量、变化率、比例和强度。",
    "E1-5_05": "企业自身运营消耗的可再生来源能源总量，包括可再生燃料及适用的外购、自发自用可再生能源；发电量不必然等于消费量。百分比、装机功率、能源强度、增长/减少量不是能源消费绝对总量。",
    "E1-5_12": "企业自身运营消耗的天然气能源量。结合直接行主体、载体层级和表头判断；‘其他直接能源’、煤或石油不是天然气。保留不同实体、期间的原值和原单位；同一测量可能同时以 GWh 和 TJ 等单位展示，不应相加。",
}


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_core() -> None:
    core = json.loads(CORE_100.read_text(encoding="utf-8"))
    core["core_schema_version"] = "1.1.0"
    core["common_code_sets"].extend(
        [
            {
                "code_set_id": "extractesg.core.codeset.currency",
                "extensible": True,
                "values": [
                    {"code": code, "labels": {"zh": zh, "en": en}}
                    for code, zh, en in [
                        ("cny", "人民币", "Chinese yuan"),
                        ("eur", "欧元", "Euro"),
                        ("usd", "美元", "US dollar"),
                        ("gbp", "英镑", "Pound sterling"),
                        ("jpy", "日元", "Japanese yen"),
                        ("hkd", "港元", "Hong Kong dollar"),
                        ("other", "其他货币", "Other currency"),
                    ]
                ],
            },
            {
                "code_set_id": "extractesg.core.codeset.currency-scale",
                "extensible": True,
                "values": [
                    {"code": code, "labels": {"zh": zh, "en": en}}
                    for code, zh, en in [
                        ("unit", "元", "unit"),
                        ("thousand", "千", "thousand"),
                        ("ten_thousand", "万", "ten thousand"),
                        ("million", "百万", "million"),
                        ("hundred_million", "亿", "hundred million"),
                        ("billion", "十亿", "billion"),
                        ("other", "其他量级", "other scale"),
                    ]
                ],
            },
        ]
    )
    units = [
        ("watt-hour", "Wh", "energy", "瓦时", "watt-hour", "3600"),
        ("kilowatt-hour", "kWh", "energy", "千瓦时", "kilowatt-hour", "3600000"),
        ("megawatt-hour", "MWh", "energy", "兆瓦时", "megawatt-hour", "3600000000"),
        ("gigawatt-hour", "GWh", "energy", "吉瓦时", "gigawatt-hour", "3600000000000"),
        ("terawatt-hour", "TWh", "energy", "太瓦时", "terawatt-hour", "3600000000000000"),
        ("megajoule", "MJ", "energy", "兆焦", "megajoule", "1000000"),
        ("gigajoule", "GJ", "energy", "吉焦", "gigajoule", "1000000000"),
        ("terajoule", "TJ", "energy", "太焦", "terajoule", "1000000000000"),
        ("tonne-coal-equivalent", "tce", "energy", "吨标准煤", "tonne of coal equivalent", "29307600000"),
        ("kilogram-co2e", "kgCO2e", "ghg-emissions", "千克二氧化碳当量", "kilogram CO2 equivalent", "1"),
        ("tonne-co2e", "tCO2e", "ghg-emissions", "吨二氧化碳当量", "tonne CO2 equivalent", "1000"),
        ("kilotonne-co2e", "ktCO2e", "ghg-emissions", "千吨二氧化碳当量", "kilotonne CO2 equivalent", "1000000"),
        ("megatonne-co2e", "MtCO2e", "ghg-emissions", "百万吨二氧化碳当量", "megatonne CO2 equivalent", "1000000000"),
        ("cny", "CNY", "currency", "人民币", "Chinese yuan", None),
        ("eur", "EUR", "currency", "欧元", "Euro", None),
        ("usd", "USD", "currency", "美元", "US dollar", None),
        ("gbp", "GBP", "currency", "英镑", "Pound sterling", None),
        ("jpy", "JPY", "currency", "日元", "Japanese yen", None),
        ("hkd", "HKD", "currency", "港元", "Hong Kong dollar", None),
        ("mwh-per-monetary-unit", "MWh/currency", "energy-intensity", "兆瓦时/货币单位", "MWh per monetary unit", None),
        ("tco2e-per-monetary-unit", "tCO2e/currency", "ghg-intensity", "吨二氧化碳当量/货币单位", "tCO2e per monetary unit", None),
    ]
    for slug, symbol, dimension, zh, en, scale in units:
        item = {
            "unit_id": f"extractesg.core.unit.{slug}",
            "symbol": symbol,
            "dimension": f"extractesg.core.unit-dimension.{dimension}",
            "labels": {"zh": zh, "en": en},
        }
        if scale is not None:
            item["scale_to_si"] = scale
        core["common_units"].append(item)
    write_json(CORE_110, core)


def cid(package_id: str, slug: str) -> str:
    return f"{package_id}.concept.{slug}"


def source_id(package_id: str, suffix: str) -> str:
    return f"{package_id}.source.{suffix}"


def concept(package_id: str, slug: str, zh: str, en: str, *, aliases=None, broader=None,
            related=None, excluded=None, applicable=None, abbreviations=None, formulas=None,
            description: str = "") -> dict:
    aliases = aliases or {}
    seen = {" ".join(zh.split()).casefold(), " ".join(en.split()).casefold()}
    clean_aliases = {}
    for language, values in aliases.items():
        clean = []
        for value in values:
            normalized = " ".join(value.split()).casefold()
            if normalized and normalized not in seen:
                clean.append(value)
                seen.add(normalized)
        if clean:
            clean_aliases[language] = clean
    clean_abbreviations = []
    for value in abbreviations or []:
        normalized = " ".join(value.split()).casefold()
        if normalized not in seen:
            clean_abbreviations.append(value)
            seen.add(normalized)
    clean_formulas = []
    for value in formulas or []:
        normalized = " ".join(value.split()).casefold()
        if normalized not in seen:
            clean_formulas.append(value)
            seen.add(normalized)
    return {
        "concept_id": cid(package_id, slug),
        "labels": {"zh": zh, "en": en},
        "aliases": clean_aliases,
        "abbreviations": clean_abbreviations,
        "formulas": clean_formulas,
        "broader_concept_ids": [cid(package_id, item) for item in broader or []],
        "related_concept_ids": [cid(package_id, item) for item in related or []],
        "excluded_concept_ids": [cid(package_id, item) for item in excluded or []],
        "applicable_context_concept_ids": [cid(package_id, item) for item in applicable or []],
        "description": description or f"ESRS 语义概念：{zh}。",
        "source_refs": [source_id(package_id, "eu-2023-2772")],
    }


def code_set(package_id: str, slug: str, values: list[tuple[str, str, str]], *, extensible=False):
    return {
        "code_set_id": f"{package_id}.codeset.{slug}",
        "extensible": extensible,
        "values": [
            {"code": code, "labels": {"zh": zh, "en": en}}
            for code, zh, en in values
        ],
    }


def dimension(package_id: str, slug: str, zh: str, en: str, *, codeset=None, value_type="enum", hierarchical=False):
    item = {
        "dimension_id": f"{package_id}.dimension.{slug}",
        "value_type": value_type,
        "labels": {"zh": zh, "en": en},
        "hierarchical": hierarchical,
        "description": f"按{zh}分解并保留报告原值。",
    }
    if codeset:
        item["code_set_id"] = f"{package_id}.codeset.{codeset}"
    return item


def metric(package_id: str, row: dict) -> dict:
    number = row["dp"]
    concept_groups = [[cid(package_id, item)] for item in row["concepts"]]
    result = {
        "metric_id": f"{package_id}.dp{number}",
        "source_datapoint_id": f"{row['dr']}_{number}",
        "disclosure_requirement": row["dr"],
        "paragraph": row["paragraph"],
        "application_requirements": row.get("ars", []),
        "labels": {"zh": row["zh"], "en": row["en"]},
        "official_data_type": row["official"],
        "data_class": row["class"],
        "value_family": row["family"],
        "obligation_class": row.get("obligation", "mandatory_if_material"),
        "allowed_value_origins": row.get("origins", ["reported"]),
        "cardinality": {"minimum": 0, "maximum": None},
        "required_dimension_ids": row.get("required_dimensions", []),
        "optional_dimension_ids": row.get("optional_dimensions", []),
        "description": SEMANTIC_DEFINITIONS.get(f"{row['dr']}_{number}", row.get("description", row["zh"])),
        "reporting_requirement": row.get(
            "requirement",
            "每个期间、边界和维度组合形成独立事实；保留报告原值、单位和可复现证据。",
        ),
        "source_refs": [
            source_id(package_id, "eu-2023-2772"),
            source_id(package_id, "efrag-ig3-2025"),
        ],
        "subject_concept_groups": concept_groups,
        "context_concept_ids": [cid(package_id, item) for item in row.get("contexts", [])],
        "excluded_concept_ids": [cid(package_id, item) for item in row.get("excluded", [])],
    }
    for field in (
        "fact_grain",
        "measurement_kind",
        "required_semantic_discriminators",
        "confusable_metric_ids",
        "evidence_form",
        "extraction_strategy",
        "identity_axes",
    ):
        if field in row:
            result[field] = row[field]
    return result


def element(package_id: str, dp: str, code: str, zh: str, en: str, *, role: str,
            record: str, storage: str, target: str, primary="string", fallback=None,
            codeset=None, unit_dimension=None, fixed=None, required=True, nullable=False,
            minimum=None, maximum=None, concepts=None, value_roots=None, repeated=False,
            description=None) -> dict:
    value_contract = {
        "primary_type": primary,
        "fallback_types": fallback or [],
        "code_set_id": codeset,
        "unit_dimension": unit_dimension,
        "fixed_value": fixed,
        "minimum": minimum,
        "maximum": maximum,
    }
    return {
        "element_id": f"{package_id}.dp{dp}.element.{code}",
        "metric_id": f"{package_id}.dp{dp}",
        "element_code": code,
        "labels": {"zh": zh, "en": en},
        "description": description or f"{zh}；保留报告原始表达并按合同类型化。",
        "semantic_role": role,
        "requirement_level": "required" if required else "optional",
        "cardinality": {"minimum": 1 if required else 0, "maximum": None if repeated else 1},
        "null_allowed": nullable,
        "value_contract": value_contract,
        "binding": {"record_type": record, "storage": storage, "target": target},
        "source_refs": [
            source_id(package_id, "eu-2023-2772"),
            source_id(package_id, "efrag-ig3-2025"),
        ],
        "concept_ids": [cid(package_id, item) for item in concepts or []],
        "value_concept_root_ids": [cid(package_id, item) for item in value_roots or []],
    }


def context_elements(package_id: str, dp: str, record: str) -> list[dict]:
    values = [
        element(package_id, dp, "reporting_period", "报告期", "Reporting period", role="period",
                record=record, storage="core_field", target="reporting_period_raw", required=True, nullable=True,
                concepts=["reporting-period"]),
        element(package_id, dp, "reporting_boundary", "报告边界", "Reporting boundary", role="scope",
                record=record, storage="core_field", target="reporting_boundary", required=True, nullable=True,
                concepts=["reporting-boundary"]),
        element(package_id, dp, "additional_breakdown", "其他分解维度", "Additional breakdown", role="breakdown",
                record=record, storage="attribute", target="additional_breakdown", primary="text",
                required=False, nullable=True, repeated=True,
                description="未能归入专门维度的其他可见分解标签；不得替代已有的报告实体、能源载体或聚合角色字段。"),
    ]
    if record == "quantitative_observation":
        values.append(
            element(
                package_id, dp, "reporting_entity", "报告实体", "Reporting entity",
                role="dimension", record=record, storage="dimension",
                target=f"{package_id}.dimension.reporting-entity", primary="string",
                fallback=["text"], required=False, nullable=True, repeated=True,
                description="与该数值直接对应的公司、子公司、项目、场地或业务实体可见名称。",
            )
        )
    return values


def aggregation_role_element(package_id: str, dp: str, fixed: str) -> dict:
    labels = {
        "total": ("指标总量", "Metric total"),
        "subtotal": ("指标子总计", "Metric subtotal"),
        "component": ("指标组成项", "Metric component"),
    }
    zh, en = labels[fixed]
    return element(
        package_id, dp, "aggregation_role", "指标聚合角色", "Metric aggregation role",
        role="context", record="quantitative_observation", storage="dimension",
        target=f"{package_id}.dimension.aggregation-role", primary="enum",
        fallback=["text"], codeset=f"{package_id}.codeset.aggregation-role",
        fixed=fixed, concepts=[],
        description=f"该 ESRS 数据点在指标层级中的固定角色：{zh}（{en}）。",
    )


def fixed_dimension(package_id: str, dp: str, code: str, zh: str, en: str, *, dimension_slug: str,
                    code_set_slug: str, fixed: str, concept_slug: str, record="quantitative_observation") -> dict:
    return element(
        package_id, dp, code, zh, en, role="dimension", record=record, storage="dimension",
        target=f"{package_id}.dimension.{dimension_slug}", primary="enum", fallback=["text"],
        codeset=f"{package_id}.codeset.{code_set_slug}", fixed=fixed, concepts=[concept_slug],
    )


def value_and_unit(package_id: str, dp: str, value_code: str, value_zh: str, value_en: str,
                   *, unit_code: str, unit_zh: str, unit_en: str, unit_dimension: str) -> list[dict]:
    return [
        element(package_id, dp, value_code, value_zh, value_en, role="value",
                record="quantitative_observation", storage="core_field", target="value_raw",
                primary="decimal", fallback=["decimal_range"], unit_dimension=unit_dimension),
        element(package_id, dp, unit_code, unit_zh, unit_en, role="unit",
                record="quantitative_observation", storage="core_field", target="unit_raw",
                primary="identifier", fallback=["string"], unit_dimension=unit_dimension),
    ]


def percentage_elements(package_id: str, dp: str) -> list[dict]:
    return [
        element(package_id, dp, "percentage", "百分比", "Percentage", role="value",
                record="quantitative_observation", storage="core_field", target="value_raw",
                primary="decimal", fallback=["decimal_range"],
                unit_dimension="extractesg.core.unit-dimension.ratio", minimum=0, maximum=100),
        element(package_id, dp, "percentage_unit", "百分比单位", "Percentage unit", role="unit",
                record="quantitative_observation", storage="core_field", target="unit_raw",
                primary="identifier", unit_dimension="extractesg.core.unit-dimension.ratio", fixed="%"),
    ]


def money_elements(package_id: str, dp: str, value_code="net_revenue") -> list[dict]:
    return [
        element(package_id, dp, value_code, "净收入", "Net revenue", role="value",
                record="quantitative_observation", storage="core_field", target="value_raw",
                primary="decimal", fallback=["decimal_range"], unit_dimension="extractesg.core.unit-dimension.currency"),
        element(package_id, dp, "currency", "货币", "Currency", role="unit",
                record="quantitative_observation", storage="core_field", target="unit_raw",
                primary="identifier", fallback=["string"], unit_dimension="extractesg.core.unit-dimension.currency"),
        element(package_id, dp, "currency_code", "货币代码", "Currency code", role="context",
                record="quantitative_observation", storage="attribute", target="currency_code",
                primary="enum", fallback=["string"], codeset="extractesg.core.codeset.currency", nullable=True),
        element(package_id, dp, "currency_scale", "货币量级", "Currency scale", role="context",
                record="quantitative_observation", storage="attribute", target="currency_scale",
                primary="enum", fallback=["string"], codeset="extractesg.core.codeset.currency-scale", nullable=True),
    ]


def qualitative_elements(package_id: str, dp: str, *, extras=None) -> list[dict]:
    values = [
        element(package_id, dp, "statement", "披露原文", "Disclosure statement", role="value",
                record="qualitative_assertion", storage="core_field", target="statement_raw", primary="text"),
        *context_elements(package_id, dp, "qualitative_assertion")[:2],
    ]
    values.extend(extras or [])
    return values


def sources(package_id: str, dr: str, law_locator: str, ig_locator: str) -> list[dict]:
    return [
        {
            "source_id": source_id(package_id, "eu-2023-2772"),
            "title": "Commission Delegated Regulation (EU) 2023/2772 — ESRS E1",
            "authority": "authoritative_law",
            "version_or_date": "2023-12-22",
            "locator": law_locator,
            "url": SOURCE_URL,
            "notes": f"{dr} 的法律语义、计量单位和应用要求以本来源为准。",
        },
        {
            "source_id": source_id(package_id, "efrag-ig3-2025"),
            "title": "EFRAG IG 3 List of ESRS Data Points",
            "authority": "non_authoritative_implementation_guidance",
            "version_or_date": "2025-06",
            "locator": ig_locator,
            "url": IG3_URL,
            "notes": "用于数据点清单、条件和 phase-in 交叉核对；与法规冲突时以法规正文为准。",
        },
    ]


def manifest(package_id: str, module: str, dr: str, package_version: str) -> dict:
    return {
        "format_version": "1.1",
        "package_id": package_id,
        "package_version": package_version,
        "status": "draft",
        "standard": {
            "framework_id": "esrs",
            "framework_version": "2023-12-22",
            "jurisdiction": "European Union",
            "module_id": module,
            "disclosure_requirement": dr,
            "effective_from": "2023-12-22",
            "effective_to": None,
        },
        "core_schema_id": "extractesg.core",
        "core_schema_version": "1.1.0",
        "default_language": "en",
        "supported_languages": ["en", "zh"],
        "files": {name: f"{name}.json" for name in [
            "metrics", "elements", "relations", "code_sets", "dimensions",
            "derivations", "validation_rules", "sources", "concepts",
        ]},
    }


def validation_rules(package_id: str, percentage_dps: list[str], derived_dps: list[str]) -> list[dict]:
    rules = [
        {
            "rule_id": f"{package_id}.validation.reported-result-requires-evidence",
            "target": package_id,
            "assertion": "evidence_required",
            "severity": "error",
            "parameters": {"when_value_origin": "reported"},
            "message": "Every reported result record must reference reproducible evidence.",
        }
    ]
    for dp in percentage_dps:
        rules.append(
            {
                "rule_id": f"{package_id}.validation.dp{dp}-percentage-range",
                "target": f"{package_id}.dp{dp}.element.percentage",
                "assertion": "numeric_range",
                "severity": "error",
                "parameters": {
                    "element_id": f"{package_id}.dp{dp}.element.percentage",
                    "minimum": 0,
                    "maximum": 100,
                },
                "message": "Percentage must be between 0 and 100 unless the source evidence is explicitly a range.",
            }
        )
    for dp in derived_dps:
        rules.append(
            {
                "rule_id": f"{package_id}.validation.dp{dp}-matching-context",
                "target": f"{package_id}.dp{dp}",
                "assertion": "matching_context",
                "severity": "error",
                "parameters": {"fields": ["reporting_period", "reporting_boundary", "unit_dimension", "dimension_values"]},
                "message": "Derivation operands must share period, reporting boundary and applicable dimensions, with compatible units.",
            }
        )
    return rules


def derivation(package_id: str, target: str, operation: str, operands: list[tuple[str, str]], *, unit_policy: str,
               required_match_fields: list[str] | None = None) -> dict:
    result = {
        "rule_id": f"{package_id}.derivation.dp{target}-{operation}",
        "target_metric_id": f"{package_id}.dp{target}",
        "operation": operation,
        "operands": [
            {
                "operand_id": f"dp{source}",
                "role": "numerator" if operation == "ratio" and index == 0 else "denominator" if operation == "ratio" else "addend",
                "description": description,
                "source_metric_id": f"{package_id}.dp{source}",
            }
            for index, (source, description) in enumerate(operands)
        ],
        "required_match_dimensions": [],
        "unit_policy": unit_policy,
        "guards": ([{"path": "denominator.value_numeric", "operator": "neq", "value": 0}] if operation == "ratio" else []),
        "execution_stage": "data_processing",
        "reported_value_priority": True,
        "source_refs": [source_id(package_id, "eu-2023-2772")],
    }
    if required_match_fields:
        result["required_match_fields"] = required_match_fields
    return result


def build_e1_5() -> None:
    package_id = "esrs.2023-set1.e1-5"
    dr = "E1-5"
    dim = lambda slug: f"{package_id}.dimension.{slug}"
    rows = [
        ("01", "37", ["AR 35"], "Total energy consumption from own operations", "自身运营的能源消费总量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "total-energy"], "amount", "total", "consumption", None),
        ("02", "37(a)", ["AR 32", "AR 33"], "Total energy consumption from fossil sources", "化石能源消费总量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "fossil-energy"], "amount", "fossil", "consumption", None),
        ("03", "37(b)", [], "Energy consumption from nuclear sources", "核能消费量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "nuclear-energy"], "amount", "nuclear", "consumption", None),
        ("04", "AR 34", ["AR 34"], "Share of nuclear sources in total energy consumption", "核能占能源消费总量的比例", "Percentage", "quantitative", "percentage", ["energy-consumption", "nuclear-energy", "energy-share"], "percentage", "nuclear", "consumption", None),
        ("05", "37(c)", [], "Total energy consumption from renewable sources", "可再生能源消费总量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "renewable-energy"], "amount", "renewable", "consumption", None),
        ("06", "37(c)(i)", [], "Fuel consumption from renewable sources", "可再生燃料消费量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "renewable-fuel"], "amount", "renewable", "consumption", "renewable_fuel"),
        ("07", "37(c)(ii)", [], "Purchased or acquired renewable electricity, heat, steam and cooling", "外购或购入的可再生电力、热力、蒸汽和冷却能源", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "renewable-energy", "purchased-energy"], "amount", "renewable", "consumption", "purchased_electricity_heat_steam_cooling"),
        ("08", "37(c)(iii)", [], "Self-generated non-fuel renewable energy", "自产非燃料可再生能源", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "renewable-energy", "self-generated-energy"], "amount", "renewable", "consumption", "self_generated_non_fuel"),
        ("09", "AR 34", ["AR 34"], "Share of renewable sources in total energy consumption", "可再生能源占能源消费总量的比例", "Percentage", "quantitative", "percentage", ["energy-consumption", "renewable-energy", "energy-share"], "percentage", "renewable", "consumption", None),
        ("10", "38(a)", ["AR 33"], "Fuel consumption from coal and coal products", "煤炭及煤炭制品的燃料消费量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "fossil-energy", "coal-energy"], "amount", "fossil", "consumption", "coal"),
        ("11", "38(b)", ["AR 33"], "Fuel consumption from crude oil and petroleum products", "原油及石油产品的燃料消费量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "fossil-energy", "petroleum-energy"], "amount", "fossil", "consumption", "petroleum"),
        ("12", "38(c)", ["AR 33"], "Fuel consumption from natural gas", "天然气燃料消费量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "fossil-energy", "natural-gas-energy"], "amount", "fossil", "consumption", "natural_gas"),
        ("13", "38(d)", ["AR 33"], "Fuel consumption from other fossil sources", "其他化石能源燃料消费量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "fossil-energy", "other-fossil-energy"], "amount", "fossil", "consumption", "other_fossil"),
        ("14", "38(e)", ["AR 33"], "Purchased or acquired electricity, heat, steam and cooling from fossil sources", "外购或购入的化石电力、热力、蒸汽和冷却能源", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "fossil-energy", "purchased-energy"], "amount", "fossil", "consumption", "purchased_electricity_heat_steam_cooling"),
        ("15", "AR 34", ["AR 34"], "Share of fossil sources in total energy consumption", "化石能源占能源消费总量的比例", "Percentage", "quantitative", "percentage", ["energy-consumption", "fossil-energy", "energy-share"], "percentage", "fossil", "consumption", None),
        ("16", "39", [], "Non-renewable energy production", "非可再生能源生产量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-production", "non-renewable-energy"], "amount", "non_renewable", "production", None),
        ("17", "39", [], "Renewable energy production", "可再生能源生产量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-production", "renewable-energy"], "amount", "renewable", "production", None),
        ("18", "40", ["AR 36"], "Energy intensity in high climate impact sectors", "高气候影响行业的能源强度", "Energy intensity (MWh per monetary unit)", "quantitative", "energy_intensity", ["energy-intensity", "high-climate-impact-sector"], "intensity", None, "consumption", None),
        ("19", "41", [], "Total energy consumption in high climate impact sectors", "高气候影响行业能源消费总量", "Energy/MWh", "quantitative", "quantitative_energy", ["energy-consumption", "high-climate-impact-sector"], "amount", "total", "consumption", None),
        ("20", "42", [], "High climate impact sectors used to determine energy intensity", "用于确定能源强度的高气候影响行业清单", "Table/list", "qualitative", "structure", ["high-climate-impact-sector", "energy-intensity"], "sector_list", None, None, None),
        ("21", "43", ["AR 38"], "Reconciliation of net revenue used for energy intensity", "能源强度所用净收入的对账说明", "Narrative", "qualitative", "narrative", ["net-revenue", "reconciliation", "energy-intensity"], "narrative", None, None, None),
        ("22", "AR 38(b)", ["AR 38"], "Net revenue from activities in high climate impact sectors", "高气候影响行业活动的净收入", "Monetary", "quantitative", "monetary", ["net-revenue", "high-climate-impact-sector"], "money", None, None, None),
        ("23", "AR 38(b)", ["AR 38"], "Other net revenue", "其他净收入", "Monetary", "quantitative", "monetary", ["net-revenue", "reconciliation"], "money", None, None, None),
    ]
    metrics = []
    elements = []
    for dp, paragraph, ars, en, zh, official, data_class, family, concepts, kind, source, flow, carrier in rows:
        required_dims = []
        optional_dims = ["extractesg.core.dimension.business-segment", "extractesg.core.dimension.geography"]
        if data_class == "quantitative":
            optional_dims.append(dim("reporting-entity"))
        if kind in {"amount", "percentage"}:
            required_dims.extend([dim("energy-source"), dim("energy-flow")])
        if kind == "amount":
            required_dims.append(dim("aggregation-role"))
        if carrier:
            required_dims.append(dim("energy-carrier"))
        if dp in {"18", "19", "20"}:
            optional_dims.append(dim("high-climate-impact-sector"))
        if kind == "money":
            required_dims.append(dim("revenue-scope"))
        obligation = "mandatory_if_material"
        if dp in {"10", "11", "12", "13", "14", "15", "18", "19", "20", "21"}:
            obligation = "conditional_if_material"
        if dp in {"22", "23"}:
            obligation = "conditional_voluntary_if_material"
        row = {
            "dp": dp, "dr": dr, "paragraph": paragraph, "ars": ars, "en": en, "zh": zh,
            "official": official, "class": data_class, "family": family, "concepts": concepts,
            "obligation": obligation, "required_dimensions": required_dims,
            "optional_dimensions": optional_dims,
            "contexts": ["reporting-period", "reporting-boundary"],
            "origins": ["reported", "derived"] if dp in {"01", "02", "04", "05", "09", "15", "18"} else ["reported"],
        }
        if dp == "18":
            row["description"] = "以高气候影响行业的能源消费量除以相应净收入形成能源强度；法规计量语义为 MWh/货币单位。"
        metrics.append(metric(package_id, row))
        if kind == "amount":
            elements.extend(value_and_unit(package_id, dp, "energy_amount", "能源量", "Energy amount",
                                           unit_code="energy_unit", unit_zh="能源单位", unit_en="Energy unit",
                                           unit_dimension="extractesg.core.unit-dimension.energy"))
            source_concept = {
                "total": "total-energy",
                "fossil": "fossil-energy",
                "nuclear": "nuclear-energy",
                "renewable": "renewable-energy",
                "non_renewable": "non-renewable-energy",
            }[source]
            elements.append(fixed_dimension(package_id, dp, "energy_source", "能源来源", "Energy source",
                                            dimension_slug="energy-source", code_set_slug="energy-source", fixed=source,
                                            concept_slug=source_concept))
            elements.append(fixed_dimension(package_id, dp, "energy_flow", "能源流向", "Energy flow",
                                            dimension_slug="energy-flow", code_set_slug="energy-flow", fixed=flow,
                                            concept_slug="energy-production" if flow == "production" else "energy-consumption"))
            if carrier:
                carrier_concept = {
                    "coal": "coal-energy", "petroleum": "petroleum-energy", "natural_gas": "natural-gas-energy",
                    "other_fossil": "other-fossil-energy", "renewable_fuel": "renewable-fuel",
                    "purchased_electricity_heat_steam_cooling": "purchased-energy",
                    "self_generated_non_fuel": "self-generated-energy",
                }[carrier]
                elements.append(fixed_dimension(package_id, dp, "energy_carrier", "能源载体", "Energy carrier",
                                                dimension_slug="energy-carrier", code_set_slug="energy-carrier", fixed=carrier,
                                                concept_slug=carrier_concept))
            aggregation_role = (
                "total" if dp in {"01", "02", "05", "16", "17", "19"}
                else "component"
            )
            elements.append(aggregation_role_element(package_id, dp, aggregation_role))
            if dp in {"18", "19"}:
                pass
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))
        elif kind == "percentage":
            elements.extend(percentage_elements(package_id, dp))
            elements.append(fixed_dimension(package_id, dp, "energy_source", "能源来源", "Energy source",
                                            dimension_slug="energy-source", code_set_slug="energy-source", fixed=source,
                                            concept_slug=concepts[1]))
            elements.append(fixed_dimension(package_id, dp, "energy_flow", "能源流向", "Energy flow",
                                            dimension_slug="energy-flow", code_set_slug="energy-flow", fixed=flow,
                                            concept_slug="energy-consumption"))
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))
        elif kind == "intensity":
            elements.extend(value_and_unit(package_id, dp, "energy_intensity", "能源强度", "Energy intensity",
                                           unit_code="intensity_unit", unit_zh="强度单位", unit_en="Intensity unit",
                                           unit_dimension="extractesg.core.unit-dimension.energy-intensity"))
            elements.append(fixed_dimension(package_id, dp, "energy_flow", "能源流向", "Energy flow",
                                            dimension_slug="energy-flow", code_set_slug="energy-flow", fixed="consumption",
                                            concept_slug="energy-consumption"))
            elements.extend([
                element(package_id, dp, "numerator_energy_unit", "分子能源单位", "Numerator energy unit", role="context",
                        record="quantitative_observation", storage="attribute", target="numerator_energy_unit",
                        primary="identifier", fallback=["string"], unit_dimension="extractesg.core.unit-dimension.energy", nullable=True),
                element(package_id, dp, "denominator_currency", "分母货币", "Denominator currency", role="context",
                        record="quantitative_observation", storage="attribute", target="denominator_currency",
                        primary="enum", fallback=["string"], codeset="extractesg.core.codeset.currency", nullable=True),
                element(package_id, dp, "denominator_scale", "分母货币量级", "Denominator currency scale", role="context",
                        record="quantitative_observation", storage="attribute", target="denominator_scale",
                        primary="enum", fallback=["string"], codeset="extractesg.core.codeset.currency-scale", nullable=True),
                element(package_id, dp, "high_climate_impact_sector", "高气候影响行业", "High climate impact sector", role="dimension",
                        record="quantitative_observation", storage="dimension", target=dim("high-climate-impact-sector"),
                        required=False, nullable=True, repeated=True, concepts=["high-climate-impact-sector"]),
            ])
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))
        elif kind == "sector_list":
            extras = [element(package_id, dp, "high_climate_impact_sector", "高气候影响行业", "High climate impact sector",
                              role="subject", record="qualitative_assertion", storage="dimension",
                              target=dim("high-climate-impact-sector"), primary="string", fallback=["text"],
                              concepts=["high-climate-impact-sector"], value_roots=["high-climate-impact-sector"])]
            elements.extend(qualitative_elements(package_id, dp, extras=extras))
        elif kind == "narrative":
            elements.extend(qualitative_elements(package_id, dp))
        elif kind == "money":
            elements.extend(money_elements(package_id, dp))
            revenue_scope = "high_climate_impact" if dp == "22" else "other"
            elements.append(fixed_dimension(package_id, dp, "revenue_scope", "净收入范围", "Revenue scope",
                                            dimension_slug="revenue-scope", code_set_slug="revenue-scope", fixed=revenue_scope,
                                            concept_slug="high-climate-impact-sector" if dp == "22" else "net-revenue"))
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))

    concepts = [
        concept(package_id, "energy", "能源", "Energy", aliases={"zh": ["能耗"], "en": ["power and fuel"]}),
        concept(package_id, "energy-consumption", "能源消费", "Energy consumption", aliases={"zh": ["能源消耗", "能源使用", "能源消耗量", "能源消费量"], "en": ["energy use", "energy consumed"]}, broader=["energy"], related=["energy-production"]),
        concept(package_id, "energy-production", "能源生产", "Energy production", aliases={"zh": ["能源产量", "发电量", "能源产生量"], "en": ["energy generated", "power generation"]}, broader=["energy"], related=["energy-consumption"]),
        concept(package_id, "total-energy", "能源总量", "Total energy", aliases={"zh": ["能源消费总量", "总能源消耗"], "en": ["total energy consumption"]}, broader=["energy"]),
        concept(package_id, "fossil-energy", "化石能源", "Fossil energy", aliases={"zh": ["化石来源能源", "化石燃料", "化石能源消耗"], "en": ["fossil sources", "fossil fuel energy"]}, broader=["energy"], excluded=["nuclear-energy", "renewable-energy"]),
        concept(package_id, "nuclear-energy", "核能", "Nuclear energy", aliases={"zh": ["核能源"], "en": ["nuclear sources"]}, broader=["energy"], excluded=["fossil-energy", "renewable-energy"]),
        concept(package_id, "renewable-energy", "可再生能源", "Renewable energy", aliases={"zh": ["绿色能源"], "en": ["renewable sources"]}, broader=["energy"], excluded=["fossil-energy", "nuclear-energy"]),
        concept(package_id, "non-renewable-energy", "非可再生能源", "Non-renewable energy", aliases={"en": ["non renewable energy"]}, broader=["energy"]),
        concept(package_id, "renewable-fuel", "可再生燃料", "Renewable fuel", aliases={"en": ["renewable fuels"]}, broader=["renewable-energy"]),
        concept(package_id, "purchased-energy", "外购能源", "Purchased energy", aliases={"zh": ["购入能源", "外购电力", "购入电力", "间接能源", "电力", "蒸汽", "热力", "冷却"], "en": ["acquired energy", "purchased electricity", "purchased heat", "purchased steam", "purchased cooling"]}, broader=["energy"], excluded=["coal-energy", "petroleum-energy", "natural-gas-energy", "other-fossil-energy"]),
        concept(package_id, "self-generated-energy", "自产能源", "Self-generated energy", aliases={"zh": ["自发能源"], "en": ["self generated energy"]}, broader=["energy"]),
        concept(package_id, "coal-energy", "煤炭能源", "Coal energy", aliases={"zh": ["煤炭及煤炭制品", "煤炭", "原煤", "焦炭"], "en": ["coal and coal products"]}, broader=["fossil-energy"], excluded=["petroleum-energy", "natural-gas-energy", "other-fossil-energy", "purchased-energy"]),
        concept(package_id, "petroleum-energy", "石油能源", "Petroleum energy", aliases={"zh": ["原油及石油产品", "石油产品", "汽油", "柴油", "煤油", "航空煤油", "燃料油", "重油", "液化石油气"], "en": ["crude oil and petroleum products", "gasoline", "petrol", "diesel", "kerosene", "fuel oil", "LPG"]}, broader=["fossil-energy"], excluded=["coal-energy", "natural-gas-energy", "other-fossil-energy", "purchased-energy"]),
        concept(package_id, "gasoline", "汽油", "Gasoline", aliases={"en": ["petrol"]}, broader=["petroleum-energy"]),
        concept(package_id, "diesel", "柴油", "Diesel", aliases={"zh": ["柴油燃料"]}, broader=["petroleum-energy"]),
        concept(package_id, "kerosene", "煤油", "Kerosene", aliases={"zh": ["航空煤油"]}, broader=["petroleum-energy"]),
        concept(package_id, "fuel-oil", "燃料油", "Fuel oil", aliases={"zh": ["重油"]}, broader=["petroleum-energy"]),
        concept(package_id, "lpg", "液化石油气", "Liquefied petroleum gas", aliases={"zh": ["石油气"], "en": ["LPG"]}, broader=["petroleum-energy"]),
        concept(package_id, "natural-gas-energy", "天然气能源", "Natural gas energy", aliases={"zh": ["天然气燃料", "天然气", "天然氣", "天然气消费", "天然气用量", "燃气"], "en": ["natural gas fuel", "natural gas consumption"]}, broader=["fossil-energy"], excluded=["coal-energy", "petroleum-energy", "other-fossil-energy", "purchased-energy"]),
        concept(package_id, "other-fossil-energy", "其他化石能源", "Other fossil energy", aliases={"zh": ["其他直接能源", "其他化石来源"], "en": ["other fossil sources", "other direct energy"]}, broader=["fossil-energy"], excluded=["coal-energy", "petroleum-energy", "natural-gas-energy", "purchased-energy"]),
        concept(package_id, "energy-share", "能源占比", "Energy share", aliases={"zh": ["能源比例"], "en": ["share of energy consumption"]}, related=["energy-consumption"]),
        concept(package_id, "energy-intensity", "能源强度", "Energy intensity", aliases={"zh": ["能耗强度"], "en": ["energy consumption intensity"]}, formulas=["energy consumption / net revenue"], related=["net-revenue"]),
        concept(package_id, "high-climate-impact-sector", "高气候影响行业", "High climate impact sector", aliases={"zh": ["高气候影响部门"], "en": ["high climate impact sectors"]}, abbreviations=["HCI sector"]),
        concept(package_id, "net-revenue", "净收入", "Net revenue", aliases={"zh": ["营业净收入"], "en": ["revenue used for intensity"]}),
        concept(package_id, "reconciliation", "对账", "Reconciliation", aliases={"zh": ["衔接说明"], "en": ["reconciliation to financial statements"]}),
        concept(package_id, "reporting-period", "报告期", "Reporting period", aliases={"zh": ["报告年度"], "en": ["reporting year"]}),
        concept(package_id, "reporting-boundary", "报告边界", "Reporting boundary", aliases={"zh": ["报告范围"], "en": ["organisational boundary"]}),
    ]
    code_sets = [
        code_set(package_id, "energy-source", [("total", "全部能源", "Total energy"), ("fossil", "化石能源", "Fossil"), ("nuclear", "核能", "Nuclear"), ("renewable", "可再生能源", "Renewable"), ("non_renewable", "非可再生能源", "Non-renewable")]),
        code_set(package_id, "energy-flow", [("consumption", "消费", "Consumption"), ("production", "生产", "Production")]),
        code_set(package_id, "energy-carrier", [("coal", "煤炭及煤炭制品", "Coal and coal products"), ("petroleum", "原油及石油产品", "Crude oil and petroleum products"), ("gasoline", "汽油", "Gasoline"), ("diesel", "柴油", "Diesel"), ("kerosene", "煤油", "Kerosene"), ("fuel_oil", "燃料油", "Fuel oil"), ("lpg", "液化石油气", "Liquefied petroleum gas"), ("natural_gas", "天然气", "Natural gas"), ("other_fossil", "其他化石来源", "Other fossil sources"), ("renewable_fuel", "可再生燃料", "Renewable fuel"), ("purchased_electricity_heat_steam_cooling", "外购电力、热力、蒸汽和冷却", "Purchased electricity, heat, steam and cooling"), ("self_generated_non_fuel", "自产非燃料能源", "Self-generated non-fuel energy")]),
        code_set(package_id, "aggregation-role", [("total", "指标总量", "Metric total"), ("subtotal", "指标子总计", "Metric subtotal"), ("component", "指标组成项", "Metric component")]),
        code_set(package_id, "revenue-scope", [("high_climate_impact", "高气候影响行业活动", "High climate impact sector activities"), ("other", "其他活动", "Other activities"), ("total", "总净收入", "Total net revenue")]),
    ]
    dimensions = [
        dimension(package_id, "energy-source", "能源来源", "Energy source", codeset="energy-source"),
        dimension(package_id, "energy-flow", "能源流向", "Energy flow", codeset="energy-flow"),
        dimension(package_id, "energy-carrier", "能源载体", "Energy carrier", codeset="energy-carrier", hierarchical=True),
        dimension(package_id, "reporting-entity", "报告实体", "Reporting entity", value_type="string", hierarchical=True),
        dimension(package_id, "aggregation-role", "指标聚合角色", "Metric aggregation role", codeset="aggregation-role"),
        dimension(package_id, "high-climate-impact-sector", "高气候影响行业", "High climate impact sector", value_type="string", hierarchical=True),
        dimension(package_id, "revenue-scope", "净收入范围", "Revenue scope", codeset="revenue-scope"),
    ]
    relations = [
        {
            "relation_id": f"{package_id}.relation.high-climate-impact-conditional",
            "relation_type": "conditional_activation", "source_metric_ids": [],
            "target_metric_ids": [f"{package_id}.dp{dp}" for dp in ["10", "11", "12", "13", "14", "15", "18", "19", "20", "21", "22", "23"]],
            "member_metric_ids": [],
            "activation_predicate": {"path": "report_context.has_high_climate_impact_sector_activity", "operator": "eq", "value": True},
            "policy": "activate-when-condition-met",
            "description": "Only applies when the undertaking has activities in high climate impact sectors.",
            "source_refs": [source_id(package_id, "eu-2023-2772")],
        },
        {
            "relation_id": f"{package_id}.relation.energy-production-conditional",
            "relation_type": "conditional_activation", "source_metric_ids": [],
            "target_metric_ids": [f"{package_id}.dp16", f"{package_id}.dp17"], "member_metric_ids": [],
            "activation_predicate": {"path": "report_context.produces_energy", "operator": "eq", "value": True},
            "policy": "activate-when-condition-met", "description": "Production metrics apply when the undertaking produces energy.",
            "source_refs": [source_id(package_id, "eu-2023-2772")],
        },
        {
            "relation_id": f"{package_id}.relation.intensity-context",
            "relation_type": "contextualizes",
            "source_metric_ids": [f"{package_id}.dp{dp}" for dp in ["19", "20", "21", "22", "23"]],
            "target_metric_ids": [f"{package_id}.dp18"], "member_metric_ids": [],
            "activation_predicate": None, "policy": "retain-linked-context",
            "description": "Energy amount, sectors and reconciled revenue explain the reported energy intensity.",
            "source_refs": [source_id(package_id, "eu-2023-2772")],
        },
    ]
    derivations = [
        derivation(package_id, "01", "sum", [("02", "fossil"), ("03", "nuclear"), ("05", "renewable")], unit_policy="compatible-energy-units"),
        derivation(package_id, "02", "sum", [("10", "coal"), ("11", "petroleum"), ("12", "gas"), ("13", "other"), ("14", "purchased")], unit_policy="compatible-energy-units"),
        derivation(package_id, "05", "sum", [("06", "fuel"), ("07", "purchased"), ("08", "self-generated")], unit_policy="compatible-energy-units"),
        derivation(package_id, "04", "ratio", [("03", "nuclear"), ("01", "total")], unit_policy="percentage-of-compatible-energy"),
        derivation(package_id, "09", "ratio", [("05", "renewable"), ("01", "total")], unit_policy="percentage-of-compatible-energy"),
        derivation(package_id, "15", "ratio", [("02", "fossil"), ("01", "total")], unit_policy="percentage-of-compatible-energy"),
        derivation(package_id, "18", "ratio", [("19", "energy"), ("22", "revenue")], unit_policy="energy-per-monetary-unit"),
    ]
    package_dir = ROOT / f"packages/esrs/2023-set1/e1-5/{E1_5_PACKAGE_VERSION}"
    payloads = {
        "manifest": manifest(package_id, "esrs.e1-5", dr, E1_5_PACKAGE_VERSION), "metrics": metrics, "elements": elements,
        "concepts": concepts, "relations": relations, "code_sets": code_sets, "dimensions": dimensions,
        "derivations": derivations, "validation_rules": validation_rules(package_id, ["04", "09", "15"], ["01", "02", "04", "05", "09", "15", "18"]),
        "sources": sources(package_id, dr, "ESRS E1 paragraphs 35–43 and AR 32–38", "ESRS E1 worksheet rows 84–106 (E1-5_01 through E1-5_23)"),
    }
    for name, payload in payloads.items():
        write_json(package_dir / f"{name}.json", payload)


def build_e1_6() -> None:
    package_id = "esrs.2023-set1.e1-6"
    dr = "E1-6"
    dim = lambda slug: f"{package_id}.dimension.{slug}"
    rows = [
        ("01", "44", ["AR 39"], "Gross Scope 1, 2, 3 and total GHG emissions table", "范围1、2、3及温室气体排放总量的披露结构", "Table", "structure", "structure", ["ghg-emissions", "gross-ghg-emissions"], "structure"),
        ("02", "50", [], "Financial control and operational control boundary table", "财务控制与运营控制边界表", "Table", "structure", "structure", ["ghg-emissions", "control-boundary"], "control_structure"),
        ("03", "AR 41", ["AR 41"], "GHG emissions disaggregation", "温室气体排放分解维度", "Table", "structure", "structure", ["ghg-emissions", "disaggregation"], "structure"),
        ("04", "AR 46(d)", ["AR 46"], "Scope 3 categories using the GHG Protocol", "采用《温室气体核算体系》的范围3类别结构", "Table", "structure", "structure", ["scope3-emissions", "ghg-protocol"], "scope3_protocol"),
        ("05", "AR 50", ["AR 50"], "Scope 3 categories using ISO 14064-1", "采用 ISO 14064-1 的范围3类别结构", "Table", "structure", "structure", ["scope3-emissions", "iso-14064"], "scope3_iso"),
        ("06", "AR 52", ["AR 52"], "Scope 3 value-chain disaggregation", "范围3价值链分解结构", "Table", "structure", "structure", ["scope3-emissions", "value-chain"], "value_chain"),
        ("07", "48(a)", ["AR 43"], "Gross Scope 1 GHG emissions", "范围1温室气体排放总量", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["scope1-ghg-emissions"], "ghg"),
        ("08", "48(b)", ["AR 44"], "Percentage of Scope 1 GHG emissions from regulated emission trading schemes", "受监管排放交易体系覆盖的范围1排放比例", "Percentage", "quantitative", "percentage", ["scope1-emissions", "emissions-trading-scheme"], "percentage"),
        ("09", "49(a), 52(a)", ["AR 45", "AR 47"], "Gross location-based Scope 2 GHG emissions", "基于位置法的范围2温室气体排放总量", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["gross-ghg-emissions", "scope2-emissions", "location-based"], "ghg"),
        ("10", "49(b), 52(b)", ["AR 45", "AR 47"], "Gross market-based Scope 2 GHG emissions", "基于市场法的范围2温室气体排放总量", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["gross-ghg-emissions", "scope2-emissions", "market-based"], "ghg"),
        ("11", "51", ["AR 46"], "Gross Scope 3 GHG emissions by significant category", "按重大类别划分的范围3温室气体排放总量", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["gross-ghg-emissions", "scope3-emissions", "scope3-category"], "ghg"),
        ("12", "44, 52(a)", ["AR 47"], "Total GHG emissions using location-based Scope 2", "范围1＋位置法范围2＋范围3温室气体排放总量", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["total-ghg-emissions", "location-based"], "ghg"),
        ("13", "44, 52(b)", ["AR 47"], "Total GHG emissions using market-based Scope 2", "范围1＋市场法范围2＋范围3温室气体排放总量", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["total-ghg-emissions", "market-based"], "ghg"),
        ("14", "47", [], "Changes in reporting boundary, methods, data or emission factors", "报告边界、方法、数据或排放因子的变更及可比性", "Narrative", "qualitative", "narrative", ["comparability-change", "ghg-emissions"], "narrative"),
        ("15", "AR 39(b)", ["AR 39"], "Methods, assumptions, emission factors and calculation tools", "温室气体方法、假设、排放因子和计算工具", "Narrative", "qualitative", "narrative", ["methodology", "emission-factor", "calculation-tool"], "narrative"),
        ("16", "AR 42(c)", ["AR 42"], "Significant events and changes between reporting dates", "报告日期之间的重大事件和变化", "Narrative", "qualitative", "narrative", ["comparability-change", "reporting-period"], "narrative"),
        ("17", "AR 43(c)", ["AR 43"], "Biogenic CO2 emissions excluded from Scope 1", "范围1中单独披露的生物源二氧化碳排放", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["biogenic-co2", "scope1-emissions"], "ghg"),
        ("18", "AR 45(d)", ["AR 45"], "Share of Scope 2 emissions linked to contractual instruments", "与合同工具相关的范围2排放比例", "Percentage", "quantitative", "percentage", ["scope2-emissions", "contractual-instrument"], "percentage"),
        ("19", "AR 45(d)", ["AR 45"], "Types of contractual instruments", "合同工具类型", "List/text", "qualitative", "structure", ["contractual-instrument", "scope2-emissions"], "instrument_list"),
        ("20", "AR 45(d)", ["AR 45"], "Share of market-based Scope 2 emissions linked to bundled instruments", "基于市场法且关联捆绑工具的范围2排放比例", "Percentage", "quantitative", "percentage", ["scope2-emissions", "market-based", "bundled-instrument"], "percentage"),
        ("21", "AR 45(d)", ["AR 45"], "Share linked to bundled energy attribute claims", "关联捆绑能源属性声明的比例", "Percentage", "quantitative", "percentage", ["scope2-emissions", "bundled-attribute-claim"], "percentage"),
        ("22", "AR 45(d)", ["AR 45"], "Share linked to unbundled energy attribute claims", "关联非捆绑能源属性声明的比例", "Percentage", "quantitative", "percentage", ["scope2-emissions", "unbundled-attribute-claim"], "percentage"),
        ("23", "AR 45(d)", ["AR 45"], "Types and mix of contractual instruments", "合同工具的类型与组合说明", "Narrative", "qualitative", "narrative", ["contractual-instrument", "scope2-emissions"], "instrument_list"),
        ("24", "AR 45(e)", ["AR 45"], "Biogenic CO2 emissions excluded from Scope 2", "范围2中单独披露的生物源二氧化碳排放", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["biogenic-co2", "scope2-emissions"], "ghg"),
        ("25", "AR 46(g)", ["AR 46"], "Percentage of Scope 3 emissions calculated using primary data", "使用从供应商或价值链伙伴取得的一手数据计算的范围3排放比例", "Percentage", "quantitative", "percentage", ["scope3-emissions", "primary-data"], "percentage"),
        ("26", "AR 46(i)", ["AR 46"], "Reasons for excluding a Scope 3 category", "排除范围3类别的理由", "Narrative", "qualitative", "narrative", ["scope3-emissions", "excluded-category"], "excluded_category"),
        ("27", "AR 46(i)", ["AR 46"], "Scope 3 categories included in the inventory", "纳入盘查的范围3类别清单", "List", "qualitative", "structure", ["scope3-emissions", "scope3-category"], "category_list"),
        ("28", "AR 46(j)", ["AR 46"], "Biogenic CO2 emissions excluded from Scope 3", "范围3中单独披露的生物源二氧化碳排放", "GHG emissions/tCO2e", "quantitative", "quantitative_ghg", ["biogenic-co2", "scope3-emissions"], "ghg"),
        ("29", "AR 46(h)", ["AR 46"], "Scope 3 boundaries, methods, assumptions and tools", "范围3边界、方法、假设与工具", "Narrative", "qualitative", "narrative", ["scope3-emissions", "methodology", "control-boundary"], "narrative"),
        ("30", "53", ["AR 53"], "Location-based GHG intensity", "基于位置法的温室气体排放强度", "GHG intensity (tCO2e per monetary unit)", "quantitative", "ghg_intensity", ["ghg-intensity", "location-based"], "intensity"),
        ("31", "53", ["AR 53"], "Market-based GHG intensity", "基于市场法的温室气体排放强度", "GHG intensity (tCO2e per monetary unit)", "quantitative", "ghg_intensity", ["ghg-intensity", "market-based"], "intensity"),
        ("32", "55", ["AR 55"], "Reconciliation of net revenue used for GHG intensity", "温室气体强度所用净收入的对账说明", "Narrative", "qualitative", "narrative", ["net-revenue", "reconciliation", "ghg-intensity"], "narrative"),
        ("33", "AR 55", ["AR 55"], "Total net revenue", "净收入总额", "Monetary", "quantitative", "monetary", ["net-revenue"], "money"),
        ("34", "AR 55", ["AR 55"], "Net revenue used to calculate GHG intensity", "用于计算温室气体强度的净收入", "Monetary", "quantitative", "monetary", ["net-revenue", "ghg-intensity"], "money"),
        ("35", "AR 55", ["AR 55"], "Other net revenue", "其他净收入", "Monetary", "quantitative", "monetary", ["net-revenue", "reconciliation"], "money"),
    ]
    fixed_by_dp = {
        "07": ("scope1", None), "08": ("scope1", None), "09": ("scope2", "location_based"),
        "10": ("scope2", "market_based"), "11": ("scope3", None), "12": ("total", "location_based"),
        "13": ("total", "market_based"), "17": ("scope1", None), "18": ("scope2", None),
        "20": ("scope2", "market_based"), "21": ("scope2", None), "22": ("scope2", None),
        "24": ("scope2", None), "25": ("scope3", None), "28": ("scope3", None),
        "30": ("total", "location_based"), "31": ("total", "market_based"),
    }
    confusable_families = [
        {"09", "10"}, {"12", "13"}, {"17", "24", "28"},
        {"20", "21", "22"}, {"26", "27"}, {"30", "31"},
        {"33", "34", "35"},
    ]
    metrics = []
    elements = []
    phase_in = {"04", "05", "06", "11", "12", "13", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35"}
    for dp, paragraph, ars, en, zh, official, data_class, family, concepts, kind in rows:
        required_dims = []
        optional_dims = ["extractesg.core.dimension.business-segment", "extractesg.core.dimension.geography"]
        if data_class == "quantitative":
            optional_dims.append(dim("reporting-entity"))
        scope_method = fixed_by_dp.get(dp)
        if scope_method:
            required_dims.append(dim("ghg-scope"))
            if scope_method[1]:
                required_dims.append(dim("scope2-method"))
        if dp in {"11", "26", "27"}:
            optional_dims.append(dim("scope3-category"))
        if kind == "ghg":
            required_dims.append(dim("aggregation-role"))
        obligation = "mandatory_if_material"
        if dp in {"04", "05"}:
            obligation = "alternative_if_material"
        elif dp in {"20", "21", "22"}:
            obligation = "voluntary_if_material"
        elif dp in phase_in:
            obligation = "conditional_if_material"
        row = {
            "dp": dp, "dr": dr, "paragraph": paragraph, "ars": ars, "en": en, "zh": zh,
            "official": official, "class": data_class, "family": family, "concepts": concepts,
            "obligation": obligation, "required_dimensions": required_dims, "optional_dimensions": optional_dims,
            "contexts": ["reporting-period", "reporting-boundary"],
            "origins": ["reported", "derived"] if dp in {"12", "13", "30", "31"} else ["reported"],
            "fact_grain": (
                "one_source_value_cell_per_period_entity"
                if data_class == "quantitative"
                else "one_evidenced_claim_or_list_member"
            ),
            "measurement_kind": family,
            "required_semantic_discriminators": [
                item for item in (
                    "reporting_period",
                    "reporting_entity" if data_class == "quantitative" else None,
                    "ghg_scope" if scope_method else None,
                    "scope_2_method" if scope_method and scope_method[1] else None,
                    "scope3_category" if dp in {"04", "05", "11", "26", "27"} else None,
                    "revenue_scope" if dp in {"33", "34", "35"} else None,
                ) if item
            ],
            "confusable_metric_ids": sorted(
                f"{package_id}.dp{member}"
                for family_members in confusable_families if dp in family_members
                for member in family_members if member != dp
            ),
            "evidence_form": (
                ["structured_table_row", "linked_method_context"]
                if data_class == "quantitative"
                else ["bounded_text_or_list_member", "linked_method_context"]
            ),
            "extraction_strategy": (
                "reported_or_derived" if dp in {"12", "13", "30", "31"} else "reported"
            ),
            "identity_axes": [
                item for item in (
                    "source_row_topic", "reporting_period",
                    "reporting_entity" if data_class == "quantitative" else None,
                    "entity_aggregation_role" if data_class == "quantitative" else None,
                    "ghg_scope" if scope_method else None,
                    "scope_2_method" if scope_method and scope_method[1] else None,
                    "scope3_category" if dp in {"04", "05", "11", "26", "27"} else None,
                ) if item
            ],
        }
        metrics.append(metric(package_id, row))
        if kind in {"structure", "control_structure", "scope3_protocol", "scope3_iso", "value_chain"}:
            if kind == "control_structure":
                elements.extend(
                    qualitative_elements(
                        package_id,
                        dp,
                        extras=[
                            element(
                                package_id,
                                dp,
                                "control_boundary",
                                "控制边界",
                                "Control boundary",
                                role="subject",
                                record="qualitative_assertion",
                                storage="dimension",
                                target=dim("control-boundary"),
                                primary="enum",
                                fallback=["text"],
                                codeset=f"{package_id}.codeset.control-boundary",
                                concepts=["control-boundary"],
                                value_roots=["control-boundary"],
                            )
                        ],
                    )
                )
            elif kind in {"scope3_protocol", "scope3_iso"}:
                classification = "ghg_protocol" if kind == "scope3_protocol" else "iso_14064_1"
                classification_concept = "ghg-protocol" if kind == "scope3_protocol" else "iso-14064"
                elements.extend(
                    qualitative_elements(
                        package_id,
                        dp,
                        extras=[
                            fixed_dimension(
                                package_id,
                                dp,
                                "scope3_classification",
                                "范围3分类体系",
                                "Scope 3 classification",
                                dimension_slug="scope3-classification",
                                code_set_slug="scope3-classification",
                                fixed=classification,
                                concept_slug=classification_concept,
                                record="qualitative_assertion",
                            ),
                            element(
                                package_id,
                                dp,
                                "scope3_category",
                                "范围3类别",
                                "Scope 3 category",
                                role="subject",
                                record="qualitative_assertion",
                                storage="dimension",
                                target=dim("scope3-category"),
                                primary="enum",
                                fallback=["text"],
                                codeset=f"{package_id}.codeset.scope3-category",
                                concepts=["scope3-category"],
                                value_roots=["scope3-category"],
                                description="一个实际披露的范围3类别成员；总计、合计、小计、表头和注释不得作为类别值。",
                            ),
                        ],
                    )
                )
            elif kind == "value_chain":
                elements.extend(
                    qualitative_elements(
                        package_id,
                        dp,
                        extras=[
                            element(
                                package_id,
                                dp,
                                "value_chain_stage",
                                "价值链阶段",
                                "Value-chain stage",
                                role="subject",
                                record="qualitative_assertion",
                                storage="dimension",
                                target=dim("value-chain-stage"),
                                primary="enum",
                                fallback=["text"],
                                codeset=f"{package_id}.codeset.value-chain-stage",
                                concepts=["value-chain"],
                                description="一个实际披露的价值链阶段成员；合计、表头和注释不得作为阶段值。",
                            )
                        ],
                    )
                )
            else:
                elements.append(element(package_id, dp, "disaggregation_basis", "分解依据", "Disaggregation basis", role="dimension",
                                        record="reporting_task", storage="attribute", target="disaggregation_basis",
                                        primary="text", required=False, nullable=True, repeated=True, concepts=["disaggregation"]))
        elif kind == "ghg":
            elements.extend(value_and_unit(package_id, dp, "ghg_emissions", "温室气体排放量", "GHG emissions",
                                           unit_code="ghg_unit", unit_zh="温室气体单位", unit_en="GHG unit",
                                           unit_dimension="extractesg.core.unit-dimension.ghg-emissions"))
            if scope_method:
                scope, method_value = scope_method
                scope_concept = {"scope1": "scope1-emissions", "scope2": "scope2-emissions", "scope3": "scope3-emissions", "total": "total-ghg-emissions"}[scope]
                elements.append(fixed_dimension(package_id, dp, "ghg_scope", "温室气体范围", "GHG scope",
                                                dimension_slug="ghg-scope", code_set_slug="ghg-scope", fixed=scope,
                                                concept_slug=scope_concept))
                if method_value:
                    method_concept = "location-based" if method_value == "location_based" else "market-based"
                    elements.append(fixed_dimension(package_id, dp, "scope2_method", "范围2方法", "Scope 2 method",
                                                    dimension_slug="scope2-method", code_set_slug="scope2-method", fixed=method_value,
                                                    concept_slug=method_concept))
            if dp == "11":
                elements.append(element(package_id, dp, "scope3_category", "范围3类别", "Scope 3 category", role="subject",
                                        record="quantitative_observation", storage="dimension", target=dim("scope3-category"),
                                        primary="enum", fallback=["text"], codeset=f"{package_id}.codeset.scope3-category",
                                        repeated=False, concepts=["scope3-category"], value_roots=["scope3-category"]))
            if dp in {"17", "24", "28"}:
                elements.append(element(package_id, dp, "biogenic_status", "生物源排放状态", "Biogenic status", role="category",
                                        record="quantitative_observation", storage="attribute", target="biogenic_status",
                                        primary="enum", codeset=f"{package_id}.codeset.biogenic-status", fixed="biogenic_co2_excluded",
                                        concepts=["biogenic-co2"]))
            aggregation_role = (
                "total" if dp in {"12", "13"}
                else "component" if dp in {"17", "24", "28"}
                else "subtotal"
            )
            elements.append(aggregation_role_element(package_id, dp, aggregation_role))
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))
        elif kind == "percentage":
            elements.extend(percentage_elements(package_id, dp))
            if scope_method:
                scope, method_value = scope_method
                scope_concept = {"scope1": "scope1-emissions", "scope2": "scope2-emissions", "scope3": "scope3-emissions", "total": "total-ghg-emissions"}[scope]
                elements.append(fixed_dimension(package_id, dp, "ghg_scope", "温室气体范围", "GHG scope",
                                                dimension_slug="ghg-scope", code_set_slug="ghg-scope", fixed=scope,
                                                concept_slug=scope_concept))
                if method_value:
                    elements.append(fixed_dimension(package_id, dp, "scope2_method", "范围2方法", "Scope 2 method",
                                                    dimension_slug="scope2-method", code_set_slug="scope2-method", fixed=method_value,
                                                    concept_slug="market-based" if method_value == "market_based" else "location-based"))
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))
        elif kind == "intensity":
            elements.extend(value_and_unit(package_id, dp, "ghg_intensity", "温室气体排放强度", "GHG intensity",
                                           unit_code="intensity_unit", unit_zh="强度单位", unit_en="Intensity unit",
                                           unit_dimension="extractesg.core.unit-dimension.ghg-intensity"))
            scope, method_value = scope_method
            elements.append(fixed_dimension(package_id, dp, "ghg_scope", "温室气体范围", "GHG scope",
                                            dimension_slug="ghg-scope", code_set_slug="ghg-scope", fixed=scope,
                                            concept_slug="total-ghg-emissions"))
            elements.append(fixed_dimension(package_id, dp, "scope2_method", "范围2方法", "Scope 2 method",
                                            dimension_slug="scope2-method", code_set_slug="scope2-method", fixed=method_value,
                                            concept_slug="location-based" if method_value == "location_based" else "market-based"))
            elements.extend([
                element(package_id, dp, "numerator_ghg_unit", "分子温室气体单位", "Numerator GHG unit", role="context",
                        record="quantitative_observation", storage="attribute", target="numerator_ghg_unit",
                        primary="identifier", fallback=["string"], unit_dimension="extractesg.core.unit-dimension.ghg-emissions", nullable=True),
                element(package_id, dp, "denominator_currency", "分母货币", "Denominator currency", role="context",
                        record="quantitative_observation", storage="attribute", target="denominator_currency",
                        primary="enum", fallback=["string"], codeset="extractesg.core.codeset.currency", nullable=True),
                element(package_id, dp, "denominator_scale", "分母货币量级", "Denominator currency scale", role="context",
                        record="quantitative_observation", storage="attribute", target="denominator_scale",
                        primary="enum", fallback=["string"], codeset="extractesg.core.codeset.currency-scale", nullable=True),
            ])
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))
        elif kind == "money":
            elements.extend(money_elements(package_id, dp))
            revenue_scope = {"33": "total", "34": "intensity_denominator", "35": "other"}[dp]
            elements.append(fixed_dimension(package_id, dp, "revenue_scope", "净收入范围", "Revenue scope",
                                            dimension_slug="revenue-scope", code_set_slug="revenue-scope", fixed=revenue_scope,
                                            concept_slug="net-revenue"))
            elements.extend(context_elements(package_id, dp, "quantitative_observation"))
        else:
            extras = []
            if kind == "instrument_list":
                extras.append(element(package_id, dp, "contractual_instrument_type", "合同工具类型", "Contractual instrument type",
                                      role="subject", record="qualitative_assertion", storage="dimension",
                                      target=dim("contractual-instrument-type"), primary="enum", fallback=["text"],
                                      codeset=f"{package_id}.codeset.contractual-instrument-type", required=False, nullable=True,
                                      repeated=True, concepts=["contractual-instrument"], value_roots=["contractual-instrument"]))
            if kind in {"excluded_category", "category_list"}:
                extras.append(element(package_id, dp, "scope3_category", "范围3类别", "Scope 3 category", role="subject",
                                      record="qualitative_assertion", storage="dimension", target=dim("scope3-category"),
                                      primary="enum", fallback=["text"], codeset=f"{package_id}.codeset.scope3-category",
                                      concepts=["scope3-category"], value_roots=["scope3-category"]))
            elements.extend(qualitative_elements(package_id, dp, extras=extras))

    concepts = [
        concept(package_id, "ghg-emissions", "温室气体排放", "GHG emissions", aliases={"zh": ["温室气体排放量", "溫室氣體排放", "溫室氣體排放量"], "en": ["greenhouse gas emissions"]}, abbreviations=["GHG emissions"], formulas=["CO2e", "CO₂e", "CO_{{2}}e"]),
        concept(package_id, "gross-ghg-emissions", "温室气体排放总量", "Gross GHG emissions", aliases={"zh": ["温室气体总排放"], "en": ["gross greenhouse gas emissions"]}, broader=["ghg-emissions"]),
        concept(package_id, "scope1-emissions", "范围1排放", "Scope 1 emissions", aliases={"zh": ["范围1", "范围一", "范畴1", "范畴一", "範圍1", "範圍一", "範疇1", "範疇一", "直接温室气体排放", "直接溫室氣體排放"], "en": ["scope1", "scope I", "direct GHG emissions"]}, broader=["ghg-emissions"], excluded=["scope2-emissions", "scope3-emissions"]),
        concept(package_id, "scope1-ghg-emissions", "范围1温室气体排放", "Scope 1 GHG emissions", aliases={"zh": ["范围1 温室气体排放量", "范围一温室气体排放", "范畴一温室气体排放", "範圍1 溫室氣體排放量", "范围一GHG排放", "范围1 GHG排放", "直接温室气体排放量"], "en": ["gross scope 1 GHG emissions", "scope one greenhouse gas emissions"]}, broader=["scope1-emissions"]),
        concept(package_id, "scope2-emissions", "范围2排放", "Scope 2 emissions", aliases={"zh": ["范围2", "范围二", "范畴2", "范畴二", "範圍2", "範圍二", "购入能源间接排放"], "en": ["scope2", "scope II", "energy indirect GHG emissions"]}, broader=["ghg-emissions"], excluded=["scope1-emissions", "scope3-emissions"]),
        concept(package_id, "scope3-emissions", "范围3排放", "Scope 3 emissions", aliases={"zh": ["范围3", "范围三", "范畴3", "范畴三", "範圍3", "範圍三", "其他间接温室气体排放"], "en": ["scope3", "scope III", "other indirect GHG emissions"]}, broader=["ghg-emissions"], excluded=["scope1-emissions", "scope2-emissions"]),
        concept(package_id, "total-ghg-emissions", "温室气体排放合计", "Total GHG emissions", aliases={"zh": ["范围1、2、3排放总和"], "en": ["total scope 1 2 and 3 emissions"]}, broader=["ghg-emissions"]),
        concept(package_id, "location-based", "基于位置法", "Location-based", aliases={"zh": ["位置法"], "en": ["location based method"]}, excluded=["market-based"]),
        concept(package_id, "market-based", "基于市场法", "Market-based", aliases={"zh": ["市场法"], "en": ["market based method"]}, excluded=["location-based"]),
        concept(package_id, "ghg-intensity", "温室气体排放强度", "GHG intensity", aliases={"zh": ["碳排放强度"], "en": ["greenhouse gas intensity"]}, formulas=["total GHG emissions / net revenue"], excluded=["emissions-trading-scheme"]),
        concept(package_id, "emissions-trading-scheme", "排放交易体系", "Emission trading scheme", aliases={"zh": ["碳交易体系", "排放权交易体系", "碳排放权交易市场", "碳市场覆盖"], "en": ["regulated ETS", "regulated emission trading scheme", "emissions trading system"]}, excluded=["ghg-intensity"]),
        concept(package_id, "biogenic-co2", "生物源二氧化碳", "Biogenic CO2", aliases={"zh": ["生物质二氧化碳"], "en": ["biogenic carbon dioxide"]}, formulas=["biogenic CO2"]),
        concept(package_id, "contractual-instrument", "合同工具", "Contractual instrument", aliases={"zh": ["能源合同工具"], "en": ["contractual instruments"]}),
        concept(package_id, "bundled-instrument", "捆绑合同工具", "Bundled contractual instrument", aliases={"en": ["bundled instruments"]}, broader=["contractual-instrument"]),
        concept(package_id, "bundled-attribute-claim", "捆绑能源属性声明", "Bundled energy attribute claim", aliases={"en": ["bundled energy attribute claims"]}, broader=["contractual-instrument"]),
        concept(package_id, "unbundled-attribute-claim", "非捆绑能源属性声明", "Unbundled energy attribute claim", aliases={"en": ["unbundled energy attribute claims"]}, broader=["contractual-instrument"]),
        concept(package_id, "primary-data", "一手数据", "Primary data", aliases={"zh": ["供应商或价值链伙伴数据"], "en": ["supplier-specific data"]}),
        concept(package_id, "methodology", "核算方法", "Methodology", aliases={"zh": ["计算方法和假设"], "en": ["calculation methodology and assumptions"]}),
        concept(package_id, "emission-factor", "排放因子", "Emission factor", aliases={"zh": ["排放系数"], "en": ["emissions factor"]}),
        concept(package_id, "calculation-tool", "计算工具", "Calculation tool", aliases={"zh": ["核算工具"], "en": ["calculation tools"]}),
        concept(package_id, "comparability-change", "可比性变更", "Comparability change", aliases={"zh": ["基准年或边界变更"], "en": ["methodology boundary or data change"]}),
        concept(package_id, "control-boundary", "控制边界", "Control boundary", aliases={"zh": ["组织边界"], "en": ["financial or operational control"]}),
        concept(package_id, "disaggregation", "分解", "Disaggregation", aliases={"zh": ["排放分拆"], "en": ["GHG disaggregation"]}),
        concept(package_id, "ghg-protocol", "温室气体核算体系", "GHG Protocol", aliases={"en": ["Greenhouse Gas Protocol"]}),
        concept(package_id, "iso-14064", "ISO 14064-1 标准", "ISO 14064-1", aliases={"zh": ["温室气体 ISO 标准"], "en": ["ISO 14064 Part 1"]}),
        concept(package_id, "value-chain", "价值链", "Value chain", aliases={"zh": ["上游和下游价值链"], "en": ["upstream and downstream value chain"]}),
        concept(package_id, "scope3-category", "范围3类别", "Scope 3 category", aliases={"zh": ["范围三类别"], "en": ["scope 3 categories"]}),
        concept(package_id, "excluded-category", "排除的范围3类别", "Excluded Scope 3 category", aliases={"zh": ["未纳入范围三类别"], "en": ["excluded scope 3 categories"]}, broader=["scope3-category"]),
        concept(package_id, "net-revenue", "净收入", "Net revenue", aliases={"zh": ["营业净收入"], "en": ["revenue denominator"]}),
        concept(package_id, "reconciliation", "对账", "Reconciliation", aliases={"zh": ["与财务报表衔接"], "en": ["reconciliation to financial statements"]}),
        concept(package_id, "reporting-period", "报告期", "Reporting period", aliases={"zh": ["报告年度"], "en": ["reporting year"]}),
        concept(package_id, "reporting-boundary", "报告边界", "Reporting boundary", aliases={"zh": ["报告范围"], "en": ["organisational boundary"]}),
        concept(package_id, "carbon-removal", "碳移除", "Carbon removal", aliases={"zh": ["温室气体移除"], "en": ["GHG removal"]}, excluded=["gross-ghg-emissions"]),
        concept(package_id, "carbon-credit", "碳信用", "Carbon credit", aliases={"zh": ["碳抵消"], "en": ["carbon offset"]}, excluded=["gross-ghg-emissions"]),
        concept(package_id, "avoided-emission", "避免排放", "Avoided emission", aliases={"zh": ["减排量"], "en": ["avoided GHG emissions"]}, excluded=["gross-ghg-emissions"]),
    ]
    # Make the gross-emissions exclusion relation symmetric so it compiles into
    # package-driven hard negatives without E1-specific query code.
    for item in concepts:
        if item["concept_id"] == cid(package_id, "gross-ghg-emissions"):
            item["excluded_concept_ids"] = [cid(package_id, slug) for slug in ["carbon-removal", "carbon-credit", "avoided-emission"]]
    code_sets = [
        code_set(package_id, "ghg-scope", [("scope1", "范围1", "Scope 1"), ("scope2", "范围2", "Scope 2"), ("scope3", "范围3", "Scope 3"), ("total", "合计", "Total")]),
        code_set(package_id, "scope2-method", [("location_based", "基于位置法", "Location-based"), ("market_based", "基于市场法", "Market-based")]),
        code_set(package_id, "scope3-classification", [("ghg_protocol", "温室气体核算体系", "GHG Protocol"), ("iso_14064_1", "ISO 14064-1", "ISO 14064-1")]),
        code_set(package_id, "scope3-category", [(f"category_{index:02d}", zh, en) for index, (zh, en) in enumerate([
            ("购入商品和服务", "Purchased goods and services"), ("资本品", "Capital goods"), ("燃料和能源相关活动", "Fuel- and energy-related activities"),
            ("上游运输和配送", "Upstream transportation and distribution"), ("运营中产生的废弃物", "Waste generated in operations"), ("商务旅行", "Business travel"),
            ("员工通勤", "Employee commuting"), ("上游租赁资产", "Upstream leased assets"), ("下游运输和配送", "Downstream transportation and distribution"),
            ("售出产品的加工", "Processing of sold products"), ("售出产品的使用", "Use of sold products"), ("售出产品的报废处理", "End-of-life treatment of sold products"),
            ("下游租赁资产", "Downstream leased assets"), ("特许经营", "Franchises"), ("投资", "Investments")], 1)], extensible=True),
        code_set(package_id, "control-boundary", [("consolidated_group", "合并财务报表集团", "Consolidated accounting group"), ("operational_control", "运营控制", "Operational control"), ("financial_control", "财务控制", "Financial control"), ("equity_share", "权益比例", "Equity share")], extensible=True),
        code_set(package_id, "contractual-instrument-type", [("supplier_specific_contract", "供应商特定合同", "Supplier-specific contract"), ("energy_attribute_certificate", "能源属性证书", "Energy attribute certificate"), ("power_purchase_agreement", "购电协议", "Power purchase agreement"), ("residual_mix", "剩余组合", "Residual mix"), ("other", "其他", "Other")], extensible=True),
        code_set(package_id, "value-chain-stage", [("upstream", "上游", "Upstream"), ("own_operations", "自身运营", "Own operations"), ("downstream", "下游", "Downstream")]),
        code_set(package_id, "biogenic-status", [("biogenic_co2_excluded", "从范围排放中排除并单独披露的生物源二氧化碳", "Biogenic CO2 excluded from scope and disclosed separately")]),
        code_set(package_id, "aggregation-role", [("total", "指标总量", "Metric total"), ("subtotal", "指标子总计", "Metric subtotal"), ("component", "指标组成项", "Metric component")]),
        code_set(package_id, "revenue-scope", [("total", "净收入总额", "Total net revenue"), ("intensity_denominator", "强度计算所用净收入", "Net revenue used for intensity"), ("other", "其他净收入", "Other net revenue")]),
    ]
    dimensions = [
        dimension(package_id, "ghg-scope", "温室气体范围", "GHG scope", codeset="ghg-scope"),
        dimension(package_id, "scope2-method", "范围2核算方法", "Scope 2 method", codeset="scope2-method"),
        dimension(package_id, "scope3-classification", "范围3分类体系", "Scope 3 classification", codeset="scope3-classification"),
        dimension(package_id, "scope3-category", "范围3类别", "Scope 3 category", codeset="scope3-category", hierarchical=True),
        dimension(package_id, "control-boundary", "控制边界", "Control boundary", codeset="control-boundary"),
        dimension(package_id, "contractual-instrument-type", "合同工具类型", "Contractual instrument type", codeset="contractual-instrument-type"),
        dimension(package_id, "value-chain-stage", "价值链阶段", "Value-chain stage", codeset="value-chain-stage"),
        dimension(package_id, "revenue-scope", "净收入范围", "Revenue scope", codeset="revenue-scope"),
        dimension(package_id, "reporting-entity", "报告实体", "Reporting entity", value_type="string", hierarchical=True),
        dimension(package_id, "aggregation-role", "指标聚合角色", "Metric aggregation role", codeset="aggregation-role"),
    ]
    relations = [
        {
            "relation_id": f"{package_id}.relation.scope3-classification-alternative",
            "relation_type": "alternative_group", "source_metric_ids": [], "target_metric_ids": [],
            "member_metric_ids": [f"{package_id}.dp04", f"{package_id}.dp05"],
            "activation_predicate": None, "policy": "one-or-more-permitted-alternatives",
            "description": "The Scope 3 category table can use the GHG Protocol or ISO 14064-1 classification.",
            "source_refs": [source_id(package_id, "eu-2023-2772")],
        },
        {
            "relation_id": f"{package_id}.relation.phase-in-under-750",
            "relation_type": "conditional_activation", "source_metric_ids": [],
            "target_metric_ids": [f"{package_id}.dp{dp}" for dp in sorted(phase_in)], "member_metric_ids": [],
            "activation_predicate": {"path": "report_context.e1_6_scope3_total_phase_in_exempt", "operator": "eq", "value": False},
            "policy": "activate-unless-phase-in-exempt",
            "description": "Scope 3, total emissions and related intensity disclosures are activated unless the applicable first-year phase-in exemption is used.",
            "source_refs": [source_id(package_id, "eu-2023-2772")],
        },
        {
            "relation_id": f"{package_id}.relation.ghg-method-context",
            "relation_type": "contextualizes",
            "source_metric_ids": [f"{package_id}.dp{dp}" for dp in ["14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29"]],
            "target_metric_ids": [f"{package_id}.dp{dp}" for dp in ["07", "09", "10", "11", "12", "13"]],
            "member_metric_ids": [], "activation_predicate": None, "policy": "retain-linked-context",
            "description": "Methods, changes, biogenic emissions, instruments and Scope 3 inventory context explain gross emissions.",
            "source_refs": [source_id(package_id, "eu-2023-2772")],
        },
        {
            "relation_id": f"{package_id}.relation.intensity-context",
            "relation_type": "contextualizes",
            "source_metric_ids": [f"{package_id}.dp{dp}" for dp in ["32", "33", "34", "35"]],
            "target_metric_ids": [f"{package_id}.dp30", f"{package_id}.dp31"],
            "member_metric_ids": [], "activation_predicate": None, "policy": "retain-linked-context",
            "description": "Revenue reconciliation and denominators explain both GHG intensity methods.",
            "source_refs": [source_id(package_id, "eu-2023-2772")],
        },
    ]
    derivations = [
        derivation(package_id, "12", "sum", [("07", "scope1"), ("09", "scope2 location"), ("11", "scope3")], unit_policy="compatible-ghg-units", required_match_fields=["reporting_period_raw", "reporting_boundary", dim("reporting-entity")]),
        derivation(package_id, "13", "sum", [("07", "scope1"), ("10", "scope2 market"), ("11", "scope3")], unit_policy="compatible-ghg-units", required_match_fields=["reporting_period_raw", "reporting_boundary", dim("reporting-entity")]),
        derivation(package_id, "30", "ratio", [("12", "location total"), ("34", "revenue")], unit_policy="ghg-per-monetary-unit", required_match_fields=["reporting_period_raw", "reporting_boundary", dim("reporting-entity")]),
        derivation(package_id, "31", "ratio", [("13", "market total"), ("34", "revenue")], unit_policy="ghg-per-monetary-unit", required_match_fields=["reporting_period_raw", "reporting_boundary", dim("reporting-entity")]),
    ]
    package_dir = ROOT / f"packages/esrs/2023-set1/e1-6/{E1_6_PACKAGE_VERSION}"
    payloads = {
        "manifest": manifest(package_id, "esrs.e1-6", dr, E1_6_PACKAGE_VERSION), "metrics": metrics, "elements": elements,
        "concepts": concepts, "relations": relations, "code_sets": code_sets, "dimensions": dimensions,
        "derivations": derivations,
        "validation_rules": validation_rules(package_id, ["08", "18", "20", "21", "22", "25"], ["12", "13", "30", "31"]),
        "sources": sources(package_id, dr, "ESRS E1 paragraphs 44–55 and AR 39–55; ESRS 1 Appendix C phase-in provisions", "ESRS E1 worksheet rows 107–141 (E1-6_01 through E1-6_35)"),
    }
    for name, payload in payloads.items():
        write_json(package_dir / f"{name}.json", payload)


if __name__ == "__main__":
    if not CORE_110.exists():
        build_core()
    build_e1_5()
    build_e1_6()
