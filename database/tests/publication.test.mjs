import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../scripts/db.mjs';
import {importBundle,reviewRecord} from '../scripts/import-bundle.mjs';
import {seedMetricDictionary,saveMeasurementContext,processOutbox,readDataset,NORMALIZATION_RULE_VERSION,scientificDimensionKeys} from '../scripts/publication.mjs';

const dictionary=JSON.parse(await readFile(new URL('../../PlatESG/catalog/metric-dictionary.v1.json',import.meta.url),'utf8'));
const definition=id=>dictionary.metrics.find(m=>m.source_datapoint_id===id);
async function database() {const db=await PGlite.create(); await applyMigrations(db); await seedMetricDictionary(db,dictionary); return db;}
let sequence=0;
async function candidate(db,id='E1-6_07',{value='0',unit=null,comparator='exact',lower=null,upper=null,metric_override=null}={}) {
  const metric=metric_override || definition(id),binding=metric.standard_binding,n=++sequence;
  const raw=value;
  const bundle={root:`/synthetic/${n}`,manifest:{job_id:`synthetic-${n}`},bundleDigest:String(n).padStart(64,'0'),
    request:{package_id:binding.package_id,package_version:binding.package_version},
    standard:{manifest:{standard:{framework_id:'esrs',framework_version:'2023-set1'}},compilation:{source_digest:binding.digest},
      metrics:[{metric_id:binding.metric_id,source_datapoint_id:id,labels:{zh:metric.label_zh}}],elements:[]},
    records:{reporting_tasks:[],quantitative_observations:[],qualitative_assertions:[],attribute_values:[],dimension_values:[],
      evidence_references:[{evidence_id:`e-${n}`,source_record_id:`r-${n}`,document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1,ir_object_id:`cell-${n}`,pdf_page_index:0,evidence_excerpt:`Synthetic fixture only: ${raw}`} ]}};
  if(metric.value_type==='statement') bundle.records.qualitative_assertions.push({assertion_id:`r-${n}`,metric_id:binding.metric_id,statement_raw:value});
  else bundle.records.quantitative_observations.push({observation_id:`r-${n}`,metric_id:binding.metric_id,value_raw:raw,value_numeric:value,
    unit_raw:unit || metric.canonical_unit,comparator,value_lower:lower,value_upper:upper});
  const imported=await importBundle(db,bundle,{upstream_system:'synthetic_test',organization_ref:'synthetic_group',report_ref:`report-${n}`,
    report_version_ref:`report-${n}-v1`,source_artifact_ref:`synthetic-${n}.pdf`});
  return (await db.query(`SELECT i.disclosure_id,m.disclosure_standard_mapping_id AS mapping_id FROM esg.import_items i
    JOIN esg.disclosure_standard_mappings m USING(disclosure_id) WHERE i.import_batch_id=$1`,[imported.import_batch_id])).rows[0];
}
function context(item,id='E1-6_07',overrides={}) {
  const metric=definition(id),dimensions={};
  for(const axis of [...metric.required_dimensions,...metric.comparison_dimensions])
    if(axis!=='measurement_method') dimensions[axis]=metric.fixed_dimensions[axis] || metric.dimension_constraints[axis]?.allowed_values?.[0]
      || metric.comparison_allowed_values?.[axis]?.[0] || 'verified_fixture_basis';
  if(scientificDimensionKeys(metric).has('entity_kind'))dimensions.entity_kind='group';
  return {mapping_id:item.mapping_id,entity_ref:'synthetic_group',period_start:'2024-01-01',period_end:'2024-12-31',boundary_code:'group_consolidated',
    value_role:metric.value_role,measurement_method:metric.fixed_dimensions.measurement_method || 'verified_fixture_method',dimensions,
    reported_unit:metric.canonical_unit,operator_ref:'test_reviewer',reason:'Explicit review of labeled synthetic fixture',...overrides};
}
async function approve(db,item,{relation='exact',satisfaction='supports'}={}) {
  await reviewRecord(db,{disclosure_id:item.disclosure_id,decision:'approved',operator_ref:'fact_reviewer',reason:'Synthetic source verified'});
  await reviewRecord(db,{mapping_id:item.mapping_id,decision:'approved',operator_ref:'mapping_reviewer',reason:'Synthetic semantics verified',mapping_relation:relation,satisfaction_status:satisfaction});
}
async function state(db,item) {return (await db.query('SELECT * FROM esg.publication_states WHERE mapping_id=$1',[item.mapping_id])).rows[0];}

