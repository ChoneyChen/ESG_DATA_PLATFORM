// Explicit real v4 retention replay in an isolated in-memory PGlite database.
// This is an importer/API contract test, never report semantic approval.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp,cp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {dispatch,dictionaryFile,platformRoot} from '../service.mjs';
import {applyPlatformMigrations} from '../database.mjs';
import {applyMigrations} from '../../../database/scripts/db.mjs';
import {loadBundle} from '../../../database/scripts/import-bundle.mjs';
import {seedMetricDictionary,processOutbox} from '../../../database/scripts/publication.mjs';
import {saveExtensionProposal,recordExtensionDefinitionDecision,registerExtensionRelease} from '../../../database/scripts/extension-derivation.mjs';
const requireDB=createRequire(path.join(platformRoot,'../database/package.json'));
const {PGlite}=await import(pathToFileURL(requireDB.resolve('@electric-sql/pglite')).href);
const bundleRoot=process.env.ESG_TEST_TERMINAL_BUNDLE;
// A configured bad path is a hard failure, not a skip.
const actual=bundleRoot?await loadBundle(bundleRoot):null;
const metadata={upstream_system:'isolated-terminal-real-replay',organization_ref:'isolated-real-cnpc',
  report_ref:'isolated-real-v4-report',report_version_ref:'isolated-real-v4-report-v1',
  source_artifact_ref:'pdf-807cc03cca274a6dea6ab7e93',source_sha256:'807cc03cca274a6dea6ab7e938d78cf6f2db26411d1339a7db54b64695173ac9',
  report_title:'真实中国石油v4来源保全的隔离软件回放（未作语义审核）',
  report_period_start:'2025-01-01',report_period_end:'2025-12-31'};
const count=async(db,table)=>(await db.query(`SELECT count(*)::int AS n FROM ${table}`)).rows[0].n;
async function setup() {
  const db=await PGlite.create();await applyMigrations(db);await applyPlatformMigrations(db);
  const {file_sha256,...dictionary}=await dictionaryFile();await seedMetricDictionary(db,dictionary);
  for(const release of actual.runtimeSnapshot?.extension_releases || []) {
    // Copy the supplied approved release/receipt into an isolated registry.
    // No approval is created in the running database or asserted by this test.
    const proposal={schema_version:'project-extension-proposal-1.0',status:'proposed',
      proposal_id:release.approval.proposal_id,definition:release.definition,
      reason:'Isolated replay of the supplied immutable approved release'};
    await saveExtensionProposal(db,{proposal,submitted_by:'isolated-fixture-loader'});
    await recordExtensionDefinitionDecision(db,{receipt:release.approval},
      {actor_ref:release.approval.actor_ref,authority_roles:['approve_extension_definition']});
    await registerExtensionRelease(db,{release});
  }
  await processOutbox(db);
  await dispatch(db,'register_entity',{entity_ref:metadata.organization_ref,name_zh:'隔离回放真实报告主体',entity_kind:'group',
    industry_code:'oil',country_code:'CN',is_demo:false});
  return db;
}

