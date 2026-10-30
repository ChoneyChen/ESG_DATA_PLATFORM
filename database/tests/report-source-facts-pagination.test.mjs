import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../scripts/db.mjs';
import {readReportSourceFacts} from '../scripts/report-source-facts.mjs';
import {dispatch} from '../queries/service.mjs';

const encode=payload=>Buffer.from(JSON.stringify(payload)).toString('base64url');
const anchor={v:1,report_ref:'synthetic-report-a',created_at:'2026-10-07T01:02:03.123456Z',snapshot_id:'00000000-0000-5000-a000-000000000001'};
async function setup() {
  const db=await PGlite.create();await applyMigrations(db);
  for(const [reportRef,count,offset] of [['synthetic-report-a',1207,0],['synthetic-report-b',4,2000]]) {
    const report=randomUUID();
    await db.query(`INSERT INTO esg.source_report_refs(source_report_id,upstream_system,organization_ref,report_ref,report_version_ref,source_artifact_ref,report_title_snapshot)
      VALUES($1,'synthetic-pagination','synthetic-organization',$2,$2,'synthetic://pagination','分页软件回归来源（合成）')`,[report,reportRef]);
    const rows=Array.from({length:count},(_,index)=>({snapshot_id:`00000000-0000-5000-a000-${String(index+offset).padStart(12,'0')}`,
      source_fact_ref:`synthetic-source-${index+offset}`,created_at:`2026-10-07T01:02:03.${index<501?'123456':index<1004?'123457':'123999'}Z`}));
    await db.query(`INSERT INTO esg.report_source_fact_snapshots(snapshot_id,source_report_id,source_fact_ref,snapshot_digest,document_id,source_ir_run_id,
      source_ir_revision,kind,value_raw,unit_raw,identity_raw,physical_sources,quality_status,provenance_status,identity_basis,snapshot_payload,created_at)
      SELECT item.snapshot_id::uuid,$1,item.source_fact_ref,repeat('a',64),'synthetic-document','synthetic-ir',1,'quantitative','001.2300','原文单位',
        '{"boundary_note":"合成分页样本"}','[]','unbound','unbound','frozen_manifest',
        jsonb_build_object('source_fact_id',item.source_fact_ref,'value_raw','001.2300'),item.created_at::timestamptz
      FROM jsonb_to_recordset($2::jsonb) AS item(snapshot_id text,source_fact_ref text,created_at text)`,[report,JSON.stringify(rows)]);
  }
  return db;
}

test('1207 source snapshots page at 500 with exact microseconds, no duplicates or omissions, and stable original DTO',async()=>{
  const db=await setup();try {
    const expected=(await db.query(`SELECT snapshot_id FROM esg.report_source_fact_snapshots s JOIN esg.source_report_refs r USING(source_report_id)
      WHERE r.report_ref=$1 ORDER BY s.created_at,s.snapshot_id`,['synthetic-report-a'])).rows.map(row=>row.snapshot_id);
    const seen=[],sizes=[];let cursor=null;
    for(let page=0;page<4;page++) {
      const result=await dispatch(db,'source_facts',{report_ref:'synthetic-report-a',limit:500,cursor});
      sizes.push(result.items.length);seen.push(...result.items.map(row=>row.snapshot_id));
      assert.ok(result.items.every(row=>row.report_ref==='synthetic-report-a'&&row.value_raw==='001.2300'
        &&row.mapping_state==='unmapped'&&row.publication_status==='source_only_not_published'&&!Object.hasOwn(row,'_cursor_created_at')));
      assert.equal(result.has_more,Boolean(result.next_cursor));
      if(page===0)assert.equal(JSON.parse(Buffer.from(result.next_cursor,'base64url')).created_at,'2026-10-07T01:02:03.123456Z');
      if(!result.next_cursor)break;
      cursor=result.next_cursor;
    }
    assert.deepEqual(sizes,[500,500,207]);assert.equal(new Set(seen).size,1207);assert.deepEqual(seen,expected);
    const other=await readReportSourceFacts(db,{report_ref:'synthetic-report-b',limit:500});
    assert.equal(other.items.length,4);assert.equal(other.next_cursor,null);assert.equal(other.has_more,false);
  } finally {await db.close();}
});

test('source cursors bind the report filter and retain UUID/time as SQL parameters',async()=>{
  const db=await setup();try {
    const first=await readReportSourceFacts(db,{report_ref:'synthetic-report-a',limit:500});
    for(const report_ref of ['synthetic-report-b',null,"synthetic-report-a' OR TRUE --"])
      await assert.rejects(readReportSourceFacts(db,{report_ref,cursor:first.next_cursor}),e=>e.code==='invalid_cursor');
    const calls=[],wrapped={query:async(sql,args)=>{calls.push({sql,args});return db.query(sql,args);}};
    const page=await readReportSourceFacts(wrapped,{report_ref:'synthetic-report-a',limit:500,cursor:first.next_cursor});
    assert.equal(page.items.length,500);
    assert.ok(calls[0].sql.includes('($2::timestamptz,$3::uuid)'));
    assert.deepEqual(calls[0].args,['synthetic-report-a','2026-10-07T01:02:03.123456Z',
      JSON.parse(Buffer.from(first.next_cursor,'base64url')).snapshot_id,501]);
    assert.equal((await readReportSourceFacts(db,{report_ref:"x' OR TRUE --"})).items.length,0);
  } finally {await db.close();}
});

test('invalid, oversized, noncanonical and unsupported source cursors fail before querying',async()=>{
  let calls=0;const db={query:async()=>{calls++;throw new Error('invalid cursor reached query');}};
  const invalid=['','***','x'.repeat(2049),encode({...anchor,v:2}),encode({...anchor,unexpected:true}),
    encode({...anchor,created_at:'2026-02-29T01:02:03.123456Z'}),encode({...anchor,created_at:'2026-10-07T24:02:03.123456Z'}),
    encode({...anchor,created_at:'2026-10-07T01:02:03.123Z'}),encode({...anchor,snapshot_id:"x'; SELECT 1 --"}),
    Buffer.from(JSON.stringify(anchor)+' ').toString('base64url'),encode(anchor)+'='];
  for(const cursor of invalid)
    await assert.rejects(readReportSourceFacts(db,{report_ref:'synthetic-report-a',cursor}),e=>e.code==='invalid_cursor');
  assert.equal(calls,0);
  for(const limit of [0,1001,1.5])await assert.rejects(readReportSourceFacts(db,{limit}),/limit/);
  assert.equal(calls,0);
});

test('unfiltered source pagination binds null scope and ends with an explicit null cursor',async()=>{
  const db=await setup();try {
    const first=await readReportSourceFacts(db,{limit:1000});
    assert.equal(first.items.length,1000);assert.equal(JSON.parse(Buffer.from(first.next_cursor,'base64url')).report_ref,null);
    const last=await readReportSourceFacts(db,{limit:1000,cursor:first.next_cursor});
    const ids=[...first.items,...last.items].map(row=>row.snapshot_id);
    assert.equal(last.items.length,211);assert.equal(last.next_cursor,null);assert.equal(new Set(ids).size,1211);
    const empty=await readReportSourceFacts(db,{report_ref:'synthetic-missing-report'});
    assert.deepEqual(empty.items,[]);assert.equal(empty.next_cursor,null);assert.equal(empty.has_more,false);
  } finally {await db.close();}
});
