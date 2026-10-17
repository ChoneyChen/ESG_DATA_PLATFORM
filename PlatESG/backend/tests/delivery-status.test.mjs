import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,cp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {dispatch,dictionaryFile,platformRoot} from '../service.mjs';
import {applyPlatformMigrations} from '../database.mjs';
import {applyMigrations} from '../../../database/scripts/db.mjs';
import {seedMetricDictionary,processOutbox} from '../../../database/scripts/publication.mjs';
import {demoContext} from '../demo.mjs';
import {readDeliveryStatus} from '../delivery_status.mjs';
import {loadBundle} from '../../../database/scripts/import-bundle.mjs';
const requireDB=createRequire(path.join(platformRoot,'../database/package.json'));
const {PGlite}=await import(pathToFileURL(requireDB.resolve('@electric-sql/pglite')).href);
const sha=value=>createHash('sha256').update(value).digest('hex');
const native='report-'+'a'.repeat(32),sourceSha=sha('explicitly synthetic report bytes');
const metadata={upstream_system:'isolated-status-fixture',organization_ref:'status-company',report_ref:'status-report',report_version_ref:'status-report:asset:1',
  source_artifact_ref:'pdf-'+sourceSha.slice(0,24),source_sha256:sourceSha,report_period_start:'2025-01-01',report_period_end:'2025-12-31'};
