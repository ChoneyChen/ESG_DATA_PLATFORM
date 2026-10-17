import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {dispatch,dictionaryFile,platformRoot} from '../service.mjs';
import {applyPlatformMigrations} from '../database.mjs';
import {applyMigrations} from '../../../ExtractESG/esg_database/scripts/db.mjs';
import {seedMetricDictionary,processOutbox} from '../../../ExtractESG/esg_database/scripts/publication.mjs';

const requireResult=createRequire(path.join(platformRoot,'../ExtractESG/esg_database/package.json'));
const {PGlite}=await import(pathToFileURL(requireResult.resolve('@electric-sql/pglite')).href);
const sha=value=>createHash('sha256').update(value).digest('hex');
async function setup() {
  const db=await PGlite.create();
  await applyMigrations(db);await applyPlatformMigrations(db);
  const {file_sha256,...dictionary}=await dictionaryFile();await seedMetricDictionary(db,dictionary);await processOutbox(db);
  await dispatch(db,'register_entity',{entity_ref:'fixture_entity',name_zh:'合成审计企业',entity_kind:'listed_company',industry_code:'test',country_code:'CN',is_demo:true});
  return db;
}
async function fixture(root,{extraOutput=false}={}) {
  const dictionary=await dictionaryFile();
  const metric=dictionary.metrics.find(m=>m.source_datapoint_id==='E1-5_01'),binding=metric.standard_binding;
  const standard=JSON.parse(await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/dist',binding.package_id,binding.package_version,'package.json'),'utf8'));
  const selected=[metric];
  const output=extraOutput?[metric,dictionary.metrics.find(m=>m.source_datapoint_id==='E1-5_05')]:selected;
  await mkdir(path.join(root,'input'),{recursive:true});await mkdir(path.join(root,'results'),{recursive:true});
  const request={ir_run_id:'synthetic_audit_ir',package_id:binding.package_id,package_version:binding.package_version,metric_ids:selected.map(m=>m.standard_binding.metric_id)};
  await writeFile(path.join(root,'manifest.json'),JSON.stringify({job_id:'synthetic_audit_job',status:'completed',schema_version:'targeted-extraction-result-v1',
    bundle_layout_version:'targeted-result-bundle-v1',contract_validation:{status:'passed',basis:'synthetic_fixture'},document:{document_id:'synthetic_audit_doc'}}));
  await writeFile(path.join(root,'input/request.json'),JSON.stringify(request));
  await writeFile(path.join(root,'input/standard-package.json'),JSON.stringify(standard));
  const files={'reporting-tasks':[],'quantitative-observations':output.map((m,index)=>({observation_id:`synthetic_${index}`,metric_id:m.standard_binding.metric_id,value_numeric:'10',value_raw:'10',unit_raw:'MWh',comparator:'exact'})),
    'qualitative-assertions':[],'attribute-values':[],'dimension-values':[],
    'evidence-references':output.map((m,index)=>({evidence_id:`synthetic_evidence_${index}`,source_record_id:`synthetic_${index}`,document_id:'synthetic_audit_doc',ir_run_id:request.ir_run_id,
      ir_revision:1,ir_object_id:`synthetic_cell_${index}`,pdf_page_index:0,evidence_excerpt:`Explicit synthetic fixture ${m.source_datapoint_id}:10 MWh`}))};
  for(const [file,records] of Object.entries(files)) await writeFile(path.join(root,'results',`${file}.jsonl`),records.map(r=>JSON.stringify(r)).join('\n'));
  return {bundle_path:root,report_metadata:{upstream_system:'synthetic_audit',organization_ref:'fixture_entity',report_ref:'synthetic_report',report_version_ref:'synthetic_report_v1',
    source_artifact_ref:'synthetic://audit_fixture',source_sha256:sha('synthetic'),report_title:'仅用于软件回归的虚构报告'}};
}
async function withFixture(callback,options={}) {
  const root=await mkdtemp(path.join(tmpdir(),'platesg-service-audit-'));
  const saved=process.env.PLATESG_IMPORT_ROOTS;process.env.PLATESG_IMPORT_ROOTS=root;
  const db=await setup();
  try {await callback(db,await fixture(root,options));}
  finally {await db.close();await rm(root,{recursive:true,force:true});if(saved===undefined)delete process.env.PLATESG_IMPORT_ROOTS;else process.env.PLATESG_IMPORT_ROOTS=saved;}
}

test('candidate import freezes a scope atomically and does not publish or approve',async()=>{
  await withFixture(async(db,input)=>{
    const first=await dispatch(db,'import',input);assert.equal(first.imported,1);
    const repeated=await dispatch(db,'import',input);assert.equal(repeated.idempotent,true);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM plat.extraction_scopes')).rows[0].n,1);
    assert.equal((await db.query('SELECT quality_status FROM esg.esg_disclosures')).rows[0].quality_status,'candidate');
    assert.equal((await dispatch(db,'observations',{limit:100})).items.length,0);
  });
});

test('operator handoff persists one durable receipt and filters report candidates on the server',async()=>{
  await withFixture(async(db,input)=>{
    const first=await dispatch(db,'deliver',input);
    assert.equal(first.status,'imported');assert.match(first.receipt_id,/^[0-9a-f]{64}$/);
    const repeated=await dispatch(db,'deliver',input);
    assert.equal(repeated.receipt_id,first.receipt_id);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM plat.import_receipts')).rows[0].n,1);
    assert.equal((await dispatch(db,'candidates',{report_ref:'synthetic_report',source_datapoint_id:'E1-5_01'})).items.length,1);
    assert.equal((await dispatch(db,'candidates',{report_ref:'another_report'})).items.length,0);
    assert.equal((await dispatch(db,'sources',{})).items.length,0);
    const count=(await dispatch(db,'report_summaries',{})).items[0];
    assert.equal(count.report_ref,'synthetic_report');assert.equal(count.review_count,1);assert.equal(count.published_count,0);
    await assert.rejects(dispatch(db,'source_document',{source_id:'0'.repeat(64)}),error=>error.code==='not_found');
  });
});

test('local file deletion preserves SQL snapshots and releases the source for a new processing run',async()=>{
  await withFixture(async(db,input)=>{
    await dispatch(db,'deliver',input);
    const scope={source_sha256:input.report_metadata.source_sha256,job_ids:['synthetic_audit_job'],ir_run_ids:['synthetic_audit_ir']};
    const before=await dispatch(db,'lifecycle_references',scope);
    assert.ok(before.blockers.length>0);
    const held=await dispatch(db,'lifecycle_reserve',{...scope,local_files_only:true,reason:'Delete local files, retain SQL evidence'});
    assert.equal(held.blockers.length,0);assert.ok(held.reservation_id);
    assert.ok(held.counts.candidate_count>0);
    // The same source cannot be imported during physical file removal.
    await assert.rejects(dispatch(db,'import',input),/正在回收|已清除/);
    await dispatch(db,'lifecycle_reservation',{reservation_id:held.reservation_id,state:'quarantined',reason:'Removing local files'});
    await dispatch(db,'lifecycle_reservation',{reservation_id:held.reservation_id,state:'released',reason:'Local files removed, snapshot retained'});
    const after=await dispatch(db,'lifecycle_references',scope);
    assert.deepEqual(after.counts,before.counts);
    const renewed=await dispatch(db,'lifecycle_reserve',{...scope,local_files_only:true,reason:'New local processing lifecycle'});
    assert.ok(renewed.reservation_id);
    await dispatch(db,'lifecycle_reservation',{reservation_id:renewed.reservation_id,state:'released',reason:'Finished synthetic check'});
  });
});

test('scope write failure rolls back candidate facts and report references',async()=>{
  await withFixture(async(db,input)=>{
    const broken={query:db.query.bind(db),transaction:callback=>db.transaction(tx=>callback(new Proxy(tx,{get(target,key){
      if(key==='query')return async(sql,args)=>{if(sql.includes('INSERT INTO plat.extraction_scopes'))throw new Error('injected scope persistence failure');return target.query(sql,args);};
      const value=target[key];return typeof value==='function'?value.bind(target):value;}})))};
    await assert.rejects(dispatch(broken,'import',input),/injected scope persistence failure/);
    for(const table of ['import_batches','esg_disclosures','source_report_refs']) assert.equal((await db.query(`SELECT count(*)::int AS n FROM esg.${table}`)).rows[0].n,0);
    assert.equal((await dispatch(db,'import',input)).imported,1);
  });
});

test('an output record outside request selection is rejected before any writes',async()=>{
  await withFixture(async(db,input)=>{
    await assert.rejects(dispatch(db,'import',input));
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.import_batches')).rows[0].n,0);
  },{extraOutput:true});
});

test('context cannot silently change verified registered entity kind',async()=>{
  await withFixture(async(db,input)=>{
    await dispatch(db,'import',input);
    const mapping=(await db.query('SELECT disclosure_standard_mapping_id AS mapping_id FROM esg.disclosure_standard_mappings')).rows[0];
    await assert.rejects(dispatch(db,'context',{mapping_id:mapping.mapping_id,entity_ref:'fixture_entity',period_start:'2024-01-01',period_end:'2024-12-31',
      boundary_code:'own_operations',value_role:'actual',dimensions:{entity_kind:'group'},reported_unit:'MWh',operator_ref:'auditor',reason:'Synthetic test'}),/entity_kind disagrees/);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.measurement_contexts')).rows[0].n,0);
  });
});

test('comparison returns all cohort exclusions without selecting a company as reference',async()=>{
  const db=await setup();try {
    const dictionary=await dictionaryFile(),metric=dictionary.metrics.find(m=>m.source_datapoint_id==='E1-5_01');
    await dispatch(db,'register_entity',{entity_ref:'fixture_other',name_zh:'另一合成企业',entity_kind:'listed_company',industry_code:'test',country_code:'CN',is_demo:true});
    const version=(await db.query(`INSERT INTO esg.dataset_versions(dictionary_version,content_digest) VALUES($1,$2) RETURNING dataset_version`,[dictionary.dictionary_version,sha('comparison_fixture')])).rows[0].dataset_version;
    for(const [index,entity] of ['fixture_entity','fixture_other'].entries()) {
      const observation=randomUUID();
      const dimensions={entity_kind:'listed_company',energy_source:'all',energy_flow:'consumption',aggregation_role:'total',energy_accounting_basis:index===0?'basis_a':'basis_b'};
      await db.query(`INSERT INTO esg.curated_observations(observation_id,metric_id,definition_version,identity_key,entity_ref,period_start,period_end,boundary_code,
        value_role,measurement_method,dimensions,canonical_unit,numeric_value,precision_qualifier,comparison_eligibility,comparison_reasons,normalization_rule_version)
        VALUES($1,$2,$6,$3,$4,'2024-01-01','2024-12-31','own_operations','actual','fixture_method',$5,'MWh','10','exact','comparable','[]','fixture_rule')`,
        [observation,metric.metric_id,sha(entity),entity,dimensions,metric.definition_version]);
      await db.query('INSERT INTO esg.dataset_membership VALUES($1,$2)',[version,observation]);
    }
    await db.query('UPDATE esg.dataset_versions SET sealed=true WHERE dataset_version=$1',[version]);
    const compared=await dispatch(db,'comparisons',{metric_id:metric.metric_id,entity_refs:['fixture_entity','fixture_other'],dataset_version:Number(version),dimensions:{}});
    assert.equal(compared.rows.length,0);assert.equal(compared.available_cohorts.length,2);assert.equal(compared.excluded.length,2);
    assert.ok(compared.excluded.every(r=>r.reasons.includes('comparison_contract_required:multiple_cohorts')));
    const selected=await dispatch(db,'comparisons',{metric_id:metric.metric_id,entity_refs:['fixture_entity','fixture_other'],dataset_version:Number(version),dimensions:{energy_accounting_basis:'basis_a'}});
    assert.equal(selected.rows.length,1);assert.equal(selected.rows[0].entity_ref,'fixture_entity');
    assert.equal(selected.excluded[0].entity_ref,'fixture_other');
  }finally{await db.close();}
});

test('coverage decisions remain period-specific and explicitly current after a frozen publication snapshot',async()=>{
  const db=await setup();try {
    const snapshot=(await dispatch(db,'datasets',{})).latest;
    for(const [year,status] of [[2024,'not_applicable'],[2025,'not_disclosed_in_reviewed_scope']])
      await dispatch(db,'coverage_decision',{entity_ref:'fixture_entity',source_datapoint_id:'E1-5_01',status,
        period_start:`${year}-01-01`,period_end:`${year}-12-31`,evidence_scope:{basis:'synthetic scope for service regression only',pages:[1,2]},
        operator_ref:'auditor',reason:'明确标注的合成审核范围'});
    const overall=await dispatch(db,'internal_coverage',{dataset_version:snapshot,entity_ref:'fixture_entity'});
    assert.equal(overall.coverage_state,'current_audited_state');
    assert.ok(overall.coverage_as_of);assert.ok(overall.publication_as_of);
    const metric=overall.items.find(m=>m.source_datapoint_id==='E1-5_01');
    assert.deepEqual(metric.periods.map(p=>[p.period_start,p.status]),[['2024-01-01','not_applicable'],['2025-01-01','not_disclosed_in_reviewed_scope']]);
    const specific=await dispatch(db,'internal_coverage',{dataset_version:snapshot,entity_ref:'fixture_entity',period_start:'2025-01-01',period_end:'2025-12-31'});
    assert.equal(specific.items.find(m=>m.source_datapoint_id==='E1-5_01').status,'not_disclosed_in_reviewed_scope');
    assert.equal((await dispatch(db,'datasets',{})).latest,snapshot);
  }finally{await db.close();}
});

test('source lifecycle check identifies candidate bundle, IR and PDF references',async()=>{
  await withFixture(async(db,input)=>{
    assert.equal((await dispatch(db,'lifecycle_references',{job_ids:['synthetic_audit_job']})).blockers.length,0);
    await dispatch(db,'import',input);
    for(const scope of [{job_ids:['synthetic_audit_job']},{ir_run_ids:['synthetic_audit_ir']},{source_sha256:input.report_metadata.source_sha256}]) {
      const checked=await dispatch(db,'lifecycle_references',scope);
      assert.equal(checked.verified,true);assert.equal(checked.counts.candidate_count,1);
      assert.equal(checked.blockers[0].code,'candidate_source_reference');
      assert.equal((await dispatch(db,'lifecycle_reserve',{...scope,reason:'不能删除候选来源'})).reservation_id,undefined);
    }
  });
});

test('cleanup reservations block imports until source restore releases them',async()=>{
  await withFixture(async(db,input)=>{
    const reservation=await dispatch(db,'lifecycle_reserve',{job_ids:['synthetic_audit_job'],ir_run_ids:['synthetic_audit_ir'],reason:'回收未入库结果'});
    assert.ok(reservation.reservation_id);
    await dispatch(db,'lifecycle_reservation',{reservation_id:reservation.reservation_id,state:'quarantined',reason:'文件已进入回收区'});
    await assert.rejects(dispatch(db,'import',input),/正在回收/);
    const {loadBundle,importBundle}=await import('../../../ExtractESG/esg_database/scripts/import-bundle.mjs');
    const bundle=await loadBundle(input.bundle_path);
    await assert.rejects(importBundle(db,bundle,input.report_metadata),/正在回收/,'direct CLI import must obey the same source reservation');
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.import_batches')).rows[0].n,0);
    await dispatch(db,'lifecycle_reservation',{reservation_id:reservation.reservation_id,state:'released',reason:'来源已恢复'});
    assert.equal((await dispatch(db,'import',input)).imported,1);
  });
});

test('planning check cannot authorize cleanup after a candidate arrives',async()=>{
  await withFixture(async(db,input)=>{
    const scope={source_sha256:input.report_metadata.source_sha256};
    assert.equal((await dispatch(db,'lifecycle_references',scope)).blockers.length,0);
    await dispatch(db,'import',input);
    const reserved=await dispatch(db,'lifecycle_reserve',{...scope,reason:'执行时必须再次检查'});
    assert.equal(reserved.blockers[0].code,'candidate_source_reference');
    assert.equal((await db.query('SELECT count(*)::int AS n FROM plat.artifact_lifecycle_reservations')).rows[0].n,0);
  });
});

test('overlapping simultaneous cleanup commands acquire only one reservation',async()=>{
  const db=await setup();try {
    const scope={source_sha256:'a'.repeat(64),ir_run_ids:['fixture-ir'],reason:'并发回收测试'};
    const results=await Promise.all([dispatch(db,'lifecycle_reserve',scope),dispatch(db,'lifecycle_reserve',scope)]);
    assert.equal(results.filter(result=>result.reservation_id).length,1);
    assert.equal(results.filter(result=>result.blockers[0]?.code==='source_cleanup_reserved').length,1);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM plat.artifact_lifecycle_reservations')).rows[0].n,1);
  }finally{await db.close();}
});

test('stable reservation IDs are retryable and an early recovery release prevents late acquire',async()=>{
  const db=await setup();try {
    const scope={job_ids:['fixture-timeout-job'],reservation_id:randomUUID(),reason:'响应丢失后重试同一请求'};
    const first=await dispatch(db,'lifecycle_reserve',scope);
    const retry=await dispatch(db,'lifecycle_reserve',scope);
    assert.equal(first.reservation_id,scope.reservation_id);assert.equal(retry.reservation_id,scope.reservation_id);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM plat.artifact_lifecycle_reservations')).rows[0].n,1);
    await assert.rejects(dispatch(db,'lifecycle_reserve',{...scope,job_ids:['different-job']}),/身份冲突/);
    await dispatch(db,'lifecycle_reservation',{reservation_id:scope.reservation_id,state:'released',reason:'恢复未确认的请求'});
    await assert.rejects(dispatch(db,'lifecycle_reserve',scope),/请求已结束/);
    const lateId=randomUUID();
    const released=await dispatch(db,'lifecycle_reservation',{reservation_id:lateId,state:'released',reason:'恢复命令先于延迟的取得请求'});
    assert.equal(released.state,'released');
    await assert.rejects(dispatch(db,'lifecycle_reserve',{...scope,reservation_id:lateId}),/请求已结束/);
    assert.equal((await db.query("SELECT count(*)::int AS n FROM plat.artifact_lifecycle_reservations WHERE state<>'released'")).rows[0].n,0);
  }finally{await db.close();}
});

test('published source references remain protected independently of current review decisions',async()=>{
  const db=await setup();const previous=process.env.PLATESG_ALLOW_DEMO_SEED;process.env.PLATESG_ALLOW_DEMO_SEED='1';
  try {
    await dispatch(db,'seed_demo',{});
    const source=(await db.query('SELECT source_sha256 FROM esg.source_report_refs LIMIT 1')).rows[0];
    const checked=await dispatch(db,'lifecycle_references',{source_sha256:source.source_sha256});
    assert.equal(checked.blockers[0].code,'published_source_reference');
    assert.ok(checked.counts.published_count>0);
    await db.query("UPDATE esg.esg_disclosures SET quality_status='rejected'");
    assert.equal((await dispatch(db,'lifecycle_references',{source_sha256:source.source_sha256})).blockers[0].code,'published_source_reference');
  }finally{await db.close();if(previous===undefined)delete process.env.PLATESG_ALLOW_DEMO_SEED;else process.env.PLATESG_ALLOW_DEMO_SEED=previous;}
});
