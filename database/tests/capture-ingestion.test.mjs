import test from 'node:test';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../scripts/db.mjs';
import {ingestExport,receiptFor} from '../queries/ingestion.mjs';

test('sparse captures, exact decimals, qualitative text, idempotent receipts and conflicts',async()=>{
  const db=await PGlite.create();
  try {
    await applyMigrations(db);
    const fact=(id,value)=>({digest:id,fact:{source_fact_id:id,source_anchor_id:id+'-anchor',document_id:'document',ir_run_id:'ir',ir_revision:1,
      value,identity:{dimensions:{}},evidence:[{role:value.kind==='assertion'?'statement':'value',excerpt:value.value_raw||value.statement_raw}]}});
    const payload={manifest:{schema_version:'extraction-export-1.0',export_id:'export-one',clause_targets:{C:'digest'}},digest:'one',
      facts:[fact('quantity',{kind:'quantitative',value_raw:'1,530.14000000000000000001',value_numeric:'1530.14000000000000000001',unit_raw:'吨'}),
        fact('count',{kind:'quantitative',value_raw:'0',value_numeric:'0'}),fact('statement',{kind:'assertion',statement_raw:'报告实施了排放监测。'})],
      relations:[],targets:{C:{clause_digest:'digest'}},coverage:[],diagnostics:[],rejected:[]};
    const first=await ingestExport(db,payload);
    assert.equal(first.accepted_facts,3);assert.equal(first.rejected_facts,0);
    assert.equal(first.publication_status,'not_evaluated');assert.equal(first.dispositions.length,3);
    assert.deepEqual(await ingestExport(db,payload),first);
    assert.deepEqual(await receiptFor(db,'export-one'),first);
    assert.equal((await db.query("SELECT value_numeric::text AS value FROM esg.captured_facts WHERE source_fact_id='quantity'")).rows[0].value,'1530.14000000000000000001');
    assert.equal((await db.query("SELECT unit_raw FROM esg.captured_facts WHERE source_fact_id='count'")).rows[0].unit_raw,null);
    await assert.rejects(ingestExport(db,{...payload,digest:'different'}),{code:'export_identity_conflict'});
    const partial=await ingestExport(db,{...payload,manifest:{...payload.manifest,export_id:'export-two'},digest:'two',
      facts:[{...payload.facts[0],digest:'changed'},fact('new',{kind:'quantitative',value_raw:'2'})]});
    assert.equal(partial.status,'partial');assert.equal(partial.accepted_facts,1);assert.equal(partial.rejected_facts,1);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.captured_facts')).rows[0].n,4);
  } finally {await db.close();}
});
