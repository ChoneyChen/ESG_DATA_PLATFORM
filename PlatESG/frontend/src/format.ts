import type { Metric, Observation } from "./types";

export function clauseReference(item: {
  source_datapoint_id?: string | null;
  clause_kind?: string;
  parent_package_id?: string;
}) {
  if (item.source_datapoint_id) return item.source_datapoint_id;
  const parent = item.parent_package_id?.split(".").pop()?.toUpperCase();
  return parent ? `${parent} · 项目延伸` : "项目延伸条款";
}

export function valueOriginLabel(origin?: string) {
  return origin === "derived" ? "经审核计算" : "报告披露";
}

const labels: Record<string, string> = {
  ratio: "%",
  percent: "%",
  "unit.ratio": "%",
  "unit.energy.mwh": "MWh",
  "unit.mass.tonne": "吨",
  "unit.volume.m3": "m³",
  "unit.emissions.tco2e": "tCO₂e",
  "unit.count": "个",
  m3: "m³",
  tonne: "吨",
  tonnes: "吨",
  tCO2e: "tCO₂e",
};

export function unitLabel(unit: string | null | undefined) {
  return unit ? labels[unit] || unit : "未明确单位";
}

// Decimal strings remain strings for display; Number is used only inside chart geometry.
export function formatDecimal(input: string | null | undefined): string {
  if (input == null) return "—";
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(input);
  if (!match) return input;
  const [, sign, integer, fraction] = match;
  return `${sign}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${fraction !== undefined ? `.${fraction}` : ""}`;
}

export function shiftDecimal(input: string, places: number) {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(input);
  if (!match) return input;
  const [, sign, integer, fraction = ""] = match;
  const digits = integer + fraction;
  const position = integer.length + places;
  const value =
    position >= digits.length
      ? digits + "0".repeat(position - digits.length)
      : position <= 0
        ? `0.${"0".repeat(-position)}${digits}`
        : `${digits.slice(0, position)}.${digits.slice(position)}`;
  return sign + value.replace(/^0+(?=\d)/, "");
}

function ratioUnit(unit: string | null) {
  return ["ratio", "unit.ratio"].includes(unit || "");
}

export function formatValue(
  observation: Pick<
    Observation,
    | "value_numeric"
    | "lower_bound"
    | "upper_bound"
    | "canonical_unit"
    | "statement_text"
    | "precision_qualifier"
  >,
) {
  const transform = (value: string) =>
    formatDecimal(
      ratioUnit(observation.canonical_unit) ? shiftDecimal(value, 2) : value,
    );
  const qualifier = observation.precision_qualifier || "";
  const comparator = (
    {
      less_than: "<",
      lt: "<",
      less_or_equal: "≤",
      lte: "≤",
      greater_than: ">",
      gt: ">",
      greater_or_equal: "≥",
      gte: "≥",
      approximate: "≈",
      approximately: "≈",
    } as Record<string, string>
  )[qualifier];
  if (observation.value_numeric !== null)
    return `${comparator ? `${comparator} ` : ""}${transform(observation.value_numeric)}`;
  if (observation.lower_bound !== null || observation.upper_bound !== null) {
    if (observation.lower_bound !== null && observation.upper_bound !== null)
      return `${transform(observation.lower_bound)} – ${transform(observation.upper_bound)}`;
    return observation.lower_bound !== null
      ? `${comparator || "下界"} ${transform(observation.lower_bound)}`
      : `${comparator || "上界"} ${transform(observation.upper_bound!)}`;
  }
  return observation.statement_text || "—";
}

export function periodLabel(
  observation: Pick<Observation, "period_start" | "period_end">,
) {
  const start = observation.period_start;
  const end = observation.period_end;
  if (!start && !end) return "期间待确认";
  if (start === end) return end || "";
  if (
    start?.endsWith("-01-01") &&
    end?.endsWith("-12-31") &&
    start.slice(0, 4) === end.slice(0, 4)
  )
    return `${end.slice(0, 4)} 年度`;
  return `${start || "?"} 至 ${end || "?"}`;
}

