import type {Row} from './api';

/**
 * One projection for every page: report identity, evidence, extraction, handoff,
 * review and publication. Pages read this instead of composing their own state,
 * so the library, the run monitor, review and publishing cannot disagree.
 */
export type ReportRun={
 assetId:string;
 reportRef:string;
 title:string;
 fileName:string;
 publisher:string;
 year:number|undefined;
 code:string;
 identity:{state:'complete'|'incomplete';missing:string[]};
 evidence:{state:'none'|'running'|'failed'|'ready';ocr:number;ir:number;irRunId?:string};
 extraction:{state:'none'|'queued'|'running'|'ready'|'failed';sessions:Row[];latest?:Row;selectedMetrics:number};
 handoff:{state:'not_started'|'pending'|'delivered'|'failed';detail?:string};
 review:{state:'none'|'review'|'ready'|'blocked';total:number;pending:number;approved:number;blocked:number};
 publication:{state:'draft'|'published'|'withdrawn'|'deleted';version:number;observations:number;publicVisible:boolean};
 stage:string;
 blockingReason?:string;
 nextAction:{code:string;label:string;route:string};
 activeTasks:Row[];
};

const IDENTITY_KEYS=['title','publisher_name','publisher_entity_ref','reporting_year','period_start','period_end','language','report_kind'];
const STAGE_ORDER=['identity','evidence','extraction','handoff','review','publication'];

export function identityGaps(asset:Row):string[]{
 const metadata=asset.report_metadata || {};
 return IDENTITY_KEYS.filter(key=>metadata[key]===null||metadata[key]===undefined||String(metadata[key]).trim()==='');
}
export function assetTitle(asset:Row):string{
 const metadata=asset.report_metadata || {};
 return String(metadata.title || asset.title || asset.file_name || asset.asset_id);
}
function assetCode(asset:Row):string{
 const metadata=asset.report_metadata || {};
 if(asset.report_code)return String(asset.report_code);
 return metadata.publisher_entity_ref&&metadata.reporting_year?`${metadata.publisher_entity_ref}/${metadata.reporting_year}`:'';
}
export function reportRefOf(asset:Row):string{
 return String(asset.report_id || asset.report_ref || '');
}
function ocrVersions(asset:Row):Row[]{return Array.isArray(asset.ocr_versions)?asset.ocr_versions:[];}
function irVersions(ocr:Row):Row[]{return Array.isArray(ocr.ir_versions)?ocr.ir_versions:(ocr.linked_ir_ids || []).map((id:string)=>({run_id:id}));}

/** Tasks carry a display title, not a foreign key; match the report it names. */
export function taskMatchesAsset(task:Row,asset:Row):boolean{
 if(task.asset_id&&task.asset_id===asset.asset_id)return true;
 if(task.report_ref&&asset.report_id&&task.report_ref===asset.report_id)return true;
 const title=String(task.title||'').replace(/^(?:Document IR|报告提取|OCR|IR 定向修复)\s*·\s*/,'').trim();
 if(!title)return false;
 const name=String(asset.file_name||'').trim();
 const stem=name.replace(/\.pdf$/i,'');
 const reportTitle=String(asset.report_metadata?.title||'').trim();
 return Boolean(name&&title.includes(name))||Boolean(stem&&stem.length>6&&title.includes(stem))||Boolean(reportTitle&&reportTitle.length>6&&title.includes(reportTitle));
}
function sessionMatchesAsset(session:Row,asset:Row):boolean{
 const input=session.request?.input || {};
 if(input.asset_id&&input.asset_id===asset.asset_id)return true;
 const name=String(session.name||'');
 const stem=String(asset.file_name||'').replace(/\.pdf$/i,'');
 const title=String(asset.report_metadata?.title||'');
 return (title.length>6&&name.includes(title))||(stem.length>6&&name.includes(stem));
}

