import {createHash} from 'node:crypto';
const object=value=>value!==null && typeof value==='object' && !Array.isArray(value);
const nonempty=value=>typeof value==='string' && value.trim().length>0;
const stableJSON=value=>JSON.stringify(value,(_,v)=>object(v)
  ? Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
const digest=value=>createHash('sha256').update(value).digest('hex');
const uuid=value=>{const h=digest(value);return `${h.slice(0,8)}-${h.slice(8,12)}-5${h.slice(13,16)}-a${h.slice(17,20)}-${h.slice(20,32)}`;};

function frozenIdentity(bundle) {
  const document=bundle.manifest?.document || {},evidence=bundle.records.evidence_references || [];
  const documentIds=new Set(evidence.map(source=>source.document_id).filter(nonempty));
  const declaredDocument=document.document_id;
  const documentId=declaredDocument || (documentIds.size===1?[...documentIds][0]:null);
  if(!nonempty(documentId) || [...documentIds].some(id=>id!==documentId))
    throw new Error('Report SourceFact archive requires one frozen document identity');
  const declaredRun=bundle.request.ir_run_id || document.ir_run_id;
  if(document.ir_run_id && declaredRun!==document.ir_run_id)
    throw new Error('Report SourceFact request IR differs from its frozen document');
  const versions=new Map();
  for(const source of evidence) {
    if(!nonempty(source.ir_run_id)||!Number.isInteger(source.ir_revision)||source.ir_revision<1)
      throw new Error('Report SourceFact evidence has invalid IR identity');
    const existing=versions.get(source.ir_run_id);
    if(existing && existing!==source.ir_revision)throw new Error('Report SourceFact evidence mixes revisions of one IR run');
    versions.set(source.ir_run_id,source.ir_revision);
  }
  const runId=declaredRun || (versions.size===1?[...versions.keys()][0]:null);
  if(!nonempty(runId))throw new Error('Report SourceFact archive requires a frozen IR identity');
  const revision=document.ir_revision ?? versions.get(runId) ?? null;
  if(revision!=null && (!Number.isInteger(revision)||revision<1))throw new Error('Frozen report IR revision is invalid');
  if(versions.has(runId) && revision!=null && versions.get(runId)!==revision)
    throw new Error('Report SourceFact evidence differs from the frozen IR revision');
  const transition=bundle.evidenceTransition;
  const allowed=new Map([[runId,revision]]);
  if(transition) {
    if(!nonempty(transition.from_ir_run_id)||!nonempty(transition.to_ir_run_id)
      || transition.from_ir_run_id===transition.to_ir_run_id || transition.to_ir_run_id!==runId)
      throw new Error('Report SourceFact evidence transition does not target the frozen IR');
    const priorRevision=transition.from_ir_revision ?? versions.get(transition.from_ir_run_id) ?? null;
    if(priorRevision!=null && (!Number.isInteger(priorRevision)||priorRevision<1))throw new Error('Evidence transition IR revision is invalid');
    allowed.set(transition.from_ir_run_id,priorRevision);
  }
  if([...versions.keys()].some(run=>!allowed.has(run)))throw new Error('Report SourceFact evidence uses an unrelated IR run');
  return {documentId,runId,allowed,basis:declaredDocument?'frozen_manifest':'legacy_single_evidence'};
}
function validateFact(fact,identity) {
  if(!object(fact)||!nonempty(fact.source_fact_id)||!['quantitative','assertion'].includes(fact.kind)
    || !identity.allowed.has(fact.ir_run_id)
    || !Number.isInteger(fact.ir_revision)||fact.ir_revision<1)
    throw new Error('Report SourceFact does not belong to the frozen document/IR identity');
  if(fact.document_id!==identity.documentId)
    throw new Error('Report SourceFact belongs to another report; frozen document mismatch');
  const revision=identity.allowed.get(fact.ir_run_id);
  if(revision!=null && fact.ir_revision!==revision)throw new Error('Report SourceFact differs from the frozen/Core evidence revision');
  for(const key of ['source_row_label','value_raw','statement_raw','unit_raw'])
    if(fact[key]!=null && typeof fact[key]!=='string')throw new Error(`Report SourceFact ${key} must preserve source text`);
  if(fact.identity_raw!=null && !object(fact.identity_raw))throw new Error('Report SourceFact identity_raw must be an object');
  if(fact.quality_status!=null && !['grounded','needs_confirmation','unbound'].includes(fact.quality_status))
    throw new Error('Unsupported report SourceFact quality status');
  const physical=fact.physical_sources ?? [];
  if(!Array.isArray(physical))throw new Error('Report SourceFact physical_sources must be an array');
  for(const source of physical) {
    if(!object(source)||!nonempty(source.ir_object_id)||!Number.isInteger(source.pdf_page_index)||source.pdf_page_index<0
      || source.document_id!=null && source.document_id!==fact.document_id
      || source.ir_run_id!=null && source.ir_run_id!==fact.ir_run_id
      || source.ir_revision!=null && source.ir_revision!==fact.ir_revision)
      throw new Error('Report SourceFact physical source differs from its frozen identity');
  }
  return {physical,quality:physical.length?(fact.quality_status || 'needs_confirmation'):'unbound',
    identity_basis:fact.ir_run_id!==identity.runId?'evidence_transition':identity.basis};
}
/** Archive everything read; Core linkage remains an independent, explicit axis. */
export async function archiveReportSourceFacts(tx,{bundle,source_report_id,import_batch_id,facts,bindings,neutral=false}) {
  if(!facts.size)return {source_fact_snapshots:0};
  const identity=frozenIdentity(bundle);
  for(const [ref,fact] of facts) {
    const validated=validateFact(fact,identity),snapshot_digest=digest(stableJSON(fact));
    const snapshot_id=uuid(`report-source-fact-v1:${source_report_id}:${ref}:${snapshot_digest}`);
    await tx.query(`INSERT INTO esg.report_source_fact_snapshots(snapshot_id,source_report_id,source_fact_ref,snapshot_digest,
      document_id,source_ir_run_id,source_ir_revision,kind,source_row_label,value_raw,statement_raw,unit_raw,identity_raw,
      physical_sources,quality_status,provenance_status,identity_basis,snapshot_payload,fact_role)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19) ON CONFLICT DO NOTHING`,
      [snapshot_id,source_report_id,ref,snapshot_digest,fact.document_id,fact.ir_run_id,fact.ir_revision,fact.kind,
        fact.source_row_label ?? null,fact.value_raw ?? null,fact.statement_raw ?? null,fact.unit_raw ?? null,
        fact.identity_raw || {},validated.physical,validated.quality,validated.physical.length?'physical':'unbound',validated.identity_basis,fact,
        neutral?'neutral':'clause_source']);
    const core_bindings=[...bindings.values()].filter(binding=>binding.source_fact_id===ref)
      .sort((a,b)=>a.core_record_id.localeCompare(b.core_record_id));
    await tx.query(`INSERT INTO esg.import_source_facts(import_batch_id,source_fact_ref,snapshot_id,core_bindings)
      VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING`,[import_batch_id,ref,snapshot_id,core_bindings]);
  }
  return {source_fact_snapshots:facts.size};
}

const cursorError=message=>{throw Object.assign(new Error(message),{code:'invalid_cursor'});};
function cursorTimestamp(value) {
  const match=typeof value==='string'&&/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.\d{6}Z$/.exec(value);
  if(!match)return false;
  const [year,month,day,hour,minute,second]=match.slice(1).map(Number);
  const leap=year%4===0&&(year%100!==0||year%400===0);
  return year>=1&&month>=1&&month<=12&&day>=1&&day<=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31][month-1]
    &&hour<=23&&minute<=59&&second<=59;
}
function decodeSourceCursor(cursor,reportRef) {
  if(cursor==null)return null;
  if(typeof cursor!=='string'||!cursor.length||cursor.length>2048||!/^[A-Za-z0-9_-]+$/.test(cursor))
    cursorError('来源事实分页游标无效，请从当前报告第一页重新读取。');
  let parsed;
  try {
    const bytes=Buffer.from(cursor,'base64url'),raw=bytes.toString('utf8');
    if(bytes.toString('base64url')!==cursor)throw new Error('noncanonical encoding');
    parsed=JSON.parse(raw);
    if(!object(parsed)||JSON.stringify(parsed)!==raw||Object.keys(parsed).sort().join(',')!=='created_at,report_ref,snapshot_id,v'
      ||parsed.v!==1||!cursorTimestamp(parsed.created_at)
      ||typeof parsed.snapshot_id!=='string'||!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(parsed.snapshot_id))
      throw new Error('invalid cursor payload');
  } catch {cursorError('来源事实分页游标格式或版本无效，请从第一页重新读取。');}
  if(parsed.report_ref!==reportRef)cursorError('分页游标属于另一报告范围，请从所选报告第一页重新读取。');
  return parsed;
}