test('new contexts only accept definition-declared dimensions while historical contexts and snapshots remain readable',async()=>{
  const db=await database();try {
    const item=await candidate(db),reviewed=context(item);
    const invalid={...reviewed,dimensions:{...reviewed.dimensions,boundary_note:'额外说明不属于科学身份'}};
    const before=(await db.query('SELECT count(*)::int AS n FROM esg.outbox_events')).rows[0].n;
    await assert.rejects(saveMeasurementContext(db,invalid),/未声明.*boundary_note.*补充信息/);
    await assert.rejects(saveMeasurementContext(db,{...reviewed,dimensions:{...reviewed.dimensions,period_start:'2024-01-01'}}),/独立字段/);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.measurement_contexts')).rows[0].n,0);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.outbox_events')).rows[0].n,before);
    // Explicitly simulate an immutable context persisted before the new gate.
    const historical=(await db.query(`INSERT INTO esg.measurement_contexts(context_id,mapping_id,revision,entity_ref,period_start,period_end,
      boundary_code,value_role,measurement_method,dimensions,reported_unit,operator_ref,reason)
      VALUES(gen_random_uuid(),$1,1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING context_id`,
      [invalid.mapping_id,invalid.entity_ref,invalid.period_start,invalid.period_end,invalid.boundary_code,invalid.value_role,
        invalid.measurement_method,invalid.dimensions,invalid.reported_unit,invalid.operator_ref,'Synthetic pre-gate historical fixture'])).rows[0];
    await approve(db,item);await processOutbox(db);
    const frozen=await readDataset(db),snapshot=structuredClone(frozen.observations);
    assert.equal(snapshot[0].dimensions.boundary_note,invalid.dimensions.boundary_note);
    await assert.rejects(saveMeasurementContext(db,invalid),/未声明/);
    assert.deepEqual((await readDataset(db,frozen.dataset_version)).observations,snapshot);
    assert.equal((await db.query('SELECT dimensions FROM esg.measurement_contexts WHERE context_id=$1',[historical.context_id])).rows[0].dimensions.boundary_note,invalid.dimensions.boundary_note);
    const qualitative=await candidate(db,'E2-4_09',{value:'合成方法说明'});
    await assert.rejects(saveMeasurementContext(db,context(qualitative,'E2-4_09',{dimensions:{entity_kind:'group'}})),/未声明.*entity_kind/);
    await saveMeasurementContext(db,context(qualitative,'E2-4_09'));
  }finally{await db.close();}
});

test('scientific dimension declarations include explicit constraints and exclude typed context columns',()=>{
  assert.deepEqual([...scientificDimensionKeys({required_dimensions:['scope'],comparison_dimensions:['entity_kind','measurement_method'],
    required_context_fields:['period_start','method_basis'],fixed_dimensions:{flow:'input'},dimension_constraints:{pollutant:{}},
    typed_context_constraints:{measurement_method:{},extra_axis:{}},comparison_allowed_values:{risk_class:['high']}})].sort(),
    ['entity_kind','extra_axis','flow','method_basis','pollutant','risk_class','scope']);
});

