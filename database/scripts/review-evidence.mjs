/**
 * Review evidence: read the axes the extraction already saw.
 *
 * A delivered source fact carries verbatim identity fields (scope, pollutant,
 * medium, category, aggregation role, consolidation approach, denominator,
 * method, boundary) and, for older projections, raw dimensions. They use the
 * report's own words, not the metric dictionary's codes, so the review layer
 * used to ignore them and ask a person to retype every axis. This module
 * normalises those words into the dictionary's controlled codes, records where
 * each value came from, and leaves genuinely absent axes unresolved.
 *
 * It is pure data and functions: no report, clause or model special cases.
 */
export const REVIEW_EVIDENCE_VERSION = 'review-evidence-v1';

const nonempty = value => typeof value === 'string' && value.trim().length > 0;
const text = value => value == null ? '' : typeof value === 'string' ? value : String(value);
const clean = value => text(value)
  .replace(/<[^>]+>/g, '')
  .replace(/\^\{[^}]*\}/g, '')
  .replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹]+/g, '')
  .replace(/\s+/g, ' ')
  .trim();
const compact = value => clean(value).toLowerCase().replace(/\s+/g, '');

// Extraction identity keys are standard-neutral report reading axes. Each one
// can feed the platform dimensions with the same meaning.
const IDENTITY_AXES = Object.freeze({
  pollutant: ['pollutant', 'substance'],
  medium: ['pollution_medium'],
  scope: ['ghg_scope'],
  aggregation_role: ['aggregation_role'],
  category: ['scope3_category'],
  consolidation_approach: ['consolidation_approach'],
  method: ['measurement_method'],
  denominator: ['denominator_scope', 'intensity_denominator'],
  amount_basis: ['amount_basis'],
  statistical_time_basis: ['statistical_time_basis'],
  site: ['site'],
  business_segment: ['business_segment'],
});

const POLLUTANTS = [
  ['so2', /二氧化硫|亚硫酸|sulphur dioxide|sulfur dioxide|\bso2\b/],
  ['sox', /硫氧化物|sulfur oxides|sulphur oxides|\bsox\b/],
  ['nox', /氮氧化物|nitrogen oxides|\bnox\b/],
  ['vocs', /挥发性有机(?:化合物|物)|volatile organic|\bvocs?\b/],
  ['pm', /颗粒物|粉尘|particulate|\bpm(?:10|2\.5)?\b/],
  ['cod', /化学需氧量|chemical oxygen demand|\bcod\b/],
  ['ammonia_nitrogen', /氨氮|ammonia nitrogen|\bnh3-?n\b/],
  ['total_phosphorus', /总磷|total phosphorus|\btp\b/],
  ['total_nitrogen', /总氮|total nitrogen|\btn\b/],
  ['mercury', /汞(?:及其化合物)?|mercury|\bhg\b/],
  ['lead', /铅(?:及其化合物)?|\blead\b|\bpb\b/],
  ['arsenic', /砷(?:及其化合物)?|arsenic|\bas\b/],
  ['cadmium', /镉(?:及其化合物)?|cadmium|\bcd\b/],
  ['chromium', /铬(?:及其化合物)?|chromium|\bcr\b/],
  ['copper', /铜(?:及其化合物)?|\bcopper\b|\bcu\b/],
  ['zinc', /锌(?:及其化合物)?|\bzinc\b|\bzn\b/],
  ['nickel', /镍(?:及其化合物)?|\bnickel\b|\bni\b/],
  ['hydrogen_chloride', /氯化氢|hydrogen chloride|\bhcl\b/],
  ['hydrogen_sulfide', /硫化氢|hydrogen sulfide|\bh2s\b/],
  ['ammonia', /氨气|ammonia|\bnh3\b/],
  ['carbon_monoxide', /一氧化碳|carbon monoxide|\bco\b/],
  ['sulfuric_acid_mist', /硫酸雾|sulfuric acid mist/],
  ['fluoride', /氟化物|fluoride/],
  ['chlorine', /氯气|chlorine|\bcl2\b/],
  ['nmhc', /非甲烷总烃|non-?methane hydrocarbon|\bnmhc\b/],
  ['tsp', /总悬浮颗粒物|total suspended particulate|\btsp\b/],
];