export const topicLabels: Record<string, string> = {
  E1: "气候",
  E2: "污染",
  E3: "水资源",
  E4: "生物多样性",
  E5: "循环经济",
  S1: "自有劳动力",
  S2: "价值链劳动者",
  S3: "受影响社区",
  S4: "消费者",
  G1: "商业行为",
};

export const dimensionLabels: Record<string, string> = {
  entity_ref: "测量主体",
  canonical_unit: "标准单位",
  reported_unit: "报告单位",
  metric_id: "指标定义",
  pollutant: "污染物",
  pollutant_name: "污染物",
  analyte: "分析物",
  substance: "物质",
  substance_category: "物质分类",
  hazard_class: "危害分类",
  flow: "流向",
  scope3_category: "范围 3 类别",
  geography: "地理范围",
  water_risk: "水风险",
  population: "人员范围",
  currency: "币种",
  period_basis: "期间口径",
  reporting_boundary: "披露边界",
  organizational_boundary: "组织边界",
  measurement_method: "计量方法",
  methodology: "计量方法",
  activity: "业务活动",
  entity_kind: "主体类型",
  value_role: "数值角色",
  definition_version: "定义版本",
  period_start: "期间起始",
  period_end: "期间结束",
  boundary_code: "披露边界",
  energy_source: "能源来源",
  energy_flow: "能源流向",
  aggregation_role: "聚合口径",
  energy_accounting_basis: "能源核算基础",
  scope: "排放范围",
  ghg_scope: "温室气体范围",
  amount_basis: "毛排放或净排放",
  scope_2_method: "范围2方法",
  accounting_standard: "核算标准",
  gwp_basis: "全球变暖潜势口径",
  scope3_categories: "范围 3 类别覆盖",
  category_coverage: "类别覆盖",
  media: "排放介质",
  pollution_medium: "排放介质",
  water_flow: "水流向",
  water_risk_area: "水风险区域",
  water_risk_basis: "水风险识别口径",
  consolidation_approach: "合并核算方法",
  release_boundary: "排放边界",
  pollutant_definition: "污染物定义",
  concern_class: "关注物质分类",
  material_flow: "物质流向",
  classification_basis: "物质分类基础",
  water_measure: "水量类型",
  water_definition: "水量定义",
  water_risk_class: "水风险等级",
  risk_classification_method: "风险识别方法",
  estimation_method: "估算方法",
  denominator_scope: "分母口径",
};