test('every original ID remains immutable; four approval/publication axes are independent',async()=>{
  const db=await database();try {
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.metric_definition_versions')).rows[0].n,dictionary.metrics.length);
    const initial=await readDataset(db);
    assert.equal(initial.dictionary_version,dictionary.dictionary_version);
    assert.equal(initial.observations.length,0);
    const item=await candidate(db);
    await saveMeasurementContext(db,context(item));
    await reviewRecord(db,{disclosure_id:item.disclosure_id,decision:'approved',operator_ref:'reviewer',reason:'checked source'});
    await processOutbox(db);
    assert.equal((await readDataset(db)).observations.length,0);
    assert.equal((await state(db,item)).fact_review_status,'approved');
    assert.equal((await state(db,item)).mapping_review_status,'review_required');
    await approve(db,item,{relation:'partial',satisfaction:'unknown'});
    await processOutbox(db);
    assert.ok((await state(db,item)).reason_codes.includes('mapping_not_equivalent'));
    await approve(db,item);await processOutbox(db);
    const published=(await readDataset(db)).observations[0];
    assert.equal(published.numeric_value,'0');
    assert.equal(published.source_datapoint_id,'E1-6_07');
    assert.equal(published.comparison_eligibility,'comparable');
    assert.equal((await state(db,item)).publication_status,'published');
    const drift=structuredClone(dictionary);drift.metrics[0].label_zh='Silent semantic drift';
    await assert.rejects(seedMetricDictionary(db,drift),/Immutable (metric definition|dictionary snapshot)/);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.metric_definition_versions')).rows[0].n,dictionary.metrics.length);
  } finally {await db.close();}
});

test('dictionary initialization leaves approved fact events pending under a manual publication hold',async()=>{
  const db=await database();try {
    const initial=await readDataset(db);
    const item=await candidate(db);
    await saveMeasurementContext(db,context(item));
    await approve(db,item);
    const before=(await db.query("SELECT event_id,status FROM esg.outbox_events ORDER BY event_id")).rows;
    await seedMetricDictionary(db,dictionary);
    assert.deepEqual((await db.query("SELECT event_id,status FROM esg.outbox_events ORDER BY event_id")).rows,before);
    assert.equal((await readDataset(db)).dataset_version,initial.dataset_version);
    assert.equal((await readDataset(db)).observations.length,0);
    await processOutbox(db);
    assert.equal((await readDataset(db)).observations.length,1);
  }finally{await db.close();}
});

test('dictionary releases reuse immutable concepts and freeze membership independently',async()=>{
  const db=await database();try {
    const baseline=await readDataset(db);
    const next={...structuredClone(dictionary),dictionary_version:'test-2.0.0'};
    await seedMetricDictionary(db,next);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.metric_definition_versions')).rows[0].n,dictionary.metrics.length);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.dictionary_membership')).rows[0].n,dictionary.metrics.length*2);
    await processOutbox(db);
    assert.equal((await readDataset(db)).dictionary_version,'test-2.0.0');
    assert.equal((await readDataset(db,baseline.dataset_version)).dictionary_version,dictionary.dictionary_version);
    await seedMetricDictionary(db,dictionary); // restart with an older release cannot promote it
    await processOutbox(db);
    assert.equal((await readDataset(db)).dictionary_version,'test-2.0.0');
    await assert.rejects(seedMetricDictionary(db,{...next,metrics:next.metrics.slice(1)}),/Immutable dictionary/);
    await assert.rejects(db.query('DELETE FROM esg.dictionary_membership WHERE dictionary_version=$1',[dictionary.dictionary_version]),/immutable dictionary membership/);
    await assert.rejects(db.query('UPDATE esg.dictionary_versions SET snapshot_digest=$1 WHERE dictionary_version=$2',['a'.repeat(64),dictionary.dictionary_version]),/immutable dictionary snapshot/);
  }finally{await db.close();}
});

