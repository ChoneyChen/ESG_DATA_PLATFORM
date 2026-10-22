import {useEffect,useRef,useState} from 'react';
import {assetFile,credentials,items,navigate,request,verifiedEvidence,type Row} from '../api';
import {useWorkspace} from '../state';
import {Diagnostics,Notice,ResourceState,Section} from './ui';
import {EvidenceExplorer} from './EvidenceExplorer';

const propertyNames:Record<string,string>={pollutant:'污染物',reporting_period:'报告期间',
 period_start:'期间起始日',period_end:'期间结束日',source_row_label:'原始表行名称',
 reporting_boundary:'报告边界',boundary:'边界',business_segment:'业务分部',
 subject:'报告主体',method:'披露方法'};
function rawProperty(value:unknown):string {
 return Array.isArray(value)&&value.every(item=>typeof item==='string')?value.join('、'):
  value!==null&&typeof value==='object'?'此属性保留原始结构，请展开来源诊断核对。':String(value ?? '未记录');
}

/** Unmapped readings stay in the review workspace, outside published metrics. */
export function ReportSourceFacts({reportRef}:{reportRef?:string|null}) {
 const {auth}=useWorkspace();
 return <SourceFactPages key={`${auth}:${reportRef || ''}`} reportRef={reportRef}/>;
}
type SourceFactPage={rows:Row[],hasMore:boolean,nextCursor:string|null};
function SourceFactPages({reportRef}:{reportRef?:string|null}) {
 const [pages,setPages]=useState<SourceFactPage[]>([]),[pageIndex,setPageIndex]=useState(0);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const controller=useRef<AbortController|null>(null);
 const lastAttempt=useRef<{cursor:string|null,index:number}>({cursor:null,index:0});
 const [chosenId,setChosenId]=useState<string>();
 const loadPage=async(cursor:string|null,index:number)=>{
  controller.current?.abort();const active=new AbortController();controller.current=active;
  lastAttempt.current={cursor,index};
  setBusy(true);setError('');
  const path=`/internal/v1/source-facts?limit=500${reportRef?`&report_ref=${encodeURIComponent(reportRef)}`:''}${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`;
  try {
   const data=await request('publication',path,{signal:active.signal});
   if(active.signal.aborted)return;
   const page={rows:items(data),hasMore:data.has_more===true,nextCursor:typeof data.next_cursor==='string'&&data.next_cursor?data.next_cursor:null};
   setPages(previous=>[...previous.slice(0,index),page]);setPageIndex(index);setChosenId(undefined);
  }catch(e){if(!active.signal.aborted)setError((e as Error).message);}
  finally{if(!active.signal.aborted)setBusy(false);}
 };
 useEffect(()=>{
  if(credentials.ready)void loadPage(null,0);
  return()=>controller.current?.abort();
 // The keyed parent remounts on report or credentials changes. Background
 // workspace refreshes must not erase a user's already verified page history.
 },[]);
 const current=pages[pageIndex],rows=current?.rows || [],loadedCount=pages.reduce((sum,page)=>sum+page.rows.length,0);
 // A selection belongs to this query's current rows, never a previous report.
 const chosen=rows.find(row=>row.snapshot_id===chosenId),location=verifiedEvidence(chosen?.evidence_location);
 const first=chosen?.physical_sources?.[0];
 const page=Number.isInteger(first?.pdf_page_index)?first.pdf_page_index+1:1;
 return <Section title="已读取的报告来源事实">
  <Notice>这里保留已读取的原始表行及属性，包括尚未对应条款的内容。保存来源不代表事实、条款映射或正式发布已获批准。</Notice>
  <button className="secondary" disabled={busy||!credentials.ready} onClick={()=>void loadPage(null,0)}>重新读取来源事实</button>
  {!credentials.ready&&<Notice>数据平台未连接，暂不能浏览报告来源事实。</Notice>}
  <details><summary>浏览来源事实（已读取 {loadedCount} 条；本页 {rows.length} 条）</summary>
   <ResourceState error={error} loading={busy&&!rows.length} empty={!!credentials.ready&&!rows.length&&!busy&&!error}/>
   {error&&<button className="secondary" disabled={busy} onClick={()=>void loadPage(lastAttempt.current.cursor,lastAttempt.current.index)}>重试读取</button>}
   <div className="table-scroll"><table><thead><tr><th>报告与原始指标</th><th>原始披露期间</th><th>原值</th><th>条款映射</th><th>证据</th><th>操作</th></tr></thead>
    <tbody>{rows.map(row=><tr key={row.snapshot_id}>
     <td>{row.report_title_snapshot || row.report_title || row.report_ref}<small className="table-secondary">{row.source_row_label || '来源名称待确认'}</small></td>
     <td>{rawProperty(row.identity_raw?.reporting_period)}</td>
     <td>{row.value_raw ?? row.statement_raw ?? '原值尚未读取'} {row.unit_raw || ''}</td>
     <td>{row.mapping_state==='has_core'?'已有条款投影，仍需独立审核':'尚无条款投影'}</td>
     <td>{row.provenance_status==='physical'?'原始位置已保存':'证据尚未绑定'}</td>
     <td><button className="secondary" onClick={()=>setChosenId(row.snapshot_id)}>核对表行与属性</button></td>
    </tr>)}</tbody></table></div>
   {!!pages.length&&<div className="between" aria-label="来源事实分页">
    <button className="secondary" disabled={busy||pageIndex===0} onClick={()=>{setPageIndex(index=>index-1);setChosenId(undefined);setError('');}}>上一页</button>
    <span>第 {pageIndex+1} 页 · 已读取 {loadedCount} 条</span>
    <button className="secondary" disabled={busy||(!pages[pageIndex+1]&&!current?.nextCursor)} onClick={()=>{
     if(pages[pageIndex+1]){setPageIndex(index=>index+1);setChosenId(undefined);setError('');}
     else if(current?.nextCursor)void loadPage(current.nextCursor,pageIndex+1);
    }}>{busy?'读取中…':pages[pageIndex+1]?'下一页':'加载更多'}</button>
   </div>}
   {current?.hasMore&&<Notice>此查询还有来源事实未读取。已读取数量不代表报告的全部来源事实数量。</Notice>}
   {current?.hasMore&&!current.nextCursor&&<Notice tone="warning">服务尚未提供续页凭据，请核对当前部署版本；不能将当前页视为完整结果。</Notice>}
   {current&&!current.hasMore&&<p className="muted">当前查询已读至末页。保存的来源事实不等于 PDF 中所有表行均已读取。</p>}
  </details>
  {chosen&&<><h3>{chosen.source_row_label || '所选来源事实'}</h3>
   <p className="raw-text">{chosen.value_raw ?? chosen.statement_raw ?? '原值尚未读取'} {chosen.unit_raw || ''}</p>
   <dl className="facts">{Object.entries(chosen.identity_raw || {}).map(([key,value])=><div key={key}>
    <dt>{propertyNames[key] || key}</dt><dd>{rawProperty(value)}</dd></div>)}</dl>
   <Diagnostics value={chosen.identity_raw} title="来源原始属性结构"/>
   {location.assetId&&<a href={assetFile(location.assetId,page)} target="_blank" rel="noreferrer">打开原报告</a>}
   {location.irRunId&&<EvidenceExplorer irRunId={location.irRunId} assetId={location.assetId} startPage={page-1} focusObject={first?.ir_object_id}/>}
   {!location.assetId&&!location.irRunId&&<Notice>{chosen.evidence_location?.reason || '固定原文入口尚未验证'}</Notice>}
   {chosen.mapping_state==='unmapped'&&<Notice>此表行尚未对应所选条款。可在标准管理中提出并审核延伸定义，再明确选择后续提取范围。<button className="text-button" onClick={()=>navigate('/standards')}>查看标准与延伸条款</button></Notice>}
  </>}
 </Section>;
}