export const reasonLabels: Record<string, string> = {
  missing_context: "比较口径不完整",
  missing_dimensions: "必要维度不完整",
  missing_boundary: "披露边界待确认",
  missing_period: "期间待确认",
  missing_method: "计量方法待确认",
  not_comparable: "不满足当前比较条件",
  boundary_mismatch: "披露边界不同",
  period_mismatch: "期间不匹配",
  method_mismatch: "计量方法不同",
  unit_mismatch: "单位不兼容",
  dimensions_mismatch: "维度口径不同",
  no_observation: "没有符合条件的已发布观测",
  no_published_observation: "没有符合条件的已发布观测",
  package_planned: "标准包待建设",
  not_extracted: "尚未提取",
  review_required: "等待审核",
  rejected: "已拒绝",
  pending: "待审核",
  withdrawn: "已撤回",
  approved: "已通过",
  published: "已发布",
  superseded: "已被修订替代",
  not_disclosed_in_reviewed_scope: "已审核范围内未披露",
  not_applicable: "不适用",
  source_unreadable: "来源无法读取",
  definition_mismatch: "指标定义版本不同",
  conflict_unresolved: "来源冲突待解决",
  structured_display_only: "定性信息支持结构化展示",
  mixed: "各期间状态不同",
  not_selected: "本次未选择",
  qualitative_display_only: "定性信息支持结构化展示",
  interval_not_point: "区间观测保留展示",
  numeric_point_required: "本次比较需要明确数值",
  actual_measure_required: "本次比较需要实际披露值",
  restated_value_requires_matching_revision: "重述值需匹配修订口径",
  one_observation_per_entity_required:
    "同一企业存在多条符合条件的观测，需明确修订或范围",
  no_published_observation_in_dataset: "此数据版本没有相应已发布观测",
  "comparison_contract_required:multiple_cohorts":
    "存在多个独立口径，请选择一致口径",
  not_available: "此版本暂无数据",
  matching_boundary: "披露边界一致",
  matching_period: "报告期间一致",
  matching_method: "计量方法一致",
  matching_unit: "单位一致或可规范换算",
  equal_value_same_context_prefer_reported:
    "同一口径下数值一致，优先展示报告披露值",
  reviewed_context_missing: "测量主体、期间或口径尚未确认",
  mapping_not_equivalent: "报告披露内容与指标定义不完全对应",
  physical_source_missing: "原文证据不完整",
  source_fact_superseded: "该披露已被更新版本替代",
  clause_identity_mismatch: "披露条款与指标定义不一致",
  value_role_mismatch: "数值类型不符合指标定义",
  statement_missing: "缺少报告原文陈述",
  numeric_value_missing: "缺少数值披露",
  source_unit_unresolved: "报告原始单位尚未确认",
  reported_unit_not_reviewed: "报告单位尚未核实",
  unit_incompatible_or_unknown: "单位未知或无法换算为指标标准单位",
  reported_unit_disagrees_with_source: "确认的单位与报告原始单位不一致",
  non_finite_numeric_value: "数值格式不符合要求",
  ratio_out_of_range: "比例超出指标允许范围",
  metric_value_out_of_range: "数值超出指标允许范围",
  invalid_supersession_identity: "重述值的主体、期间或口径与原披露不一致",
  unresolved_source_conflict: "相同口径下的来源数值存在待核对差异",
  explicitly_superseded: "该观测已由修订版本替代",
  not_in_major_profile: "该指标尚未纳入当前数据范围",
  metric_not_ready: "该指标尚未具备发布条件",
  standard_snapshot_mismatch: "披露采用的标准版本与指标定义不一致",
  extension_execution_snapshot_mismatch:
    "延伸指标采用的定义版本与确认版本不一致",
  definition_not_approved: "计算定义尚未确认",
  extension_definition_not_approved: "延伸指标定义尚未确认",
  extension_release_snapshot_mismatch: "计算采用的定义版本与确认版本不一致",
  target_extension_definition_mismatch: "目标指标与计算定义不一致",
  unsupported_or_unapproved_derivation_rule: "计算公式尚未确认或不满足要求",
  operand_selection_does_not_match_rule:
    "参与计算的来源数量或类型不符合公式要求",
  input_dataset_not_sealed: "输入数据版本尚未完成发布",
  derived_target_identity_incomplete: "计算结果的主体、期间或口径不完整",
  derived_value_outside_definition_bounds: "计算结果超出指标允许范围",
  extension_not_in_published_dictionary: "该延伸指标尚未纳入此版本的指标目录",
  frozen_calculation_receipt_differs: "计算结果或来源口径与已确认结果不一致",
  unresolved_reported_derived_conflict: "报告披露值与计算结果存在待核对差异",
  calculation_pending: "计算结果尚未确认",
  calculation_review_required: "计算结果需要进一步核对",
  calculation_rejected: "计算结果未获确认",
  calculation_approved: "计算结果已确认",
  normalization_rule_snapshot_missing: "单位换算依据尚未确认",
  target_unit_conversion_unavailable: "无法换算为目标指标单位",
  non_terminating_conversion: "单位换算无法得到符合要求的精确十进制结果",
  unit_conversion_failed: "单位换算未完成",
  invalid_extension_request: "计算输入不符合要求",
};