test('exact decimal conversion, ratios, and rejected source-unit contradiction',async()=>{
  const db=await database();try {
    const item=await candidate(db,'E1-5_01',{value:'900719925474099312345.678901',unit:'kWh'});
    await saveMeasurementContext(db,context(item,'E1-5_01',{reported_unit:'kWh'}));await approve(db,item);await processOutbox(db);
    assert.equal((await readDataset(db)).observations[0].numeric_value,'900719925474099312.345678901');
    const ratio=await candidate(db,'E1-5_09',{value:'17.25',unit:'%'});
    await saveMeasurementContext(db,context(ratio,'E1-5_09',{reported_unit:'%'}));await approve(db,ratio);await processOutbox(db);
    assert.equal((await readDataset(db)).observations.find(o=>o.source_datapoint_id==='E1-5_09').numeric_value,'0.1725');
    await saveMeasurementContext(db,context(item,'E1-5_01',{reported_unit:'MWh'}));await processOutbox(db);
    assert.ok((await state(db,item)).reason_codes.includes('reported_unit_disagrees_with_source'));
    const bad=await candidate(db,'E1-5_09',{value:'110',unit:'%'});
    await saveMeasurementContext(db,context(bad,'E1-5_09',{reported_unit:'%'}));await approve(db,bad);await processOutbox(db);
    assert.ok((await state(db,bad)).reason_codes.includes('ratio_out_of_range'));
  }finally{await db.close();}
});

test('scope, energy and water semantics require explicit reviewed fixed values; compare metadata can be absent',async()=>{
  const db=await database();try {
    const scope=await candidate(db,'E1-6_09',{value:'10'});
    await saveMeasurementContext(db,context(scope,'E1-6_09',{measurement_method:'market_based'}));await approve(db,scope);await processOutbox(db);
    assert.ok((await state(db,scope)).reason_codes.includes('fixed_dimension_mismatch:measurement_method'));
    const valid=context(scope,'E1-6_09');delete valid.dimensions.gwp_basis;
    await saveMeasurementContext(db,valid);await processOutbox(db);
    const obs=(await readDataset(db)).observations[0];
    assert.equal(obs.comparison_eligibility,'not_comparable');
    assert.ok(obs.comparison_reasons.includes('missing_comparison_dimension:gwp_basis'));
    const water=await candidate(db,'E3-4_01',{value:'10'});const invalid=context(water,'E3-4_01');invalid.dimensions.water_measure='withdrawal';
    await saveMeasurementContext(db,invalid);await approve(db,water);await processOutbox(db);
    assert.ok((await state(db,water)).reason_codes.includes('fixed_dimension_mismatch:water_measure'));
    const missing=context(water,'E3-4_01');delete missing.dimensions.aggregation_role;
    await saveMeasurementContext(db,missing);await processOutbox(db);
    assert.ok((await state(db,water)).reason_codes.includes('missing_required_dimension:aggregation_role'));
  }finally{await db.close();}
});

test('source conflicts block the current projection; frozen history survives rejection and stale queued approvals',async()=>{
  const db=await database();try {
    const first=await candidate(db,'E1-6_07',{value:'10'});await saveMeasurementContext(db,context(first));await approve(db,first);await processOutbox(db);
    const original=await readDataset(db),version=original.dataset_version;
    const second=await candidate(db,'E1-6_07',{value:'11'});await saveMeasurementContext(db,context(second));await approve(db,second);await processOutbox(db);
    assert.equal((await readDataset(db)).observations.length,0);
    assert.ok((await state(db,first)).reason_codes.includes('unresolved_source_conflict'));
    assert.ok((await state(db,second)).reason_codes.includes('unresolved_source_conflict'));
    assert.deepEqual(await readDataset(db,version),original);
    await reviewRecord(db,{disclosure_id:second.disclosure_id,decision:'rejected',operator_ref:'reviewer',reason:'Conflicting source not accepted'});await processOutbox(db);
    assert.equal((await readDataset(db)).observations.length,1);
    // Queue an approval, then revoke it before the publisher wakes.
    await reviewRecord(db,{disclosure_id:first.disclosure_id,decision:'approved',operator_ref:'reviewer',reason:'temporarily accepted'});
    await reviewRecord(db,{disclosure_id:first.disclosure_id,decision:'rejected',operator_ref:'reviewer',reason:'withdraw acceptance'});await processOutbox(db,{limit:1});
    assert.equal((await readDataset(db)).observations.length,0);
    assert.equal((await state(db,first)).publication_status,'withdrawn');
    assert.deepEqual(await readDataset(db,version),original);
    await db.query('UPDATE esg.esg_disclosures SET raw_disclosure_text=$1 WHERE disclosure_id=$2',['external accidental mutation',first.disclosure_id]);
    assert.deepEqual(await readDataset(db,version),original);
  }finally{await db.close();}
});

