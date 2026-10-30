/** Authenticated delivery read model. Never imports, repairs, reviews or publishes. */
import {readFile, realpath} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {loadBundle} from '../../scripts/frozen-bundle.mjs';

const hash=value=>createHash('sha256').update(value).digest('hex');
const stable=value=>JSON.stringify(value,(_,v)=>v && typeof v==='object' && !Array.isArray(v)
  ?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
const fault=(code,message)=>Object.assign(new Error(message),{code});
const nullCounts=()=>({archived_source_count:null,selected_candidate_count:null,internal_candidate_count:null,published_observation_count:null});
const zeroCounts=()=>({archived_source_count:0,selected_candidate_count:0,internal_candidate_count:0,published_observation_count:0});
const labels={
  no_artifact:'此标准包尚无可核对的冻结成果',not_imported:'数据库未找到此冻结成果的交付批次',
  receipt_unconfirmed:'来源已写入数据库，但尚无此成果版本的成功交付回执',
  delivery_failed:'最近核实的交付尝试失败',artifact_unavailable:'冻结成果文件暂不可读，不能核对当前版本',
  artifact_identity_mismatch:'成果任务、父会话、来源或冻结版本不一致',report_identity_mismatch:'登记报告版本、主体或固定PDF与数据库记录不一致',
  scope_limit_exceeded:'历史批次超过本次核对上限，数量尚未完整核实',candidate_scope_unclassified:'历史候选缺少明确的输出或内部依赖分类',
  publication_unavailable:'数据库暂不可读，不能判断交付状态',partial_delivery:'部分标准包或当前成果版本尚未交付',
  historical_binding_unverified:'历史批次缺少可核实的父会话或固定结构证据',receipt_limit_exceeded:'对应批次的回执超过核对上限，确认状态未知',
};
const reasons=codes=>[...new Set(codes)].map(code=>({code,label_zh:labels[code] || '此状态尚需核实'}));
const withReasons=value=>({...value,reason_messages:reasons(value.reason_codes || [])});
function validateScope(input) {
  const keys=(value,allowed)=>value && typeof value==='object' && !Array.isArray(value)
    && Object.keys(value).every(key=>allowed.includes(key));
  if(!keys(input,['native_session_id','report_identity','fixed_source','job_ids','artifacts'])
    || !keys(input.report_identity,['upstream_system','organization_ref','report_ref','report_version_ref','source_artifact_ref','source_sha256','report_period_start','report_period_end'])
    || !keys(input.fixed_source,['document_id','ir_run_id','ir_revision']))throw fault('invalid_request','Unknown delivery identity field');
  if(!input || !/^report-[a-f0-9]{32}$/.test(input.native_session_id || '')
    || !Array.isArray(input.job_ids) || !input.job_ids.length || input.job_ids.length>100
    || new Set(input.job_ids).size!==input.job_ids.length || input.job_ids.some(id=>!/^tx-[a-zA-Z0-9._-]{1,150}$/.test(id))
    || !Array.isArray(input.artifacts) || input.artifacts.length>100)throw fault('invalid_request','Invalid bounded delivery scope');
  const r=input.report_identity,f=input.fixed_source;
  if(!r || ['upstream_system','organization_ref','report_ref','report_version_ref','source_artifact_ref','report_period_start','report_period_end'].some(k=>typeof r[k]!=='string'||!r[k].trim())
    || !/^[a-f0-9]{64}$/.test(r.source_sha256 || '') || !f || !f.document_id || !f.ir_run_id || !Number.isInteger(f.ir_revision)||f.ir_revision<1)
    throw fault('invalid_request','Explicit fixed report and IR identity required');
  const jobs=new Set();
  for(const a of input.artifacts) {
    if(!keys(a,['job_id','artifact_revision_id','bundle_path','manifest_sha256'])
      || !input.job_ids.includes(a.job_id)||jobs.has(a.job_id)||!/^(original|partial-[a-f0-9]{32})$/.test(a.artifact_revision_id || '')
      || !/^[a-f0-9]{64}$/.test(a.manifest_sha256 || '') || typeof a.bundle_path!=='string' || !path.isAbsolute(a.bundle_path))
      throw fault('invalid_request','Invalid or duplicate current revision');
    jobs.add(a.job_id);
  }
}
function fixedMatch(manifest,input) {
  return manifest.report_session_id===input.native_session_id && documentMatch(manifest,input);
}
function documentMatch(manifest,input) {
  const d=manifest.document || {},f=input.fixed_source;
  return d.document_id===f.document_id
    && d.ir_run_id===f.ir_run_id && d.ir_revision===f.ir_revision;
}
async function permittedRoot(target,roots) {
  const resolved=await realpath(target);
  const allowed=await Promise.all(roots.map(async root=>{try{return await realpath(root);}catch{return null;}}));
  if(!allowed.some(root=>root && (resolved===root || resolved.startsWith(root+path.sep))))
    throw fault('artifact_identity_mismatch','Artifact outside controlled roots');
  return resolved;
}
async function verifyArtifact(a,input,roots,loader=loadBundle) {
  const root=await permittedRoot(a.bundle_path,roots);
  const expectedJobRoot=a.artifact_revision_id==='original'?root:path.dirname(path.dirname(root));
  if(path.basename(expectedJobRoot)!==a.job_id || (a.artifact_revision_id!=='original'
    && (path.basename(root)!==a.artifact_revision_id || path.basename(path.dirname(root))!=='terminal-revisions')))
    throw fault('artifact_identity_mismatch','Revision path does not belong to its original job');
  const raw=await readFile(path.join(root,'manifest.json'));
  if(hash(raw)!==a.manifest_sha256)throw fault('artifact_identity_mismatch','Frozen manifest bytes changed');
  const bundle=await loader(root);
  if(stable(JSON.parse(raw))!==stable(bundle.manifest)
    || hash(await readFile(path.join(root,'manifest.json')))!==a.manifest_sha256)
    throw fault('artifact_identity_mismatch','Manifest changed while reading the frozen artifact');
  const ir=JSON.parse(await readFile(path.join(root,'input/ir-manifest.json'),'utf8'));
  const revision=bundle.manifest.artifact_revision?.revision_id || 'original';
  if(bundle.request.report_session_id!==input.native_session_id)
    throw fault('artifact_identity_mismatch','The immutable request does not name this parent session');
  if(bundle.manifest.report_session_id==null) {
    const proof=JSON.parse(await readFile(path.join(root,'artifact-revision.json'),'utf8'));
    const master=await readFile(path.join(root,'input/report-scope.json'));
    const scope=JSON.parse(master);
    const canonical=JSON.stringify(scope,(_,v)=>v && typeof v==='object' && !Array.isArray(v)
      ?Object.fromEntries(Object.keys(v).sort().map(key=>[key,v[key]])):v);
    if(proof.frozen_master_request?.session_id!==input.native_session_id
      || proof.frozen_master_request?.file_sha256!==hash(master)
      || proof.frozen_master_request?.canonical_sha256!==hash(canonical)
      || scope.input?.ir_run_id!==input.fixed_source.ir_run_id)
      throw fault('artifact_identity_mismatch','Terminal parent proof lacks the original frozen master identity');
  }
  if(bundle.manifest.job_id!==a.job_id || revision!==a.artifact_revision_id || !documentMatch(bundle.manifest,input)
    || bundle.manifest.report_session_id!=null && bundle.manifest.report_session_id!==input.native_session_id
    || bundle.request.report_session_id!==input.native_session_id || bundle.request.ir_run_id!==input.fixed_source.ir_run_id
    || (ir.run_id || ir.ir_run_id)!==input.fixed_source.ir_run_id || ir.ir_revision!==input.fixed_source.ir_revision
    || ir.document_id!==input.fixed_source.document_id || ir.source?.pdf_sha256!==input.report_identity.source_sha256)
    throw fault('artifact_identity_mismatch','Frozen report/session/IR/revision differs from requested identity');
  return {...a,bundle_path:root,bundle_digest:bundle.bundleDigest,manifest_digest:hash(stable(bundle.manifest))};
}

/** Add only to new import results. Historical receipt rows remain untouched. */
export async function deliveryDescriptor(bundle,metadata) {
  try {
    const raw=await readFile(path.join(bundle.root,'manifest.json'));
    const ir=JSON.parse(await readFile(path.join(bundle.root,'input/ir-manifest.json'),'utf8'));
    const m=JSON.parse(raw),d=m.document || {};
    if(stable(m)!==stable(bundle.manifest) || !/^report-[a-f0-9]{32}$/.test(bundle.request.report_session_id || '')
      || m.report_session_id!=null && m.report_session_id!==bundle.request.report_session_id
      || !d.document_id || !d.ir_run_id || !Number.isInteger(d.ir_revision)
      || d.ir_run_id!==bundle.request.ir_run_id || (ir.run_id || ir.ir_run_id)!==d.ir_run_id
      || ir.ir_revision!==d.ir_revision || ir.document_id!==d.document_id || ir.source?.pdf_sha256!==metadata.source_sha256)return null;
    return {schema_version:'delivery-identity-v1',native_session_id:bundle.request.report_session_id,job_id:m.job_id,
      parent_identity_basis:'frozen_request',
      artifact_revision_id:m.artifact_revision?.revision_id || 'original',manifest_sha256:hash(raw),bundle_digest:bundle.bundleDigest,
      report_identity:Object.fromEntries(['upstream_system','organization_ref','report_ref','report_version_ref','source_artifact_ref','source_sha256',
        'report_period_start','report_period_end'].map(k=>[k,metadata[k] ?? null])),
      fixed_source:{document_id:d.document_id,ir_run_id:d.ir_run_id,ir_revision:d.ir_revision}};
  } catch{return null;}
}
function descriptorMatches(d,input,artifact=null) {
  return d?.schema_version==='delivery-identity-v1' && d.native_session_id===input.native_session_id
    && stable(d.report_identity)===stable(input.report_identity) && stable(d.fixed_source)===stable(input.fixed_source)
    && input.job_ids.includes(d.job_id) && (!artifact || (d.job_id===artifact.job_id && d.artifact_revision_id===artifact.artifact_revision_id
      && d.manifest_sha256===artifact.manifest_sha256 && d.bundle_digest===artifact.bundle_digest));
}

async function counts(db,batchIds,datasetVersion) {
  if(Array.isArray(batchIds) && !batchIds.length)return {counts:zeroCounts(),reason_codes:[]};
  const scope=Array.isArray(batchIds)?'SELECT import_batch_id FROM esg.import_batches WHERE import_batch_id=ANY($1::uuid[])'
    :'SELECT import_batch_id FROM esg.import_batches WHERE source_report_id=$1::uuid';
  const parameter=Array.isArray(batchIds)?batchIds:batchIds.report_id;
  const source=(await db.query(`SELECT count(DISTINCT snapshot_id)::int AS n FROM esg.import_source_facts
    WHERE import_batch_id IN (${scope})`,[parameter])).rows[0].n;
  // Classification is parsed as JSON, never guessed from textual regex or an
  // insertion delta. The query is bounded; missing/oversized classifications
  // leave the affected axes unknown while preserving independent SQL counts.
  const records=(await db.query(`SELECT DISTINCT i.disclosure_id,i.bundle_record_id,b.bundle_job_id,m.mapping_reason
    FROM esg.import_items i JOIN esg.import_batches b USING(import_batch_id)
    LEFT JOIN esg.disclosure_standard_mappings m USING(disclosure_id)
    WHERE i.import_batch_id IN (${scope}) ORDER BY i.disclosure_id,i.bundle_record_id LIMIT 10001`,[parameter])).rows;
  const grouped=new Map();
  for(const r of records) {
    let detail;try{detail=JSON.parse(r.mapping_reason);}catch{}
    const roles=grouped.get(r.disclosure_id) || new Set();
    if(detail?.bundle_job_id===r.bundle_job_id && detail?.core_record_id===r.bundle_record_id
      && ['selected_output','internal_dependency'].includes(detail.candidate_scope_role))roles.add(detail.candidate_scope_role);
    grouped.set(r.disclosure_id,roles);
  }
  const classified=records.length<=10000 && [...grouped.values()].every(roles=>roles.size>0);
  const published=(await db.query(`WITH disclosures AS (SELECT DISTINCT disclosure_id FROM esg.import_items WHERE import_batch_id IN (${scope})),
    related AS (SELECT os.observation_id FROM esg.observation_sources os JOIN disclosures d USING(disclosure_id)
      UNION SELECT dl.observation_id FROM esg.derived_observation_lineage dl JOIN esg.extension_operand_lineage el USING(candidate_id)
      JOIN disclosures d USING(disclosure_id))
    SELECT count(DISTINCT related.observation_id)::int AS n FROM related JOIN esg.dataset_membership dm USING(observation_id)
    WHERE dm.dataset_version=$2`,[parameter,datasetVersion])).rows[0].n;
  return {counts:{archived_source_count:source,selected_candidate_count:classified?[...grouped.values()].filter(r=>r.has('selected_output')).length:null,
    internal_candidate_count:classified?[...grouped.values()].filter(r=>r.has('internal_dependency')).length:null,published_observation_count:published},
    reason_codes:classified?[]:['candidate_scope_unclassified']};
}
function archivedStatus(c){return c.selected_candidate_count || c.internal_candidate_count || c.selected_candidate_count===null?'candidates_imported':'source_archived';}

export async function readDeliveryStatus(db,input,{importRoots,bundleLoader=loadBundle}={}) {
  validateScope(input);
  const roots=importRoots || process.env.PLATESG_IMPORT_ROOTS?.split(path.delimiter).filter(Boolean) || [];
  const verified=new Map(),bad=new Map();
  for(const a of input.artifacts) {
    try{verified.set(a.job_id,await verifyArtifact(a,input,roots,bundleLoader));}
    catch(exc){bad.set(a.job_id,{artifact_revision_id:a.artifact_revision_id,status:exc.code==='ENOENT'?'artifact_unavailable':'identity_mismatch',identity_verified:false,
      identity_basis:'unknown',import_batch_id:null,bundle_digest:null,receipt:null,counts:nullCounts(),
      reason_codes:[exc.code==='ENOENT'?'artifact_unavailable':'artifact_identity_mismatch']});}
  }
  const base={schema_version:'delivery-status-v1',checked_at:new Date().toISOString(),native_session_id:input.native_session_id,
    report_ref:input.report_identity.report_ref,report_counts_scope:'fixed_report_version'};
  return db.transaction(async tx=>{
    await tx.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
    const identity=input.report_identity;
    const reports=(await tx.query(`SELECT source_report_id,organization_ref,source_artifact_ref,source_sha256,metadata
      FROM esg.source_report_refs WHERE upstream_system=$1 AND report_ref=$2 AND report_version_ref=$3`,
      [identity.upstream_system,identity.report_ref,identity.report_version_ref])).rows;
    if(reports.some(r=>r.organization_ref!==identity.organization_ref || r.source_artifact_ref!==identity.source_artifact_ref
      || r.source_sha256!==identity.source_sha256 || r.metadata.report_period_start!==identity.report_period_start || r.metadata.report_period_end!==identity.report_period_end))
      return withReasons({...base,status:'identity_mismatch',identity_basis:'unknown',publication_dataset_version:null,
        report_counts:nullCounts(),session_counts:nullCounts(),current_revision_counts:nullCounts(),
        jobs:input.job_ids.map(job_id=>withReasons({job_id,status:'identity_mismatch',identity_basis:'unknown',
          counts:nullCounts(),artifacts:[],reason_codes:['report_identity_mismatch']})),reason_codes:['report_identity_mismatch']});
    const dataset=Number((await tx.query('SELECT COALESCE(max(dataset_version),0) AS n FROM esg.dataset_versions WHERE sealed')).rows[0].n);
    const reportId=reports[0]?.source_report_id || null;
    // Exactly named child batches only. The frozen stage snapshot establishes
    // their parent and IR identity, even if historical source files are gone.
    const batches=reportId?(await tx.query(`SELECT b.import_batch_id,b.bundle_job_id,b.bundle_digest,b.bundle_ref,
      (SELECT jsonb_agg(jsonb_build_object('manifest_digest',e.manifest_digest,'report_session_id',e.manifest_snapshot->'report_session_id',
        'document',jsonb_build_object('document_id',e.manifest_snapshot->'document'->'document_id',
          'ir_run_id',e.manifest_snapshot->'document'->'ir_run_id','ir_revision',e.manifest_snapshot->'document'->'ir_revision')))
        FROM (SELECT * FROM plat.import_stage_events WHERE import_batch_id=b.import_batch_id
          ORDER BY created_at,manifest_digest LIMIT 101) e) AS stages
      FROM esg.import_batches b WHERE b.source_report_id=$1 AND b.bundle_job_id=ANY($2::text[])
      ORDER BY b.created_at,b.import_batch_id LIMIT 1001`,[reportId,input.job_ids])).rows:[];
    const excessive=batches.length>1000 || batches.some(b=>(b.stages || []).length>100);
    // The first terminal exporter omitted a top-level parent projection, while
    // preserving its original request. Verify those exact frozen bytes and the
    // persisted Bundle/stage digests; never synthesize a parent or rewrite it.
    const parentProofs=new Map();
    if(!excessive)for(const b of batches) {
      const stages=(b.stages || []).filter(s=>s.report_session_id==null && documentMatch(s,input));
      if(!stages.length)continue;
      try {
        let a=verified.get(b.bundle_job_id);
        if(!a || a.bundle_digest!==b.bundle_digest || !stages.some(s=>s.manifest_digest===a.manifest_digest)) {
          const raw=await readFile(path.join(b.bundle_ref,'manifest.json')),m=JSON.parse(raw);
          a=await verifyArtifact({job_id:b.bundle_job_id,artifact_revision_id:m.artifact_revision?.revision_id || 'original',
            bundle_path:b.bundle_ref,manifest_sha256:hash(raw)},input,roots,bundleLoader);
        }
        if(a.bundle_digest===b.bundle_digest && stages.some(s=>s.manifest_digest===a.manifest_digest))parentProofs.set(b.import_batch_id,a.manifest_digest);
      } catch{/* Missing historical proof remains unverified, not a guessed parent. */}
    }
    const stageMatch=(b,s)=>fixedMatch(s,input) || (documentMatch(s,input) && parentProofs.get(b.import_batch_id)===s.manifest_digest);
    const matching=batches.filter(b=>(b.stages || []).some(s=>stageMatch(b,s)));
    const unmatched=batches.some(b=>!matching.includes(b));
    const ids=matching.map(b=>b.import_batch_id);
    const receiptRows=(await tx.query(`SELECT r.receipt_id,r.status,r.import_batch_id,r.detail,
      a.attempt_id,a.status AS attempt_status,a.detail AS attempt_detail
      FROM plat.import_receipts r LEFT JOIN LATERAL (SELECT * FROM plat.import_receipt_attempts a
        WHERE a.receipt_id=r.receipt_id ORDER BY a.created_at DESC,a.attempt_id DESC LIMIT 1) a ON true
      WHERE COALESCE(a.detail->>'import_batch_id',r.import_batch_id::text)=ANY($1::text[])
        OR (COALESCE(a.detail,r.detail)->'delivery_identity'->>'native_session_id'=$2
          AND COALESCE(a.detail,r.detail)->'delivery_identity'->>'job_id'=ANY($3::text[])
          AND COALESCE(a.detail,r.detail)->'delivery_identity'->'report_identity'->>'report_ref'=$4)
      ORDER BY r.created_at,r.receipt_id LIMIT 1001`,[ids,input.native_session_id,input.job_ids,identity.report_ref])).rows;
    const receiptOverflow=receiptRows.length>1000;
    const receiptFor=(b,a=null)=>{
      if(receiptOverflow)return null;
      const rows=receiptRows.filter(r=>(r.attempt_detail?.import_batch_id || r.import_batch_id)===b.import_batch_id
        && (r.attempt_status || r.status)==='imported');
      const valid=rows.find(r=>{
        const d=(r.attempt_detail || r.detail)?.delivery_identity;
        return d?descriptorMatches(d,input,a):!a || path.resolve(b.bundle_ref)===a.bundle_path;
      });
      return valid?{receipt_id:valid.receipt_id,current_status:'imported',attempt_id:valid.attempt_id || null,
        identity_basis:(valid.attempt_detail || valid.detail)?.delivery_identity?'verified_descriptor':'sql_batch_stage_match'}:null;
    };
    const reportResult=await counts(tx,reportId?{report_id:reportId}:[],dataset);
    const sessionResult=excessive || unmatched?{counts:nullCounts(),reason_codes:[excessive?'scope_limit_exceeded':'historical_binding_unverified']}:await counts(tx,ids,dataset);
    const currentIds=[],jobs=[];
    for(const job_id of input.job_ids) {
      const history=matching.filter(b=>b.bundle_job_id===job_id),jobResult=excessive || batches.some(b=>b.bundle_job_id===job_id&&!matching.includes(b))
        ?{counts:nullCounts(),reason_codes:['historical_binding_unverified']}:await counts(tx,history.map(b=>b.import_batch_id),dataset);
      let artifact=bad.get(job_id),a=verified.get(job_id);
      if(a) {
        const found=history.find(b=>b.bundle_digest===a.bundle_digest && b.stages.some(s=>s.manifest_digest===a.manifest_digest && stageMatch(b,s)));
        const result=await counts(tx,found?[found.import_batch_id]:[],dataset),receipt=found?receiptFor(found,a):null;
        if(found)currentIds.push(found.import_batch_id);
        const failure=!receiptOverflow && receiptRows.find(r=>(r.attempt_status || r.status)==='failed'
          && descriptorMatches((r.attempt_detail || r.detail)?.delivery_identity,input,a));
        const failedReceipt=failure?{receipt_id:failure.receipt_id,current_status:'failed',attempt_id:failure.attempt_id || null,identity_basis:'verified_descriptor'}:null;
        artifact={artifact_revision_id:a.artifact_revision_id,status:found?(receipt?archivedStatus(result.counts):'committed_without_acknowledgement'):failure?'failed':'not_imported',
          identity_verified:true,identity_basis:receipt?.identity_basis || failedReceipt?.identity_basis || (found?'sql_batch_stage_match':'unknown'),
          import_batch_id:found?.import_batch_id || null,bundle_digest:a.bundle_digest,receipt:receipt || failedReceipt,counts:result.counts,
          reason_codes:[...result.reason_codes,...(found?(receipt?[]:['receipt_unconfirmed']):failure?['delivery_failed']:['not_imported'])]};
      }
      const known=history.length && history.every(b=>receiptFor(b));
      jobs.push(withReasons({job_id,status:artifact?.status || 'no_artifact',identity_basis:known?'sql_batch_stage_match':'unknown',
        counts:jobResult.counts,artifacts:artifact?[withReasons(artifact)]:[],reason_codes:artifact?.reason_codes || ['no_artifact']}));
    }
    const currentResult=bad.size || excessive?{counts:nullCounts(),reason_codes:excessive?['scope_limit_exceeded']:[...new Set([...bad.values()].flatMap(a=>a.reason_codes))]}:await counts(tx,[...new Set(currentIds)],dataset);
    const statuses=jobs.map(j=>j.status),confirmed=statuses.every(s=>['source_archived','candidates_imported'].includes(s));
    const anyCommitted=ids.length>0;
    const status=excessive || unmatched?'unknown':bad.size?([...bad.values()].some(a=>a.status==='identity_mismatch')?'identity_mismatch':'artifact_unavailable')
      :confirmed?archivedStatus(sessionResult.counts)
      :statuses.every(s=>s==='no_artifact')?'no_artifact':statuses.every(s=>s==='committed_without_acknowledgement')?'committed_without_acknowledgement':anyCommitted?'partial'
      :statuses.some(s=>s==='identity_mismatch')?'identity_mismatch':statuses.some(s=>s==='artifact_unavailable')?'artifact_unavailable'
      :statuses.every(s=>s==='failed')?'failed':'not_imported';
    return withReasons({...base,status,identity_basis:confirmed?(jobs.every(j=>j.artifacts[0]?.identity_basis==='verified_descriptor')?'verified_descriptor':'sql_batch_stage_match'):'unknown',
      publication_dataset_version:dataset,report_counts:reportResult.counts,session_counts:sessionResult.counts,current_revision_counts:currentResult.counts,jobs,
      reason_codes:[...new Set([...reportResult.reason_codes,...sessionResult.reason_codes,...currentResult.reason_codes,
        ...(status==='partial'?['partial_delivery']:[]),...(receiptOverflow?['receipt_limit_exceeded']:[])])]});
  });
}
