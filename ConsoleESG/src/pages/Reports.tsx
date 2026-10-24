import {useEffect,useMemo,useRef,useState,type FormEvent} from 'react';
import {assetFile,credentials,items,navigate,post,reportLabel,request,verifiedEvidence,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Actions,Badge,dateLabel,Diagnostics,Field,Notice,ResourceState,Section} from '../components/ui';
import {EvidenceExplorer} from '../components/EvidenceExplorer';
import {RecordManagement} from '../components/RecordManagement';
import {BatchRecordDelete} from '../components/BatchRecordDelete';
import {startLibraryProduction} from '../library-workflow';
import '../library.css';
import {useLibraryResource} from '../library-resource';
import {isArchived,workActions} from '../management-api';

export const reportTabs=[['overview','概览'],['source','原文'],['processing','处理与证据'],['extraction','提取'],['review','审核'],['publication','发布记录']];

function reportItems(value:Row|undefined){return items(value,'reports').length?items(value,'reports'):items(value,'assets').length?items(value,'assets'):items(value);}

/**
 * Extraction state of one PDF, in operator terms.
 *
 * Reading is the first step of extraction, so the row speaks about extraction
 * only: not started, working, ready, or failed. The page-reading versions stay
 * available underneath for inspection, never as a separate entry point.
 */
function readState(asset:Row, versions:Row[]){
  if(asset.processing_status==='running'||versions.some(v=>['running','queued'].includes(v.status)))return {key:'running',label:'提取中',tone:'running',next:'查看进度'};
  // A failed run with no readable version must still read as failed; otherwise
  // it is indistinguishable from a report that was never started.
  if(asset.processing_status==='failed'||versions.some(v=>v.status==='failed'))return {key:'failed',label:'提取失败',tone:'failed',next:'重新提取'};
  if(!versions.length)return {key:'new',label:'未提取',tone:'',next:'提取指标'};
  const structures=versions.flatMap(irVersions);
  if(!structures.length)return {key:'read',label:'读取中 · 待建证据',tone:'review',next:'继续提取'};
  return {key:'ready',label:'已读取',tone:'ready',next:'重新提取'};
}

const FILTERS=[['all','全部'],['unread','未提取'],['ready','已读取'],['failed','有问题']] as const;