test('duplicate equal values merge with source links; context retries do not create revisions',async()=>{
  const db=await database();try {
    const first=await candidate(db,'E1-6_07',{value:'10.000'});const ctx=context(first);
    const saved=await saveMeasurementContext(db,ctx);assert.equal((await saveMeasurementContext(db,ctx)).context_id,saved.context_id);
    await approve(db,first);await processOutbox(db);
    const version=(await readDataset(db)).dataset_version;
    const second=await candidate(db,'E1-6_07',{value:'10'});await saveMeasurementContext(db,context(second));await approve(db,second);await processOutbox(db);
    const current=await readDataset(db);assert.equal(current.observations.length,1);assert.equal(current.observations[0].sources.length,2);
    assert.equal((await readDataset(db,version)).observations[0].sources.length,1);
    assert.equal((await processOutbox(db)).processed,0);
    assert.equal((await readDataset(db)).dataset_version,current.dataset_version);
    await assert.rejects(db.query('UPDATE esg.measurement_contexts SET reason=$1 WHERE context_id=$2',['mutate',saved.context_id]),/immutable snapshot/);
    await assert.rejects(db.query('DELETE FROM esg.dataset_membership WHERE dataset_version=$1',[current.dataset_version]),/immutable dataset membership/);
  }finally{await db.close();}
});

test('intervals and qualitative methodology publish as display records without invented points',async()=>{
  const db=await database();try {
    const ranged=await candidate(db,'E1-6_07',{value:'10',comparator:'range',lower:'10',upper:'20'});
    await saveMeasurementContext(db,context(ranged));await approve(db,ranged);await processOutbox(db);
    const interval=(await readDataset(db)).observations[0];assert.equal(interval.numeric_value,null);assert.equal(interval.lower_bound,'10');assert.equal(interval.upper_bound,'20');
    assert.ok(interval.comparison_reasons.includes('interval_not_point'));
    const statement=await candidate(db,'E2-4_09',{value:'Synthetic report states that measurements use verified facility instruments.'});
    await saveMeasurementContext(db,context(statement,'E2-4_09'));await approve(db,statement);await processOutbox(db);
    const methodology=(await readDataset(db)).observations.find(o=>o.source_datapoint_id==='E2-4_09');
    assert.match(methodology.statement,/facility instruments/);assert.equal(methodology.value_role,'context');assert.equal(methodology.canonical_unit,null);
    assert.ok(methodology.comparison_reasons.includes('qualitative_display_only'));
  }finally{await db.close();}
});

