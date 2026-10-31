import test from 'node:test';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
import {applyCaptureSchema,captureSchemaStatus} from '../runtime/schema.mjs';
import {ingestExport} from '../queries/ingestion.mjs';
import {databaseDataDir} from '../runtime/connection.mjs';
import {readFile} from 'node:fs/promises';

test('capture initializes without any dictionary or legacy publication tables',async()=>{
  const db=await PGlite.create();
  try {
    assert.equal((await captureSchemaStatus(db)).ready,false);
    assert.equal((await applyCaptureSchema(db)).ready,true);
    assert.equal((await db.query("SELECT to_regclass('esg.dictionary_membership') AS name")).rows[0].name,null);
    assert.equal((await db.query("SELECT to_regclass('esg.curated_observations') AS name")).rows[0].name,null);
    const receipt=await ingestExport(db,{manifest:{schema_version:'extraction-export-1.1',authority:'facts',export_id:'new-capture',clause_targets:{}},
      digest:'content',facts:[{digest:'f',fact:{source_fact_id:'f',source_anchor_id:'a',document_id:'d',ir_run_id:'ir',ir_revision:1,
        value:{kind:'quantitative',value_raw:'12'},identity:{dimensions:{}},evidence:[]}}],targets:{},relations:[],coverage:[],diagnostics:[]});
    assert.equal(receipt.accepted_facts,1);
    await applyCaptureSchema(db);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.captured_facts')).rows[0].n,1);
  } finally {await db.close();}
});

test('capture configuration never inherits a legacy publication data directory',()=>{
  const selected=databaseDataDir({ESG_DATABASE_PROFILE:'capture',ESG_DATA_ROOT:'/new/data',PLATESG_DATA_DIR:'/legacy/data'});
  assert.equal(selected,'/new/data/databases/capture');
});

test('unmanaged previous tables cannot silently activate as a current database',async()=>{
  const db=await PGlite.create();
  try {
    await db.exec('CREATE SCHEMA esg;CREATE SCHEMA esg_api;');
    await db.exec(await readFile(new URL('../migrations/0019_capture_ingestion.sql',import.meta.url),'utf8'));
    await db.query("INSERT INTO esg.capture_exports(export_id,input_digest,manifest,receipt,coverage,diagnostics) VALUES('existing','unchanged','{}','{}','[]','[]')");
    await assert.rejects(applyCaptureSchema(db),/not adopted/);
    assert.equal((await db.query("SELECT input_digest FROM esg.capture_exports WHERE export_id='existing'")).rows[0].input_digest,'unchanged');
    assert.equal((await captureSchemaStatus(db)).ready,false);
  } finally {await db.close();}
});