export function Reports({params=new URLSearchParams()}:{params?:URLSearchParams}){
 const {refresh}=useWorkspace();
 const [query,setQuery]=useState(''),[filter,setFilter]=useState<string>('all'),[selected,setSelected]=useState<Set<string>>(new Set()),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[failedUploads,setFailedUploads]=useState<File[]>([]);
 const resource=useLibraryResource('report'),profile=useResource('document','/api/pipeline/extraction-profiles/platform-major?topics=E1,E2,E3'),catalog=useResource('document','/api/pipeline/standards/catalog');
 const rows=items(resource.data),unlinked=items(resource.data,'unlinked_ocr'),unlinkedIr=items(resource.data,'unlinked_ir');
 const search=query.trim().toLowerCase();
 const matches=(row:Row)=>`${reportLabel(row)} ${row.file_name || ''} ${row.run_id || ''} ${row.document_label || ''}`.toLowerCase().includes(search);
 const described=rows.map(asset=>{const versions=ocrVersions(asset);return {asset,versions,state:readState(asset,versions)};});
 const visible=described.filter(({asset,versions,state})=>{
  if(!matches(asset)&&!versions.some(matches))return false;
  if(filter==='unread')return state.key==='new';
  if(filter==='ready')return state.key==='ready';
  if(filter==='failed')return state.key==='failed';
  return true;
 });
 const visibleUnlinked=unlinked.filter(matches),visibleUnlinkedIr=unlinkedIr.filter(matches);

 async function uploadFiles(files:File[]){
  setBusy(true);const messages:string[]=[];const failed:File[]=[];
  for(const file of files){try{const body=new FormData();body.append('file',file);const asset=await request('document','/api/report-assets',{method:'POST',body});await post('document','/api/reports',{asset_id:asset.asset_id});messages.push(`${file.name}：已加入`);}catch(e){failed.push(file);messages.push(`${file.name}：${(e as Error).message}`);}}
  setFailedUploads(failed);setMessage(messages.join('；'));setBusy(false);refresh();
 }
 async function upload(event:FormEvent<HTMLFormElement>){
  event.preventDefault();const form=event.currentTarget;const files=Array.from((form.elements.namedItem('file') as HTMLInputElement).files || []);form.reset();
  await uploadFiles(files);
 }
 async function start(assets:Row[],extract:boolean){setBusy(true);setMessage('');try{await startLibraryProduction(assets,extract?'processing_extraction':'processing',profile.data,catalog.data,!extract&&assets.some(a=>ocrVersions(a).length>0)?'reprocess':'auto');setMessage('已加入处理队列，右下角查看处理状态。');refresh();}catch(e){setMessage((e as Error).message);}finally{setBusy(false);}}
 const unavailable=busy||resource.data?.compatibility_view;
 const selectedRows=rows.filter(a=>selected.has(a.asset_id));
 const ocrCount=resource.data?.ocr_count ?? new Set([...rows.flatMap(ocrVersions),...unlinked].map(r=>r.run_id)).size;
 const pendingDeletions=items(resource.data,'pending_deletions');
 const counts={all:rows.length,unread:described.filter(d=>d.state.key==='new').length,ready:described.filter(d=>d.state.key==='ready').length,failed:described.filter(d=>d.state.key==='failed').length};

 return <>
  <div className="page-heading">
    <div><span className="eyebrow">第 1 步 · 报告</span><h1>报告资产</h1><p>选一份 PDF 就能开始提取；读取页面与构建结构证据在提取中自动完成。</p></div>
    <div className="asset-summary"><span><strong>{rows.length}</strong> 份 PDF</span><span><strong>{ocrCount}</strong> 个读取版本</span><span><strong>{described.filter(d=>d.state.key==='new').length}</strong> 未提取</span></div>
  </div>
  {resource.data?.compatibility_view&&<Notice>当前显示已有服务的来源关系。新的处理与删除入口将在服务更新后启用，正在执行的任务继续处理。</Notice>}
  {pendingDeletions.length>0&&<div className="asset-pending-deletions">{pendingDeletions.map(pending=>{
   const name=pending.kind==='pdf'?rows.find(row=>row.asset_id===pending.identity)?.file_name:pending.identity;
   return <Notice key={`${pending.kind}:${pending.identity}`} tone={pending.status==='deletion_failed'?'error':'info'}><strong>{name || pending.identity}</strong> · {pending.status==='deletion_failed'?`删除未完成：${pending.error || '请刷新后重试删除'}`:'正在停止关联任务，退出后自动删除。'}</Notice>;
  })}</div>}

  <Section title="加入报告">
    <form onSubmit={upload} className="upload-form"><input aria-label="选择PDF文件" name="file" type="file" accept="application/pdf,.pdf" multiple required/><button disabled={busy}>上传 PDF</button>{failedUploads.length>0&&<button type="button" className="secondary" disabled={busy} onClick={()=>void uploadFiles(failedUploads)}>重试失败的 {failedUploads.length} 个文件</button>}<small className="muted">上传后直接点“提取指标”；身份信息不靠文件名推断。</small></form>
    {message&&<Notice>{message}</Notice>}
  </Section>

  <Section title="报告" action={selectedRows.length>0?<BatchRecordDelete rows={selectedRows} kind="asset" idKey="asset_id" label="删除所选报告"/>:undefined}>
   <div className="library-toolbar">
    <input aria-label="搜索报告资产" placeholder="搜索 PDF、报告名称或版本编号" value={query} onChange={e=>setQuery(e.target.value)}/>
    <div className="library-filters" role="group" aria-label="按读取状态筛选">
     {FILTERS.map(([key,title])=><button type="button" key={key} className={filter===key?'secondary selected':'ghost'} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{title} <small>{counts[key]}</small></button>)}
    </div>
    {selectedRows.length>0&&<div className="library-main-actions">
     <button disabled={unavailable||!profile.data||!!profile.error||!!catalog.error} onClick={()=>start(selectedRows,true)}>提取主要指标（{selectedRows.length} 份）</button>
    </div>}
   </div>
   <ResourceState error={resource.error || profile.error || catalog.error} loading={resource.loading&&!resource.data} empty={!!resource.data&&!visible.length&&!visibleUnlinked.length&&!visibleUnlinkedIr.length}/>
   <div className="asset-list" role="list">
    {visible.map(({asset,versions,state})=>{
     return <article className={`asset-row ${state.tone}`} key={asset.asset_id} id={`asset-${asset.asset_id}`} role="listitem">
      <div className="asset-main">
       <input type="checkbox" aria-label={`选择 ${reportLabel(asset)}`} checked={selected.has(asset.asset_id)} onChange={e=>{const next=new Set(selected);e.target.checked?next.add(asset.asset_id):next.delete(asset.asset_id);setSelected(next);}}/>
       <span className="asset-file-icon" aria-hidden="true">PDF</span>
       <div className="asset-parent-name">
        <button className="text-button asset-title" title={reportLabel(asset)} onClick={()=>navigate(`/reports/${encodeURIComponent(asset.asset_id)}`)}>{reportLabel(asset)}</button>
        <small>
         {asset.report_metadata?.title?`${asset.file_name} · `:''}
         {typeof asset.size_bytes==='number'?`${(asset.size_bytes/1024/1024).toFixed(1)} MB · `:''}
         {dateLabel(asset.created_at || asset.modified_at)}
         {versions.length?` · ${versions.length} 个读取版本可复用`:''}
        </small>
        {asset.state_notice&&<small className="asset-note">{asset.state_notice}</small>}
       </div>
       <span className={`asset-state ${state.tone}`}>{state.label}</span>
       <div className="asset-primary-action">
        {state.key==='running'
         ? <button className="secondary" onClick={()=>navigate(`/reports/${encodeURIComponent(asset.asset_id)}/processing`)}>查看进度</button>
         : <button onClick={()=>navigate(`/extractions/new?asset=${encodeURIComponent(asset.asset_id)}`)}>{state.next}</button>}
       </div>
       <details className="asset-more"><summary aria-label={`${reportLabel(asset)} 的更多操作`}>⋯</summary>
        <div className="library-row-actions">
         <button className="secondary" disabled={unavailable||!profile.data||!!profile.error||!!catalog.error} onClick={()=>start([asset],true)}>直接提取主要指标</button>
         <RecordManagement record={asset} kind="asset" id={asset.asset_id} compact/>
        </div>
       </details>
      </div>
     </article>;
    })}
   </div>
   {visibleUnlinked.length>0&&<div className="unlinked-ocr"><h3>未关联原 PDF · {visibleUnlinked.length} 个页面读取版本</h3><p>未找到明确匹配的 PDF 来源，可查看或删除现有版本。</p>{visibleUnlinked.map((row,index)=><OcrVersion key={row.run_id} row={row} index={index}/>)}</div>}
   {visibleUnlinkedIr.length>0&&<div className="unlinked-ocr"><h3>未关联读取版本的结构证据 · {visibleUnlinkedIr.length} 个版本</h3><p>原读取版本已不存在或未明确关联，可查看或删除这些结构记录。</p>{visibleUnlinkedIr.map(ir=><StructureVersion key={ir.run_id || ir.ir_run_id} row={ir}/>)}</div>}
   <details className="major-profile"><summary>本次主要指标 · {profile.data?.output_count ?? '…'} 项</summary><div className="chips">{profile.data?.selection_snapshot?.source_datapoint_ids?.map((id:string)=><span className="clause-chip" key={id}>{id}</span>)}</div><button className="text-button" onClick={()=>navigate('/extractions/new')}>自定义指标与高级设置</button></details>
  </Section>
 </>;
}