test('failed publication leaves a retryable outbox and no partial dataset',async()=>{
  const db=await database();try {
    const initialVersion=(await readDataset(db)).dataset_version;
    const item=await candidate(db);await saveMeasurementContext(db,context(item));await approve(db,item);
    const broken={query:db.query.bind(db),transaction:callback=>db.transaction(tx=>callback(new Proxy(tx,{get(target,key){
      if(key==='query') return async(sql,args)=>{if(sql.includes('INSERT INTO esg.publication_states')) throw new Error('injected publication failure');return target.query(sql,args);};
      const value=target[key];return typeof value==='function'?value.bind(target):value;}})))};
    await assert.rejects(processOutbox(broken),/injected publication failure/);
    assert.equal((await readDataset(db)).dataset_version,initialVersion);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.curated_observations')).rows[0].n,0);
    assert.ok((await db.query("SELECT attempts FROM esg.outbox_events WHERE status='pending'")).rows.every(r=>r.attempts===1));
    await processOutbox(db);const current=await readDataset(db);assert.equal(current.observations.length,1);
    assert.equal((await processOutbox(db)).processed,0);assert.equal((await readDataset(db)).dataset_version,current.dataset_version);
  }finally{await db.close();}
});

test('explicit restatement supersedes a compatible observation while historic revisions remain accessible',async()=>{
  const db=await database();try {
    const original=await candidate(db,'E1-6_07',{value:'10'});await saveMeasurementContext(db,context(original));await approve(db,original);await processOutbox(db);
    const old=await readDataset(db),previous=old.observations[0];
    const revised=await candidate(db,'E1-6_07',{value:'12'});
    await assert.rejects(saveMeasurementContext(db,context(revised,'E1-6_07',{value_role:'restated'})),/explicit supersession/);
    await saveMeasurementContext(db,context(revised,'E1-6_07',{value_role:'restated',measurement_method:'a_different_ghg_method',supersedes_observation_id:previous.observation_id}));
    await approve(db,revised);await processOutbox(db);
    assert.ok((await state(db,revised)).reason_codes.includes('invalid_supersession_identity'));
    assert.equal((await readDataset(db)).observations[0].numeric_value,'10');
    await saveMeasurementContext(db,context(revised,'E1-6_07',{value_role:'restated',supersedes_observation_id:previous.observation_id}));await approve(db,revised);await processOutbox(db);
    const current=await readDataset(db);assert.equal(current.observations.length,1);assert.equal(current.observations[0].numeric_value,'12');
    assert.equal((await state(db,original)).publication_status,'superseded');assert.deepEqual(await readDataset(db,old.dataset_version),old);
    await processOutbox(db);assert.equal((await readDataset(db)).dataset_version,current.dataset_version);
  }finally{await db.close();}
});

test('NaN and Infinity do not escape PostgreSQL numeric into published datasets',async()=>{
  const db=await database();try {
    const item=await candidate(db);await saveMeasurementContext(db,context(item));await approve(db,item);
    for(const special of ['NaN','Infinity','-Infinity']) {
      await db.query('UPDATE esg.structured_values SET numeric_value=$1::numeric WHERE disclosure_id=$2',[special,item.disclosure_id]);
      await reviewRecord(db,{disclosure_id:item.disclosure_id,decision:'approved',operator_ref:'reviewer',reason:'Trigger reevaluation synthetic malformed value'});await processOutbox(db);
      assert.equal((await readDataset(db)).observations.length,0);assert.ok((await state(db,item)).reason_codes.includes('non_finite_numeric_value'));
    }
  }finally{await db.close();}
});

test('ratio bounds belong to each metric definition, allowing signed and greater-than-one ratios',async()=>{
  const db=await PGlite.create();try {
    await applyMigrations(db);
    const metric={...structuredClone(definition('E1-5_09')),metric_id:'synthetic.signed_ratio',source_datapoint_id:'SYNTHETIC-RATIO_01',
      label_zh:'合成的有符号比率',value_constraints:{minimum:'-10',maximum:'10'}};
    await seedMetricDictionary(db,{dictionary_version:'synthetic-1.0.0',profile_id:'synthetic.signed-ratio',metrics:[metric]});
    for(const [index,value] of ['-0.25','1.25'].entries()) {
      const item=await candidate(db,metric.source_datapoint_id,{value,unit:'ratio',metric_override:metric});
      await saveMeasurementContext(db,context(item,'E1-5_09',{entity_ref:`synthetic_ratio_${index}`,reported_unit:'ratio'}));await approve(db,item);
    }
    await processOutbox(db);
    assert.deepEqual((await readDataset(db)).observations.map(o=>o.numeric_value).sort(),['-0.25','1.25']);
  }finally{await db.close();}
});