test('actual v4 source-only terminal revision delivers 291 immutable candidates, retries atomically and never publishes',
  {skip:!actual},async()=>{
    assert.equal(actual.manifest.artifact_revision.source_only,true);
    assert.equal(actual.sourceFacts.facts.length,291);
    assert.equal(actual.records.quantitative_observations.length+actual.records.qualitative_assertions.length,0);
    assert.equal(actual.request.selection_snapshot.source_datapoint_ids.length,16);
    assert.equal(actual.runtimeSnapshot.selections.length,1);
    const db=await setup(),previous=process.env.PLATESG_IMPORT_ROOTS;
    process.env.PLATESG_IMPORT_ROOTS=path.resolve(bundleRoot);
    try {
      const frozenDatasets=await dispatch(db,'datasets',{});
      const frozenPublic=(await db.query('SELECT * FROM esg.curated_observations ORDER BY observation_id')).rows;
      const events=(await db.query('SELECT * FROM esg.outbox_events ORDER BY event_id')).rows;
      const input={bundle_path:bundleRoot,report_metadata:metadata,selection_snapshot:actual.request.selection_snapshot};
      let inserts=0;
      const failing={query:db.query.bind(db),transaction:callback=>db.transaction(tx=>callback(new Proxy(tx,{get(target,key){
        if(key==='query')return async(sql,args)=>{
          if(sql.includes('INSERT INTO esg.report_source_fact_snapshots')&&++inserts===100)throw Error('isolated archive crash at snapshot 100');
          return target.query(sql,args);};
        const value=target[key];return typeof value==='function'?value.bind(target):value;
      }})))};
      const failed=await dispatch(failing,'deliver',input);
      assert.equal(failed.status,'failed');assert.match(failed.detail.message,/isolated archive crash/);
      for(const table of ['esg.import_batches','esg.report_source_fact_snapshots','esg.import_source_facts','esg.esg_disclosures'])assert.equal(await count(db,table),0);
      const delivered=await dispatch(db,'deliver',input);
      assert.equal(delivered.status,'imported');assert.equal(delivered.receipt_id,failed.receipt_id);
      assert.equal(delivered.imported,0);assert.equal(delivered.source_fact_snapshots,291);
      assert.equal(delivered.result_state,'partial');assert.equal(delivered.publication_status,'not_published');
      const ir=JSON.parse(await readFile(path.join(bundleRoot,'input/ir-manifest.json'),'utf8'));
      const statusScope={native_session_id:actual.request.report_session_id,
        report_identity:Object.fromEntries(['upstream_system','organization_ref','report_ref','report_version_ref','source_artifact_ref',
          'source_sha256','report_period_start','report_period_end'].map(k=>[k,metadata[k]])),
        fixed_source:{document_id:ir.document_id,ir_run_id:ir.run_id,ir_revision:ir.ir_revision},
        job_ids:[actual.manifest.job_id],artifacts:[{job_id:actual.manifest.job_id,artifact_revision_id:actual.manifest.artifact_revision.revision_id,
          bundle_path:bundleRoot,manifest_sha256:createHash('sha256').update(await readFile(path.join(bundleRoot,'manifest.json'))).digest('hex')}]};
      const status=await dispatch(db,'delivery_status',statusScope);
      assert.equal(status.status,'source_archived');
      assert.deepEqual(status.current_revision_counts,{archived_source_count:291,selected_candidate_count:0,internal_candidate_count:0,published_observation_count:0});
      assert.equal(status.jobs[0].artifacts[0].receipt.receipt_id,delivered.receipt_id);
      assert.deepEqual(delivered.selection_snapshot.source_datapoint_ids,actual.request.selection_snapshot.source_datapoint_ids);
      assert.deepEqual(delivered.selection_snapshot.extension_selections,actual.runtimeSnapshot.selections);
      assert.equal(await count(db,'esg.report_source_fact_snapshots'),291);
      assert.equal(await count(db,'esg.import_source_facts'),291);
      assert.equal(await count(db,'esg.esg_disclosures'),0);
      assert.equal(await count(db,'esg.disclosure_standard_mappings'),0);
      assert.equal(await count(db,'esg.curated_observations'),0);
      const archived=(await db.query('SELECT source_fact_ref,snapshot_payload,identity_raw,unit_raw,quality_status FROM esg.report_source_fact_snapshots')).rows;
      const expected=new Map(actual.sourceFacts.facts.map(({review_status,...immutable})=>[immutable.source_fact_id,immutable]));
      for(const row of archived) {
        assert.deepEqual(row.snapshot_payload,expected.get(row.source_fact_ref));
        assert.deepEqual(row.identity_raw,{});assert.equal(row.quality_status,'needs_confirmation');
        assert.equal(row.unit_raw,expected.get(row.source_fact_ref).unit_raw??null);
      }
      const repeated=await dispatch(db,'deliver',input);
      assert.equal(repeated.receipt_id,delivered.receipt_id);assert.equal(repeated.idempotent,true);
      assert.equal(repeated.source_fact_snapshots,291);
      assert.equal(await count(db,'esg.import_batches'),1);
      assert.equal(await count(db,'esg.report_source_fact_snapshots'),291);
      assert.deepEqual(await dispatch(db,'datasets',{}),frozenDatasets);
      assert.deepEqual((await db.query('SELECT * FROM esg.curated_observations ORDER BY observation_id')).rows,frozenPublic);
      assert.deepEqual((await db.query('SELECT * FROM esg.outbox_events ORDER BY event_id')).rows,events);
      const wrong=structuredClone(input);wrong.selection_snapshot.source_datapoint_ids.pop();
      const conflict=await dispatch(db,'deliver',wrong);
      assert.equal(conflict.status,'failed');assert.equal(conflict.detail.code,'import_scope_conflict');
      assert.equal(await count(db,'esg.report_source_fact_snapshots'),291);
    } finally {await db.close();if(previous===undefined)delete process.env.PLATESG_IMPORT_ROOTS;else process.env.PLATESG_IMPORT_ROOTS=previous;}
  });

test('copied actual terminal revision rejects changed source bytes and preserves its original artifact',
  {skip:!actual},async()=>{
    const tmp=await mkdtemp(path.join(tmpdir(),'terminal-bundle-tamper-'));
    try {
      const before=await readFile(path.join(bundleRoot,'results/source-facts.json'));
      await cp(bundleRoot,tmp,{recursive:true});
      await writeFile(path.join(tmp,'results/source-facts.json'),Buffer.concat([before,Buffer.from(' ')]));
      await assert.rejects(loadBundle(tmp),/Terminal artifact revision content changed/);
      assert.deepEqual(await readFile(path.join(bundleRoot,'results/source-facts.json')),before);
    } finally {await rm(tmp,{recursive:true,force:true});}
  });
