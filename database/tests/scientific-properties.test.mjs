import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../scripts/db.mjs';
import {importBundle,reviewRecord} from '../scripts/import-bundle.mjs';
import {seedMetricDictionary,saveMeasurementContext,processOutbox,readDataset} from '../scripts/publication.mjs';
import {availableReportedProperties,resolveReportedProperties} from '../scripts/scientific-properties.mjs';
import {dispatch} from '../../PlatESG/backend/service.mjs';
import {applyPlatformMigrations} from '../../PlatESG/backend/database.mjs';
import {publicReportedProperty} from '../../PlatESG/backend/public_contracts.mjs';

const dictionary=JSON.parse(await readFile(new URL('../../PlatESG/catalog/metric-dictionary.v1.json',import.meta.url),'utf8'));
const metric=dictionary.metrics.find(item=>item.source_datapoint_id==='E1-6_07');
const locator={ir_object_id:'synthetic-note',pdf_page_index:1,document_id:'synthetic-doc',ir_run_id:'synthetic-ir',
  bbox_x0:10,bbox_y0:20,bbox_x1:30,bbox_y1:40};
const sourceIdentity={document_id:'synthetic-doc',ir_run_id:'synthetic-ir',ir_revision:1};
const boundEvidence=(text,locators=[locator])=>({evidence_contract_version:'bound-identity-evidence-v1',
  evidence_origin:'source_text',source_identity:sourceIdentity,span_id:'synthetic-note-span',text,locators});
const scientific={boundary_note:'仅自身运营，不含合营企业',method_note:'报告声明按照实测数据汇总',
  energy_source:['天然气','可再生电力'],site_count:4,includes_subsidiaries:false,
  additional_breakdown:{unpublished:'structured raw object remains in the candidate'},
  source_object_id:'synthetic-engine-object',engine_version:'synthetic-v1',artifact_path:'/Users/synthetic/private.json'};
const source={source_payload:{source_fact:{...sourceIdentity,identity_raw:scientific,
  identity_evidence:{boundary_note:[boundEvidence('边界：仅自身运营，不含合营企业')],
    method_note:[boundEvidence('这段并不支持字段值')]}}},
  sources:[{page_number:1,source_quote:'虚构的软件验收来源：范围1为20 tCO2e。',bbox:{x0:1,y0:2,x1:3,y1:4}}]};
const selection=(source_field,label_zh)=>({source_field,ordinal:1,label_zh});

test('only scientific raw fields are selectable, field evidence is grounded and source-only evidence is explicit',()=>{
  const available=availableReportedProperties(source),byKey=key=>available.find(item=>item.field_key===key);
  assert.equal(byKey('boundary_note').evidence_scope,'field');
  assert.deepEqual(byKey('boundary_note').evidence,[{page_number:2,source_quote:'边界：仅自身运营，不含合营企业',bbox:{x0:10,y0:20,x1:30,y1:40}}]);
  assert.equal(byKey('method_note').evidence_scope,'source');
  assert.equal(byKey('method_note').evidence[0].page_number,1);
  assert.equal(byKey('additional_breakdown').selectable,false);
  assert.equal(byKey('additional_breakdown').value_raw,null);
  assert.equal(byKey('site_count').value_raw,'4');
  assert.equal(byKey('includes_subsidiaries').value_raw,'false');
  assert.deepEqual(byKey('energy_source').value_raw,['天然气','可再生电力']);
  assert.ok(!available.some(item=>['source_object_id','engine_version','artifact_path'].includes(item.field_key)));
  const properties=resolveReportedProperties(source,[selection('identity_raw.method_note','方法说明'),selection('identity_raw.boundary_note','统计边界')]);
  assert.equal(properties[0].value_raw,scientific.boundary_note);
  assert.equal(properties[0].unit_raw,null);
  assert.equal(properties[1].evidence_scope,'source');
});