test('versioned Chinese unit aliases preserve explicit scale factors and unresolved source units block publication',async()=>{
  const db=await database();try {
    const examples=[
      ['E1-5_01','万千瓦时','1.2345','12.345'],
      ['E1-5_01','亿千瓦时','900719925474099312345.678901','90071992547409931234567890.1'],
      ['E1-5_01','吉焦','3.6','1'],
      ['E1-6_07','万吨CO2e','1.2345','12345'],
      ['E1-6_07','万吨二氧化碳当量','2.5','25000'],
      ['E1-6_07','亿吨二氧化碳当量','1.78','178000000'],
      ['E1-6_07','亿tCO₂e','900719925474099312345.678901','90071992547409931234567890100'],
      ['E1-5_01','亿兆瓦时','0.00000001','1'],
      ['E2-4_02','万吨','1.125','11250'],
      ['E2-4_02','亿吨','0.000012345','1234.5'],
      ['E3-4_01','万立方米','0.125','1250'],
      ['E3-4_01','亿立方米','0.125','12500000'],
      ['E3-4_01','百万立方米','1.125','1125000'],
      ['E1-5_09','百分比','12.5','0.125']
    ];
    for(const [index,[id,unit,value,expected]] of examples.entries()) {
      const item=await candidate(db,id,{unit,value});
      await saveMeasurementContext(db,context(item,id,{entity_ref:`synthetic_units_${index}`,reported_unit:unit}));await approve(db,item);await processOutbox(db);
      const observation=(await readDataset(db)).observations.find(o=>o.entity_ref===`synthetic_units_${index}`);
      assert.equal(observation.numeric_value,expected);
      assert.equal(observation.normalization_rule_version,NORMALIZATION_RULE_VERSION);
      assert.equal(observation.sources[0].unit_conversion.source_unit,unit);
      assert.equal(observation.sources[0].unit_conversion.rule_version,NORMALIZATION_RULE_VERSION);
      assert.match(observation.sources[0].unit_conversion.rule_digest,/^[0-9a-f]{64}$/);
      if(unit==='吉焦')assert.equal(observation.sources[0].unit_conversion.arithmetic,'rational_decimal_40');
    }
    for(const [index,unit] of ['custom_unrecognized_unit',null].entries()) {
      const item=await candidate(db,'E3-4_01',{unit:unit || 'm3',value:'10'});
      if(unit===null)await db.query('UPDATE esg.structured_values SET raw_unit=NULL WHERE disclosure_id=$1',[item.disclosure_id]);
      await saveMeasurementContext(db,context(item,'E3-4_01',{entity_ref:`synthetic_unresolved_${index}`,reported_unit:'m3'}));await approve(db,item);await processOutbox(db);
      assert.ok((await state(db,item)).reason_codes.includes('source_unit_unresolved'));
      assert.ok(!(await readDataset(db)).observations.some(o=>o.entity_ref===`synthetic_unresolved_${index}`));
    }
    const scaled=await candidate(db,'E3-4_01',{unit:'万立方米',value:'10'});
    await saveMeasurementContext(db,context(scaled,'E3-4_01',{entity_ref:'synthetic_wrong_scale',reported_unit:'m3'}));await approve(db,scaled);await processOutbox(db);
    assert.ok((await state(db,scaled)).reason_codes.includes('reported_unit_disagrees_with_source'));
    const ambiguous=await candidate(db,'E1-5_01',{unit:'mWh',value:'10'});
    await saveMeasurementContext(db,context(ambiguous,'E1-5_01',{entity_ref:'synthetic_si_prefix',reported_unit:'MWh'}));await approve(db,ambiguous);await processOutbox(db);
    assert.ok((await state(db,ambiguous)).reason_codes.includes('source_unit_unresolved'));
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.normalization_rule_versions')).rows[0].n,1);
    await assert.rejects(db.query('UPDATE esg.normalization_rule_versions SET definition=$1', [{}]),/immutable snapshot/);
  }finally{await db.close();}
});