export function ocrVersions(asset:Row):Row[]{return Array.isArray(asset.ocr_versions)?asset.ocr_versions:[];}
export function irVersions(ocr:Row):Row[]{return Array.isArray(ocr.ir_versions)?ocr.ir_versions:(ocr.linked_ir_ids || []).map((id:string)=>({run_id:id}));}

export function StructureVersion({row,asset}:{row:Row,asset?:Row}){
 const id=row.run_id || row.ir_run_id;
 return <div className="structure-version"><span className="structure-node" aria-hidden="true">↳</span><span className="mono">{id}</span>{row.status&&<Badge value={row.status}/>}<button className="text-button" onClick={()=>navigate(`/processing?ir=${encodeURIComponent(id)}`)}>查看结构</button><button className="secondary" onClick={()=>navigate(`/extractions/new?${asset?`asset=${encodeURIComponent(asset.asset_id)}&`:''}ir=${encodeURIComponent(id)}`)}>按此版本选择指标</button>{row.available_actions?.length>0&&<RecordManagement record={row} kind="ir" id={id} compact/>}</div>;
}

function OcrVersion({row,asset,index}:{row:Row,asset?:Row,index:number}){
 const structures=irVersions(row);
 return <div className="ocr-version" data-ocr-id={row.run_id}>
  <div className="ocr-version-main"><span className="ocr-file-icon" aria-hidden="true">OCR</span><div className="ocr-version-name"><strong>读取版本 {index+1}</strong><span className="mono">{row.run_id}</span><small>{row.page_count===undefined?'页数未记录':`${row.page_count} 页`} · {dateLabel(row.completed_at || row.updated_at || row.created_at)}</small>{!asset&&<small>{row.document_label || row.title || '报告名称未记录'}</small>}</div><Badge value={row.status}/><div className="library-row-actions"><button className="secondary" onClick={()=>navigate(`/ocr/${encodeURIComponent(row.run_id)}`)}>查看读取结果</button><RecordManagement record={row} kind="ocr" id={row.run_id} compact/></div></div>
  {structures.length>0?<div className="ocr-structures"><span className="structure-caption">结构证据 · {structures.length} 个版本</span>{structures.map(ir=><StructureVersion key={ir.run_id || ir.ir_run_id} row={ir} asset={asset}/>)}</div>:<p className="ocr-structure-empty">尚无结构证据；报告处理完成结构构建后，可用于指标提取。</p>}
 </div>;
}