export function buildReportRun(asset:Row,context:{sessions:Row[];publications:Row[];tasks:Row[]}):ReportRun{
 const metadata=asset.report_metadata || {};
 const missing=identityGaps(asset);
 const ocr=ocrVersions(asset);
 const structures=ocr.flatMap(irVersions);
 const irRunId=asset.ir_run_id || structures.find(row=>row.run_id)?.run_id;
 const failed=String(asset.processing_status||'')==='failed'||ocr.some(row=>row.status==='failed');
 const busy=String(asset.processing_status||'')==='running'||ocr.some(row=>['running','queued'].includes(String(row.status)));
 const evidenceState=busy?'running':failed?'failed':structures.length?'ready':ocr.length?'running':'none';

 const sessions=context.sessions.filter(session=>sessionMatchesAsset(session,asset));
 const latest=[...sessions].sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')))[0];
 const execution=String(latest?.execution_status||'');
 const extractionState=!sessions.length?'none'
  :['running','queued','waiting_credentials','planned'].includes(execution)?'running'
  :['failed','interrupted'].includes(execution)||['failed'].includes(String(latest?.result_status))?'failed'
  :'ready';
 const counts=latest?.counts || {};
 const selectedMetrics=Number(counts.selected_metrics || counts.original_output_count || 0);

 const publication=context.publications.find(row=>reportRefOf(asset)&&String(row.report_ref)===reportRefOf(asset));
 const publicationState=String(publication?.status || 'draft') as ReportRun['publication']['state'];
 const total=Number(publication?.candidate_count || 0);
 const approved=Number(publication?.approved_count || 0);
 const pending=Number(publication?.pending_count ?? Math.max(0,total-approved));
 const blocked=Number(publication?.blocked_count || 0);
 const reviewState=publicationState==='published'?'ready':blocked?'blocked':approved>0&&pending===0?'ready':total||pending?'review':'none';

 const delivered=String(latest?.database_status||'')==='imported'||approved>0;
 const handoffState=!sessions.length?'not_started':delivered?'delivered':extractionState==='running'?'pending':'failed';
 const handoffDetail=!sessions.length?undefined:delivered?undefined:`成果尚未交付到数据平台（${String(latest?.database_status||'未交付')}）`;

 const activeTasks=context.tasks.filter(task=>taskMatchesAsset(task,asset));
 const run:ReportRun={
  assetId:String(asset.asset_id),reportRef:reportRefOf(asset),title:assetTitle(asset),fileName:String(asset.file_name||''),
  publisher:String(metadata.publisher_name||''),year:typeof metadata.reporting_year==='number'?metadata.reporting_year:undefined,
  code:assetCode(asset),
  identity:{state:missing.length?'incomplete':'complete',missing},
  evidence:{state:evidenceState,ocr:ocr.length,ir:structures.length,irRunId},
  extraction:{state:extractionState,sessions,latest,selectedMetrics},
  handoff:{state:handoffState,detail:handoffDetail},
  review:{state:reviewState,total,pending,approved,blocked},
  publication:{state:publicationState,version:Number(publication?.publication_version||0),
   observations:Number(publication?.observation_count||0),publicVisible:publication?.public_visible===true},
  stage:'identity',nextAction:{code:'identity',label:'补身份',route:`/reports/${encodeURIComponent(String(asset.asset_id))}`},
  activeTasks,
 };
 Object.assign(run,decide(run));
 return run;
}