test('a unique rule upgrade event re-evaluates pending observations without changing frozen history',async()=>{
  const db=await database();try {
    const item=await candidate(db,'E3-4_01',{unit:'custom_unit',value:'10'});
    await saveMeasurementContext(db,context(item,'E3-4_01',{reported_unit:'m3'}));await approve(db,item);
    // Simulate an older local draft that treated an unknown source unit as m3.
    const oldObservation='11111111-1111-4111-a111-111111111111';
    const metric=definition('E3-4_01');
    await db.query(`INSERT INTO esg.curated_observations(observation_id,metric_id,definition_version,identity_key,entity_ref,period_start,period_end,boundary_code,value_role,
      measurement_method,dimensions,canonical_unit,numeric_value,comparison_eligibility,comparison_reasons,normalization_rule_version)
      VALUES($1,$2,$4,$3,'synthetic_group','2024-01-01','2024-12-31','group_consolidated','actual','verified_fixture_method','{}','m3','10','not_comparable','[]','esg-units-1.0.0')`,
      [oldObservation,metric.metric_id,'1'.repeat(64),metric.definition_version]);
    const oldVersion=(await db.query(`INSERT INTO esg.dataset_versions(dictionary_version,content_digest) VALUES('1.0.0',$1) RETURNING dataset_version`,['2'.repeat(64)])).rows[0].dataset_version;
    await db.query('INSERT INTO esg.dataset_membership VALUES($1,$2)',[oldVersion,oldObservation]);await db.query('UPDATE esg.dataset_versions SET sealed=true WHERE dataset_version=$1',[oldVersion]);
    await db.query("UPDATE esg.outbox_events SET status='processed',processed_at=CURRENT_TIMESTAMP");
    await processOutbox(db);
    const latest=await readDataset(db);assert.ok(latest.dataset_version>Number(oldVersion));assert.equal(latest.observations.length,0);
    assert.equal((await readDataset(db,oldVersion)).observations[0].normalization_rule_version,'esg-units-1.0.0');
    assert.equal((await readDataset(db,oldVersion)).observations[0].numeric_value,'10');
    assert.ok((await state(db,item)).reason_codes.includes('source_unit_unresolved'));
    assert.equal((await db.query("SELECT count(*)::int AS n FROM esg.outbox_events WHERE event_type='normalization'")).rows[0].n,1);
    assert.equal((await processOutbox(db)).processed,0);
  }finally{await db.close();}
});

test('a report keeps its printed classification beside the clause classification',async()=>{
  const db=await database();try {
    const item=await candidate(db);
    // The printed row says 风能; the clause fixes energy_source=renewable.
    // Both readings must survive publication: the clause one carries the
    // comparison contract, the printed one is what the report actually said.
    await db.query(`INSERT INTO esg.disclosure_dimensions(disclosure_dimension_id,disclosure_id,dimension_key,ordinal,raw_value,canonical_value)
      VALUES(gen_random_uuid(),$1,'energy_carrier',1,'风能',NULL),(gen_random_uuid(),$1,'energy_source',1,'风能','renewable')`,[item.disclosure_id]);
    await saveMeasurementContext(db,context(item));
    await approve(db,item);await processOutbox(db);
    const published=(await readDataset(db)).observations[0];
    assert.equal(published.dimensions.entity_kind,'group');
    assert.deepEqual(published.reported_dimensions.energy_carrier,['风能']);
    assert.deepEqual(published.reported_dimensions.energy_source,['风能']);
    assert.ok(published.source_row_label);
  }finally{await db.close();}
});