const SCOPE3_CATEGORIES = [
  [1, /购买的商品和服务|采购商品|purchased goods|category\s*0?1\b|类别\s*0?1/],
  [2, /资本商品|capital goods|category\s*0?2\b|类别\s*0?2/],
  [3, /燃料和能源相关|fuel[- ]and[- ]energy|category\s*0?3\b|类别\s*0?3/],
  [4, /上游运输|upstream transport|category\s*0?4\b|类别\s*0?4/],
  [5, /运营产生的废弃物|waste generated in operations|category\s*0?5\b|类别\s*0?5/],
  [6, /商务旅行|商务差旅|business travel|category\s*0?6\b|类别\s*0?6/],
  [7, /员工通勤|employee commuting|category\s*0?7\b|类别\s*0?7/],
  [8, /上游租赁|upstream leased|category\s*0?8\b|类别\s*0?8/],
  [9, /下游运输|downstream transport|category\s*0?9\b|类别\s*0?9/],
  [10, /售出产品的加工|processing of sold products|category\s*10\b|类别\s*10/],
  [11, /售出产品的使用|use of sold products|category\s*11\b|类别\s*11/],
  [12, /售出产品的报废|end[- ]of[- ]life|category\s*12\b|类别\s*12/],
  [13, /下游租赁|downstream leased|category\s*13\b|类别\s*13/],
  [14, /特许经营|franchise|category\s*14\b|类别\s*14/],
  [15, /投资活动|投资|investment|category\s*15\b|类别\s*15/],
];

const POPULATIONS = [
  ['board_members', /董事会|董事|board/],
  ['management', /高级管理层|高管|管理层|management/],
  ['employees', /员工|雇员|职工|employee|workforce/],
  ['value_chain_workers', /供应商|价值链|supplier|value chain/],
  ['communities', /社区|community|communities/],
  ['consumers', /消费者|客户|consumer|customer/],
];

function pollutant(value) {
  const raw = compact(value);
  if (!raw) return null;
  const hit = POLLUTANTS.find(([, pattern]) => pattern.test(raw));
  return hit ? hit[0] : null;
}

function medium(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/废气|空气|大气|air/.test(raw)) return 'air';
  if (/废水|污水|水体|水|water/.test(raw)) return 'water';
  if (/土壤|soil/.test(raw)) return 'soil';
  return null;
}

function scope(value) {
  const raw = compact(value);
  if (!raw) return null;
  // "Scope 1+2", "SCOPE1+2" and "范围一、范围二" name the combined total even
  // though only one scope word is spelled out.
  if (/scope\s*1\s*[+&、,，/]\s*2|范围[一1]\s*[+&、,，/]\s*[二2]|范围一[、和及]范围二/i.test(raw)) return 'total';
  // A label naming one scope is that scope even when it says "total"; a label
  // naming several scopes is the combined total.
  const named = [/范围一|范围1|scope1|第一|直接|direct/, /范围二|范围2|scope2|second/,
    /范围三|范围3|scope3|third/].filter(pattern => pattern.test(raw)).length;
  if (named > 1) return 'total';
  if (/范围三|范围3|scope3|third/.test(raw)) return 'scope_3';
  if (/范围二|范围2|scope2|second/.test(raw)) return 'scope_2';
  if (/范围一|范围1|scope1|第一|直接|direct/.test(raw)) return 'scope_1';
  if (/总计|总量|合计|total/.test(raw)) return 'total';
  return null;
}

function aggregation(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/小计|subtotal|sub-total/.test(raw)) return 'subtotal';
  if (/其中|分项|单项|component|of which/.test(raw)) return 'component';
  if (/总计|总量|合计|总和|累计|total|sum/.test(raw)) return 'total';
  return null;
}

function category(value) {
  const raw = compact(value);
  if (!raw) return null;
  // A named or numbered category stays that category even when its label also
  // says "total" (a total within category 1 is still category 1).
  for (const [number, pattern] of SCOPE3_CATEGORIES) if (pattern.test(raw)) return `category_${number}`;
  if (/总量|合计|全部类别|所有类别|all categories|total/.test(raw)) return 'total';
  return null;
}

function consolidation(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/运营控制|operational[\s_-]*control/.test(raw)) return 'operational_control';
  if (/财务控制|financial[\s_-]*control/.test(raw)) return 'financial_control';
  if (/股权比例|权益比例|equity[\s_-]*share/.test(raw)) return 'equity_share';
  return null;
}

function method(value) {
  const raw = clean(value);
  if (!raw) return null;
  if (/地点法|位置法|基于位置|基于地点|location[\s_-]*based|绿电未抵扣|未抵扣绿电/i.test(raw)) return 'location_based';
  if (/市场法|基于市场|market[\s_-]*based|绿电抵扣|抵扣绿电/i.test(raw)) return 'market_based';
  return null;
}

