import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../runtime/frozen_schema.mjs';
import {importBundle,loadBundle} from '../scripts/frozen-bundle.mjs';
import {readReportSourceFacts} from '../scripts/report-source-facts.mjs';
import {applyPlatformMigrations} from '../runtime/connection.mjs';
import {dispatch} from '../queries/service.mjs';

const dictionary=JSON.parse(await readFile(new URL('../../database/catalog/metric-dictionary.v1.json',import.meta.url),'utf8'));
const metric=dictionary.metrics.find(item=>item.source_datapoint_id==='E2-4_02'),binding=metric.standard_binding;
const hash=text=>createHash('sha256').update(text).digest('hex');
const metadata={upstream_system:'synthetic_source_archive_test',organization_ref:'synthetic_reporter',report_ref:'synthetic-report',
  report_version_ref:'synthetic-report-v1',source_artifact_ref:'synthetic://no-real-pdf',report_title:'额外来源行合成软件验收报告'};
function fact(id,overrides={}) {
  return {source_fact_id:id,source_anchor_id:'synthetic-anchor-'+id,source_key:'synthetic-source-key',document_id:'synthetic-document',
    ir_run_id:'synthetic-ir',ir_revision:1,source_object_id:'synthetic-table',source_row_id:id,source_row_label:'报告原始行 '+id,
    kind:'quantitative',value_raw:'123.4500',unit_raw:'原文单位',identity_raw:{pollutant:'氮氧化物',boundary_note:'仅自身运营',
      activity_groups:['炼化','销售'],additional_breakdown:{unknown_subdivision:'候选对象原样保全'}},
    identity_evidence:{boundary_note:[{text:'边界仅自身运营',locators:[{ir_object_id:'synthetic-note',pdf_page_index:1}]}]},
    unverified_identity_raw:{gwp_basis:'报告未明确'},physical_sources:[{ir_object_id:'synthetic-cell-'+id,pdf_page_index:0}],
    quality_status:'grounded',interpretation_version:'synthetic-reader-v1',review_status:'candidate',...overrides};
}
function bundle({core=true,key='one'}={}) {
  const records={reporting_tasks:[],quantitative_observations:[],qualitative_assertions:[],attribute_values:[],dimension_values:[],evidence_references:[]};
  if(core) {
    records.quantitative_observations=[{observation_id:'synthetic-core',metric_id:binding.metric_id,value_raw:'123.4500',
      value_numeric:'123.4500',unit_raw:'原文单位',comparator:'exact'}];
    records.evidence_references=[{evidence_id:'synthetic-evidence',source_record_id:'synthetic-core',document_id:'synthetic-document',
      ir_run_id:'synthetic-ir',ir_revision:1,ir_object_id:'synthetic-cell-mapped',pdf_page_index:0,evidence_excerpt:'虚构的软件回归来源：123.4500 原文单位'}];
  }
  return {root:'/synthetic/report-source-facts',manifest:{job_id:'synthetic-job-'+key,
    document:{document_id:'synthetic-document',ir_run_id:'synthetic-ir',ir_revision:1}},bundleDigest:hash(key),
    request:{ir_run_id:'synthetic-ir',package_id:binding.package_id,package_version:binding.package_version,metric_ids:[binding.metric_id]},
    standard:{manifest:{standard:{framework_id:'esrs',framework_version:'2023-set1'}},compilation:{source_digest:binding.digest},
      metrics:[{metric_id:binding.metric_id,source_datapoint_id:metric.source_datapoint_id,labels:{zh:metric.label_zh}}],elements:[]},
    records,sourceFacts:{facts:[fact('mapped'),fact('extra-row',{value_raw:'0.0001234500',unit_raw:'公斤',source_row_label:'额外物理量，不属于所选条款'}),
      fact('unbound-row',{kind:'assertion',value_raw:null,statement_raw:'暂未绑定物理出处的原文候选',physical_sources:[],quality_status:'unbound'})]},
    sourceMappings:{core_bindings:core?[{core_record_id:'synthetic-core',source_fact_id:'mapped',metric_id:binding.metric_id}]:[]}};
}
async function setup(){const db=await PGlite.create();await applyMigrations(db);await applyPlatformMigrations(db);return db;}
async function count(db,table){return (await db.query(`SELECT count(*)::int AS n FROM esg.${table}`)).rows[0].n;}