/** One primary action per report; everything else stays secondary. */
function decide(run:ReportRun):Pick<ReportRun,'stage'|'blockingReason'|'nextAction'>{
 const assetRoute=`/reports/${encodeURIComponent(run.assetId)}`;
 const extractRoute=`/extractions/new?asset=${encodeURIComponent(run.assetId)}${run.evidence.irRunId?`&ir=${encodeURIComponent(run.evidence.irRunId)}`:''}`;
 const identityFirst=():Pick<ReportRun,'stage'|'blockingReason'|'nextAction'>=>({stage:'identity',
  blockingReason:`报告身份缺 ${run.identity.missing.length} 项：${run.identity.missing.join('、')}`,
  nextAction:{code:'identity',label:`补身份（${run.identity.missing.length}）`,route:assetRoute}});
 // Identity never blocks reading or extraction; it blocks delivery and publishing,
 // because a report without a registered identity cannot become a published source.
 if(run.evidence.state==='running')return {stage:'evidence',nextAction:{code:'evidence_progress',label:'查看读取进度',route:`${assetRoute}/processing`}};
 if(run.evidence.state==='failed')return {stage:'evidence',blockingReason:'页面读取失败，需要重新提取',
  nextAction:{code:'evidence_retry',label:'重新提取',route:extractRoute}};
 if(run.evidence.state==='none')return {stage:'evidence',nextAction:{code:'extract',label:'提取指标',route:extractRoute}};
 if(run.extraction.state==='failed')return {stage:'extraction',blockingReason:'提取任务失败或中断，成果可能不完整',
  nextAction:{code:'extraction_retry',label:'重新提取',route:extractRoute}};
 if(run.extraction.state==='running'||run.extraction.state==='queued')return {stage:'extraction',
  nextAction:{code:'extraction_progress',label:'查看提取成果',route:`/extractions/${encodeURIComponent(String(run.extraction.latest?.session_id||''))}`}};
 if(run.extraction.state==='none')return {stage:'extraction',nextAction:{code:'extract',label:'提取指标',route:extractRoute}};
 if(run.publication.state==='published')return {stage:'publication',
  nextAction:{code:'publication_view',label:'查看发布',route:`/publication?report=${encodeURIComponent(run.reportRef)}`}};
 if(run.handoff.state==='failed'&&run.identity.state==='incomplete')return identityFirst();
 if(run.review.state==='blocked')return {stage:'review',blockingReason:`${run.review.blocked} 条候选存在发布阻塞`,
  nextAction:{code:'review_blocked',label:'处理发布阻塞',route:`/review?report=${encodeURIComponent(run.reportRef)}`}};
 if(run.handoff.state==='failed')return {stage:'handoff',blockingReason:run.handoff.detail,
  nextAction:{code:'handoff',label:'交付并审核',route:`/extractions/${encodeURIComponent(String(run.extraction.latest?.session_id||''))}`}};
 if(run.review.total===0)return {stage:'handoff',nextAction:{code:'handoff',label:'交付并审核',route:`/extractions/${encodeURIComponent(String(run.extraction.latest?.session_id||''))}`}};
 if(run.review.state==='review')return {stage:'review',blockingReason:`${run.review.pending} 条候选待核对`,
  nextAction:{code:'review',label:`审核（${run.review.pending}）`,route:`/review?report=${encodeURIComponent(run.reportRef)}`}};
 if(run.review.approved>0&&run.identity.state==='incomplete')return identityFirst();
 if(run.review.approved>0)return {stage:'publication',nextAction:{code:'publish',label:'发布该报告',route:`/publication?report=${encodeURIComponent(run.reportRef)}`}};
 return {stage:'review',nextAction:{code:'review',label:'进入审核',route:`/review?report=${encodeURIComponent(run.reportRef)}`}};
}

export function buildReportRuns(input:{assets:Row[];sessions?:Row[];publications?:Row[];tasks?:Row[]}):{runs:ReportRun[];unlinkedTasks:Row[]}{
 const sessions=input.sessions || [],publications=input.publications || [],tasks=input.tasks || [];
 const runs=input.assets.map(asset=>buildReportRun(asset,{sessions,publications,tasks}));
 const linked=new Set(runs.flatMap(run=>run.activeTasks.map(task=>String(task.task_id))));
 return {runs,unlinkedTasks:tasks.filter(task=>!linked.has(String(task.task_id)))};
}

export function runCounts(runs:ReportRun[]){
 const count=(predicate:(run:ReportRun)=>boolean)=>runs.filter(predicate).length;
 return {
  all:runs.length,
  identity:count(run=>run.identity.state==='incomplete'),
  evidence:count(run=>run.evidence.state==='none'||run.evidence.state==='running'||run.evidence.state==='failed'),
  review:count(run=>run.review.state==='review'||run.review.state==='blocked'),
  publish:count(run=>run.review.state==='ready'&&run.publication.state!=='published'),
  published:count(run=>run.publication.state==='published'),
  blocked:count(run=>Boolean(run.blockingReason)&&run.publication.state!=='published'),
 };
}

/** Stage chips for the run monitor: each report shows the same six axes everywhere. */
export function stageChips(run:ReportRun){
  const state=(key:string):'done'|'active'|'blocked'|'todo'=>({
   identity:run.identity.state==='complete'?'done':run.stage==='identity'?'active':'todo',
   evidence:run.evidence.state==='ready'?'done':run.stage==='evidence'?(run.evidence.state==='failed'?'blocked':'active'):'todo',
   extraction:run.extraction.state==='ready'?'done':run.stage==='extraction'?(run.extraction.state==='failed'?'blocked':'active'):'todo',
   handoff:run.handoff.state==='delivered'?'done':run.stage==='handoff'?(run.handoff.state==='failed'?'blocked':'active'):'todo',
   review:run.review.state==='ready'?'done':run.stage==='review'?(run.review.state==='blocked'?'blocked':'active'):'todo',
   publication:run.publication.state==='published'?'done':run.stage==='publication'?'active':'todo',
  } as Record<string,'done'|'active'|'blocked'|'todo'>)[key];
  const labels:Record<string,string>={identity:'身份',evidence:'证据',extraction:'提取',handoff:'交付',review:'审核',publication:'发布'};
  return STAGE_ORDER.map(key=>({key,label:labels[key],state:state(key)}));
}