export function ReportDetail({assetId,tab='overview'}:{assetId:string,tab?:string}){
 const resource=useLibraryResource('report');const asset=items(resource.data).find(a=>a.asset_id===assetId);const [identityOpen,setIdentityOpen]=useState(false);const identity=useResource('document',identityOpen&&asset?.report_id?`/api/reports/${encodeURIComponent(asset.report_id)}`:null);
 if(tab!=='overview'&&tab!=='source')return <ReportWorkbenchDetail assetId={assetId} tab={tab}/>;
 return <><div className="page-heading"><div><button className="text-button" onClick={()=>navigate(`/reports?asset=${encodeURIComponent(assetId)}`)}>← 报告资产</button><h1>{asset?reportLabel(asset):'PDF 报告'}</h1><p>PDF 原文 → 页面读取版本 → 结构证据 → 提取</p></div><div className="button-row"><button className="secondary" onClick={()=>navigate(`/reports?asset=${encodeURIComponent(assetId)}`)}>全部版本</button><button disabled={!asset} onClick={()=>navigate(`/extractions/new?asset=${encodeURIComponent(assetId)}`)}>用这份报告提取</button></div></div><ResourceState error={resource.error} loading={resource.loading&&!asset}/>{asset&&<><Section title="PDF 原文"><iframe className="pdf-library-preview" title="PDF报告原文" src={assetFile(assetId)}/></Section><Section title={`这份 PDF 的页面读取版本 · ${ocrVersions(asset).length}`}>{ocrVersions(asset).map((row,index)=><OcrVersion key={row.run_id} row={row} asset={asset} index={index}/>)}{!ocrVersions(asset).length&&<p className="muted">尚未生成页面读取版本。</p>}</Section><details className="panel" open={identityOpen} onToggle={e=>setIdentityOpen(e.currentTarget.open)}><summary>报告信息</summary>{identity.data?<MetadataEditor report={identity.data}/>:<ResourceState error={identity.error} loading={identity.loading}/>}<p>{asset.file_name}</p></details><RecordManagement record={asset} kind="asset" id={assetId}/></>}</>;
}

