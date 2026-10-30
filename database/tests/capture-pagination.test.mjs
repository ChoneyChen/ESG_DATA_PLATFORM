import test from 'node:test';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../scripts/db.mjs';
import {capturedFacts} from '../queries/captures.mjs';

test('more than 1000 captured facts remain fully queryable and cursors bind filters',async()=>{
  const db=await PGlite.create();
  try {
    await applyMigrations(db);
    await db.query("INSERT INTO esg.capture_exports(export_id,input_digest,manifest,receipt,coverage,diagnostics) VALUES('export-many','frozen','{}','{}','[]','[]')");
    await db.exec(`INSERT INTO esg.captured_facts(source_fact_id,source_anchor_id,document_id,ir_run_id,ir_revision,kind,value_raw,record_digest,record)
      SELECT 'f-'||lpad(i::text,5,'0'),'a-'||i,'d','ir',1,'quantitative',i::text,'digest-'||i,
      jsonb_build_object('source_fact_id','f-'||lpad(i::text,5,'0'),'identity',jsonb_build_object('reporting_period',
        jsonb_build_object('value',CASE WHEN i%2=0 THEN '2023' ELSE '2024' END))) FROM generate_series(1,1205) i;
      INSERT INTO esg.capture_export_facts SELECT 'export-many',source_fact_id FROM esg.captured_facts;`);
    let cursor=null;const seen=new Set();let pages=0;
    do {
      const page=await capturedFacts(db,{export_id:'export-many',limit:200,cursor});
      assert.equal(page.total,1205);assert.equal(page.returned,page.items.length);
      for(const row of page.items){assert(!seen.has(row.record.source_fact_id));seen.add(row.record.source_fact_id);}
      cursor=page.next_cursor;pages++;
    } while(cursor);
    assert.equal(pages,7);assert.equal(seen.size,1205);
    const old=await capturedFacts(db,{export_id:'export-many',limit:200,period:'2023'});
    assert.equal(old.total,602);assert.equal(old.periods.reduce((n,p)=>n+p.facts,0),1205);
    assert(old.items.every(row=>row.record.identity.reporting_period.value==='2023'));
    await assert.rejects(capturedFacts(db,{export_id:'export-many',limit:200,period:'2024',cursor:old.next_cursor}),{code:'invalid_cursor'});
  } finally {await db.close();}
});
