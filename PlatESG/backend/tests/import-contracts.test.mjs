import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {randomUUID,createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {dispatch,dictionaryFile,platformRoot} from '../service.mjs';
import {applyPlatformMigrations} from '../database.mjs';
import {applyMigrations} from '../../../database/scripts/db.mjs';
import {loadBundle} from '../../../database/scripts/import-bundle.mjs';
import {seedMetricDictionary,processOutbox} from '../../../database/scripts/publication.mjs';
import {loadAllowedImportContract,resolveImportCatalog,validateMajorImportScope} from '../import_contracts.mjs';

const requireDB=createRequire(path.join(platformRoot,'../database/package.json'));
const {PGlite}=await import(pathToFileURL(requireDB.resolve('@electric-sql/pglite')).href);
const id='esrs-platform-major.dictionary-1.0.0';
const allowed=await loadAllowedImportContract(id);
const frozenRoot=process.env.ESG_TEST_HISTORICAL_BUNDLE;
const legacyRoot=process.env.ESG_TEST_LEGACY_BUNDLE;
// Explicitly configured missing/malformed artifacts fail instead of being skipped.
if(frozenRoot)assert.equal((await loadBundle(frozenRoot)).request.selection_snapshot.dictionary_version,'1.0.0');
if(legacyRoot)assert.equal((await loadBundle(legacyRoot)).request.selection_snapshot,null);
const registration={contract_id:id,expected_release_sha256:allowed.release_sha256,operator_ref:'isolated-admin',reason:'仅隔离回放登记原始历史实物，不激活旧字典'};
const metadata={upstream_system:'isolated-real-artifact-replay',organization_ref:'isolated-cnpc-group',report_ref:'isolated-cnpc-report',
  report_version_ref:'isolated-cnpc-report-v1',source_artifact_ref:'pdf-807cc03cca274a6dea6ab7e93',
  source_sha256:'807cc03cca274a6dea6ab7e938d78cf6f2db26411d1339a7db54b64695173ac9',
  report_title:'中国石油天然气集团有限公司2025年企业社会责任报告（仅隔离回放）'};
async function setup() {
  const db=await PGlite.create();await applyMigrations(db);await applyPlatformMigrations(db);
  const {file_sha256,...dictionary}=await dictionaryFile();await seedMetricDictionary(db,dictionary);await processOutbox(db);
  await dispatch(db,'register_entity',{entity_ref:metadata.organization_ref,name_zh:'隔离回放中国石油集团',entity_kind:'group',industry_code:'oil',country_code:'CN',is_demo:false});
  return db;
}
async function counts(db) {return (await db.query(`SELECT
  (SELECT count(*) FROM esg.import_batches)::int AS batches,
  (SELECT count(*) FROM esg.report_source_fact_snapshots)::int AS sources,
  (SELECT count(*) FROM esg.esg_disclosures)::int AS candidates,
  (SELECT count(*) FROM esg.curated_observations)::int AS observations`)).rows[0];}
async function withDB(fn) {
  const db=await setup(),before=process.env.PLATESG_IMPORT_ROOTS;
  const roots=[frozenRoot,legacyRoot].filter(Boolean).map(root=>path.resolve(root));
  if(roots.length)process.env.PLATESG_IMPORT_ROOTS=roots.join(path.delimiter);
  try{await fn(db);}finally{await db.close();if(before===undefined)delete process.env.PLATESG_IMPORT_ROOTS;else process.env.PLATESG_IMPORT_ROOTS=before;}
}
const input={bundle_path:frozenRoot,report_metadata:metadata};

test('approved import-only historical artifacts preserve original file SHA, membership and package versions',async()=>{
  assert.equal(allowed.dictionary_file_sha256,'befc855b58b6359ea125ca97a44b094d1138a36de68445cd6dea9d88f55bd631');
  assert.equal(allowed.profile_file_sha256,'d7ad96e459edbef7b89b1582bb93709d08711b1d6721d492468a20e54aa1cdbe');
  assert.equal(allowed.ready_source_datapoint_ids.length,16);
  assert.equal(allowed.package_bindings['esrs.2023-set1.e1-5'].package_version,'2.1.1');
  await assert.rejects(loadAllowedImportContract('../../metric-dictionary.v1.json'),e=>e.code==='import_catalog_unregistered');
});

test('import-only registration is immutable and never changes active publication dictionary, datasets or events',async()=>{
  await withDB(async db=>{
    const before=(await db.query('SELECT * FROM esg.dictionary_versions ORDER BY release_order')).rows;
    const datasets=await dispatch(db,'datasets',{}),events=(await db.query('SELECT * FROM esg.outbox_events ORDER BY event_id')).rows;
    const first=await dispatch(db,'register_import_catalog',registration);
    assert.equal(first.permission,'import_only');
    const repeated=await dispatch(db,'register_import_catalog',{...registration,reason:'幂等重试保留首次审核'});
    assert.equal(repeated.reason,registration.reason);
    assert.deepEqual((await db.query('SELECT * FROM esg.dictionary_versions ORDER BY release_order')).rows,before);
    assert.deepEqual(await dispatch(db,'datasets',{}),datasets);
    assert.deepEqual((await db.query('SELECT * FROM esg.outbox_events ORDER BY event_id')).rows,events);
    await assert.rejects(db.query("UPDATE plat.import_contract_releases SET reason='overwrite'"),/immutable import-only/);
    await assert.rejects(db.query('DELETE FROM plat.import_contract_releases'),/immutable import-only/);
    await assert.rejects(dispatch(db,'register_import_catalog',{...registration,expected_release_sha256:'0'.repeat(64)}),e=>e.code==='import_catalog_digest_mismatch');
    await assert.rejects(dispatch(db,'register_import_catalog',{...registration,dictionary_path:'/tmp/untrusted.json'}),e=>e.code==='import_catalog_invalid');
  });
});

test('current frozen major scope remains accepted without historical registration (synthetic protocol fixture)',async()=>{
  await withDB(async db=>{
    const directory=await mkdtemp(path.join(tmpdir(),'esg-current-frozen-scope-'));
    const previous=process.env.PLATESG_IMPORT_ROOTS;process.env.PLATESG_IMPORT_ROOTS=directory;
    try {
      const dictionary=await dictionaryFile(),metric=dictionary.metrics.find(m=>m.source_datapoint_id==='E1-5_01'),binding=metric.standard_binding;
      const profileBytes=await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/profiles/esrs/2023-set1/platform-major.json'));
      const profile=JSON.parse(profileBytes),sha=value=>createHash('sha256').update(value).digest('hex');
      const scope={profile_id:profile.profile_id,profile_version:profile.profile_version,profile_digest:sha(profileBytes),
        dictionary_version:dictionary.dictionary_version,dictionary_digest:dictionary.file_sha256,
        source_datapoint_ids:[metric.source_datapoint_id],package_digests:{[binding.package_id]:binding.digest},selection_kind:'platform_major'};
      const standard=JSON.parse(await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/dist',binding.package_id,binding.package_version,'package.json'),'utf8'));
      await mkdir(path.join(directory,'input'));await mkdir(path.join(directory,'results'));
      await writeFile(path.join(directory,'manifest.json'),JSON.stringify({job_id:'synthetic-current-frozen-scope',execution_status:'partial',
        schema_version:'targeted-extraction-result-v1',bundle_layout_version:'targeted-result-bundle-v1',contract_validation:{status:'passed',basis:'synthetic_protocol_fixture'}}));
      await writeFile(path.join(directory,'input/request.json'),JSON.stringify({ir_run_id:'synthetic-ir',package_id:binding.package_id,
        package_version:binding.package_version,metric_ids:[binding.metric_id],selection_snapshot:scope}));
      await writeFile(path.join(directory,'input/standard-package.json'),JSON.stringify(standard));
      for(const name of ['reporting-tasks','quantitative-observations','qualitative-assertions','attribute-values','dimension-values','evidence-references'])
        await writeFile(path.join(directory,'results',name+'.jsonl'),'');
      const result=await dispatch(db,'import',{bundle_path:directory,report_metadata:{...metadata,report_ref:'synthetic-current-scope',
        report_title:'仅当前范围软件合同回归（合成，无事实）',source_artifact_ref:'synthetic://current-scope',source_sha256:sha('synthetic-current-scope')}});
      assert.equal(result.imported,0);assert.equal(result.import_contract.permission,'current_import');
      assert.equal(result.import_contract.scope_provenance,'bundle_frozen');
      assert.deepEqual(result.selection_snapshot,scope);
      assert.equal((await dispatch(db,'import_catalogs',{})).items.length,0);
    } finally {await rm(directory,{recursive:true,force:true});if(previous===undefined)delete process.env.PLATESG_IMPORT_ROOTS;else process.env.PLATESG_IMPORT_ROOTS=previous;}
  });
});

test('actual old frozen Bundle first delivery succeeds only after historical registration, without invented facts',{skip:!frozenRoot},async()=>{
  await withDB(async db=>{
    const original=await loadBundle(frozenRoot);
    assert.equal(original.request.selection_snapshot.dictionary_version,'1.0.0');
    assert.equal(original.sourceFacts.facts.length,0);assert.equal(original.selectedRecords.quantitative_observations.length,0);
    await assert.rejects(dispatch(db,'import',input),e=>e.code==='import_catalog_unregistered');
    assert.deepEqual(await counts(db),{batches:0,sources:0,candidates:0,observations:0});
    await dispatch(db,'register_import_catalog',registration);
    const imported=await dispatch(db,'deliver',input);
    assert.equal(imported.status,'imported');assert.equal(imported.imported,0);
    assert.equal(imported.import_contract.permission,'import_only');
    assert.equal(imported.import_contract.dictionary_version,'1.0.0');
    assert.equal(imported.import_contract.scope_provenance,'bundle_frozen');
    assert.deepEqual(imported.selection_snapshot,original.request.selection_snapshot);
    assert.equal((await dispatch(db,'deliver',input)).idempotent,true);
    assert.deepEqual(await counts(db),{batches:1,sources:0,candidates:0,observations:0});
    assert.equal((await db.query('SELECT dictionary_version FROM esg.dictionary_versions ORDER BY release_order DESC LIMIT 1')).rows[0].dictionary_version,'1.2.0');
  });
});

test('actual frozen Bundle failed receipt can retry after registration, importing exactly one batch',{skip:!frozenRoot},async()=>{
  await withDB(async db=>{
    const failed=await dispatch(db,'deliver',input);
    assert.equal(failed.status,'failed');assert.equal(failed.detail.code,'import_catalog_unregistered');
    await dispatch(db,'register_import_catalog',registration);
    const retried=await dispatch(db,'retry_receipt',{receipt_id:failed.receipt_id,operator_ref:'isolated-admin',reason:'登记后重试相同交付'});
    assert.equal(retried.current_status,'imported');
    assert.equal((await dispatch(db,'deliver',input)).idempotent,true);
    assert.equal((await counts(db)).batches,1);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM plat.import_receipts')).rows[0].n,1);
  });
});

test('external scope cannot replace original frozen scope, including a claimed current dictionary',{skip:!frozenRoot},async()=>{
  await withDB(async db=>{
    const old=(await loadBundle(frozenRoot)).request.selection_snapshot;
    for(const changed of [{...old,dictionary_version:'1.2.0'}, {...old,source_datapoint_ids:[...old.source_datapoint_ids,'S1-6_02']},
      {...old,package_digests:{...old.package_digests,'esrs.2023-set1.e1-5':'0'.repeat(64)}}])
      await assert.rejects(dispatch(db,'import',{...input,selection_snapshot:changed}),e=>e.code==='import_scope_conflict');
    assert.equal((await counts(db)).batches,0);
  });
});

test('an external historical scope on a legacy Bundle is saved as externally declared, never as original frozen scope',{skip:!frozenRoot||!legacyRoot},async()=>{
  await withDB(async db=>{
    await dispatch(db,'register_import_catalog',registration);
    const supplied=(await loadBundle(frozenRoot)).request.selection_snapshot;
    const imported=await dispatch(db,'import',{bundle_path:legacyRoot,report_metadata:metadata,selection_snapshot:supplied});
    assert.equal(imported.imported,5);
    assert.equal(imported.import_contract.scope_provenance,'externally_declared');
    assert.equal(imported.selection_snapshot.scope_provenance,'externally_declared');
    assert.equal(imported.selection_snapshot.dictionary_version,'1.0.0');
    assert.equal((await loadBundle(legacyRoot)).request.selection_snapshot,null);
    assert.equal((await db.query('SELECT scope_snapshot FROM plat.extraction_scopes')).rows[0].scope_snapshot.scope_provenance,'externally_declared');
  });
});

test('historical import preserves a nonempty frozen public dataset and its exact original DTO',{skip:!frozenRoot},async()=>{
  await withDB(async db=>{
    const dictionary=await dictionaryFile(),metric=dictionary.metrics.find(m=>m.source_datapoint_id==='E1-5_01');
    await dispatch(db,'register_entity',{entity_ref:'synthetic-frozen-observation',name_zh:'冻结观测软件回归样本（合成）',entity_kind:'listed_company',industry_code:'test',country_code:'CN',is_demo:true});
    const observation=randomUUID(),identity=createHash('sha256').update('synthetic-frozen-public-contract').digest('hex');
    await db.query(`INSERT INTO esg.curated_observations(observation_id,metric_id,definition_version,identity_key,entity_ref,period_start,period_end,boundary_code,
      value_role,measurement_method,dimensions,canonical_unit,numeric_value,precision_qualifier,comparison_eligibility,comparison_reasons,normalization_rule_version)
      VALUES($1,$2,$3,$4,'synthetic-frozen-observation','2024-01-01','2024-12-31','own_operations','actual','synthetic_test',
      $5,'MWh','123.4500','exact','not_comparable','["synthetic_fixture"]','synthetic-rule')`,
      [observation,metric.metric_id,metric.definition_version,identity,{energy_source:'all',energy_flow:'consumption',aggregation_role:'total'}]);
    const version=(await db.query(`INSERT INTO esg.dataset_versions(dictionary_version,content_digest) VALUES($1,$2) RETURNING dataset_version`,[dictionary.dictionary_version,identity])).rows[0].dataset_version;
    await db.query('INSERT INTO esg.dataset_membership VALUES($1,$2)',[version,observation]);
    await db.query('UPDATE esg.dataset_versions SET sealed=true WHERE dataset_version=$1',[version]);
    const before=await dispatch(db,'observations',{dataset_version:Number(version)});
    assert.equal(before.items.length,1);assert.equal(before.items[0].value_numeric,'123.4500');
    await dispatch(db,'register_import_catalog',registration);
    await dispatch(db,'deliver',input);
    assert.deepEqual(await dispatch(db,'observations',{dataset_version:Number(version)}),before);
  });
});

test('failure after actual legacy source archiving rolls back archive, candidates and scope together',{skip:!legacyRoot},async()=>{
  await withDB(async db=>{
    const broken={query:db.query.bind(db),transaction:callback=>db.transaction(tx=>callback(new Proxy(tx,{get(target,key){
      if(key==='query')return async(sql,args)=>{if(sql.includes('INSERT INTO plat.extraction_scopes'))throw new Error('isolated injected scope persistence failure');return target.query(sql,args);};
      const value=target[key];return typeof value==='function'?value.bind(target):value;}})))};
    await assert.rejects(dispatch(broken,'import',{bundle_path:legacyRoot,report_metadata:metadata}),/isolated injected/);
    assert.deepEqual(await counts(db),{batches:0,sources:0,candidates:0,observations:0});
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.source_report_refs')).rows[0].n,0);
  });
});

test('historical scope validates original profile SHA, ready membership and all declared package bindings',{skip:!frozenRoot},async()=>{
  await withDB(async db=>{
    await dispatch(db,'register_import_catalog',registration);
    const bundle=await loadBundle(frozenRoot),scope=bundle.request.selection_snapshot;
    const catalog=await resolveImportCatalog(db,scope,{});
    const originals=bundle.standard.metrics.filter(m=>bundle.request.metric_ids.includes(m.metric_id)).map(m=>m.source_datapoint_id);
    validateMajorImportScope(scope,catalog,bundle,originals);
    assert.throws(()=>validateMajorImportScope({...scope,profile_digest:'0'.repeat(64)},catalog,bundle,originals),e=>e.code==='import_catalog_digest_mismatch');
    assert.throws(()=>validateMajorImportScope({...scope,source_datapoint_ids:[...scope.source_datapoint_ids,'S1-6_02']},catalog,bundle,originals),e=>e.code==='import_scope_mismatch');
    assert.throws(()=>validateMajorImportScope({...scope,package_digests:{...scope.package_digests,'esrs.2023-set1.e2-4':'0'.repeat(64)}},catalog,bundle,originals),e=>e.code==='standard_snapshot_mismatch');
    await assert.rejects(resolveImportCatalog(db,{...scope,dictionary_digest:'0'.repeat(64)},{}),e=>e.code==='import_catalog_unregistered');
  });
});

test('actual nonempty legacy Bundle preserves its five source facts but cannot be rebound or published under new packages',{skip:!legacyRoot},async()=>{
  await withDB(async db=>{
    const before=await dispatch(db,'datasets',{}),original=await loadBundle(legacyRoot);
    assert.equal(original.request.selection_snapshot,null);assert.equal(original.sourceFacts.facts.length,5);
    const imported=await dispatch(db,'deliver',{bundle_path:legacyRoot,report_metadata:metadata});
    assert.equal(imported.status,'imported');assert.equal(imported.imported,5);
    assert.equal(imported.selection_snapshot.dictionary_version,'unassigned');
    assert.equal(imported.selection_snapshot.scope_provenance,'legacy_explicit_without_frozen_catalog');
    assert.equal(imported.import_contract.dictionary_version,null);
    assert.equal(imported.import_contract.scope_provenance,'legacy_explicit_without_frozen_catalog');
    assert.deepEqual(await counts(db),{batches:1,sources:5,candidates:5,observations:0});
    const archive=await dispatch(db,'source_facts',{report_ref:metadata.report_ref});
    for(const fact of original.sourceFacts.facts){const saved=archive.items.find(row=>row.source_fact_ref===fact.source_fact_id);
      assert.deepEqual(saved.identity_raw,fact.identity_raw);assert.equal(saved.value_raw,fact.value_raw);assert.equal(saved.unit_raw,fact.unit_raw);}
    // Isolated negative gate only: these artificial approvals are not a real report accuracy acceptance.
    const candidate=(await dispatch(db,'candidates',{report_ref:metadata.report_ref})).items.find(row=>row.source_datapoint_id==='E2-4_02');
    await dispatch(db,'review',{disclosure_id:candidate.disclosure_id,decision:'approved',operator_ref:'isolated-gate-test',reason:'仅隔离发布门禁负例，不代表真实来源审核通过'});
    await dispatch(db,'review',{mapping_id:candidate.mapping_id,decision:'approved',mapping_relation:'exact',satisfaction_status:'supports',operator_ref:'isolated-gate-test',reason:'仅隔离发布门禁负例'});
    await dispatch(db,'context',{mapping_id:candidate.mapping_id,entity_ref:metadata.organization_ref,period_start:'2025-01-01',period_end:'2025-12-31',
      boundary_code:'own_operations',value_role:'actual',measurement_method:null,dimensions:{pollutant:'nox',pollution_medium:'air'},reported_unit:'万吨',
      operator_ref:'isolated-gate-test',reason:'仅隔离发布门禁负例；上下文不是实际业务审核'});
    await processOutbox(db,{limit:1000});
    const state=(await db.query('SELECT reason_codes FROM esg.publication_states WHERE mapping_id=$1',[candidate.mapping_id])).rows[0];
    assert.ok(state.reason_codes.includes('standard_snapshot_mismatch'));
    assert.equal((await counts(db)).observations,0);
    assert.deepEqual(await dispatch(db,'datasets',{}),before);
    assert.deepEqual((await db.query('SELECT scope_snapshot FROM plat.extraction_scopes')).rows[0].scope_snapshot,imported.selection_snapshot);
  });
});