function ReportWorkbenchDetail({assetId,tab='overview'}:{assetId:string,tab?:string}){
 const {refresh}=useWorkspace();const assets=useResource('document','/api/report-assets?include_archived=true');const aggregate=useResource('document',`/api/console/reports/${encodeURIComponent(assetId)}`);const asset=aggregate.data?.asset_id ? aggregate.data : aggregate.data?.asset || items(assets.data,'assets').find(a=>a.asset_id===assetId);const reportId=asset?.report_id;const identity=useResource('document',reportId?`/api/reports/${encodeURIComponent(reportId)}`:null);const processing=useResource('document','/api/report-processing/runs?include_archived=true');const extraction=useResource('document','/api/report-sessions?include_archived=true&view=summary');const candidates=useResource('publication',reportId?`/internal/v1/candidates?report_ref=${encodeURIComponent(reportId)}`:null,true);
 const standaloneJobs:Row[]=aggregate.data?.standalone_jobs || [];const runs:Row[]=aggregate.data?.processing_runs || items(processing.data,'runs').filter(r=>r.asset_id===assetId);const sessions:Row[]=aggregate.data?.extraction_sessions || items(extraction.data,'sessions').filter(s=>s.request?.input?.asset_id===assetId || s.source_asset_id===assetId || s.asset_id===assetId);const irId=asset?.ir_run_id || runs.find(r=>r.ir_run_id)?.ir_run_id;const metadata=identity.data?.metadata || asset?.report_metadata || {};const [message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 async function register(){setBusy(true);try{await post('document','/api/reports',{asset_id:assetId});refresh();}catch(e){setMessage((e as Error).message);}finally{setBusy(false);}}
 const rows:Row[]=(aggregate.data?.candidates || items(candidates.data)).filter((c:Row)=>c.report_ref===reportId || c.report_ref===assetId || c.source_sha256 && c.source_sha256===asset?.sha256);const records:Row[]=aggregate.data?.publication_records || rows.filter(c=>c.publication_status==='published');
 return <><div className="page-heading"><div><button className="text-button" onClick={()=>navigate('/reports')}>← 报告库</button><h1>{asset?reportLabel(asset):'报告详情'}</h1><p>{metadata.publisher_name || '发布者待确认'} · {metadata.reporting_year || '报告期待确认'}</p></div><div className="button-row"><button disabled={!asset||isArchived(asset)} onClick={()=>navigate(`/extractions/new?asset=${encodeURIComponent(assetId)}${irId?`&ir=${encodeURIComponent(irId)}`:''}`)}>提取指标</button></div></div><div className="tabs" role="tablist">{reportTabs.map(([code,title])=><button role="tab" aria-selected={tab===code} key={code} onClick={()=>navigate(`/reports/${assetId}/${code}`)}>{title}</button>)}</div><ResourceState error={assets.error || aggregate.error} loading={!asset&&assets.loading}/>{message && <Notice tone="error">{message}</Notice>}{asset && <>
 <Section title="资产管理"><RecordManagement record={asset} kind="asset" id={assetId}/></Section>
 {tab==='overview' && <><div className="stat-grid"><article><small>处理</small><Badge value={asset.processing_status}/></article><article><small>提取任务</small><strong>{sessions.length}</strong></article><article><small>待审核</small><strong>{aggregate.data?.review_count ?? (credentials.ready?candidates.error?'—':candidates.data?rows.filter(c=>c.fact_review_status!=='approved'||c.mapping_review_status!=='approved').length:'…':'平台未连接')}</strong></article><article><small>已发布</small><strong>{aggregate.data?.published_count ?? (credentials.ready?candidates.error?'—':candidates.data?records.length:'…':'平台未连接')}</strong></article></div><Section title="报告身份与版本">{identity.data?<MetadataEditor report={identity.data}/>:reportId?<ResourceState error={identity.error} loading={identity.loading}/>:<><Notice>这份 PDF 尚未登记业务报告身份。文件名仅用于找到文件，发布者与报告期需明确核对。</Notice><button disabled={busy} onClick={register}>登记报告身份</button></>}<dl className="facts"><dt>文件名</dt><dd>{asset.file_name}</dd><dt>文件大小</dt><dd>{asset.size_bytes?`${(asset.size_bytes/1024/1024).toFixed(1)} MB`:'未记录'}</dd><dt>内容指纹</dt><dd className="mono">{asset.sha256}</dd><dt>最后更新</dt><dd>{dateLabel(asset.modified_at)}</dd></dl><Actions actions={workActions(asset.available_actions)} revision={asset.lifecycle?.revision}/></Section></>}
 {tab==='source' && <Section title="不可变报告原文"><iframe className="pdf-standalone" title="报告 PDF 原文" src={assetFile(assetId)}/></Section>}
 {tab==='processing' && <><Section title="处理记录"><ResourceState error={processing.error}/>{runs.map(run=><article className="work-item" key={run.processing_id}><strong>{reportLabel(run)}</strong><Badge value={run.status}/><button className="secondary" onClick={()=>navigate(`/processing/${run.processing_id}`)}>阶段与证据操作</button><RecordManagement record={run} kind="processing" id={run.processing_id} compact/></article>)}{!runs.length&&<Notice>历史读取成果仍可保留；请创建报告处理任务以核对兼容性并构建固定证据版本。</Notice>}</Section><ReportEvidence current={aggregate.data?.current_processing_evidence} history={aggregate.data?.historical_fixed_evidence || []}/></>}
 {tab==='extraction' && <><Section title="报告提取与成果"><ResourceState error={extraction.error} empty={!sessions.length}/>{sessions.map(s=><article className="work-item" key={s.session_id}><div><strong>{s.name}</strong><p>{s.request?.standards?.reduce((n:number,r:Row)=>n+(r.metric_ids?.length || 0),0)} 个明确选择的输出</p></div><Badge value={s.execution_status}/><Badge value={s.result_status}/><button onClick={()=>navigate(`/extractions/${s.session_id}`)}>成果与缺口</button><RecordManagement record={s} kind="session" id={s.session_id} compact/></article>)}</Section><Section title="历史独立提取"><Notice>这些任务没有父报告会话，按后端验证的资产与证据关系关联。只读查看既有成果，不自动恢复或迁移任务。</Notice><ResourceState empty={!standaloneJobs.length}/>{standaloneJobs.map(job=><article className="work-item" key={job.job_id}><div><strong>{job.title || job.disclosure_requirement || job.job_id}</strong><p>{job.disclosure_requirement || job.standard_id || "标准身份见原任务"} · {job.ir_run_id || "来源身份见详情"}</p></div><Badge value={job.status}/><button className="secondary" onClick={()=>navigate(`/extractions/standalone/${encodeURIComponent(job.job_id)}`)}>只读查看历史成果</button><RecordManagement record={job} kind="job" id={job.job_id} compact/></article>)}</Section></>}
 {tab==='review' && <Section title="本报告候选审核">{!reportId?<Notice>这份 PDF 尚未登记业务报告身份，候选统计需要先登记报告身份。</Notice>:!credentials.ready?<Notice>数据平台未连接，暂不能读取未发布候选。</Notice>:<><ResourceState error={candidates.error} empty={!rows.length}/>{rows.map(c=><article className="work-item" key={c.mapping_id}><strong>{c.source_datapoint_id}</strong><span>{c.raw_value_text || c.raw_disclosure_text}</span><Badge value={c.fact_review_status}/><Badge value={c.mapping_review_status}/><button onClick={()=>navigate(`/review/${c.mapping_id}`)}>核对来源和口径</button></article>)}</>}<button className="secondary" disabled={!reportId} onClick={()=>navigate(`/review?report=${encodeURIComponent(reportId || '')}`)}>打开报告审核中心</button></Section>}
 {tab==='publication' && <Section title="本报告正式发布记录"><ResourceState error={aggregate.error || candidates.error} empty={!!credentials.ready&&!records.length}/>{!credentials.ready&&<Notice>数据平台未连接，暂不能读取发布管理记录；公开网站可查看正式数据。</Notice>}{records.map((c:Row)=><article className="work-item" key={c.observation_id || c.mapping_id}><strong>{c.source_datapoint_id || c.metric_id}</strong><Badge value={c.publication_status || 'published'}/><button onClick={()=>navigate(`/publication?report=${encodeURIComponent(reportId || assetId)}`)}>查看版本与来源</button></article>)}</Section>}
 </>}</>;}

const evidenceKey=(value:Row)=>`${value.ir_run_id || value.evidence_location?.ir_run_id || ''}:${value.ir_revision ?? value.evidence_location?.ir_revision ?? ''}`;

export function ReportEvidence({current,history}:{current?:Row,history:Row[]}){
 const [selected,setSelected]=useState('');const present=verifiedEvidence(current?.evidence_location || current);const chosen=history.find(item=>evidenceKey(item)===selected);const historical=verifiedEvidence(chosen?.evidence_location || chosen);
 return <><Section title="当前报告处理证据"><Notice>{current?.reason || (present.irRunId?'当前处理已明确生成结构证据。':'当前处理证据尚未生成或可用能力未确认；历史版本不自动成为本轮兼容成果。')}</Notice>{(present.irRunId || present.assetId)&&<EvidenceExplorer irRunId={present.irRunId} assetId={present.assetId}/>}</Section><Section title="历史提取固定证据版本"><Notice>这些版本属于既有提取引用。可查看原证据，复用到新处理仍需单独核对引擎、配置与来源合同。</Notice><ResourceState empty={!history.length}/>{history.map(reference=><article className="work-item" key={evidenceKey(reference)}><div><strong>{reference.label || reference.ir_run_id || reference.evidence_location?.ir_run_id}</strong><p>修订 {reference.ir_revision ?? reference.evidence_location?.ir_revision ?? '未提供'} · {reference.reason || reference.state || '历史固定引用'}</p><p>引用会话：{(reference.session_ids || []).join('、') || '见来源关联'}</p></div><button className="secondary" disabled={!verifiedEvidence(reference.evidence_location || reference).irRunId&&!verifiedEvidence(reference.evidence_location || reference).assetId} onClick={()=>setSelected(evidenceKey(reference))}>查看历史固定证据</button></article>)}{chosen&&(historical.irRunId || historical.assetId)&&<EvidenceExplorer key={selected} irRunId={historical.irRunId} assetId={historical.assetId}/>}</Section></>;
}

export function MetadataEditor({report}:{report:Row}){
 const {refresh}=useWorkspace();const [form,setForm]=useState<Row>(report.metadata || {}),[busy,setBusy]=useState(false),[message,setMessage]=useState('');useEffect(()=>{setForm(report.metadata || {});},[report.report_id,report.revision]);const fields=[['title','报告名称','text',''],['publisher_name','报告发布者','text',''],['publisher_entity_ref','发布者主体引用（显式确认）','text','必须与审核中心已登记主体的 entity_ref 完全一致'],['reporting_year','报告年','number',''],['period_start','报告期起始','date',''],['period_end','报告期结束','date',''],['language','语言','text',''],['report_kind','报告类型','text','']] as const;
 async function save(e:FormEvent){e.preventDefault();setBusy(true);setMessage('');try{const metadata:Row={};for(const [key,,type] of fields){metadata[key]=form[key]?type==='number'?Number(form[key]):form[key]:null;}await request('document',`/api/reports/${report.report_id}`,{method:'PATCH',body:JSON.stringify({metadata,expected_revision:report.revision})});setMessage('报告身份已保存。发布者不自动等同于每一项指标的测量主体。');refresh();}catch(e){setMessage((e as Error).message);}finally{setBusy(false);}}
 return <form onSubmit={save}><Notice>报告发布者与指标测量主体分别确认。不会从文件名自动填充身份。</Notice><div className="form-grid">{fields.map(([name,title,type,hint])=><Field key={name} label={title} hint={hint || undefined}><input type={type} value={form[name] ?? ''} onChange={e=>setForm({...form,[name]:e.target.value})}/></Field>)}</div><button disabled={busy}>{busy?'保存中…':'保存报告身份'}</button>{message&&<Notice>{message}</Notice>}<details><summary>已关联 PDF 版本（{report.asset_versions?.length || 0}）</summary><ul>{report.asset_versions?.map((v:Row)=><li key={v.asset_id}>版本 {v.version} · <button type="button" className="text-button" onClick={()=>navigate(`/reports/${v.asset_id}`)}>打开 PDF 资产</button> · {dateLabel(v.created_at)}</li>)}</ul><AttachVersion report={report}/></details><Diagnostics value={{report_id:report.report_id,revision:report.revision,history:report.metadata_history}} title="身份修订诊断"/></form>;
}

function AttachVersion({report}:{report:Row}){const resource=useResource('document','/api/report-assets'),{refresh}=useWorkspace();const [asset,setAsset]=useState(''),[message,setMessage]=useState('');return <div className="form-grid"><Field label="明确关联另一 PDF 为报告新版本"><select value={asset} onChange={e=>setAsset(e.target.value)}><option value="">请选择 PDF</option>{items(resource.data,'assets').filter(a=>!report.asset_versions?.some((v:Row)=>v.asset_id===a.asset_id)).map(a=><option key={a.asset_id} value={a.asset_id}>{a.file_name}</option>)}</select></Field><button type="button" className="secondary" disabled={!asset} onClick={async()=>{try{await post('document',`/api/reports/${report.report_id}/versions`,{asset_id:asset,expected_revision:report.revision});setMessage('新 PDF 版本已显式关联，既有引用保持原版本。');refresh();}catch(e){setMessage((e as Error).message);}}}>确认关联版本</button>{message&&<Notice>{message}</Notice>}</div>;}
