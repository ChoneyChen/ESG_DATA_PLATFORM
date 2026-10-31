import test from 'node:test';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
import {applyCaptureSchema} from '../runtime/schema.mjs';
import {ingestExport} from '../queries/ingestion.mjs';
import {sourceReferences,reserveSource,updateSourceReservation} from '../queries/capture_sources.mjs';

test('source ownership protects PDF bytes, permits cache cleanup and serializes late delivery',async()=>{
  const db=await PGlite.create();
  try {
    await applyCaptureSchema(db);
    const sha='a'.repeat(64), other='b'.repeat(64);
    const scope={target:'pdf',source_sha256:sha,job_ids:[],ir_run_ids:[]};
    const payload=(sha,id)=>({manifest:{schema_version:'extraction-export-1.1',authority:'facts',export_id:id,
      document:{source_sha256:sha,ir_run_id:'ir-one'},clause_targets:{}},digest:id,facts:[],relations:[],targets:{},coverage:[],diagnostics:[]});
    const reservation=await reserveSource(db,{...scope,reason:'Synthetic cleanup',actor:'test',reservation_id:'stable'});
    assert.equal(reservation.state,'reserved');
    assert.equal((await reserveSource(db,{...scope,reason:'Retry',actor:'test',reservation_id:'stable'})).reservation_id,'stable');
    await assert.rejects(ingestExport(db,payload(sha,'during-cleanup')),{code:'source_cleanup_active'});
    await updateSourceReservation(db,{reservation_id:'stable',state:'quarantined',reason:'Bytes moved'});
    await updateSourceReservation(db,{reservation_id:'stable',state:'purged',reason:'Bytes removed'});
    await assert.rejects(ingestExport(db,payload(sha,'after-removal')),{code:'source_cleanup_active'});
    await assert.rejects(updateSourceReservation(db,{reservation_id:'stable',state:'released',reason:'Invalid transition'}),{code:'reservation_state_conflict'});
    await ingestExport(db,payload(other,'current-export'));
    const references=await sourceReferences(db,{...scope,source_sha256:other});
    assert.equal(references.dependencies.length,1);assert.equal(references.blockers.length,1);
    assert(!('reservation_id' in await reserveSource(db,{...scope,source_sha256:other,reason:'Cannot delete',actor:'test'})));
    const cache=await reserveSource(db,{target:'cache',source_sha256:other,job_ids:[],ir_run_ids:['ir-one'],reason:'Cache only',actor:'test'});
    assert.equal(cache.blockers.length,0);assert.equal(cache.dependencies[0].requires_files,false);
    await ingestExport(db,payload(other,'later-independent-capture'));
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.capture_exports')).rows[0].n,2);
  } finally {await db.close();}
});