test('forged values, unsupported objects, duplicate selectors, bad ordinals and absent physical evidence fail closed',()=>{
  const select=selection('identity_raw.boundary_note','统计边界');
  for(const forged of [{...select,value_raw:'伪造值'},{...select,evidence:[]},{...select,unit_raw:'tCO2e'}])
    assert.throws(()=>resolveReportedProperties(source,[forged]),/server-resolved/);
  assert.throws(()=>resolveReportedProperties(source,[selection('identity_raw.additional_breakdown','结构描述')]),/unsupported_property_value/);
  assert.throws(()=>resolveReportedProperties(source,[select,select]),/Duplicate/);
  assert.throws(()=>resolveReportedProperties(source,[{...select,ordinal:1.1}]),/ordinal/);
  assert.throws(()=>resolveReportedProperties(source,Array(65).fill(select)),/at most 64/);
  assert.throws(()=>resolveReportedProperties({...source,sources:[]},[select]),/physical_source_missing/);
  assert.throws(()=>resolveReportedProperties(source,[selection('identity_raw.artifact_path','科学说明')]),/unavailable or technical/);
  assert.throws(()=>resolveReportedProperties(source,[{...select,label_zh:'/Users/synthetic/private.json'}]),/readable label/);
  assert.equal(availableReportedProperties({source_payload:{source_fact:{identity_raw:{description:'x'.repeat(4097)}}},sources:source.sources})[0].reason_code,'property_value_out_of_bounds');
});

test('legacy original ordinal and plain raw properties remain separate from canonical dimensions',()=>{
  const legacy={source_payload:{origin:'legacy_projection',reporting_boundary:'集团，不含新收购企业'},sources:source.sources,
    dimensions:[{dimension_key:'business_segment',ordinal:1,raw_value:'炼化'},{dimension_key:'business_segment',ordinal:2,raw_value:'销售'},
      {dimension_key:'additional_breakdown',ordinal:1,raw_value:'{"不能公开":1}'},{dimension_key:'source_id',ordinal:1,raw_value:'private'}]};
  const picked=resolveReportedProperties(legacy,[{source_field:'legacy_dimensions.business_segment',ordinal:2,label_zh:'业务分部'}]);
  assert.equal(picked[0].value_raw,'销售');
  assert.equal(picked[0].evidence_scope,'source');
  assert.equal(availableReportedProperties(legacy).find(item=>item.field_key==='additional_breakdown').selectable,false);
  assert.ok(availableReportedProperties(legacy).some(item=>item.source_field==='legacy_payload.reporting_boundary'));
});

test('actual frozen field evidence requires the full bound source text and matching IR identity',()=>{
  const withEvidence=(value,items)=>({source_payload:{source_fact:{...sourceIdentity,
    identity_raw:{reporting_year:value},identity_evidence:{reporting_year:items}}},sources:source.sources});
  const property=input=>availableReportedProperties(input)[0];
  const nested={ir_object_id:'synthetic-header-cell',pdf_page_index:2,bbox:{x0:1,y0:2,x1:30,y1:40}};
  const header={...boundEvidence('报告期间 / 2024年',[nested]),evidence_origin:'column_header',excerpt:'2024'};
  assert.equal(property(withEvidence('2024',[header])).evidence_scope,'field');
  assert.equal(property(withEvidence('2024',[header])).evidence[0].source_quote,'报告期间 / 2024年');
  assert.deepEqual(property(withEvidence('2024',[header])).evidence[0].bbox,nested.bbox);
  for(const item of [
    {span_id:'old',excerpt:'2024',locators:[nested]},
    {text:'2024',locators:[nested]},
    {...header,text:undefined},
    {...header,source_identity:{...sourceIdentity,ir_revision:2}},
    {...header,source_identity:{...sourceIdentity,document_id:'another-document'}},
    {...header,locators:[{...nested,ir_revision:2}]},
    {...header,locators:[{...nested,ir_run_id:'another-ir'}]},
    {...header,text:'累计数12024项'},
    {...header,text:'报告数2024.5'},
    {...header,text:'报告调整值-2024'},
  ]) assert.equal(property(withEvidence('2024',[item])).evidence_scope,'source');
});

test('every selected list value needs evidence from the same frozen source revision',()=>{
  const input={source_payload:{source_fact:{...sourceIdentity,identity_raw:{energy_source:['天然气','可再生电力']},
    identity_evidence:{energy_source:[boundEvidence('能源来源：天然气'),
      {...boundEvidence('能源来源：可再生电力'),source_identity:{...sourceIdentity,ir_revision:2}}]}}},sources:source.sources};
  assert.equal(availableReportedProperties(input)[0].evidence_scope,'source');
  input.source_payload.source_fact.identity_evidence.energy_source[1].source_identity=sourceIdentity;
  assert.equal(availableReportedProperties(input)[0].evidence_scope,'field');
});