test('all materialized source rows and raw properties are archived independently of selected Core, mapping or publication',async()=>{
  const db=await setup();try {
    const input=bundle(),imported=await importBundle(db,input,metadata);
    assert.equal(imported.imported,1);
    assert.equal(await count(db,'report_source_fact_snapshots'),3);
    assert.equal(await count(db,'import_source_facts'),3);
    assert.equal(await count(db,'esg_disclosures'),1);
    assert.equal(await count(db,'disclosure_standard_mappings'),1);
    assert.equal(await count(db,'curated_observations'),0);
    const archived=await dispatch(db,'source_facts',{report_ref:metadata.report_ref,limit:100});
    assert.equal(archived.items.length,3);
    const extra=archived.items.find(row=>row.source_fact_ref==='extra-row');
    assert.equal(extra.mapping_state,'unmapped');assert.equal(extra.core_binding_count,0);
    assert.equal(extra.value_raw,'0.0001234500');assert.equal(extra.unit_raw,'公斤');
    assert.deepEqual(extra.identity_raw,input.sourceFacts.facts[1].identity_raw);
    assert.equal(extra.import_batches[0].job_id,input.manifest.job_id);
    assert.equal(extra.publication_status,'source_only_not_published');
    const payload=(await db.query('SELECT snapshot_payload FROM esg.report_source_fact_snapshots WHERE snapshot_id=$1',[extra.snapshot_id])).rows[0].snapshot_payload;
    const {review_status,...immutable}=input.sourceFacts.facts[1];assert.deepEqual(payload,immutable);
    const unbound=archived.items.find(row=>row.source_fact_ref==='unbound-row');
    assert.equal(unbound.quality_status,'unbound');assert.equal(unbound.provenance_status,'unbound');
    assert.equal(archived.items.find(row=>row.source_fact_ref==='mapped').mapping_state,'has_core');
    assert.equal((await importBundle(db,input,metadata)).idempotent,true);
    assert.equal(await count(db,'import_source_facts'),3);
    const truncated=await readReportSourceFacts(db,{limit:1});assert.equal(truncated.items.length,1);assert.equal(truncated.has_more,true);
    assert.equal((await readReportSourceFacts(db,{report_ref:'other-report'})).items.length,0);
    for(const limit of [0,1001,1.5])await assert.rejects(readReportSourceFacts(db,{limit}),/limit/);
  } finally {await db.close();}
});

test('unmapped interpretation revisions coexist immutably while selected SourceFact collision rules stay unchanged',async()=>{
  const db=await setup();try {
    const first=bundle(),saved=await importBundle(db,first,metadata),original=await readReportSourceFacts(db);
    const next=bundle({key:'two'});next.sourceFacts.facts[1].identity_raw.boundary_note='范围说明的另一语义版本';
    next.sourceFacts.facts[1].interpretation_version='synthetic-reader-v2';
    await importBundle(db,next,metadata);
    const versions=(await readReportSourceFacts(db)).items.filter(row=>row.source_fact_ref==='extra-row');
    assert.equal(versions.length,2);assert.notEqual(versions[0].snapshot_digest,versions[1].snapshot_digest);
    assert.equal(await count(db,'report_source_fact_snapshots'),4);
    assert.equal(await count(db,'import_source_facts'),6);
    const retained=(await db.query('SELECT snapshot_id FROM esg.import_source_facts WHERE import_batch_id=$1 ORDER BY source_fact_ref',[saved.import_batch_id])).rows;
    assert.deepEqual(retained.map(row=>row.snapshot_id).sort(),original.items.map(row=>row.snapshot_id).sort());
    await assert.rejects(db.query("UPDATE esg.report_source_fact_snapshots SET value_raw='overwrite'"),/immutable/);
    await assert.rejects(db.query('DELETE FROM esg.import_source_facts'),/immutable/);
    const changed=bundle({key:'mapped-collision'});changed.sourceFacts.facts[0].value_raw='999';
    await assert.rejects(importBundle(db,changed,metadata),/Source fact collision/);
    assert.equal(await count(db,'report_source_fact_snapshots'),4);
    assert.equal(await count(db,'import_batches'),2);
  } finally {await db.close();}
});