const fixed={document_id:'synthetic-status-document',ir_run_id:'synthetic-status-ir',ir_revision:1};
async function fixture(output,{job='tx-status-first',core=false,parent=native}={}) {
  const root=path.join(output,job);await mkdir(path.join(root,'input'),{recursive:true});await mkdir(path.join(root,'results'),{recursive:true});
  const dict=await dictionaryFile(),metric=dict.metrics.find(m=>m.source_datapoint_id==='E1-5_01'),binding=metric.standard_binding;
  const standard=JSON.parse(await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/dist',binding.package_id,binding.package_version,'package.json'),'utf8'));
  const selected=['E1-5_01','E1-5_05'].map(id=>dict.metrics.find(m=>m.source_datapoint_id===id).standard_binding.metric_id);
  const request={package_id:binding.package_id,package_version:binding.package_version,metric_ids:selected,report_session_id:parent,ir_run_id:fixed.ir_run_id};
  const manifest={job_id:job,report_session_id:parent,document:fixed,execution_status:'completed',schema_version:'targeted-extraction-result-v1',
    bundle_layout_version:'targeted-result-bundle-v1',contract_validation:{status:'passed',basis:'synthetic_software_fixture'}};
  await writeFile(path.join(root,'manifest.json'),JSON.stringify(manifest));
  await writeFile(path.join(root,'input/request.json'),JSON.stringify(request));await writeFile(path.join(root,'input/standard-package.json'),JSON.stringify(standard));
  await writeFile(path.join(root,'input/ir-manifest.json'),JSON.stringify({document_id:fixed.document_id,run_id:fixed.ir_run_id,ir_revision:1,source:{pdf_sha256:sourceSha}}));
  const facts=[0,1,2].map(i=>({source_fact_id:'synthetic-shared-'+i,kind:'quantitative',...fixed,quality_status:'needs_confirmation',identity_raw:{},
    value_raw:String(20-i*5),unit_raw:'MWh',source_row_label:'虚构指标'+i,physical_sources:[{ir_object_id:'synthetic-cell-'+i,pdf_page_index:0}]}));
  const rows=core?selected.map((metric_id,i)=>({observation_id:'synthetic-core-'+i,metric_id,value_raw:facts[i].value_raw,value_numeric:facts[i].value_raw,unit_raw:'MWh',comparator:'exact'})):[];
  const evidence=rows.map((row,i)=>({evidence_id:'synthetic-evidence-'+i,source_record_id:row.observation_id,document_id:fixed.document_id,
    ir_run_id:fixed.ir_run_id,ir_revision:1,ir_object_id:'synthetic-cell-'+i,pdf_page_index:0,evidence_excerpt:'Explicit synthetic software fixture value '+facts[i].value_raw}));
  const files={'reporting-tasks':[],'quantitative-observations':rows,'qualitative-assertions':[],'dimension-values':[],'attribute-values':[],'evidence-references':evidence};
  for(const [file,records] of Object.entries(files))await writeFile(path.join(root,'results',file+'.jsonl'),records.map(r=>JSON.stringify(r)).join('\n'));
  await writeFile(path.join(root,'results/source-facts.json'),JSON.stringify({facts}));
  await writeFile(path.join(root,'results/metric-source-mappings.json'),JSON.stringify({core_bindings:rows.map((row,i)=>({core_record_id:row.observation_id,source_fact_id:facts[i].source_fact_id,metric_id:row.metric_id}))}));
  return {root,input:{bundle_path:root,report_metadata:metadata},job,manifest};
}
async function query(f,{otherJobs=[]}={}) {
  const m=JSON.parse(await readFile(path.join(f.root,'manifest.json'),'utf8'));
  return {native_session_id:native,report_identity:metadata,fixed_source:fixed,job_ids:[f.job,...otherJobs],artifacts:[{
    job_id:f.job,artifact_revision_id:m.artifact_revision?.revision_id || 'original',bundle_path:f.root,manifest_sha256:sha(await readFile(path.join(f.root,'manifest.json')))}]};
}
async function withFixture(callback,options={}) {
  const output=await mkdtemp(path.join(tmpdir(),'delivery-status-isolated-')),old=process.env.PLATESG_IMPORT_ROOTS;
  process.env.PLATESG_IMPORT_ROOTS=output;
  const db=await PGlite.create();
  try{await applyMigrations(db);await applyPlatformMigrations(db);const {file_sha256,...dict}=await dictionaryFile();await seedMetricDictionary(db,dict);await processOutbox(db);
    await dispatch(db,'register_entity',{entity_ref:metadata.organization_ref,name_zh:'明确虚构的软件测试企业',entity_kind:'group',industry_code:'test',country_code:'CN',is_demo:true});
    await callback(db,await fixture(output,options),output);
  }finally{await db.close();await rm(output,{recursive:true,force:true});if(old===undefined)delete process.env.PLATESG_IMPORT_ROOTS;else process.env.PLATESG_IMPORT_ROOTS=old;}
}
const count=async(db,table)=>(await db.query('SELECT count(*)::int AS n FROM '+table)).rows[0].n;
const expected={archived_source_count:3,selected_candidate_count:0,internal_candidate_count:0,published_observation_count:0};

test('source-only official receipts survive refresh with real SQL counts, partial child and read-only queries',async()=>withFixture(async(db,f)=>{
  const first=await dispatch(db,'deliver',f.input),original=(await db.query('SELECT * FROM plat.import_receipts')).rows;
  assert.equal(first.imported,0);assert.equal(first.delivery_identity.native_session_id,native);
  const before=await dispatch(db,'datasets',{}),events=await count(db,'esg.outbox_events');
  const readOnly={transaction:cb=>db.transaction(tx=>cb(new Proxy(tx,{get(t,k){if(k==='query')return async(sql,args)=>{
    assert.match(sql.trim(),/^(SELECT|WITH|SET TRANSACTION)\b/i);return t.query(sql,args);};const v=t[k];return typeof v==='function'?v.bind(t):v;}})))};
  const result=await dispatch(readOnly,'delivery_status',await query(f,{otherJobs:['tx-status-not-entered']}));
  assert.equal(result.status,'partial');assert.deepEqual(result.session_counts,expected);assert.deepEqual(result.current_revision_counts,expected);
  assert.equal(result.jobs[0].status,'source_archived');assert.equal(result.jobs[0].artifacts[0].identity_basis,'verified_descriptor');
  assert.equal(result.jobs[1].status,'no_artifact');assert.deepEqual(result.jobs[1].counts,{...expected,archived_source_count:0});
  assert.deepEqual(await dispatch(db,'datasets',{}),before);assert.equal(await count(db,'esg.outbox_events'),events);
  assert.deepEqual((await db.query('SELECT * FROM plat.import_receipts')).rows,original);
}));

test('historical receipt without descriptor/source count uses exact SQL batch-stage binding, without history rewrite',async()=>withFixture(async(db,f)=>{
  const imported=await dispatch(db,'import',f.input),oldDetail={imported:0,idempotent:true,import_batch_id:imported.import_batch_id};
  const old=await dispatch(db,'receipt',{receipt_id:'b'.repeat(64),status:'imported',import_batch_id:imported.import_batch_id,detail:oldDetail});
  const result=await dispatch(db,'delivery_status',await query(f));assert.equal(result.status,'source_archived');
  assert.deepEqual(result.current_revision_counts,expected);assert.equal(result.jobs[0].artifacts[0].identity_basis,'sql_batch_stage_match');
  assert.deepEqual(await dispatch(db,'receipt',{receipt_id:old.receipt_id}),{...old,attempts:[],current_status:'imported',current_detail:old.detail,current_import_batch_id:old.import_batch_id});
}));

test('committed batch without acknowledgement and failed then successful retry keep distinct status and immutable audit',async()=>withFixture(async(db,f)=>{
  const fail={query:db.query.bind(db),transaction:cb=>db.transaction(tx=>cb(new Proxy(tx,{get(t,k){if(k==='query')return async(sql,args)=>{
    if(sql.includes('INSERT INTO esg.report_source_fact_snapshots'))throw new Error('isolated commit interruption');return t.query(sql,args);};const v=t[k];return typeof v==='function'?v.bind(t):v;}})))};
  const failed=await dispatch(fail,'deliver',f.input);assert.equal(failed.status,'failed');
  const readFailed=await dispatch(db,'delivery_status',await query(f));assert.equal(readFailed.status,'failed');assert.equal(readFailed.current_revision_counts.archived_source_count,0);
  const historical=(await db.query('SELECT * FROM plat.import_receipts')).rows;
  await dispatch(db,'import',f.input);
  const unconfirmed=await dispatch(db,'delivery_status',await query(f));assert.equal(unconfirmed.status,'committed_without_acknowledgement');assert.deepEqual(unconfirmed.current_revision_counts,expected);
  const retried=await dispatch(db,'retry_receipt',{receipt_id:failed.receipt_id,operator_ref:'isolated-reviewer',reason:'软件回放明确重试'});assert.equal(retried.current_status,'imported');
  const confirmed=await dispatch(db,'delivery_status',await query(f));assert.equal(confirmed.status,'source_archived');assert.deepEqual(confirmed.session_counts,expected);
  assert.ok(confirmed.jobs[0].artifacts[0].receipt.attempt_id);assert.deepEqual((await db.query('SELECT * FROM plat.import_receipts')).rows,historical);
  assert.equal((await dispatch(db,'deliver',f.input)).imported,0);
}));

test('shared physical sources across exact child batches count once; foreign parent is never silently included',async()=>withFixture(async(db,f,output)=>{
  await dispatch(db,'deliver',f.input);const second=await fixture(output,{job:'tx-status-second'});await dispatch(db,'deliver',second.input);
  const scope=await query(f,{otherJobs:[second.job]});scope.artifacts.push((await query(second)).artifacts[0]);
  const result=await dispatch(db,'delivery_status',scope);assert.deepEqual(result.session_counts,expected);assert.equal(await count(db,'esg.import_source_facts'),6);
  const foreign=await fixture(output,{job:'tx-status-foreign',parent:'report-'+'c'.repeat(32)});await dispatch(db,'deliver',foreign.input);
  const wrong=await query(f,{otherJobs:[foreign.job]});const uncertain=await dispatch(db,'delivery_status',wrong);
  assert.equal(uncertain.status,'unknown');assert.equal(uncertain.session_counts.archived_source_count,null);assert.ok(uncertain.reason_codes.includes('historical_binding_unverified'));
}));

test('revision identity cannot borrow an earlier receipt even when semantic Bundle digest is equal',async()=>withFixture(async(db,f)=>{
  await dispatch(db,'deliver',f.input);
  const basis=Buffer.from(JSON.stringify({classification:'synthetic_revision_fixture',revision:2})),digest=sha(basis),id='partial-'+digest.slice(0,32),root=path.join(f.root,'terminal-revisions',id);
  await mkdir(root,{recursive:true});
  for(const name of ['input','results','manifest.json'])await cp(path.join(f.root,name),path.join(root,name),{recursive:true});
  const m={...f.manifest,artifact_revision:{revision_id:id,materialization_digest:digest}};
  await writeFile(path.join(root,'manifest.json'),JSON.stringify(m));await writeFile(path.join(root,'input/materialization-basis.json'),basis);
  await writeFile(path.join(root,'artifact-revision.json'),JSON.stringify({schema_version:'terminal-result-revision-v1',job_id:f.job,revision_id:id,materialization_digest:digest,
    artifact_sha256:{'manifest.json':sha(await readFile(path.join(root,'manifest.json'))),'input/materialization-basis.json':digest},frozen_input_sha256:{}}));
  const next={...f,root,input:{...f.input,bundle_path:root}};const scope=await query(next);
  assert.equal((await dispatch(db,'delivery_status',scope)).jobs[0].status,'not_imported');
  await dispatch(db,'import',next.input);assert.equal((await dispatch(db,'delivery_status',scope)).jobs[0].status,'committed_without_acknowledgement');
  await dispatch(db,'deliver',next.input);const yes=await dispatch(db,'delivery_status',scope);
  assert.equal(yes.status,'source_archived');assert.equal(yes.current_revision_counts.archived_source_count,3);assert.equal(await count(db,'esg.import_batches'),1);
}));

test('selected candidate and internal role classification comes from persisted bindings, never insertion delta',async()=>withFixture(async(db,f)=>{
  const delivered=await dispatch(db,'deliver',f.input);assert.equal(delivered.imported,2);
  const repeated=await dispatch(db,'deliver',f.input);assert.equal(repeated.idempotent,true);
  const map=(await db.query('SELECT disclosure_standard_mapping_id,mapping_reason FROM esg.disclosure_standard_mappings ORDER BY disclosure_standard_mapping_id')).rows[0];
  const reason=JSON.parse(map.mapping_reason);reason.candidate_scope_role='internal_dependency';
  await db.query('UPDATE esg.disclosure_standard_mappings SET mapping_reason=$1 WHERE disclosure_standard_mapping_id=$2',[JSON.stringify(reason),map.disclosure_standard_mapping_id]);
  const result=await dispatch(db,'delivery_status',await query(f));assert.equal(result.status,'candidates_imported');
  assert.equal(result.current_revision_counts.selected_candidate_count,1);assert.equal(result.current_revision_counts.internal_candidate_count,1);
  await db.query('UPDATE esg.disclosure_standard_mappings SET mapping_reason=$1 WHERE disclosure_standard_mapping_id=$2',['历史未结构化映射说明',map.disclosure_standard_mapping_id]);
  const unknown=await dispatch(db,'delivery_status',await query(f));assert.equal(unknown.current_revision_counts.selected_candidate_count,null);
  assert.ok(unknown.reason_codes.includes('candidate_scope_unclassified'));assert.equal(unknown.current_revision_counts.archived_source_count,3);
},{core:true}));

test('wrong report/PDF/IR/parent/manifest/path and missing artifacts remain unknown rather than invented zero',async()=>withFixture(async(db,f)=>{
  await dispatch(db,'deliver',f.input);const scope=await query(f);
  for(const change of [q=>q.report_identity.source_sha256='d'.repeat(64),q=>q.fixed_source.ir_revision=2,q=>q.native_session_id='report-'+'e'.repeat(32),q=>q.artifacts[0].manifest_sha256='f'.repeat(64)]) {
    const q=structuredClone(scope);change(q);const result=await dispatch(db,'delivery_status',q);
    assert.ok(['identity_mismatch','unknown'].includes(result.status));assert.equal(result.current_revision_counts.archived_source_count,null);
  }
  const missing=structuredClone(scope);missing.artifacts[0].bundle_path=path.join(path.dirname(f.root),'tx-nonexistent');
  const unavailable=await dispatch(db,'delivery_status',missing);assert.equal(unavailable.jobs[0].status,'artifact_unavailable');assert.equal(unavailable.current_revision_counts.archived_source_count,null);
  const wrong=structuredClone(scope);wrong.artifacts.push(wrong.artifacts[0]);await assert.rejects(dispatch(db,'delivery_status',wrong),e=>e.code==='invalid_request');
  const reportMismatch=structuredClone(scope);reportMismatch.report_identity.organization_ref='different-report-publisher';
  const mismatch=await dispatch(db,'delivery_status',reportMismatch);
  // The facade validates the complete original child scope even when the SQL
  // report identity is wrong. Preserve that cause instead of an invalid DTO.
  assert.equal(mismatch.status,'identity_mismatch');assert.deepEqual(mismatch.jobs.map(j=>j.job_id),scope.job_ids);
  assert.equal(mismatch.jobs[0].status,'identity_mismatch');assert.equal(mismatch.jobs[0].counts.archived_source_count,null);
  for(const extra of [{...scope,operator_ref:'forged'},{...scope,report_identity:{...scope.report_identity,actor:'forged'}},
    {...scope,artifacts:[{...scope.artifacts[0],saved_source_fact_count:999}]}])
    await assert.rejects(dispatch(db,'delivery_status',extra),e=>e.code==='invalid_request');
}));

test('published observation count follows latest sealed dataset SQL lineage and does not change frozen earlier versions',async()=>withFixture(async(db,f)=>{
  await dispatch(db,'deliver',f.input);
  const dict=await dictionaryFile(),candidates=(await dispatch(db,'candidates',{report_ref:metadata.report_ref})).items;
  for(const c of candidates) {
    await dispatch(db,'review',{disclosure_id:c.disclosure_id,decision:'approved',operator_ref:'isolated-reviewer',reason:'合成软件证据明确审核'});
    await dispatch(db,'review',{mapping_id:c.mapping_id,decision:'approved',mapping_relation:'exact',satisfaction_status:'supports',operator_ref:'isolated-reviewer',reason:'合成软件语义明确审核'});
    const definition=dict.metrics.find(m=>m.source_datapoint_id===c.source_datapoint_id);
    await dispatch(db,'context',demoContext(definition,c.mapping_id,metadata.organization_ref,2025));
  }
  await processOutbox(db);
  const status=await dispatch(db,'delivery_status',await query(f));
  assert.equal(status.current_revision_counts.published_observation_count,2);
  assert.equal(status.publication_dataset_version,(await dispatch(db,'datasets',{})).latest);
  const earlier=(await dispatch(db,'datasets',{})).items.at(-1).dataset_version;
  assert.equal((await dispatch(db,'observations',{dataset_version:earlier})).items.length,0);
},{core:true}));

test('manifest mutation between SHA verification and Bundle reread is rejected without SQL or receipt changes',async()=>withFixture(async(db,f,output)=>{
  await dispatch(db,'deliver',f.input);const scope=await query(f),file=path.join(f.root,'manifest.json'),before=await readFile(file);
  const receipts=(await db.query('SELECT * FROM plat.import_receipts')).rows;
  try {
    const result=await readDeliveryStatus(db,scope,{importRoots:[output],bundleLoader:async root=>{
      await writeFile(file,JSON.stringify({...JSON.parse(before),generated_at:'changed-during-read'}));
      return loadBundle(root);
    }});
    assert.equal(result.status,'identity_mismatch');assert.equal(result.current_revision_counts.archived_source_count,null);
    assert.equal(result.jobs[0].artifacts[0].identity_verified,false);
    assert.deepEqual((await db.query('SELECT * FROM plat.import_receipts')).rows,receipts);
  }finally{await writeFile(file,before);}
}));

test('anonymous request without fixed parent stays unverified and excessive stage history cannot invent a partial total',async()=>withFixture(async(db,f,output)=>{
  const anonymous=await fixture(output,{job:'tx-status-anonymous',parent:null});
  const unknown=await dispatch(db,'delivery_status',await query(anonymous));
  assert.equal(unknown.status,'identity_mismatch');assert.equal(unknown.current_revision_counts.archived_source_count,null);
  const delivered=await dispatch(db,'deliver',f.input);
  await db.query(`INSERT INTO plat.import_stage_events(import_batch_id,manifest_digest,execution_status,result_state,manifest_snapshot)
    SELECT $1,repeat('0',60)||lpad(to_hex(n),4,'0'),'interrupted','partial',$2 FROM generate_series(1,101) n`,
    [delivered.import_batch_id,f.manifest]);
  const bounded=await dispatch(db,'delivery_status',await query(f));
  assert.equal(bounded.status,'unknown');assert.equal(bounded.session_counts.archived_source_count,null);
  assert.equal(bounded.current_revision_counts.archived_source_count,null);assert.equal(bounded.report_counts.archived_source_count,3);
  assert.ok(bounded.reason_codes.includes('scope_limit_exceeded'));
}));
