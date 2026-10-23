#!/usr/bin/env python3
"""Build the product dictionary without changing the frozen standard packages.

Original clause IDs identify source requirements. Semantic metric IDs identify
product concepts. Package-core membership and platform-major selection stay
separate; an executable subset requires the explicit E1/E2/E3 topic filter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
STANDARD_ROOT = PLATFORM_ROOT.parent / 'ExtractESG' / 'standard_packages'
sys.path.insert(0, str(STANDARD_ROOT / 'src'))
from esg_standard_packages.scope import compile_profile, load_registry  # noqa: E402
from esg_standard_packages.io import canonical_json_bytes  # noqa: E402

VERSION = '1.4.0'
# Definition versions continue from the last sealed file dictionary, not from
# 1.0.0: a changed definition must never reuse a version already sealed.
# Dictionary versions are shared with platform extension selections (the
# live 1.3.0 is 1.2.0 plus an approved extension), so the next changed file
# dictionary must take a version above every sealed one.
PREVIOUS = 'catalog/history/metric-dictionary.1.2.0.json'
COMMON_CONDITIONS = [
    '主体、实际期间、报告边界和证据已审核；未知口径不默认为相同。',
    '原披露值和原单位保留；精确单位换算不改变主体、定义或测量方法。',
    '缺少比较维度的记录可以展示，但不进入符合口径的比较集合。',
]
COMMON_EXCLUSIONS = [
    '标题、目录、空白和缺失状态不能替代有值事实；显式零单独保留。',
    '目标值、预测值和基准值不能充当本期实际值。',
]


def spec(metric_id, label, value_type='numeric', unit=None, dimension=None,
         required=(), comparison=(), fixed=None, allowed=None, description='',
         exclusions=(), conditions=(), aggregation='do_not_sum', **extra):
    result = dict(metric_id=metric_id, label_zh=label, value_type=value_type,
                  canonical_unit=unit, unit_dimension=dimension,
                  required_dimensions=list(required),
                  comparison_dimensions=list(dict.fromkeys(['entity_kind', 'measurement_method', *required, *comparison])) if value_type == 'numeric' else [],
                  fixed_dimensions=fixed or {},
                  dimension_constraints={k: {'allowed_values': v} for k, v in (allowed or {}).items()},
                  required_context_fields=[], typed_context_constraints={},
                  description=description or label,
                  aggregation=aggregation,
                  exclusions=[*COMMON_EXCLUSIONS, *exclusions],
                  comparison_conditions=[*COMMON_CONDITIONS, *conditions],
                  value_role='actual' if value_type == 'numeric' else 'context',
                  comparison_mode='conditional_numeric' if value_type == 'numeric' else 'structured_display')
    result.update(extra)
    return result


def energy(metric_id, label, source, *, ratio=False):
    fixed = {'energy_source': source, 'energy_flow': 'consumption'}
    required = ['energy_source', 'energy_flow']
    if ratio:
        required += ['denominator_scope']
        fixed['denominator_scope'] = 'total_energy_consumption_same_boundary'
    else:
        required += ['aggregation_role']
        fixed['aggregation_role'] = 'total'
    return spec(metric_id, label, unit='ratio' if ratio else 'MWh', dimension='ratio' if ratio else 'energy',
                required=required, comparison=['energy_accounting_basis'], fixed=fixed,
                allowed={k: [v] for k, v in fixed.items()},
                exclusions=['发电量、装机容量、强度和减排量不能作为消费量。', '不能将总量和其成员重复求和。'],
                conditions=['自身运营能源口径一致；可再生比例的分子分母必须同期间、同边界。'],
                aggregation='ratio_recompute' if ratio else 'do_not_sum',
                normalization='percent_to_ratio' if ratio else 'compatible_unit_only',
                value_constraints={'minimum': '0', **({'maximum': '1'} if ratio else {})})


def ghg(metric_id, label, scope, method=None):
    required = ['ghg_scope', 'aggregation_role']
    fixed = {'ghg_scope': scope}
    allowed = {'ghg_scope': [scope], 'aggregation_role': ['total']}
    comparison = ['gwp_basis', 'consolidation_approach', 'amount_basis', 'accounting_standard']
    if scope == 'scope_3':
        required += ['scope3_category']
        allowed['aggregation_role'] = ['total', 'subtotal', 'category']
        comparison += ['category_coverage', 'estimation_method']
    result = spec(metric_id, label, unit='tCO2e', dimension='ghg_emissions',
                  required=required, comparison=comparison, fixed=fixed, allowed=allowed,
                  exclusions=['范围1、范围2、范围3及范围1+2合计独立，不能相互替换。', '减排量、抵消量、目标和排放强度不属于绝对毛排放量。'],
                  conditions=['GWP依据、核算边界与合并方法经审核一致。', '范围3总量、类别和小计分别展示；类别覆盖与估计方法相同才可比较。'] if scope == 'scope_3' else ['GWP依据、核算边界与合并方法经审核一致。'],
                  normalization='compatible_unit_only', value_constraints={'minimum': '0'})
    # These are declared scientific axes, never defaulted from the clause name.
    # Unknown gross/net basis can remain disclosed, but cannot meet the comparison contract.
    result['comparison_allowed_values'] = {'amount_basis': ['gross']}
    if method:
        result['fixed_dimensions']['measurement_method'] = method
        result['required_context_fields'] = ['measurement_method']
        result['typed_context_constraints'] = {'measurement_method': {'allowed_values': [method]}}
        result['comparison_conditions'].append('地点法与市场法独立；方法必须由来源支持，不能根据指标名补造。')
    return result


def pollution(metric_id, label, medium):
    return spec(metric_id, label, unit='tonne', dimension='mass',
                required=['pollutant', 'pollution_medium'], fixed={'pollution_medium': medium},
                allowed={'pollution_medium': [medium]}, comparison=['pollutant_definition', 'release_boundary'],
                conditions=['污染物种类、介质、排放边界和测量方法相同；不同污染物不相加。'],
                exclusions=['温室气体、排放浓度和污染削减量不属于本污染物排放质量。'],
                normalization='compatible_unit_only', value_constraints={'minimum': '0'})


def water(metric_id, label, measure, *, risk=False):
    required = ['water_measure', 'aggregation_role']
    fixed = {'water_measure': measure, 'aggregation_role': 'total'}
    comparison = ['water_definition']
    if risk:
        required += ['geography', 'water_risk_class']
        comparison += ['risk_classification_method']
    return spec(metric_id, label, unit='m3', dimension='volume', required=required,
                comparison=comparison, fixed=fixed, allowed={k: [v] for k, v in fixed.items()},
                exclusions=['耗水、取水、排水和循环利用水量相互独立。', '储水是存量；循环利用量可能包含多次利用，不能与取水相加。'],
                conditions=['水量定义与运营边界经审核一致。', '水风险地区需保留地域、风险分类及分类方法。'] if risk else ['水量定义与运营边界经审核一致。'],
                normalization='compatible_unit_only', value_constraints={'minimum': '0'})


# Product definitions are authored independently from the clauses. Pending
# definitions remain proposals and cannot acquire executable package identities.
SPECS = {
    'E1-5_01': energy('energy.total_consumption', '自身运营能源消费总量', 'all'),
    'E1-5_05': energy('energy.renewable_consumption', '可再生能源消费总量', 'renewable'),
    'E1-5_09': energy('energy.renewable_share', '可再生能源消费占比', 'renewable', ratio=True),
    'E1-6_07': ghg('ghg.scope1', '范围1温室气体毛排放量', 'scope_1'),
    'E1-6_09': ghg('ghg.scope2_location_based', '范围2温室气体毛排放量（地点法）', 'scope_2', 'location_based'),
    'E1-6_10': ghg('ghg.scope2_market_based', '范围2温室气体毛排放量（市场法）', 'scope_2', 'market_based'),
    'E1-6_11': ghg('ghg.scope3', '范围3温室气体毛排放量', 'scope_3'),
    'E2-4_02': pollution('pollution.air_emissions', '按污染物划分的空气排放量', 'air'),
    'E2-4_03': pollution('pollution.water_emissions', '按污染物划分的水体排放量', 'water'),
    'E2-4_09': spec('pollution.measurement_methodology', '空气、水和土壤污染测量方法', 'statement',
                       description='原报告对污染物测量方法、计算、监测和验证的描述，按适用污染物与介质关联。',
                       exclusions=['不能把方法存在与否转换成污染表现评分。']),
    'E2-5_02': spec('substances.concern_input_quantity', '生产生成、使用或采购的关注物质量', unit='tonne', dimension='mass',
                       required=['substance', 'hazard_class', 'concern_class', 'material_flow', 'aggregation_role'],
                       allowed={'concern_class': ['substances_of_concern'], 'material_flow': ['generated', 'used', 'procured', 'generated_used_procured_combined'], 'aggregation_role': ['total', 'hazard_class', 'substance']},
                       fixed={'concern_class': 'substances_of_concern'},
                       comparison=['classification_basis'],
                       conditions=['物质、危害类别、关注分类和生成/使用/采购口径相同。'],
                       exclusions=['生成、使用、采购量可能重叠，不能盲目相加。', '高度关注物质的单独数量与关注物质全量不能混同。'],
                       normalization='compatible_unit_only', value_constraints={'minimum': '0'}),
    'E3-4_01': water('water.consumption', '耗水总量', 'consumption'),
    'E3-4_02': water('water.consumption_risk_areas', '水风险地区耗水总量', 'consumption', risk=True),
    'E3-4_03': water('water.recycled_reused', '循环利用及重复使用水量', 'recycled_reused'),
    'E3-4_11': water('water.withdrawal', '取水总量', 'withdrawal'),
    'E3-4_12': water('water.discharge', '排水总量', 'discharge'),
    'E4.IRO-1_14': spec('biodiversity.sensitive_site_presence', '敏感生物多样性区域内或附近场址情况', 'statement', required=['site', 'sensitivity_class']),
    'E4.SBM-3_04': spec('biodiversity.impacted_sensitive_areas', '受影响的敏感生物多样性区域', 'statement', required=['site', 'sensitivity_class', 'impact_type']),
    'E4-5_01': spec('biodiversity.negatively_impacted_site_count', '负面影响敏感区域的场址数量', unit='count', dimension='count', required=['site_definition', 'sensitivity_class', 'impact_type'], comparison=['classification_basis']),
    'E4-5_02': spec('biodiversity.negatively_impacted_site_area', '负面影响敏感区域的场址面积', unit='ha', dimension='area', required=['site_definition', 'sensitivity_class', 'impact_type'], comparison=['classification_basis', 'area_measurement_basis']),
    'E5-4_02': spec('materials.total_input_weight', '产品及技术和生物材料投入总重量', unit='tonne', dimension='mass', required=['material_scope', 'material_flow', 'aggregation_role'], exclusions=['产品重量与投入材料若重叠，不能重复求和。']),
    'E5-4_05': spec('materials.secondary_share', '二次使用或再生组分及材料占比', unit='ratio', dimension='ratio', required=['material_scope', 'denominator_scope'], aggregation='ratio_recompute'),
    'E5-5_07': spec('waste.generated', '废弃物产生总量', unit='tonne', dimension='mass', required=['waste_scope', 'waste_flow', 'aggregation_role'], fixed={'waste_flow': 'generated', 'aggregation_role': 'total'}),
    'E5-5_11': spec('waste.non_recycled_share', '未回收废弃物占比', unit='ratio', dimension='ratio', required=['waste_scope', 'denominator_scope'], aggregation='ratio_recompute', exclusions=['未回收不能直接等同于处置、填埋或危险废物。']),
    'E5-5_15': spec('waste.hazardous', '危险废弃物总量', unit='tonne', dimension='mass', required=['waste_scope', 'hazard_classification', 'aggregation_role'], comparison=['classification_basis']),
    'S1-6_02': spec('workforce.employee_headcount', '员工人头数', unit='person', dimension='count', required=['population', 'count_basis', 'statistical_time_basis'], fixed={'population': 'employees', 'count_basis': 'headcount'}, exclusions=['员工人头数、全职当量和全部自有劳动力不可互换。']),
    'S1-6_12': spec('workforce.turnover_rate', '员工流动率', unit='ratio', dimension='ratio', required=['population', 'turnover_definition', 'denominator_scope'], aggregation='ratio_recompute'),
    'S1-9_02': spec('workforce.top_management_gender_share', '最高管理层员工性别分布占比', unit='ratio', dimension='ratio', required=['population', 'gender', 'management_level_definition'], aggregation='ratio_recompute'),
    'S1-13_03': spec('workforce.training_hours', '按性别划分的平均培训小时数', unit='hour/person', dimension='training_intensity', required=['population', 'gender', 'training_scope', 'denominator_scope'], aggregation='ratio_recompute'),
    'S1-14_02': spec('workforce.work_related_fatalities', '自有劳动力因工伤或职业病死亡人数', unit='person', dimension='count', required=['population', 'fatality_cause_scope', 'aggregation_role']),
    'S1-14_05': spec('workforce.recordable_accident_rate', '自有劳动力可记录工伤事故率', unit='rate', dimension='accident_rate', required=['population', 'accident_definition', 'denominator_unit', 'rate_multiplier'], aggregation='ratio_recompute', exclusions=['事故率不是百分数；可记录事故率与误工事故频率不能互换。']),
    'S1-16_01': spec('workforce.gender_pay_gap', '性别薪酬差距', unit='ratio', dimension='ratio', required=['population', 'pay_definition', 'denominator_scope'], aggregation='ratio_recompute'),
    'S2-1_05': spec('value_chain_worker.forced_child_labour_policy', '价值链劳工政策对人口贩卖、强迫及童工的覆盖', 'statement', required=['policy_scope', 'worker_population']),
    'S2-3_02': spec('value_chain_worker.grievance_channels', '价值链劳动者申诉渠道', 'statement', required=['worker_population', 'channel_scope']),
    'S2-3_06': spec('value_chain_worker.anti_retaliation_policy', '价值链劳动者申诉反报复政策', 'statement', required=['worker_population', 'policy_scope']),
    'S2-4_11': spec('value_chain_worker.severe_human_rights_issues', '价值链严重人权问题和事件', 'statement', required=['worker_population', 'value_chain_stage', 'incident_scope']),
    'S3-1_01': spec('community.indigenous_policy', '原住民影响预防和应对政策', 'statement', required=['community_scope', 'policy_scope']),
    'S3-3_11': spec('community.grievance_channels', '受影响社区申诉渠道', 'statement', required=['community_scope', 'channel_scope']),
    'S3-4_02': spec('community.remedy_actions', '对社区实际重大影响的补救行动', 'statement', required=['community_scope', 'impact_scope', 'remedy_scope']),
    'S3-4_11': spec('community.severe_human_rights_issues', '受影响社区严重人权问题和事件', 'statement', required=['community_scope', 'incident_scope']),
    'S4-1_01': spec('consumer.impact_policies', '消费者及终端用户重大影响、风险和机遇政策', 'statement', required=['consumer_population', 'policy_scope']),
    'S4-3_02': spec('consumer.grievance_channels', '消费者及终端用户申诉渠道', 'statement', required=['consumer_population', 'channel_scope']),
    'S4-3_13': spec('consumer.complaint_count', '消费者及终端用户投诉数量', unit='count', dimension='count', required=['consumer_population', 'complaint_definition', 'channel_scope'], comparison=['business_scale_basis'], exclusions=['投诉总量缺少业务规模和渠道覆盖时不能排名。']),
    'S4-4_11': spec('consumer.severe_human_rights_issues', '消费者及终端用户严重人权问题和事件', 'statement', required=['consumer_population', 'incident_scope']),
    'G1-1_03': spec('business.anti_corruption_policy_absence', '未制定符合联合国反腐败公约的反腐败或反贿赂政策', 'statement', required=['policy_scope'], statement_polarity='negative_absence', exclusions=['原条款为未制定政策；不得反转为已制定政策。']),
    'G1-4_01': spec('business.corruption_conviction_count', '违反反腐败及反贿赂法律的定罪数量', unit='count', dimension='count', required=['legal_scope', 'counting_basis'], exclusions=['内部处分、指控和确认事件不能当法律定罪。']),
    'G1-4_02': spec('business.corruption_fines', '违反反腐败及反贿赂法律的罚款金额', unit=None, dimension='monetary', required=['legal_scope', 'currency', 'fine_basis'], comparison=['currency'], exclusions=['保留原币种；不按当前汇率隐式转换。']),
    'G1-4_04': spec('business.confirmed_corruption_count', '已确认腐败或贿赂事件数量', unit='count', dimension='count', required=['incident_definition', 'counting_basis']),
    'G1-6_01': spec('business.average_payment_days', '自合同或法定付款起算日计的平均付款天数', unit='day', dimension='duration', required=['payment_start_definition', 'invoice_scope', 'averaging_basis'], aggregation='ratio_recompute'),
    'E1-5_02': energy('energy.fossil_consumption', '化石能源消费总量', 'fossil'),
    'E1-6_12': spec('ghg.total_location_based', '范围1＋位置法范围2＋范围3温室气体排放总量', unit='tCO2e', dimension='ghg_emissions',
                       required=['ghg_scope', 'aggregation_role', 'consolidation_approach'], fixed={'ghg_scope': 'total'},
                       allowed={'ghg_scope': ['total'], 'aggregation_role': ['total']},
                       comparison=['amount_basis', 'gwp_basis', 'accounting_standard'],
                       exclusions=['范围1、范围2、范围3与合计不能相互替代；强度、减排量与抵消量不属于绝对排放量。'],
                       conditions=['GWP依据、合并方法与核算标准经审核一致。'],
                       normalization='compatible_unit_only', value_constraints={'minimum': '0'}),
    'E1-6_30': spec('ghg.intensity_location_based', '基于位置法的温室气体排放强度', unit='tCO2e-per-10k-cny', dimension='ghg-intensity',
                       required=['intensity_numerator_scope', 'intensity_denominator', 'consolidation_approach'],
                       fixed={'intensity_numerator_scope': 'scope_1_2_location_based'},
                       comparison=['revenue_definition'],
                       exclusions=['绝对排放总量不能与排放强度互换；净收入、营业收入与产值是不同的分母。'],
                       conditions=['分子范围与分母定义经审核一致；不同分母的强度不相加。'],
                       normalization='compatible_unit_only', value_constraints={'minimum': '0'}),
    'S1-13_04': spec('workforce.training_hours_per_person', '员工人均培训小时数', unit='hour/person', dimension='training_intensity',
                        required=['population', 'training_scope', 'denominator_scope'], aggregation='ratio_recompute',
                        exclusions=['培训总时长不能代替人均时长；专题培训不能当作全员人均培训。'],
                        normalization='compatible_unit_only', value_constraints={'minimum': '0'}),
    'S1-17_02': spec('workforce.discrimination_incidents', '歧视事件数量', unit='count', dimension='count',
                        required=['population', 'incident_definition', 'counting_basis'],
                        exclusions=['已确认事件、投诉与内部处分分别计数，不能互相替代。'],
                        normalization='compatible_unit_only', value_constraints={'minimum': '0'}),
    'GOV-1_06': spec('governance.board_gender_ratio', '董事会性别多样性比例', unit='ratio', dimension='ratio',
                        required=['gender', 'board_definition'], aggregation='ratio_recompute',
                        exclusions=['董事会与管理层是不同人群；占比不能与人数互换。'],
                        normalization='percent_to_ratio', value_constraints={'minimum': '0', 'maximum': '1'}),
}


def _semantic_signature(value):
    if isinstance(value, dict):
        return {key: _semantic_signature(item) for key, item in value.items()
                if key != 'definition_version' and item is not None and item != [] and item != {}
                and not (key == 'semantic_contract_version' and item == 'legacy-unpartitioned-v1')}
    if isinstance(value, list):
        return [_semantic_signature(item) for item in value]
    return value


def _next_definition_version(previous, current):
    if previous is None:
        return '1.0.0'
    if _semantic_signature(previous) == _semantic_signature(current):
        return previous['definition_version']
    major, minor, patch = (int(part) for part in previous['definition_version'].split('.'))
    return f'{major}.{minor+1}.0'


def build_dictionary(standard_root=STANDARD_ROOT, *, dictionary_version=VERSION,
                     previous_dictionary=None):
    if previous_dictionary is None:
        previous_path = PLATFORM_ROOT / PREVIOUS
        previous_dictionary = json.loads(previous_path.read_text()) if previous_path.is_file() else None
    previous_metrics = {m['source_datapoint_id']: m for m in (previous_dictionary or {}).get('metrics', [])}
    registry, catalog, packages = load_registry(standard_root)
    profile_path = standard_root / 'profiles/esrs/2023-set1/platform-major.json'
    profile = json.loads(profile_path.read_text())
    selected = profile['source_datapoint_ids']
    if set(selected) != set(SPECS):
        raise ValueError(f'The authored dictionary must match every selected original ID: {sorted(set(selected) ^ set(SPECS))}')
    records = {r['id']: r for r in catalog['records'] if r['record_kind'] == '独立编号条目'}
    modules = {original: m for m in registry['modules'] for original in m['source_datapoint_ids']}
    metrics = []
    for original in selected:
        record, module = records[original], modules[original]
        # A compiled module is not yet a platform commitment: modules generated
        # from the catalog stay planned until their full analysis is authored.
        ready = module['executable'] and module['core_analysis_state'] == 'authored_draft'
        if original not in module['core_source_datapoint_ids']:
            raise ValueError('A platform-major clause must be package-core: ' + original)
        metric = json.loads(json.dumps(SPECS[original], ensure_ascii=False))
        source_metric = None
        if ready:
            package = packages[module['disclosure_requirement']]
            source_metric = next(m for m in package.metrics if m.source_datapoint_id == original)
            contract = source_metric.measurement_contract.model_dump(mode='json')
            # A package metric without an authored Chinese label was generated
            # from the catalog; it does not replace the dictionary's authored
            # description or value role.
            if source_metric.labels.get('zh') not in (None, source_metric.labels.get('en')):
                metric['description'] = source_metric.description
                metric['value_role'] = contract['value_role']
            source_row_hash = source_metric.provenance.row_sha256
        else:
            contract = None
            # This is the compiler's exact row digest convention.
            source_row_hash = hashlib.sha256(canonical_json_bytes(record['raw_values'])).hexdigest()
        metric.update(source_datapoint_id=original,
                      original_name=record['name'], original_data_type=record['data_type'],
                      topic=record['standard'].strip(), readiness='ready' if ready else 'planned',
                      definition_status='authored_draft' if ready else 'planned_proposal',
                      package_core=True, platform_major=True,
                      standard_binding={
                          'package_id': module['package_id'],
                          'package_version': module['package_version'] if ready else None,
                          'metric_id': source_metric.metric_id if ready else None,
                          'digest': module['source_digest'] if ready else None,
                      },
                      source_measurement_contract=contract,
                      source_reference={
                          'catalog_id': catalog['catalog_id'],
                          'workbook_sha256': catalog['source_sha256'],
                          'row_sha256': source_row_hash,
                          'source_sheet': record['source_sheet'], 'source_row': record['source_row'],
                          'paragraph': record['paragraph'], 'related_ar': record['related_ar'],
                          'conditional_or_alternative': record['conditional_or_alternative'],
                          'may': record['may'],
                      })
        metric['definition_version'] = _next_definition_version(previous_metrics.get(original), metric)
        metrics.append(metric)
    if len({m['metric_id'] for m in metrics}) != len(metrics):
        raise ValueError('Semantic metric IDs must be unique')
    dictionary = dict(dictionary_version=dictionary_version, comparison_contract_version='1.0.0',
                      profile_id=profile['profile_id'], profile_version=profile['profile_version'],
                      source_catalog_id=catalog['catalog_id'],
                      source_catalog_hash=hashlib.sha256((standard_root / 'catalogs/esrs/2023-set1/ig3-original.json').read_bytes()).hexdigest(),
                      source_sha256=catalog['source_sha256'],
                      selection_terms={'package_core': '标准包内部核心条款', 'platform_major': '平台当前选定的主要条款；主要条款属于包内核心，但不激活全部核心'},
                      metrics=metrics)
    compiled_profile = compile_profile(standard_root, profile_path, topics=['E1', 'E2', 'E3'])
    compiled_profile.update(dictionary_version=dictionary_version,
                            overall_source_datapoint_ids=selected,
                            ready_source_datapoint_ids=[m['source_datapoint_id'] for m in metrics if m['readiness'] == 'ready'],
                            pending_source_datapoint_ids=[m['source_datapoint_id'] for m in metrics if m['readiness'] == 'planned'])
    return dictionary, compiled_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=PLATFORM_ROOT / 'catalog')
    parser.add_argument('--dictionary-version', default=VERSION)
    parser.add_argument('--previous-dictionary', type=Path)
    args = parser.parse_args()
    previous = json.loads(args.previous_dictionary.read_text()) if args.previous_dictionary else None
    dictionary, profile = build_dictionary(dictionary_version=args.dictionary_version, previous_dictionary=previous)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in [('metric-dictionary.v1.json', dictionary), ('extraction-profile.e1-e3.json', profile)]:
        (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'dictionary_version': dictionary['dictionary_version'], 'major_count': len(dictionary['metrics']),
                      'ready_count': len(profile['ready_source_datapoint_ids']),
                      'pending_count': len(profile['pending_source_datapoint_ids']),
                      'extraction_output_count': profile['output_count']}))


if __name__ == '__main__':
    main()
