// A cache ID never makes the database depend on a producer directory.
import {randomUUID} from 'node:crypto';
import {canonical,fault} from '../runtime/canonical.mjs';

const isPdf=scope=>scope.target==='pdf'||(scope.target!=='cache'&&scope.source_sha256&&!scope.job_ids?.length&&!scope.ir_run_ids?.length);
function keys(scope){return isPdf(scope)?['pdf:'+scope.source_sha256]:[...(scope.job_ids||[]).map(id=>'job:'+id),...(scope.ir_run_ids||[]).map(id=>'ir:'+id)].sort();}
async function lock(db,sourceKeys){
  for(const key of [...new Set(sourceKeys)].sort()){
    await db.query('INSERT INTO esg.capture_source_locks VALUES($1) ON CONFLICT DO NOTHING',[key]);
    await db.query('SELECT source_key FROM esg.capture_source_locks WHERE source_key=$1 FOR UPDATE',[key]);
  }
}
export async function lockCaptureSource(db,manifest){
  const sha=manifest.document?.source_sha256||manifest.document?.report_resource?.sha256;
  if(!sha)return;
  const key='pdf:'+sha;await lock(db,[key]);
  const active=(await db.query(`SELECT state FROM esg.capture_source_reservations WHERE state IN ('reserved','quarantined','purged') AND source_keys ? $1 LIMIT 1`,[key])).rows[0];
  if(active)throw fault('source_cleanup_active','The PDF is being cleaned or has been removed; restore its availability before delivery');
}
export async function sourceReferences(db,scope){
  const pdf=isPdf(scope);
  const rows=(await db.query(`SELECT export_id,manifest->>'run_id' AS run_id,manifest#>>'{document,ir_run_id}' AS ir_run_id
    FROM esg.capture_exports WHERE ($1::text IS NOT NULL AND COALESCE(manifest#>>'{document,source_sha256}',manifest#>>'{document,report_resource,sha256}')=$1)
    OR manifest->>'run_id'=ANY($2) OR manifest#>>'{document,ir_run_id}'=ANY($3) ORDER BY export_id`,
    [pdf?scope.source_sha256:null,scope.job_ids||[],scope.ir_run_ids||[]])).rows;
  return {schema_version:'source-references-1.0',verified:true,scope,
    dependencies:rows.map(row=>({...row,kind:pdf?'source_pdf':'cache_provenance',requires_files:pdf})),
    blockers:pdf&&rows.length?[{code:'captured_source_pdf',message:'当前原值引用此PDF；先明确移除相关数据，不能直接删除其来源。',exports:rows.length}]:[]};
}
export async function reserveSource(db,request){
  const {reason,reservation_id,local_files_only,actor,...scope}=request;
  const id=reservation_id||randomUUID();
  return db.transaction(async tx=>{
    const sourceKeys=keys(scope);await lock(tx,sourceKeys);
    const existing=(await tx.query('SELECT * FROM esg.capture_source_reservations WHERE reservation_id=$1 FOR UPDATE',[id])).rows[0];
    if(existing){if(canonical(existing.scope)!==canonical(scope))throw fault('reservation_identity_conflict','Reservation identity differs');return {...await sourceReferences(tx,scope),reservation_id:id,state:existing.state};}
    const reference=await sourceReferences(tx,scope);
    if(reference.blockers.length)return reference;
    const active=(await tx.query(`SELECT reservation_id FROM esg.capture_source_reservations WHERE state IN ('reserved','quarantined') AND source_keys ?| $1 LIMIT 1`,[sourceKeys])).rows[0];
    if(active)throw fault('source_cleanup_active','A source cleanup already holds these identities');
    await tx.query(`INSERT INTO esg.capture_source_reservations(reservation_id,scope,source_keys,state,reason,actor)
      VALUES($1,$2::jsonb,$3::jsonb,'reserved',$4,$5)`,[id,JSON.stringify(scope),JSON.stringify(sourceKeys),reason,actor]);
    return {...reference,reservation_id:id,state:'reserved'};
  });
}
export async function updateSourceReservation(db,{reservation_id,state,reason}){
  return db.transaction(async tx=>{
    const row=(await tx.query('SELECT * FROM esg.capture_source_reservations WHERE reservation_id=$1 FOR UPDATE',[reservation_id])).rows[0];
    if(!row)throw fault('not_found','Source reservation does not exist');
    if(row.state===state)return {reservation_id,state};
    const allowed={reserved:['quarantined','released'],quarantined:['released','purged'],released:[],purged:[]};
    if(!allowed[row.state].includes(state))throw fault('reservation_state_conflict','Source reservation changed');
    await tx.query('UPDATE esg.capture_source_reservations SET state=$2,reason=$3,updated_at=CURRENT_TIMESTAMP WHERE reservation_id=$1',[reservation_id,state,reason]);
    return {reservation_id,state};
  });
}
