import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {applyPlatformMigrations} from '../../../database/runtime/connection.mjs';
import {applyMigrations} from '../../../database/runtime/frozen_schema.mjs';
import {importBundle} from '../../../database/scripts/frozen-bundle.mjs';
import {seedMetricDictionary} from '../../../database/scripts/publication.mjs';
import {artifactInventory,purgeArtifacts,PURGE_RULE_VERSION} from '../../../database/scripts/purge-artifacts.mjs';
import {dictionaryFile,platformRoot} from '../../../database/queries/service.mjs';

const requireDB=createRequire(path.join(platformRoot,'../database/package.json'));
const {PGlite}=await import(pathToFileURL(requireDB.resolve('@electric-sql/pglite')).href);
let sequence=0;

async function database() {
  const db=await PGlite.create();
  await applyMigrations(db);
  await applyPlatformMigrations(db);
  const {file_sha256,...dictionary}=await dictionaryFile();
  await seedMetricDictionary(db,dictionary);
  await db.query(`INSERT INTO plat.entities(entity_ref,name_zh,entity_kind,industry_code,country_code,metadata)
    VALUES('purge-company','清理测试集团','listed_company','C39','CN','{}'::jsonb)`);
  return db;
}

async function importFact(db, sample='purge') {
  const dictionary=await dictionaryFile();
  const metric=dictionary.metrics.find(m=>m.source_datapoint_id==='E1-6_07');
  const binding=metric.standard_binding,n=++sequence;
  const bundle={root:`/synthetic/${sample}-${n}`,manifest:{job_id:`${sample}-${n}`},bundleDigest:String(n).padStart(64,'0'),
    request:{package_id:binding.package_id,package_version:binding.package_version},
    standard:{manifest:{standard:{framework_id:'esrs',framework_version:'2023-set1'}},compilation:{source_digest:binding.digest},
      metrics:[{metric_id:binding.metric_id,source_datapoint_id:'E1-6_07',labels:{zh:metric.label_zh}}],elements:[]},
    sourceMappings:{core_bindings:[{core_record_id:`r-${n}`,source_fact_id:`source-${n}`,match:'match'}]},
    sourceFacts:{facts:[{source_fact_id:`source-${n}`,kind:'quantitative',document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1,
      source_row_label:`合成指标 ${n}`,value_raw:'10',unit_raw:metric.canonical_unit,identity_raw:{reporting_period:'2025年度'},
      quality_status:'grounded',review_status:'candidate',physical_sources:[{ir_object_id:`cell-${n}`,pdf_page_index:0,printed_page_label:'1',
        bbox:{x0:1,y0:1,x1:2,y1:2},document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1}]}]},
    records:{reporting_tasks:[],quantitative_observations:[{observation_id:`r-${n}`,metric_id:binding.metric_id,value_raw:'10',value_numeric:'10',
      comparator:'exact',unit_raw:metric.canonical_unit,review_status:'pending'}],qualitative_assertions:[],attribute_values:[],dimension_values:[],
      evidence_references:[{evidence_id:`e-${n}`,source_record_id:`r-${n}`,document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1,
        ir_object_id:`cell-${n}`,pdf_page_index:0,evidence_excerpt:'合成来源值 10'}]}};
  return importBundle(db,bundle,{upstream_system:'purge_fixture',organization_ref:'purge-company',
    report_ref:`${sample}-report-${n}`,report_version_ref:`${sample}-report-${n}:asset:1`,source_artifact_ref:`pdf-${n}`,
    report_period_start:'2025-01-01',report_period_end:'2025-12-31'});
}

test('the dry run reports artifact rows and changes nothing', async()=>{
  const db=await database();try{
    await importFact(db);
    const before=await artifactInventory(db);
    assert.ok(before.artifact_rows>0);
    const result=await purgeArtifacts(db,{apply:false});
    assert.equal(result.dry_run,true);
    assert.equal(result.applied,false);
    const after=await artifactInventory(db);
    assert.deepEqual(after, before);
  }finally{await db.close();}
});

test('an applied purge clears artifacts and preserves the chain and report identity', async()=>{
  const db=await database();try{
    await importFact(db);
    const before=await artifactInventory(db);
    const reports=before.chain['esg.source_report_refs'];
    const result=await purgeArtifacts(db,{apply:true,confirm:PURGE_RULE_VERSION,operator_ref:'purge-test',reason:'清理测试产物'});
    assert.equal(result.applied,true);
    assert.equal(result.chain_preserved,true);
    assert.equal(result.dataset_baseline,'restored');
    const after=await artifactInventory(db);
    assert.equal(after.artifact_rows,0);
    for (const [table,n] of Object.entries(result.chain_before)) assert.equal(after.chain[table],n);
    assert.equal(after.chain['esg.source_report_refs'],reports);
    const baseline=(await db.query('SELECT dictionary_version,sealed FROM esg.dataset_versions WHERE dataset_version=0')).rows[0];
    const dictionary=(await db.query(`SELECT dictionary_version FROM esg.dictionary_versions WHERE sealed
      ORDER BY release_order DESC LIMIT 1`)).rows[0].dictionary_version;
    assert.equal(baseline.dictionary_version,dictionary);
    assert.equal(baseline.sealed,true);
  }finally{await db.close();}
});

test('a restart after the purge neither creates a dataset nor queues an event', async()=>{
  const db=await database();try{
    await importFact(db);
    await purgeArtifacts(db,{apply:true,confirm:PURGE_RULE_VERSION,operator_ref:'purge-test',reason:'重启稳定性'});
    const {file_sha256,...dictionary}=await dictionaryFile();
    await seedMetricDictionary(db,dictionary);
    const datasets=(await db.query('SELECT count(*)::int AS n FROM esg.dataset_versions')).rows[0].n;
    assert.equal(datasets,1);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.outbox_events')).rows[0].n,0);
  }finally{await db.close();}
});

test('the purge refuses a missing confirmation, operator or reason', async()=>{
  const db=await database();try{
    await assert.rejects(purgeArtifacts(db,{apply:true,operator_ref:'x',reason:'x'}),/确认标识/);
    await assert.rejects(purgeArtifacts(db,{apply:true,confirm:PURGE_RULE_VERSION,reason:'x'}),/操作员/);
    await assert.rejects(purgeArtifacts(db,{apply:true,confirm:PURGE_RULE_VERSION,operator_ref:'x'}),/原因/);
    assert.ok((await artifactInventory(db)).chain['esg.metric_definition_versions']>0);
  }finally{await db.close();}
});

test('the chain still accepts a fresh delivery after the purge', async()=>{
  const db=await database();try{
    await importFact(db,'before');
    await purgeArtifacts(db,{apply:true,confirm:PURGE_RULE_VERSION,operator_ref:'purge-test',reason:'清理后重新交付'});
    const imported=await importFact(db,'after');
    assert.ok(imported.import_batch_id);
    const rows=(await db.query('SELECT count(*)::int AS n FROM esg.disclosure_standard_mappings')).rows[0].n;
    assert.equal(rows,1);
  }finally{await db.close();}
});