export function reasonLabel(reason: string) {
  if (Object.hasOwn(reasonLabels, reason)) return reasonLabels[reason];
  const parts = reason.split(":");
  const prefix = parts[0];
  const axis = Object.hasOwn(dimensionLabels, parts[1])
    ? dimensionLabels[parts[1]]
    : "必要口径";
  if (
    [
      "missing_comparison_dimension",
      "missing_comparison_axis",
      "missing_dimension",
      "missing_required_dimension",
      "missing_required_context",
    ].includes(prefix)
  )
    return `${axis}待确认`;
  if (prefix === "request_dimension_differs") return `${axis}与本次筛选不同`;
  if (prefix === "dimension_mismatch") return `${axis}不同`;
  if (
    ["comparison_axis_mismatch", "comparison_dimension_mismatch"].includes(
      prefix,
    )
  )
    return `${axis}不满足当前比较条件`;
  if (["fixed_dimension_mismatch", "dimension_value_mismatch"].includes(prefix))
    return `${axis}不符合指标定义`;
  if (prefix === "shared_axis_mismatch_or_missing")
    return `参与计算的各来源${axis}缺失或不一致`;
  if (prefix === "selector_mismatch") return `${axis}不满足计算公式要求`;
  if (prefix === "operand") {
    if (parts[2] === "selector_mismatch")
      return `参与计算的来源${Object.hasOwn(dimensionLabels, parts[3]) ? dimensionLabels[parts[3]] : "必要口径"}不满足公式要求`;
    const operandReasons: Record<string, string> = {
      not_in_input_dataset_or_context: "输入数据版本没有此来源或已确认口径",
      approval_no_longer_active: "来源的确认状态已失效",
      not_currently_published: "来源已退出当前发布数据",
      fact_approval_reference_invalid: "来源事实的确认记录不匹配",
      mapping_approval_reference_invalid: "来源指标映射的确认记录不匹配",
      point_value_required: "需要精确点值，区间或上下限不能直接参与计算",
      frozen_standard_binding_mismatch: "来源采用的标准与计算定义不一致",
      context_snapshot_differs: "来源的主体、期间或口径与发布版本不一致",
    };
    const label = Object.hasOwn(operandReasons, parts[2])
      ? operandReasons[parts[2]]
      : Object.hasOwn(reasonLabels, parts[2])
        ? reasonLabels[parts[2]]
        : null;
    if (label) return `参与计算的来源：${label}`;
  }
  const reviewStates: Record<string, string> = {
    pending: "尚未审核",
    candidate: "尚未审核",
    review_required: "需要进一步审核",
    needs_confirmation: "需要进一步确认",
    rejected: "未获批准",
    approved: "已获批准",
  };
  for (const [prefix, subject] of [
    ["fact_", "来源事实"],
    ["mapping_", "指标映射"],
  ]) {
    if (
      reason.startsWith(prefix) &&
      Object.hasOwn(reviewStates, reason.slice(prefix.length))
    )
      return `${subject}${reviewStates[reason.slice(prefix.length)]}`;
  }
  // Public prose may describe a frozen coverage decision; internal codes and payloads are never public labels.
  const engineeringPayload =
    /[{}\[\]<>]|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{64}\b|(?:\/Users\/|\/tmp\/|file:\/\/)/i;
  if (
    reason.length <= 500 &&
    /[\u4e00-\u9fff]/.test(reason) &&
    !engineeringPayload.test(reason)
  )
    return reason;
  return "当前条件尚未满足，请进一步核对指标口径";
}

export function numericMetric(metric: Metric): boolean {
  return ["numeric", "decimal", "integer", "number", "ratio"].includes(
    metric.value_type,
  );
}
export function dimensionKeys(metric: Metric): string[] {
  return (metric.required_dimensions || [])
    .map((item) =>
      typeof item === "string" ? item : item.key || item.name || "",
    )
    .filter(Boolean);
}

export function editableDimensionKeys(metric: Metric): string[] {
  const separateFields = new Set([
    "entity_kind",
    "definition_version",
    "period_start",
    "period_end",
    "boundary_code",
    "measurement_method",
  ]);
  return [
    ...new Set([
      ...dimensionKeys(metric),
      ...(metric.comparison_dimensions || []),
    ]),
  ].filter(
    (key) =>
      !(key in (metric.fixed_dimensions || {})) && !separateFields.has(key),
  );
}