function denominator(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/营业收入|营收|销售额|revenue|sales/.test(raw)) return 'revenue';
  // "员工" alone names the population, not a per-employee denominator. Only
  // an explicit per-person phrase means the denominator is employee count.
  if (/每人|人均|peremployee|perperson|员工人数|headcount/.test(raw)) return 'employees';
  if (/产量|产品产量|productionoutput|production/.test(raw)) return 'production_output';
  if (/增加值|valueadded/.test(raw)) return 'value_added';
  if (/产值|outputvalue/.test(raw)) return 'output_value';
  if (/面积|area/.test(raw)) return 'area';
  if (/能耗|energy/.test(raw)) return 'energy_consumption';
  return null;
}

function hazardClassification(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/危险|有害|hazardous/.test(raw)) return 'hazardous';
  if (/非危险|无害|一般废弃物|non-?hazardous/.test(raw)) return 'non_hazardous';
  return null;
}

function waterMeasure(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/循环使用|重复利用|再生水|recycled|reused/.test(raw)) return 'recycled_reused';
  if (/取水|抽取|用水|withdrawal/.test(raw)) return 'withdrawal';
  if (/耗水|消耗|consumption/.test(raw)) return 'consumption';
  return null;
}

function denominatorScope(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/^employees?$|^peremployee$|^perperson$/.test(raw)) return 'per_employee';
  const resolved = denominator(value);
  return resolved === 'employees' ? 'per_employee' : resolved;
}

function energySource(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/可再生|清洁能源|renewable/.test(raw)) return 'renewable';
  if (/化石|fossil/.test(raw)) return 'fossil';
  if (/全部|所有|总计|合计|total|all/.test(raw)) return 'all';
  return null;
}

function accidentDefinition(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/可记录|recordable|trir/.test(raw)) return 'recordable';
  if (/失时|损失工作日|losttime|ltir/.test(raw)) return 'lost_time';
  if (/死亡|致命|fatality/.test(raw)) return 'fatality';
  return null;
}

function gender(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/女性|女员工|妇女|female|women/.test(raw)) return 'female';
  if (/男性|男员工|male|men/.test(raw)) return 'male';
  if (/其他性别|other/.test(raw)) return 'other';
  return null;
}

function managementLevel(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/高级管理层|最高管理层|高管|topmanagement|seniormanagement/.test(raw)) return 'top_management';
  if (/中层|middlemanagement/.test(raw)) return 'middle_management';
  if (/初级管理层|基层|juniormanagement|first-?line/.test(raw)) return 'junior_management';
  return null;
}

function boardDefinition(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/董事会|boardofdirectors|board/.test(raw)) return 'board_of_directors';
  return null;
}

function turnoverDefinition(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/自愿|主动离职|voluntary/.test(raw)) return 'voluntary';
  if (/非自愿|被动离职|involuntary/.test(raw)) return 'involuntary';
  if (/总体|全部|整体|总流失|总流动|overall|total/.test(raw)) return 'overall';
  return null;
}

function trainingScope(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/按性别|分性别|bygender|gender/.test(raw)) return 'by_gender';
  if (/管理层|management/.test(raw)) return 'management';
  if (/全员|全体员工|所有员工|allemployees|allstaff/.test(raw)) return 'all_employees';
  return null;
}

function denominatorUnit(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/每百万人时|百万工时|permillionhours/.test(raw)) return 'per_million_hours';
  if (/每千名员工|每千人|per1000/.test(raw)) return 'per_1000_workers';
  if (/每百名员工|每百人|per100/.test(raw)) return 'per_100_workers';
  if (/每名员工|每人|peremployee/.test(raw)) return 'per_employee';
  return null;
}

function rateMultiplier(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/每百万人时|百万工时|permillionhours/.test(raw)) return '1000000';
  if (/每千名员工|每千人|per1000/.test(raw)) return '1000';
  if (/每百名员工|每百人|per100/.test(raw)) return '100';
  return null;
}

function amountBasis(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/净额|净|net/.test(raw) && !/净零|零碳|netzero/.test(raw)) return 'net';
  if (/毛|总额|gross/.test(raw)) return 'gross';
  return null;
}

function timeBasis(value) {
  const raw = compact(value);
  if (!raw) return null;
  if (/年末|期末|年底|year[-]?end|periodend/.test(raw)) return 'period_end';
  if (/年初|期初|periodstart/.test(raw)) return 'period_start';
  if (/平均|年均|average|meanaverage/.test(raw)) return 'average';
  return null;
}

function population(value) {
  const raw = clean(value);
  if (!raw) return null;
  const hit = POPULATIONS.find(([, pattern]) => pattern.test(raw));
  return hit ? hit[0] : null;
}