test('a failed downstream mapping or outer handoff rolls back all neutral snapshots and batch associations atomically',async()=>{
  const db=await setup();try {
    const broken={query:db.query.bind(db),transaction:callback=>db.transaction(tx=>callback(new Proxy(tx,{get(target,key){
      if(key==='query')return async(sql,args)=>{if(sql.includes('INSERT INTO esg.disclosure_standard_mappings'))throw Error('injected mapping failure');return target.query(sql,args);};
      const value=target[key];return typeof value==='function'?value.bind(target):value;}})))};
    await assert.rejects(importBundle(broken,bundle(),metadata),/injected mapping/);
    for(const table of ['source_report_refs','import_batches','report_source_fact_snapshots','import_source_facts','esg_disclosures'])assert.equal(await count(db,table),0);
    await assert.rejects(db.transaction(async tx=>{await importBundle(tx,bundle(),metadata,{inTransaction:true});throw Error('outer handoff failure');}),/outer handoff/);
    assert.equal(await count(db,'report_source_fact_snapshots'),0);
    assert.equal((await importBundle(db,bundle(),metadata)).imported,1);
  } finally {await db.close();}
});

test('unmapped foreign document, unrelated IR, wrong revision and physical locators fail the complete import',async()=>{
  const db=await setup();try {
    for(const drift of [{document_id:'foreign-document'},{ir_run_id:'unrelated-ir'},{ir_revision:2},
      {physical_sources:[{ir_object_id:'foreign-cell',pdf_page_index:0,ir_run_id:'unrelated-ir'}]}]) {
      const input=bundle({key:JSON.stringify(drift)});Object.assign(input.sourceFacts.facts[1],drift);
      await assert.rejects(importBundle(db,input,metadata),/frozen|physical source/);
      assert.equal(await count(db,'import_batches'),0);assert.equal(await count(db,'report_source_fact_snapshots'),0);
    }
    const transition=bundle({key:'explicit-transition'});transition.request.ir_run_id='synthetic-ir-new';
    transition.manifest.document.ir_run_id='synthetic-ir-new';transition.manifest.document.ir_revision=2;
    transition.evidenceTransition={from_ir_run_id:'synthetic-ir',to_ir_run_id:'synthetic-ir-new',from_ir_revision:1};
    transition.sourceFacts.facts[1].ir_run_id='synthetic-ir-new';transition.sourceFacts.facts[1].ir_revision=2;
    await importBundle(db,transition,metadata);
    const rows=(await readReportSourceFacts(db)).items;
    assert.equal(rows.find(row=>row.source_fact_ref==='mapped').identity_basis,'evidence_transition');
    assert.equal(rows.find(row=>row.source_fact_ref==='extra-row').identity_basis,'frozen_manifest');
  } finally {await db.close();}
});

test('IR-only lifecycle references protect neutral snapshots even when zero standard facts exist',async()=>{
  const db=await setup();try {
    const input=bundle({core:false});await importBundle(db,input,metadata);
    assert.equal(await count(db,'esg_disclosures'),0);assert.equal(await count(db,'disclosure_standard_mappings'),0);
    const references=await dispatch(db,'lifecycle_references',{ir_run_ids:['synthetic-ir']});
    assert.equal(references.verified,true);assert.equal(references.dependencies.length,1);
    assert.equal(references.counts.candidate_count,0);assert.equal(references.counts.source_fact_count,3);
    assert.equal(references.blockers[0].code,'candidate_source_reference');
    const held=await dispatch(db,'lifecycle_reserve',{ir_run_ids:['synthetic-ir'],reason:'Synthetic cleanup must be blocked'});
    assert.ok(held.blockers.length);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM plat.artifact_lifecycle_reservations')).rows[0].n,0);
    assert.equal((await readReportSourceFacts(db)).items.every(row=>row.mapping_state==='unmapped'),true);
    assert.equal(await count(db,'curated_observations'),0);
  } finally {await db.close();}
});

test('an actual anonymous interrupted ResultExporter Bundle archives both sources while importing only its one selected Core fact',
  {skip:!process.env.ESG_TEST_PARTIAL_BUNDLE},async()=>{
    const input=await loadBundle(process.env.ESG_TEST_PARTIAL_BUNDLE),db=await setup();try {
      assert.equal(input.manifest.execution_status,'interrupted');
      const result=await importBundle(db,input,metadata);
      assert.equal(result.imported,1);assert.equal(await count(db,'report_source_fact_snapshots'),2);
      const sources=(await readReportSourceFacts(db)).items;
      assert.equal(sources.filter(source=>source.mapping_state==='has_core').length,1);
      assert.equal(sources.filter(source=>source.mapping_state==='unmapped').length,1);
      assert.equal(await count(db,'curated_observations'),0);
    } finally {await db.close();}
  });
