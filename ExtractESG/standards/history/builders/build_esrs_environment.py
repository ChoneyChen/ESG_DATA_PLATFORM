"""Build complete E1–E3 modules and shared MDR from portable, frozen sources.

All 29 modules validate in staging before installation. Existing source/package
versions are immutable. No private research path, Excel dependency or model call.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import re
import shutil
import tempfile

from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.io import canonical_json_bytes, load_json, sha256_bytes, write_json
from esrs_semantics import ALIASES, CONFUSABLE, DOMAIN_GUIDANCE, FOCUS, FOCUS_REASON, LABELS, PLATFORM_SELECTION, RELATIONS

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2.1.1'
CATALOG_PATH = Path('catalogs/esrs/2023-set1/ig3-original.json')
REGISTRY_PATH = Path('catalogs/esrs/2023-set1/registry.json')
LAW_URL = 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32023R2772'
REPRODUCTION_URL = 'https://xbrl.efrag.org/e-esrs/esrs-set1-2023.html'
FILES = ['metrics', 'elements', 'concepts', 'relations', 'code_sets', 'dimensions', 'derivations', 'validation_rules', 'sources']
COMMON_AXES = ['reporting_entity', 'reporting_period', 'reporting_boundary']
UNIT_PREFIX = 'extractesg.core.unit.'
UNIT_DIM = 'extractesg.core.unit-dimension.'


def slug(dr):
    return dr.lower().replace('.', '-')


def identity(dr):
    return 'esrs.2023-set1.' + slug(dr)


def mid(dr, source_id):
    return identity(dr) + '.dp' + source_id.rsplit('_', 1)[1]


def source_ref(dr, code):
    return identity(dr) + '.source.' + code


def provenance(catalog, row):
    return dict(catalog_id=catalog['catalog_id'], workbook_sha256=catalog['source_sha256'],
                source_sheet=row['source_sheet'], source_row=row['source_row'], raw_values=row['raw_values'],
                source_headers=row['source_headers'], row_sha256=sha256_bytes(canonical_json_bytes(row['raw_values'])),
                original_data_type=row['data_type'], **{k: row[k] for k in ['conditional_or_alternative', 'may', 'eu_regulations', 'phase_in_column_k', 'phase_in_column_l']})


def shared_reference(row):
    match = re.fullmatch(r'([^.]+)\.(MDR-[PAT])_(\d+)-(\d+)', row['id'])
    if not match:
        raise ValueError('Unrecognized shared reference: ' + row['id'])
    topic, dr, first, last = match.groups()
    adopted = int(first) == 1
    noun = {'P': 'policy', 'A': 'action', 'T': 'target'}[dr[-1]]
    return dict(source_datapoint_id=row['id'], source_sheet=row['source_sheet'], source_row=row['source_row'],
                original_disclosure_requirement=row['dr'], target_package_id=identity(dr),
                target_package_version=VERSION, target_source_datapoint_ids=[f'{dr}_{i:02}' for i in range(int(first), int(last)+1)],
                topic_binding=topic, purpose=('adopted_' if adopted else 'absent_') + noun)


def obligation(row):
    conditional = 'conditional' in (row['conditional_or_alternative'] or '').casefold()
    alternative = 'alternative' in (row['conditional_or_alternative'] or '').casefold()
    voluntary = (row['may'] or '').strip().upper() == 'V'
    if row['source_sheet']=='ESRS 2':
        return 'voluntary' if voluntary else 'conditional' if conditional else 'mandatory'
    if conditional:
        return 'conditional_voluntary_if_material' if voluntary else 'conditional_if_material'
    if voluntary:
        return 'voluntary_if_material'
    if alternative:
        return 'alternative_if_material'
    return 'mandatory_if_material'


def shape(row):
    """Typed authoring defaults plus explicit semantic resolutions, never raw-row edits."""
    raw = (row['data_type'] or '').strip().casefold()
    code = row['id']
    structure = raw == 'table'
    mixed = raw in {'narrative/monetary', 'narrative/numerical', 'decimal/percent/narrative'}
    numeric = any(t in raw for t in ['monetary', 'monerary', 'percent', 'decimal', 'integer', 'intensity', 'energy', 'ghgemissions', 'mass', 'volume'])
    value_type = 'decimal' if numeric else 'text'
    if mixed and not numeric:
        value_type='decimal' # Narrative/numerical has independently typed optional numbers.
    if raw == 'integer': value_type = 'integer'
    if raw == 'date': value_type = 'date'
    if raw == 'gyear' or code in {'MDR-T_06', 'MDR-T_19'}: value_type = 'year'; numeric = False
    dim, unit, note = None, None, None
    for token, dimension, canonical in [('ghgemissions', 'ghg-emissions', 'tonne-co2e'), ('energy', 'energy', 'megawatt-hour'), ('mass', 'mass', 'tonne'), ('volume', 'volume', 'cubic-metre'), ('percent', 'ratio', 'ratio'), ('monetary', 'currency', None), ('monerary', 'currency', None), ('integer', 'count', 'count')]:
        if token in raw:
            dim, unit = dimension, canonical
            break
    if 'intensity' in raw or code in {'E1-5_18', 'E1-5_19', 'E1-6_30', 'E1-6_31', 'E3-4_08', 'E3-4_10'}:
        dim = 'water-intensity' if code.startswith('E3') else 'energy-intensity' if code.startswith('E1-5') else 'ghg-intensity'
        unit = 'cubic-metre-per-million-eur' if code == 'E3-4_08' else None
        value_type = 'decimal'; numeric = True
        note = '强度须有分子、分母、分母量级及范围；原表类型保存在provenance，不按百分比自动归一化。'
    if code == 'E1-8_04':
        dim, unit = 'carbon-price', 'currency-per-tonne-co2e'
        note = '原表Monetary描述价格；语义为每吨CO2e价格，不能按金额合计。'
    if code == 'MDR-T_05':
        dim = unit = None; value_type = 'decimal'
        note = 'ESRS 2第80(d)段的基准值沿用被测指标的单位与精度；保留原表Integer而不舍入实际小数。'
    if code == 'BP-2_12':
        dim=unit=None;value_type='decimal'
        note='比较值与重述值的差额沿用被测指标单位并可为负；原表Integer不用于强迫计数或舍入。'
    if code in {'MDR-T_06', 'MDR-T_19'}: dim = unit = None
    if code == 'MDR-T_02': dim = unit = None
    if code in {'E3-3_05', 'E3-3_06'}:
        mixed = False; numeric = False; value_type = 'text'; dim = unit = None
        note = 'ESRS E3第24(a)/(b)段描述生态阈值及企业分配方法；原表narrative/monetary不能解释为货币金额。'
    if 'monerary' in raw: note = '原表Monerary拼写保留；采用货币金额的decimal合同。'
    data_class = 'structure' if structure else 'mixed' if mixed else 'quantitative' if numeric else 'qualitative'
    return data_class, value_type, dim, unit, note


def axes_for(dr, row):
    code = row['id']; axes = list(COMMON_AXES)
    if dr.startswith('MDR'):
        axes += ['sustainability_topic', 'policy_ref' if dr.endswith('P') else 'action_ref' if dr.endswith('A') else 'target_ref' if dr.endswith('T') else 'measured_metric_ref']
    if dr == 'E1-4': axes += ['target_ref', 'baseline_year', 'target_year', 'ghg_scope', 'accounting_method']
    if dr == 'E1-3': axes += ['action_ref', 'value_role']
    if dr == 'E1-7': axes += ['project_ref', 'value_chain_boundary', 'removal_activity', 'credit_status']
    if dr == 'E1-8': axes += ['pricing_scheme', 'ghg_scope', 'accounting_method']
    if dr in {'E1-9', 'E2-6', 'E3-5'}: axes += ['risk_type', 'scenario', 'forecast_horizon', 'financial_item', 'currency']
    if dr == 'E2-5': axes += ['substance', 'hazard_class', 'concern_class', 'material_flow', 'aggregation_role']
    if dr == 'E3-4': axes += ['water_measure', 'geography', 'water_risk_class', 'measurement_method', 'aggregation_role']
    if dr.endswith('IRO-1') or dr.endswith('SBM-3'): axes += ['risk_type', 'time_horizon', 'scenario']
    if row['source_sheet']=='ESRS 2': axes += ['sustainability_topic']
    if dr in {'E2-3', 'E3-3'}: axes += ['target_ref', 'geography', 'target_year']
    if code == 'E3-4_07': axes += ['measurement_basis']
    if code in {'E3-4_08', 'E3-4_10'} or (dr == 'E1-4' and code.rsplit('_', 1)[1] in ['05', '08', '11', '14', '17']): axes += ['denominator', 'denominator_scale']
    if code == 'E1-9_17': axes += ['energy_efficiency_class']
    return list(dict.fromkeys(axes))


def contract_for(dr, row, metric):
    data_class, value_type, dim, unit, note = shape(row)
    code = row['id']; role = 'actual'
    if data_class == 'structure': role = 'structure'
    elif dr == 'E1-4' or dr in {'E2-3', 'E3-3'} or dr == 'MDR-T': role = 'target'
    elif dr in {'E1-9', 'E2-6', 'E3-5'} or code == 'E1-3_04': role = 'forecast'
    elif data_class == 'qualitative': role = 'context'
    if code in {'E1-7_11','MDR-A_11','MDR-A_12','SBM-3_09'}:role='forecast'
    if dr=='MDR-T' and code in {'MDR-T_13','MDR-T_16','MDR-T_17'}:role='actual'
    if dr=='E1-9' and code.rsplit('_',1)[1] in ['01','02','03','04','05','06','07','08','09','14','15','16','17','21','22','24','25','29','30','31','32','33','34','35','36']:
        role='actual' # Carrying values/current exposure, not forecast cash flow.
    if code in {'MDR-T_05', 'MDR-T_06', 'MDR-T_19'}: role = 'baseline'
    quantitative = metric['data_class'] in {'quantitative', 'mixed'}
    template = 'dimensional_table' if data_class == 'structure' or (row['data_type'] or '').casefold().startswith('table/') else 'financial_effect' if dr in {'E1-9', 'E2-6', 'E3-5'} else 'policy_action_target' if role in {'target','baseline'} or dr.startswith('MDR') else 'numeric' if quantitative else 'categorical' if (row['data_type'] or '').casefold() == 'semi-narrative' else 'qualitative_claim'
    exclusions = [DOMAIN_GUIDANCE.get(dr, metric['description']), '标题、目录、空白、未披露状态不能作为带值事实；显式零与缺失分开。']
    requirements = ['主体、实际期间和报告边界由来源明确支持；未知口径不能默认为相同。', '证据支持原值/陈述及必要维度；单位转换不能消除方法或边界差异。']
    if dim == 'currency': requirements.append('同币种、同量级及同财务口径；不进行隐式汇率换算。')
    if dim == 'ratio': requirements.append('匹配分子分母和比率定义；百分数可转换为0—1比例，但原值及单位保留。')
    if 'intensity' in (dim or ''): requirements.append('分子、分母、量级、期间和运营范围完全匹配；额外强度不能默认套用净收入分母。')
    if dr == 'E2-5': requirements.append('危害类别、关注分类及输入输出流向相同；生成、使用、采购可能重叠。')
    if dr == 'E3-4': requirements.append('水量概念、运营边界、水风险判定和方法相同；耗水、取水、排水分别比较。')
    axes = metric['identity_axes']
    return dict(template=template, value_role=role, unit_dimension=UNIT_DIM+dim if dim else None,
                canonical_unit_id=UNIT_PREFIX+unit if unit else None,
                normalization='percent_to_ratio' if dim == 'ratio' else 'compatible_unit_only' if unit else 'preserve',
                identity_axes=axes, comparison_axes=axes, comparison_mode='structure_only' if data_class == 'structure' else 'conditional_numeric' if quantitative else 'structured_display',
                aggregation='ratio_recompute' if dim == 'ratio' else 'do_not_sum',
                exclusions=exclusions, comparison_requirements=requirements, semantic_type_note=note)


def element(metric, code, label, primary_type, target, record, storage='core_field', required=False, unit_dimension=None, minimum=None):
    return dict(element_id=metric['metric_id']+'.element.'+code, metric_id=metric['metric_id'], element_code=code,
                labels={'en': code.replace('_',' '), 'zh': label}, description=label+'；必须关联独立证据和该事实的身份。',
                semantic_role=code, requirement_level='required' if required else 'recommended',
                cardinality={'minimum':1 if required else 0,'maximum':1}, null_allowed=not required,
                value_contract={'primary_type':primary_type,'unit_dimension':unit_dimension,'minimum':minimum},
                binding={'record_type':record,'storage':storage,'target':target}, source_refs=metric['source_refs'],
                concept_ids=[], value_concept_root_ids=[])


def fresh_module(dr, rows, catalog):
    package = identity(dr)
    refs = [source_ref(dr,'law'), source_ref(dr,'ig3')]
    m = dict(manifest=dict(format_version='2.1', package_id=package, package_version=VERSION, status='draft',
            standard=dict(framework_id='esrs', framework_version='2023-set1', jurisdiction='EU', module_id=slug(dr), disclosure_requirement=dr, effective_from='2024-01-01'),
            core_schema_id='extractesg.core', core_schema_version='1.2.0', default_language='zh', supported_languages=['zh','en'], files={f:f+'.json' for f in FILES}), **{f:[] for f in FILES})
    m['sources'] = [dict(source_id=refs[0],title='Commission Delegated Regulation (EU) 2023/2772',authority='authoritative_law',version_or_date='2023-07-31',locator='Annex I, '+ ('ESRS 2 ' if dr.startswith('MDR') else 'ESRS '+dr.split('-')[0].split('.')[0]+' ') +dr,url=LAW_URL),
                    dict(source_id=refs[1],title=catalog['source_file'],authority='non_authoritative_implementation_guidance',version_or_date='User-provided immutable IG3 workbook',locator=rows[0]['source_sheet'],url=catalog['source_url'],notes='原始表格快照保留；不替代法律或重要性判断。'),
                    dict(source_id=source_ref(dr,'reproduction'), title='EFRAG ESRS Set 1 official reproduction',authority='official_reproduction',version_or_date='2023-set1',locator=dr,url=REPRODUCTION_URL)]
    labels = LABELS[dr].split('|')
    for row in rows:
        number=int(row['id'].rsplit('_',1)[1]);label=labels[number-1]
        data_class, value_type, dim, unit, note=shape(row)
        metric_id=mid(dr,row['id']);concept_id=package+f'.concept.dp{number:02}'
        numeric=data_class in {'quantitative','mixed'}
        description=row['name'].strip()+'。'+label+'。'+DOMAIN_GUIDANCE[dr]
        metric=dict(metric_id=metric_id,source_datapoint_id=row['id'],disclosure_requirement=dr,paragraph=str(row['paragraph'] or ''),
                    application_requirements=[str(row['related_ar'])] if row['related_ar'] else [],labels={'en':row['name'].strip(),'zh':label},
                    official_data_type=row['data_type'] or 'narrative', data_class=data_class, value_family='number' if numeric else 'statement',
                    obligation_class=obligation(row), allowed_value_origins=['reported'],cardinality={'minimum':0,'maximum':None},
                    description=description, reporting_requirement='仅接收符合本数据点定义的原报告事实，保留原值、单位、实际期间、主体、边界与可定位证据。'+DOMAIN_GUIDANCE[dr],
                    source_refs=refs,subject_concept_groups=[[concept_id]],disclosure_topic=package+'.topic.'+f'dp{number:02}',
                    disclosure_role='presentation' if data_class=='structure' else 'anchor' if number in FOCUS[dr] else 'context' if data_class=='qualitative' else 'detail',
                    identity_axes=axes_for(dr,row),required_semantic_discriminators=axes_for(dr,row),
                    fact_grain='structure_member' if data_class=='structure' else 'source_cell' if numeric else 'assertion',
                    measurement_kind=dim or 'statement',evidence_form=['table_row','narrative_statement','footnote_context'],evidence_roles=['primary','context'])
        metric['provenance']=provenance(catalog,row);metric['measurement_contract']=contract_for(dr,row,metric)
        m['metrics'].append(metric)
        m['concepts'].append(dict(concept_id=concept_id,labels=metric['labels'],description=description,source_refs=refs))
        owner='quantitative_observation' if data_class=='quantitative' else 'qualitative_assertion'
        if data_class in {'qualitative','structure','mixed'}:
            m['elements'].append(element(metric,'statement','原文陈述或独立结构成员','text','statement_raw','qualitative_assertion',required=data_class!='mixed'))
        if numeric:
            nonnegative=None if row['id'] in {'E3-4_05','E1-9_44','BP-2_12','BP-2_14'} or row['data_type']=='narrative/numerical' else 0
            m['elements'].append(element(metric,'value','披露数值',value_type,'value_numeric','quantitative_observation',required=data_class!='mixed',unit_dimension=UNIT_DIM+dim if dim else None,minimum=nonnegative))
            m['elements'].append(element(metric,'unit','报告原始单位','string','unit_raw','quantitative_observation',required=bool(dim)))
        if not numeric and value_type in {'date','year'}:
            m['elements'].append(element(metric,'date_value','披露日期或年份',value_type,'date_value',owner,'attribute'))
        owners=['quantitative_observation','qualitative_assertion'] if data_class=='mixed' else [owner]
        for record in owners:
            suffix='_numeric' if data_class=='mixed' and record=='quantitative_observation' else ''
            for axis,target,label_ in [('reporting_period','reporting_period_raw','实际披露期间'),('reporting_boundary','reporting_boundary','报告边界')]:
                m['elements'].append(element(metric,axis+suffix,label_,'string',target,record,required=axis=='reporting_period'))
            for axis in metric['identity_axes']:
                if axis in {'reporting_period','reporting_boundary'}:continue
                dimension_id=package+'.dimension.'+axis.replace('_','-')
                if not any(d['dimension_id']==dimension_id for d in m['dimensions']):
                    m['dimensions'].append(dict(dimension_id=dimension_id,value_type='string',labels={'en':axis.replace('_',' '),'zh':AXIS_LABELS[axis]},description='由报告证据识别的'+AXIS_LABELS[axis]+'；未知不能由标准默认填充。'))
                m['elements'].append(element(metric,axis+suffix,AXIS_LABELS[axis],'string',dimension_id,record,'dimension',required=axis=='reporting_entity'))
                if dimension_id not in metric.setdefault('optional_dimension_ids',[]) and axis!='reporting_entity': metric['optional_dimension_ids'].append(dimension_id)
                if axis=='reporting_entity' and dimension_id not in metric.setdefault('required_dimension_ids',[]):metric['required_dimension_ids'].append(dimension_id)
        m['validation_rules'].append(dict(rule_id=package+f'.rule.dp{number:02}-evidence',target=metric_id,assertion='evidence_required',severity='error',parameters={'when_value_origin':'any'},message='事实必须有可定位原报告证据；缺失不插入空事实。'))
        for e in [e for e in m['elements'] if e['metric_id']==metric_id and e['requirement_level']=='required']:
            m['validation_rules'].append(dict(rule_id=package+f'.rule.dp{number:02}-'+e['element_code'].replace('_','-'),target=metric_id,assertion='required_element',severity='error',parameters={'element_id':e['element_id']},message='已形成的对应类型事实需要'+e['labels']['zh']+'。'))
    apply_semantic_families(dr,m)
    return m


def apply_semantic_families(dr,module):
    metrics={int(m['source_datapoint_id'].rsplit('_',1)[1]):m for m in module['metrics']}
    for index,group in enumerate(CONFUSABLE.get(dr,[]),1):
        for number in group:
            metric=metrics[number]
            metric['confusable_metric_ids']=[metrics[n]['metric_id'] for n in group if n!=number]
            metric['disclosure_topic']=identity(dr)+f'.topic.family{index:02}'
    concepts={c['concept_id']:c for c in module['concepts']}
    for metric in module['metrics']:
        if metric['source_datapoint_id'] in ALIASES:
            concept=concepts[metric['subject_concept_groups'][0][0]]
            en=[s for s in ALIASES[metric['source_datapoint_id']] if s.isascii()]
            zh=[s for s in ALIASES[metric['source_datapoint_id']] if not s.isascii()]
            concept['aliases']={'en':en,'zh':zh}
    # Only mutually distinct water quantities exclude one another. Risk-area
    # consumption remains a subset, not a contradictory quantity definition.
    if dr=='E3-4':
        group=[1,3,4,5,11,12]
        for number in group:
            cid=metrics[number]['subject_concept_groups'][0][0]
            exclusions=[metrics[n]['subject_concept_groups'][0][0] for n in group if n!=number]
            concepts[cid]['excluded_concept_ids']=exclusions
            metrics[number]['excluded_concept_ids']=exclusions
    for index,(kind,sources,targets,description) in enumerate(RELATIONS.get(dr,[]),1):
        module['relations'].append(dict(relation_id=identity(dr)+f'.relation.family{index:02}',relation_type=kind,
            source_metric_ids=[metrics[n]['metric_id'] for n in sources],target_metric_ids=[metrics[n]['metric_id'] for n in targets],
            policy='context_only' if kind=='explains' else 'independent_disclosure' if kind=='separate_disclosure' else 'reuse_source_facts',
            description=description,source_refs=[source_ref(dr,'law')],identity_axes=COMMON_AXES))


AXIS_LABELS = dict(zip(
    ['reporting_entity','sustainability_topic','policy_ref','action_ref','target_ref','measured_metric_ref','baseline_year','target_year','ghg_scope','accounting_method','value_role','project_ref','value_chain_boundary','removal_activity','credit_status','pricing_scheme','risk_type','scenario','forecast_horizon','financial_item','currency','substance','hazard_class','concern_class','material_flow','aggregation_role','water_measure','geography','water_risk_class','measurement_method','time_horizon','measurement_basis','denominator','denominator_scale','energy_efficiency_class'],
    ['披露主体','可持续主题','政策对象','行动对象','目标对象','被测指标','基准年','目标年','排放范围','核算方法','实际或预期属性','项目对象','价值链边界','移除活动','碳信用状态','定价机制','风险类型','情景','预期时间范围','财务项目','币种','物质','危害类别','关注分类','物质流向','合计或成员身份','水量概念','地区','水风险分类','测量方法','时间范围','测量依据类别','分母','分母量级','能效等级']))


def modules_from_catalog(catalog):
    groups={}
    for row in catalog['records']:
        if row['record_kind']=='独立编号条目':groups.setdefault(row['dr'],[]).append(row)
    return groups


def context_references(dr,metrics):
    if not dr.startswith(('E1','E2','E3')):return []
    refs=[];all_ids=[m['source_datapoint_id'] for m in metrics]
    def add(target,ids,purpose,description,applies=None):
        refs.append(dict(reference_id=identity(dr)+'.context.'+purpose+'-'+slug(target),target_package_id=identity(target),target_package_version=VERSION,target_source_datapoint_ids=ids,applies_to_source_datapoint_ids=applies or all_ids,purpose=purpose,description=description,output_policy='context_only'))
    add('BP-1',['BP-1_01','BP-1_02','BP-1_04'],'report_identity','共享编制基础、合并范围和价值链覆盖；按所选条款需要使用，不推定具体事实主体。')
    quantitative=[m['source_datapoint_id'] for m in metrics if m['data_class'] in {'quantitative','mixed'}]
    if quantitative:
        add('MDR-M',['MDR-M_01','MDR-M_02'],'measurement_method','共用指标定义、方法与重大假设；必须关联被测指标和可持续主题。',quantitative)
        add('BP-2',['BP-2_07','BP-2_08','BP-2_09','BP-2_10','BP-2_14'],'measurement_method','只使用明确适用于所选指标的不确定性、方法、变更和重述说明。',quantitative)
    if dr=='E1.GOV-3':add('GOV-3',['GOV-3_01','GOV-3_03','GOV-3_04','GOV-3_05','GOV-3_06'],'governance_context','ESRS 2 GOV-3的一般激励背景；一般可持续比例不得替代气候比例。')
    if dr.endswith('IRO-1'):add('IRO-1',['IRO-1_01','IRO-1_02','IRO-1_07'],'materiality_context','共享影响与财务风险评估流程；主题特定要求仍分别举证。')
    if dr=='E1.SBM-3':add('SBM-3',['SBM-3_02','SBM-3_03','SBM-3_10'],'business_context','共享业务回应与韧性背景，不替代气候风险的具体分析结果。')
    if dr in {'E1-9','E2-6','E3-5'}:add('SBM-3',['SBM-3_08','SBM-3_09'],'financial_context','区别当前与预期财务影响；风险与期间须匹配。')
    if dr in {'E1-5','E1-6','E3-4'}:add('SBM-1',['SBM-1_06','SBM-1_24'],'business_context','收入与行业仅作为背景。收入总额不能自动成为净收入强度分母。')
    return refs


def generate(destination):
    catalog=load_json(ROOT/CATALOG_PATH); groups=modules_from_catalog(catalog)
    compiled={}; compiler=StandardPackageCompiler(); major=[i for ids in PLATFORM_SELECTION.values() for i in ids]
    references=[r for r in catalog['records'] if r['record_kind']!='独立编号条目']
    for dr,rows in groups.items():
        if dr not in FOCUS:continue
        if dr in {'E1-5','E1-6','E2-4'}:
            m={f:load_json(ROOT/f'packages/esrs/2023-set1/{slug(dr)}/2.0.0/{f}.json') for f in ['manifest',*FILES]}
            m=deepcopy(m)
            raw={r['id']:r for r in rows}
            for metric in m['metrics']:
                row=raw[metric['source_datapoint_id']]
                if not metric.get('identity_axes'):
                    metric['identity_axes']=list(dict.fromkeys([*COMMON_AXES, *[
                        e['element_code'] for e in m['elements']
                        if e['metric_id']==metric['metric_id'] and e['binding']['storage']=='dimension'
                    ]]))
                metric['provenance']=provenance(catalog,row);metric['measurement_contract']=contract_for(dr,row,metric)
        else:m=fresh_module(dr,rows,catalog)
        manifest=m['manifest'];manifest.update(format_version='2.1',package_version=VERSION,core_schema_version='1.2.0',catalog_id=catalog['catalog_id'],catalog_source_sha256=catalog['source_sha256'],core_source_datapoint_ids=[f'{dr}_{i:02}' for i in FOCUS[dr]],core_selection_reason=FOCUS_REASON[dr])
        manifest['shared_requirements']=[shared_reference(r) for r in references if r['dr']==dr]
        manifest['context_requirements']=context_references(dr,m['metrics'])
        # Blank-DR absence references stay at topic-level registry; don't invent a DR.
        source_dir=destination/f'packages/esrs/2023-set1/{slug(dr)}/{VERSION}'
        for f,value in m.items():write_json(source_dir/(f+'.json'),value)
        compiled[dr]=compiler.compile(core_path=ROOT/'core/1.2.0/core.json',module_dir=source_dir)
    registry=dict(format_version='1.0',catalog_id=catalog['catalog_id'],source_sha256=catalog['source_sha256'],modules=[],shared_requirement_references=[shared_reference(r) for r in references],topic_shared_requirements=[shared_reference(r) for r in references if r['dr'] is None],other_frameworks=[dict(framework_id=f,state='awaiting_source_catalog',executable=False,next_step='登记版本化原始标准目录，再按披露小号构造独立包。') for f in ['gri','sasb','ifrs-s1','ifrs-s2']],principles=[dict(framework_id='esrs',module_id='esrs-1',state='principles_reserved',executable=False,reason='原表没有ESRS 1独立数据点页；通用原则不伪造数据点。')])
    for dr,rows in groups.items():
        package=compiled.get(dr)
        local_major=[i for i in major if any(r['id']==i for r in rows)]
        core=package.manifest.core_source_datapoint_ids if package else local_major
        entry=dict(package_id=identity(dr),disclosure_requirement=dr,source_sheet=rows[0]['source_sheet'],state='compiled_draft' if package else 'planned',executable=bool(package),
                   source_datapoint_ids=[r['id'] for r in rows],core_source_datapoint_ids=core,
                   core_analysis_state='authored_draft' if package else 'pending_full_module_analysis',
                   major_source_datapoint_ids=local_major,source_directory=f'packages/esrs/2023-set1/{slug(dr)}/{VERSION}' if package else None,
                   package_version=VERSION if package else None,source_digest=package.compilation.source_digest if package else None,
                   runtime_path=f'dist/{identity(dr)}/{VERSION}/package.json' if package else None,
                   next_steps=['真实报告金标准验收','业务批准并发布新版本'] if package else ['逐条语义分析与核心条款选择','类型/身份/证据/比较合同','离线与真实报告验收'])
        registry['modules'].append(entry)
        if not package:
            write_json(destination/f'placeholders/esrs/2023-set1/{slug(dr)}/placeholder.json',entry)
    write_json(destination/REGISTRY_PATH,registry)
    write_json(destination/'profiles/esrs/2023-set1/platform-major.json',dict(profile_id='esrs.2023-set1.platform-major',profile_version='1.0.0',label='数据平台主要条款（用户注释二）',selection_kind='platform_major',catalog_id=catalog['catalog_id'],source_datapoint_ids=major,selection_reason='严格保留用户注释二的49个原编号；不将包内全部核心条款自动纳入。'))
    write_json(destination/'profiles/esrs/2023-set1/environment-all.json',dict(profile_id='esrs.2023-set1.environment-all',profile_version='1.0.0',label='E1–E3完整条款',selection_kind='full',catalog_id=catalog['catalog_id'],source_datapoint_ids=[r['id'] for r in catalog['records'] if r['source_sheet'] in {'ESRS E1','ESRS E2','ESRS E3'} and r['record_kind']=='独立编号条目'],selection_reason='E1–E3全部322个独立编号；MDR按引用复用。'))
    for dr,package in compiled.items():
        write_json(destination/f'profiles/esrs/2023-set1/package-core/{slug(dr)}.json',dict(profile_id=identity(dr)+'.core',profile_version='1.0.0',label=dr+'包内核心条款',selection_kind='package_core',catalog_id=catalog['catalog_id'],source_datapoint_ids=package.manifest.core_source_datapoint_ids,selection_reason=FOCUS_REASON[dr]))
    for framework in registry['other_frameworks']:
        write_json(destination/f'placeholders/{framework["framework_id"]}/placeholder.json',framework)
    write_json(destination/'placeholders/esrs/2023-set1/esrs-1/placeholder.json',registry['principles'][0])
    return compiled,registry


def install(staging, output):
    # Check every immutable source before writing any of them.
    for path in staging.rglob('*.json'):
        target=output/path.relative_to(staging)
        if ('packages' in path.relative_to(staging).parts) and target.exists() and target.read_bytes()!=path.read_bytes():
            parts=path.relative_to(staging).parts
            runtime=output/f'dist/esrs.2023-set1.{parts[3]}/{parts[4]}/package.json'
            archived=output/f'dist/.retired/esrs.2023-set1.{parts[3]}/{parts[4]}/package.json'
            if runtime.exists() or archived.exists():
                raise ValueError('Published source versions are immutable; author a new version: '+str(target))
    for path in staging.rglob('*.json'):
        target=output/path.relative_to(staging);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--publish',action='store_true');args=parser.parse_args()
    from esg_standard_packages.scope import validate_registry
    with tempfile.TemporaryDirectory(prefix='esrs-packages-') as tmp:
        staging=Path(tmp); packages,registry=generate(staging)
        validate_registry(registry,load_json(ROOT/CATALOG_PATH),packages)
        install(staging,ROOT)
    if args.publish:
        compiler=StandardPackageCompiler()
        for dr in packages:
            compiler.compile_to_path(core_path=ROOT/'core/1.2.0/core.json',module_dir=ROOT/f'packages/esrs/2023-set1/{slug(dr)}/{VERSION}',output_path=ROOT/f'dist/{identity(dr)}/{VERSION}/package.json')
    print(dict(compiled_modules=len(packages),metrics=sum(len(p.metrics) for p in packages.values()),planned_modules=sum(not r['executable'] for r in registry['modules']),published=args.publish))


if __name__=='__main__':main()