export async function readReportSourceFacts(db,{report_ref=null,limit=200,cursor=null}={}) {
  if(!Number.isInteger(limit)||limit<1||limit>1000)throw new Error('Report SourceFact limit must be from 1 to 1000');
  if(report_ref!=null && (!nonempty(report_ref)||report_ref.length>512))throw new Error('Report SourceFact report_ref must be a nonempty reference');
  const anchor=decodeSourceCursor(cursor,report_ref);
  const rows=(await db.query(`SELECT s.snapshot_id,s.source_fact_ref,s.snapshot_digest,s.source_row_label,s.kind,s.value_raw,s.statement_raw,s.unit_raw,
    s.identity_raw,s.quality_status,s.provenance_status,s.identity_basis,s.source_ir_run_id,s.source_ir_revision,
    r.report_ref,r.report_title_snapshot AS report_title,r.source_artifact_ref,r.source_sha256,s.document_id,s.physical_sources,s.created_at,
    to_char(s.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS _cursor_created_at,
    COALESCE((SELECT sum(jsonb_array_length(link.core_bindings)) FROM esg.import_source_facts link WHERE link.snapshot_id=s.snapshot_id),0)::int AS core_binding_count,
    (SELECT jsonb_agg(jsonb_build_object('import_batch_id',b.import_batch_id,'job_id',b.bundle_job_id,
      'package_id',b.package_id,'package_version',b.package_version,'core_binding_count',jsonb_array_length(link.core_bindings))
      ORDER BY b.created_at,b.import_batch_id) FROM esg.import_source_facts link JOIN esg.import_batches b USING(import_batch_id)
      WHERE link.snapshot_id=s.snapshot_id) AS import_batches
    FROM esg.report_source_fact_snapshots s JOIN esg.source_report_refs r USING(source_report_id)
    WHERE ($1::text IS NULL OR r.report_ref=$1)
    AND ($2::timestamptz IS NULL OR (s.created_at,s.snapshot_id)>($2::timestamptz,$3::uuid))
    ORDER BY s.created_at,s.snapshot_id LIMIT $4`,[report_ref,anchor?.created_at || null,anchor?.snapshot_id || null,limit+1])).rows;
  const hasMore=rows.length>limit,last=rows[Math.min(rows.length,limit)-1];
  const nextCursor=hasMore?Buffer.from(JSON.stringify({v:1,report_ref,created_at:last._cursor_created_at,snapshot_id:last.snapshot_id})).toString('base64url'):null;
  return {schema_version:'report-source-facts-v1',items:rows.slice(0,limit).map(({_cursor_created_at,...row})=>({...row,
    mapping_state:row.core_binding_count?'has_core':'unmapped',publication_status:'source_only_not_published'})),
    limit,has_more:hasMore,next_cursor:nextCursor,scope:'materialized_report_sources_not_selected_output_or_published_observations'};
}