export function contextLabel(value: string | null | undefined): string {
  if (!value) return "待确认";
  return (
    (
      {
        consolidated_group: "合并集团",
        group: "集团",
        listed_company: "上市公司",
        subsidiary: "子公司",
        legal_entity: "法人主体",
        location_based: "地点法",
        market_based: "市场法",
        reported: "报告披露方法",
        operational_control: "运营控制",
        financial_control: "财务控制",
        actual: "实际值",
        restated: "重述值",
        total: "总量",
        all: "全部",
        consumption: "消耗",
        renewable: "可再生",
        fossil: "化石能源",
        air: "空气",
        water: "水体",
        water_withdrawal: "取水",
        water_consumption: "耗水",
        withdrawal: "取水",
        recycling: "循环利用",
        discharge: "排水",
        own_operations: "自身运营",
        scope_1: "范围 1",
        scope_2: "范围 2",
        scope_3: "范围 3",
        scope_1_2: "范围1＋2合计",
        gross: "毛排放",
        net: "净排放",
        subtotal: "分项合计",
        procured: "采购",
        used: "使用",
        generated: "产生",
        generated_used_procured_combined: "产生、使用及采购合计",
        recycled_reused: "循环及重复利用",
        category: "类别",
        hazard_class: "危害类别",
        substance: "具体物质",
        substances_of_concern: "关注物质",
        total_energy_consumption_same_boundary: "同一边界总能耗",
        high: "高",
        medium: "中",
        low: "低",
        true: "是",
        false: "否",
        demo_measurement_v1: "演示计量方法 v1",
        demo_energy_accounting_v1: "演示能源核算方法 v1",
        demo_water_definition_v1: "演示水量定义 v1",
        demo_pollutant_definition_v1: "演示污染物定义 v1",
        demo_substance: "演示物质",
        demo_hazard_class: "演示危害类别",
        demo_classification_v1: "演示物质分类 v1",
        demo_100_year_gwp: "演示 100 年全球变暖潜势口径",
        demo_risk_region: "演示水风险区域",
        demo_risk_method_v1: "演示水风险识别 v1",
        demo_estimation_v1: "演示估算方法 v1",
        SO2: "二氧化硫（SO₂）",
        SOx: "硫氧化物（SOx）",
        NOx: "氮氧化物（NOx）",
        COD: "化学需氧量（COD）",
      } as Record<string, string>
    )[value] || value
  );
}

export function stableDimensions(dimensions: Record<string, unknown>) {
  return JSON.stringify(
    Object.keys(dimensions)
      .sort()
      .map((key) => [key, dimensions[key]]),
  );
}

export function trendGroups(observations: Observation[]) {
  const groups = new Map<string, Observation[]>();
  for (const observation of observations) {
    if (
      !["comparable", "eligible"].includes(observation.comparison_eligibility)
    )
      continue;
    if (
      observation.value_numeric === null ||
      !observation.period_start ||
      !observation.period_end ||
      !observation.boundary_code
    )
      continue;
    const year = observation.period_end.slice(0, 4);
    // A year-end headcount and a full-year flow have distinct time bases.
    const basis =
      observation.period_start === observation.period_end
        ? "point"
        : `${observation.period_start.slice(4)}:${observation.period_end.slice(4)}`;
    if (!/^\d{4}$/.test(year)) continue;
    const key = JSON.stringify([
      observation.entity_ref,
      observation.metric_id,
      observation.definition_version,
      observation.canonical_unit,
      observation.boundary_code,
      observation.measurement_method,
      basis,
      stableDimensions(observation.dimensions),
    ]);
    groups.set(key, [...(groups.get(key) || []), observation]);
  }
  return [...groups.values()]
    .map((group) =>
      group.sort((a, b) =>
        (a.period_end || "").localeCompare(b.period_end || ""),
      ),
    )
    .filter((group) => {
      // Multiple observations in the same period indicate alternatives/conflicts, not a trend.
      const periods = group.map(
        (item) => `${item.period_start}:${item.period_end}`,
      );
      return group.length > 1 && new Set(periods).size === periods.length;
    });
}
