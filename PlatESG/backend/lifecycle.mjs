import {randomUUID} from 'node:crypto';

const invalid = message => Object.assign(new Error(message), {code:'lifecycle_conflict'});
export function normalizeScope(input) {
  const unique = values => [...new Set((values || []).map(String))].sort();
  const scope = {job_ids:unique(input.job_ids),ir_run_ids:unique(input.ir_run_ids),
    source_sha256s:unique([...(input.source_sha256s||[]), ...(input.source_sha256?[input.source_sha256]:[])].map(hash=>String(hash).toLowerCase()))};
  if(input.local_files_only === true) scope.local_files_only = true;
  if (!scope.job_ids.length && !scope.ir_run_ids.length && !scope.source_sha256s.length)
    throw invalid('来源清理必须提供确定的任务、证据或文件身份');
  return scope;
}
function overlap(a,b) {
  return a.source_sha256s.some(hash=>b.source_sha256s.includes(hash))
    || a.job_ids.some(id=>b.job_ids.includes(id)) || a.ir_run_ids.some(id=>b.ir_run_ids.includes(id));
}
export async function lifecycleReferences(db,input) {
  const scope=normalizeScope(input);
  const reports=(await db.query(`SELECT DISTINCT r.source_report_id,r.report_title_snapshot,r.source_sha256
    FROM esg.source_report_refs r
    LEFT JOIN esg.import_batches b USING(source_report_id)
    LEFT JOIN esg.esg_disclosures d USING(source_report_id)
    WHERE r.source_sha256=ANY($1::text[])
      OR b.bundle_job_id=ANY($2::text[]) OR d.source_ir_run_id=ANY($3::text[])
      OR EXISTS (SELECT 1 FROM esg.report_source_fact_snapshots neutral
        WHERE neutral.source_report_id=r.source_report_id AND neutral.source_ir_run_id=ANY($3::text[]))
      OR EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(d.source_identity->'physical_sources','[]'::jsonb)) physical
        WHERE physical->>'ir_run_id'=ANY($3::text[]))`,
    [scope.source_sha256s,scope.job_ids,scope.ir_run_ids])).rows;
  const ids=reports.map(r=>r.source_report_id);
  const counts=(await db.query(`SELECT count(DISTINCT d.disclosure_id)::int AS candidate_count,
    count(DISTINCT os.observation_id)::int AS published_count,
    count(DISTINCT neutral.snapshot_id)::int AS source_fact_count,
    count(DISTINCT b.import_batch_id)::int AS imported_bundle_count
    FROM esg.source_report_refs r LEFT JOIN esg.esg_disclosures d USING(source_report_id)
    LEFT JOIN esg.observation_sources os USING(disclosure_id)
    LEFT JOIN esg.import_batches b USING(source_report_id)
    LEFT JOIN esg.report_source_fact_snapshots neutral USING(source_report_id)
    WHERE r.source_report_id=ANY($1::uuid[])`,[ids])).rows[0];
  const blockers=[];
  if(reports.length) blockers.push({code:counts.published_count?'published_source_reference':'candidate_source_reference',
    message:counts.published_count?'来源已被正式观测引用，不能清理。':'来源已进入候选或导入记录，不能清理。',...counts});
  return {schema_version:'lifecycle-references-v1',verified:true,scope,dependencies:reports,blockers,counts};
}
export async function lifecycleReserve(db,input) {
  if(!input.reason?.trim()) throw invalid('生命周期操作需要原因');
  return db.transaction(async tx=>{
    await tx.query('SELECT mutex_id FROM plat.artifact_lifecycle_mutex WHERE mutex_id=1 FOR UPDATE');
    const reservation_id=input.reservation_id||randomUUID();
    const requestedScope=normalizeScope(input);
    const prior=(await tx.query('SELECT * FROM plat.artifact_lifecycle_reservations WHERE reservation_id=$1',[reservation_id])).rows[0];
    if(prior) {
      if(['released','purged'].includes(prior.state)) throw invalid('来源保留请求已结束，不能重新取得同一权限');
      if(JSON.stringify(normalizeScope(prior.scope))!==JSON.stringify(requestedScope)) throw invalid('来源保留请求身份冲突');
      const references=await lifecycleReferences(tx,input);
      return {...references,blockers:prior.scope.local_files_only?[]:references.blockers,reservation_id,state:prior.state};
    }
    const result=await lifecycleReferences(tx,input);
    // A local file deletion releases execution artifacts, not immutable SQL
    // evidence snapshots or published observations. References remain intact.
    if(result.blockers.length && !input.local_files_only) return result;
    const reservations=(await tx.query("SELECT reservation_id,scope,state FROM plat.artifact_lifecycle_reservations WHERE state <> 'released'")).rows;
    const existing=reservations.find(row=>overlap(normalizeScope(row.scope),result.scope));
    if(existing) return {...result,blockers:[{code:'source_cleanup_reserved',message:'来源已有未恢复的回收操作。',reservation_id:existing.reservation_id}]};
    await tx.query('INSERT INTO plat.artifact_lifecycle_reservations(reservation_id,scope,state,reason) VALUES($1,$2,$3,$4)',
      [reservation_id,result.scope,'reserved',input.reason]);
    return {...result,blockers:input.local_files_only?[]:result.blockers,reservation_id};
  });
}
export async function lifecycleReservation(db,input) {
  if(!input.reason?.trim()) throw invalid('生命周期操作需要原因');
  return db.transaction(async tx=>{
    await tx.query('SELECT mutex_id FROM plat.artifact_lifecycle_mutex WHERE mutex_id=1 FOR UPDATE');
    const row=(await tx.query('SELECT * FROM plat.artifact_lifecycle_reservations WHERE reservation_id=$1',[input.reservation_id])).rows[0];
    if(!row) {
      if(input.state!=='released') throw invalid('来源保留记录不存在');
      // A recovery release can arrive before a delayed acquire. Its durable
      // tombstone prevents that late request from orphaning a reservation.
      return (await tx.query('INSERT INTO plat.artifact_lifecycle_reservations(reservation_id,scope,state,reason) VALUES($1,$2,$3,$4) RETURNING *',
        [input.reservation_id,{},'released',input.reason])).rows[0];
    }
    const transitions={reserved:['quarantined','released'],quarantined:['released','purged'],purged:[],released:[]};
    if(row.state!==input.state && !transitions[row.state].includes(input.state)) throw invalid('来源保留状态不允许此变更');
    if(input.state==='purged') {
      const result=await lifecycleReferences(tx,row.scope);
      if(result.blockers.length) throw invalid('来源产生了候选或正式引用，不能彻底清除');
    }
    return (await tx.query('UPDATE plat.artifact_lifecycle_reservations SET state=$1,reason=$2,updated_at=CURRENT_TIMESTAMP WHERE reservation_id=$3 RETURNING *',
      [input.state,input.reason,input.reservation_id])).rows[0];
  });
}
export async function assertImportSourcesAvailable(tx,scope) {
  await tx.query('SELECT mutex_id FROM plat.artifact_lifecycle_mutex WHERE mutex_id=1 FOR UPDATE');
  const reservations=(await tx.query("SELECT scope,state FROM plat.artifact_lifecycle_reservations WHERE state <> 'released'")).rows;
  if(reservations.some(row=>overlap(normalizeScope(row.scope),normalizeScope(scope))))
    throw invalid('来源正在回收或已清除；先恢复来源后才能导入候选');
}