const NORMALIZERS = Object.freeze({
  pollutant, substance: pollutant, pollution_medium: medium, ghg_scope: scope, aggregation_role: aggregation,
  scope3_category: category, consolidation_approach: consolidation, measurement_method: method,
  scope2_method: method, scope_2_method: method,
  denominator_scope: denominatorScope, intensity_denominator: denominator, amount_basis: amountBasis,
  statistical_time_basis: timeBasis, population, hazard_classification: hazardClassification,
  water_measure: waterMeasure, water_flow: waterMeasure, energy_source: energySource,
  accident_definition: accidentDefinition,
  gender, management_level_definition: managementLevel, board_definition: boardDefinition,
  turnover_definition: turnoverDefinition, training_scope: trainingScope,
  denominator_unit: denominatorUnit, rate_multiplier: rateMultiplier,
});

function normalize(key, value, {structured = false} = {}) {
  const normalizer = NORMALIZERS[key];
  if (normalizer) return normalizer(value);
  // A dictionary dimension with no controlled vocabulary (site, segment) may
  // keep a structured identity value verbatim. Free text from a label or a
  // statement is never turned into a code.
  return structured ? clean(value) || null : null;
}

/** Resolve one dimension from a printed label or value without a definition. */
export function normalizeDimension(key, value) {
  return normalize(key, value);
}

/**
 * Resolve platform dimensions from the evidence a delivered fact carries.
 * Later sources never overwrite an earlier, stronger one:
 * canonical extraction dimension > raw extraction dimension > source identity
 * > row label > printed unit > metric label > statement.
 */
export function resolveDimensions({definition, identity = {}, rawDimensions = [],
                                     label = '', statement = '', unit = '', metricLabel = ''}) {
  const wanted = new Set([
    ...(definition.required_dimensions || []),
    ...Object.keys(definition.dimension_constraints || {}),
    ...Object.keys(definition.typed_context_constraints || {}),
  ]);
  const values = {}, sources = {}, evidence = {};
  const take = (key, value, source, raw) => {
    if (!wanted.has(key) || nonempty(values[key]) || !nonempty(value)) return;
    values[key] = value; sources[key] = source; evidence[key] = clean(raw) || null;
  };
  for (const dimension of rawDimensions) {
    if (nonempty(dimension.canonical_value)) take(dimension.dimension_key, dimension.canonical_value,
      'extraction_dimension', dimension.raw_value);
  }
  for (const dimension of rawDimensions) {
    if (!nonempty(values[dimension.dimension_key]))
      take(dimension.dimension_key, normalize(dimension.dimension_key, dimension.raw_value, {structured: true}),
        'extraction_dimension_raw', dimension.raw_value);
  }
  for (const [identityKey, keys] of Object.entries(IDENTITY_AXES)) {
    if (!nonempty(text(identity[identityKey]))) continue;
    for (const key of keys)
      take(key, normalize(key, identity[identityKey], {structured: true}), 'source_identity', identity[identityKey]);
  }
  for (const key of wanted) {
    if (nonempty(values[key])) continue;
    for (const [value, source] of [[label, 'row_label'], [unit, 'source_unit'],
      [metricLabel, 'metric_label'], [statement, 'statement']]) {
      const normalised = normalize(key, value);
      if (nonempty(normalised)) { take(key, normalised, source, value); break; }
    }
  }
  return {values, sources, evidence};
}

const GROUP_BOUNDARY = /合并(?:口径|报表|范围|数据)?|本集团|集团(?:合并|整体|层面|口径)|全公司|公司整体|本公司及其子公司|全部子公司|consolidated|group[\s-]*wide|whole group|entire company|company[\s-]*wide/i;
const CANONICAL_GROUP = new Set(['group', 'listed_company', 'reporting_entity']);
const CANONICAL_PART = new Set(['part_of_group', 'subsidiary', 'business_segment']);

/**
 * Printed reporting boundaries are verbatim. Explicit group language is the
 * whole reporting group; anything else is a narrower scope and must stay
 * narrower. An already-normalised code keeps its meaning: `group` must not be
 * read as another narrower scope just because it is not the report's prose.
 */
export function normalizeBoundary(value) {
  const raw = clean(value);
  if (!raw) return null;
  const code = raw.toLowerCase().replace(/\s+/g, '_');
  if (CANONICAL_GROUP.has(code)) return {code: 'group', is_group: true, raw};
  if (CANONICAL_PART.has(code)) return {code: 'part_of_group', is_group: false, raw};
  return GROUP_BOUNDARY.test(raw)
    ? {code: 'group', is_group: true, raw}
    : {code: 'part_of_group', is_group: false, raw};
}