test('bound English identity keeps real word boundaries before a following period label',()=>{
  const input={source_payload:{source_fact:{...sourceIdentity,identity_raw:{reporting_entity:'Example Group'},
    identity_evidence:{reporting_entity:[boundEvidence('Example Group 2024')]}}},sources:source.sources};
  assert.equal(availableReportedProperties(input)[0].evidence_scope,'field');
  input.source_payload.source_fact.identity_evidence.reporting_entity[0].text='AnotherExample Grouping';
  assert.equal(availableReportedProperties(input)[0].evidence_scope,'source');
});

function inputBundle() {
  const binding=metric.standard_binding;
  return {root:'/synthetic/scientific-properties',manifest:{job_id:'scientific-properties-fixture'},bundleDigest:'8'.repeat(64),
    request:{package_id:binding.package_id,package_version:binding.package_version},
    standard:{manifest:{standard:{framework_id:'esrs',framework_version:'2023-set1'}},compilation:{source_digest:binding.digest},
      metrics:[{metric_id:binding.metric_id,source_datapoint_id:'E1-6_07',labels:{zh:metric.label_zh}}],elements:[]},
    sourceFacts:{facts:[{...source.source_payload.source_fact,source_fact_id:'synthetic-source',source_key:'synthetic-key',ir_revision:1,
      source_object_id:'synthetic-cell',source_row_id:'synthetic-row',source_row_label:'范围1',kind:'quantitative',value_raw:'20',unit_raw:'tCO2e',
      physical_sources:[{ir_object_id:'synthetic-cell',pdf_page_index:0}],interpretation_version:'synthetic-v1'}]},
    sourceMappings:{core_bindings:[{core_record_id:'synthetic-observation',source_fact_id:'synthetic-source'}]},
    records:{reporting_tasks:[],quantitative_observations:[{observation_id:'synthetic-observation',metric_id:binding.metric_id,
      value_raw:'20',value_numeric:'20',comparator:'exact',unit_raw:'tCO2e'}],qualitative_assertions:[],attribute_values:[],dimension_values:[],
      evidence_references:[{evidence_id:'synthetic-evidence',source_record_id:'synthetic-observation',document_id:'synthetic-doc',ir_run_id:'synthetic-ir',
        ir_revision:1,ir_object_id:'synthetic-cell',pdf_page_index:0,evidence_excerpt:source.sources[0].source_quote} ]}};
}
function context(item,property_selections) {
  const dimensions={entity_kind:'listed_company',...metric.fixed_dimensions};
  for(const axis of [...metric.required_dimensions,...metric.comparison_dimensions])
    if(axis!=='measurement_method')dimensions[axis] ??= metric.dimension_constraints?.[axis]?.allowed_values?.[0] || 'synthetic_verified_basis';
  return {mapping_id:item.mapping_id,entity_ref:'synthetic-entity',period_start:'2024-01-01',period_end:'2024-12-31',boundary_code:'own_operations',
    value_role:'actual',measurement_method:'synthetic_verified_method',dimensions,reported_unit:'tCO2e',operator_ref:'synthetic-reviewer',
    reason:'Explicit approval of labeled synthetic raw scientific descriptions',property_selections};
}
test('actual import, reviewed context transaction, public whitelist and published versions freeze supplemental properties without changing identity',async()=>{
  const db=await PGlite.create();try {
    await applyMigrations(db);await applyPlatformMigrations(db);await seedMetricDictionary(db,dictionary);
    await dispatch(db,'register_entity',{entity_ref:'synthetic-entity',name_zh:'虚构科学属性验收主体',entity_kind:'listed_company',industry_code:'test',country_code:'CN',is_demo:true});
    await importBundle(db,inputBundle(),{upstream_system:'synthetic_software_test',organization_ref:'synthetic-entity',report_ref:'synthetic-scientific-report',
      report_version_ref:'synthetic-v1',source_artifact_ref:'synthetic://no-physical-report',report_title:'科学属性合成软件验收报告'});
    const candidate=(await dispatch(db,'candidates',{limit:100})).items[0];
    assert.equal(candidate.available_reported_properties.find(property=>property.field_key==='boundary_note').evidence_scope,'field');
    assert.equal(candidate.source_payload,undefined);
    assert.equal(candidate.raw_property_dimensions,undefined);
    const selections=[selection('identity_raw.boundary_note','统计边界'),selection('identity_raw.method_note','方法说明')];
    await assert.rejects(saveMeasurementContext(db,context(candidate,[{...selections[0],value_raw:'伪造'}])),/server-resolved/);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.measurement_contexts')).rows[0].n,0);
    const saved=await saveMeasurementContext(db,context(candidate,selections));
    const retry=await saveMeasurementContext(db,context(candidate,[...selections].reverse()));
    assert.equal(retry.context_id,saved.context_id);
    const frozenContext=(await db.query('SELECT * FROM esg.measurement_contexts WHERE context_id=$1',[saved.context_id])).rows[0];
    assert.equal(frozenContext.reported_properties[0].value_raw,scientific.boundary_note);
    assert.ok(!('boundary_note' in frozenContext.dimensions));
    await assert.rejects(db.query('UPDATE esg.measurement_contexts SET reported_properties=$1 WHERE context_id=$2',[[],saved.context_id]),/immutable/);
    await reviewRecord(db,{disclosure_id:candidate.disclosure_id,decision:'approved',operator_ref:'synthetic-reviewer',reason:'Synthetic reported value is supported'});
    await reviewRecord(db,{mapping_id:candidate.mapping_id,decision:'approved',operator_ref:'synthetic-reviewer',reason:'Synthetic metric mapping is exact',mapping_relation:'exact',satisfaction_status:'supports'});
    await processOutbox(db);
    const old=await readDataset(db),observation=old.observations[0];
    assert.equal(observation.reported_properties.length,2);
    assert.equal(observation.reported_properties[0].report_title,'科学属性合成软件验收报告');
    const exposed=(await dispatch(db,'detail',{observation_id:observation.observation_id,dataset_version:old.dataset_version})).reported_properties;
    assert.equal(exposed[0].source_field,undefined);
    assert.equal(exposed[0].ordinal,undefined);
    assert.equal(exposed[0].field_key,'boundary_note');
    assert.equal(exposed[0].value_raw,scientific.boundary_note);
    assert.equal(exposed[1].evidence_scope,'source');
    const list=(await dispatch(db,'observations',{limit:100,dataset_version:old.dataset_version})).items[0];
    assert.deepEqual(list.reported_properties,exposed);
    // Mutation of a candidate source cannot rewrite an already released property.
    await db.query('UPDATE esg.esg_disclosures SET source_payload=$1 WHERE disclosure_id=$2',[{source_fact:{...source.source_payload.source_fact,
      identity_raw:{...scientific,boundary_note:'后续候选改动'}}},candidate.disclosure_id]);
    assert.deepEqual(await readDataset(db,old.dataset_version),old);
    assert.deepEqual((await dispatch(db,'detail',{observation_id:observation.observation_id,dataset_version:old.dataset_version})).reported_properties,exposed);
    const revision=await saveMeasurementContext(db,context(candidate,selections));
    assert.equal(revision.revision,2);
    await processOutbox(db);const latest=await readDataset(db);
    assert.notEqual(latest.dataset_version,old.dataset_version);
    assert.equal(latest.observations[0].identity_key,observation.identity_key);
    assert.equal(latest.observations[0].numeric_value,'20');
    assert.equal(latest.observations[0].reported_properties[0].value_raw,'后续候选改动');
    assert.equal(latest.observations[0].reported_properties[0].evidence_scope,'source');
    assert.deepEqual(await readDataset(db,old.dataset_version),old);
  } finally {await db.close();}
});

test('public property projection hides nested engineering fields and unsupported values',()=>{
  const property={...resolveReportedProperties(source,[selection('identity_raw.boundary_note','统计边界')])[0],
    internal_id:'private-uuid',artifact_path:'/Users/private',report_title:'已发布报告',evidence:[{page_number:2,source_quote:'公开原文',
      internal_id:'private',bbox:{x0:1,y0:2,x1:3,y1:4,internal_path:'/Users/private'}}]};
  const exposed=publicReportedProperty(property);
  assert.equal(exposed.internal_id,undefined);
  assert.equal(exposed.artifact_path,undefined);
  assert.equal(exposed.source_field,undefined);
  assert.equal(exposed.evidence[0].internal_id,undefined);
  assert.equal(exposed.evidence[0].bbox.internal_path,undefined);
  assert.equal(publicReportedProperty({...property,field_key:'engine_version'}),null);
  assert.equal(publicReportedProperty({...property,value_raw:{secret:'not a public raw description'}}),null);
});
