import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveDimensions, normalizeBoundary, REVIEW_EVIDENCE_VERSION} from '../../../database/scripts/review-evidence.mjs';

const definition = (required, extra = {}) => ({
  required_dimensions: required, dimension_constraints: {}, typed_context_constraints: {},
  fixed_dimensions: {}, ...extra,
});

test('report identity axes become the dictionary codes', () => {
  const resolved = resolveDimensions({
    definition: definition(['pollutant', 'pollution_medium', 'ghg_scope', 'aggregation_role',
      'scope3_category', 'consolidation_approach', 'amount_basis']),
    identity: {pollutant: 'NOx', medium: '废气', scope: '范围一', aggregation_role: '总量',
      category: '类别 7', consolidation_approach: '运营控制', amount_basis: '总额'},
  });
  assert.equal(REVIEW_EVIDENCE_VERSION, 'review-evidence-v1');
  assert.deepEqual(resolved.values, {
    pollutant: 'nox', pollution_medium: 'air', ghg_scope: 'scope_1', aggregation_role: 'total',
    scope3_category: 'category_7', consolidation_approach: 'operational_control', amount_basis: 'gross',
  });
  for (const source of Object.values(resolved.sources)) assert.equal(source, 'source_identity');
});

test('pollutant families and Chinese names normalise consistently', () => {
  const cases = {
    '二氧化硫（SO2）²': 'so2', '硫氧化物（SOx）': 'sox', '氮氧化物': 'nox', '挥发性有机物（VOCs）³': 'vocs',
    '颗粒物（PM）': 'pm', '化学需氧量（COD）': 'cod', '氨氮（NH3-N）': 'ammonia_nitrogen',
    '汞及其化合物': 'mercury', '氯化氢': 'hydrogen_chloride', '一氧化碳': 'carbon_monoxide',
  };
  for (const [label, code] of Object.entries(cases)) {
    const resolved = resolveDimensions({definition: definition(['pollutant']), label});
    assert.equal(resolved.values.pollutant, code, label);
  }
});

test('scope wording distinguishes one scope from the combined total', () => {
  const one = resolveDimensions({definition: definition(['ghg_scope']), label: '范围三排放总量'});
  assert.equal(one.values.ghg_scope, 'scope_3');
  const all = resolveDimensions({definition: definition(['ghg_scope']), label: '范围1＋范围2＋范围3排放总量'});
  assert.equal(all.values.ghg_scope, 'total');
  const scope2 = resolveDimensions({definition: definition(['ghg_scope']), identity: {scope: 'Scope 2'}});
  assert.equal(scope2.values.ghg_scope, 'scope_2');
});

test('scope 3 named categories and numbered labels agree', () => {
  const named = resolveDimensions({definition: definition(['scope3_category']), label: '购买的商品和服务'});
  assert.equal(named.values.scope3_category, 'category_1');
  const numbered = resolveDimensions({definition: definition(['scope3_category']), identity: {category: 'Category 11'}});
  assert.equal(numbered.values.scope3_category, 'category_11');
  const total = resolveDimensions({definition: definition(['scope3_category']), label: '范围三排放总量'});
  assert.equal(total.values.scope3_category, 'total');
});

test('method, denominator, time basis and population resolve from labels', () => {
  const method = resolveDimensions({definition: definition(['measurement_method']), label: '范围二（基于市场）'});
  assert.equal(method.values.measurement_method, 'market_based');
  const denominator = resolveDimensions({definition: definition(['intensity_denominator']), label: '吨二氧化碳当量/百万元营收'});
  assert.equal(denominator.values.intensity_denominator, 'revenue');
  const time = resolveDimensions({definition: definition(['statistical_time_basis']), label: '员工总数（年末）'});
  assert.equal(time.values.statistical_time_basis, 'period_end');
  const average = resolveDimensions({definition: definition(['statistical_time_basis']), label: '平均员工人数'});
  assert.equal(average.values.statistical_time_basis, 'average');
  const population = resolveDimensions({definition: definition(['population']), metricLabel: '女性董事占比'});
  assert.equal(population.values.population, 'board_members');
});

test('workforce and waste axes normalise only when the source says them', () => {
  const hazardous = resolveDimensions({definition: definition(['hazard_classification']), label: '危险废弃物总量'});
  assert.equal(hazardous.values.hazard_classification, 'hazardous');
  const nonHazardous = resolveDimensions({definition: definition(['hazard_classification']), label: '无害废弃物'});
  assert.equal(nonHazardous.values.hazard_classification, 'non_hazardous');
  const gender = resolveDimensions({definition: definition(['gender']), label: '女性员工占比'});
  assert.equal(gender.values.gender, 'female');
  const level = resolveDimensions({definition: definition(['management_level_definition']), label: '高级管理层女性占比'});
  assert.equal(level.values.management_level_definition, 'top_management');
  const turnover = resolveDimensions({definition: definition(['turnover_definition']), label: '自愿离职率'});
  assert.equal(turnover.values.turnover_definition, 'voluntary');
  const training = resolveDimensions({definition: definition(['training_scope']), label: '按性别划分的培训时长'});
  assert.equal(training.values.training_scope, 'by_gender');
  const rate = resolveDimensions({definition: definition(['denominator_unit', 'rate_multiplier']), label: '每百人工伤率'});
  assert.equal(rate.values.denominator_unit, 'per_100_workers');
  assert.equal(rate.values.rate_multiplier, '100');
  const substance = resolveDimensions({definition: definition(['substance']), identity: {pollutant: 'NOx'}});
  assert.equal(substance.values.substance, 'nox');
});

test('a canonical extraction dimension outranks its raw form and later labels', () => {
  const resolved = resolveDimensions({
    definition: definition(['pollutant']),
    rawDimensions: [{dimension_key: 'pollutant', raw_value: 'NOx', canonical_value: 'nox'}],
    label: '二氧化硫排放',
  });
  assert.equal(resolved.values.pollutant, 'nox');
  assert.equal(resolved.sources.pollutant, 'extraction_dimension');
});

test('an unstated axis stays unresolved; policy is the caller decision', () => {
  const resolved = resolveDimensions({definition: definition(['pollutant']), label: '排放量'});
  assert.equal('pollutant' in resolved.values, false);
});

test('boundary wording separates the whole group from a narrower scope', () => {
  for (const value of ['集团合并口径', '合并报表范围', '本集团', 'consolidated', '公司整体', '全公司'])
    assert.equal(normalizeBoundary(value).is_group, true, value);
  for (const value of ['下属子公司', '某基地', '中国大陆地区', '集团下属某工厂'])
    assert.equal(normalizeBoundary(value).is_group, false, value);
  assert.equal(normalizeBoundary('集团合并口径').code, 'group');
  assert.equal(normalizeBoundary('下属子公司').code, 'part_of_group');
  assert.equal(normalizeBoundary(''), null);
});
