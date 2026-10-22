import {credentials,type Row} from '../api';
import {useResource} from '../state';
import {dateLabel,Diagnostics,Notice} from './ui';

export type DeliveryCounts={archived_source_count:number|null,selected_candidate_count:number|null,
 internal_candidate_count:number|null,published_observation_count:number|null};
export type DeliveryState=Row&{status:string,checked_at?:string,report_ref?:string,
 report_counts?:DeliveryCounts,session_counts?:DeliveryCounts,current_revision_counts?:DeliveryCounts,jobs?:Row[]};
const names:Record<string,string>={source_archived:'来源已存档',candidates_imported:'标准候选已入库',
 partial:'部分成果已存档',not_imported:'尚未存档',no_artifact:'暂无可交付成果',
 committed_without_acknowledgement:'SQL已提交，回执待确认',failed:'交付失败',unknown:'存档状态待核对',
 identity_mismatch:'成果身份不符',artifact_unavailable:'固定成果不可读',
 credentials_required:'数据平台连接后核对存档'};
export const deliveryLabel=(status:unknown)=>names[String(status)] || names.unknown;
export const countLabel=(value:unknown)=>Number.isSafeInteger(value)&&Number(value)>=0?String(value):'尚未核对';
export function useDeliveryStatus(sessionId:string,reportId?:string){
 return useResource<DeliveryState>('document',`/api/console/report-sessions/${encodeURIComponent(sessionId)}/delivery-status${reportId?`?report_id=${encodeURIComponent(reportId)}`:''}`,true);
}
export function DeliveryStatusBadge({data,error,loading=false}:{data?:DeliveryState,error?:string,loading?:boolean}){
 const status=!credentials.ready?'credentials_required':error?'unknown':data?.status || 'unknown';
 return <span className={`badge ${status}`} title={error || (loading&&!data?'正在读取数据库持久交付记录':undefined)}>{deliveryLabel(status)}</span>;
}
export function SessionDeliveryBadge({sessionId}:{sessionId:string}){
 const state=useDeliveryStatus(sessionId);
 return <DeliveryStatusBadge data={state.data} error={state.error} loading={state.loading}/>;
}
function Counts({counts}:{counts?:DeliveryCounts}){
 return <dl className="facts"><dt>SQL存档来源</dt><dd>{countLabel(counts?.archived_source_count)}</dd>
  <dt>所选标准候选</dt><dd>{countLabel(counts?.selected_candidate_count)}</dd>
  <dt>内部计算候选</dt><dd>{countLabel(counts?.internal_candidate_count)}</dd>
  <dt>已发布观测</dt><dd>{countLabel(counts?.published_observation_count)}</dd></dl>;
}
export function DeliveryStatusPanel({data,error,loading=false}:{data?:DeliveryState,error?:string,loading?:boolean}){
 if(!credentials.ready)return <Notice>数据平台未连接，暂不能核对数据库存档。已保存成果数量不作为SQL入库证明。</Notice>;
 return <div aria-label="数据库持久交付状态"><div className="between"><h3>数据库存档与发布</h3><DeliveryStatusBadge data={data} error={error} loading={loading}/></div>
  {error&&<Notice tone="error">当前无法刷新存档状态：{error}。{data?'以下保留上次核对结果，不能视为当前状态。':'数量尚未核对，不能据此判断未入库。'}</Notice>}
  {loading&&!data&&<p className="muted">正在核对数据库持久回执…</p>}
  {data?.checked_at&&<p className="muted">{error?'上次':'最近'}核对：{dateLabel(data.checked_at)}{error?` · 上次状态：${deliveryLabel(data.status)}`:''}</p>}
  {data?.reason_codes?.includes('report_selection_required')&&<Notice>同一来源存在多个报告身份。请在交付表单明确选择已登记报告，再核对存档。</Notice>}
  <section aria-label="当前固定成果版本"><p>当前固定成果版本</p><Counts counts={data?.current_revision_counts}/></section>
  {Number.isSafeInteger(data?.publication_dataset_version)&&<p className="muted">发布数量核对数据集版本：{data?.publication_dataset_version}。只有该版本正式成员计入已发布观测。</p>}
  <Notice>来源存档、标准候选、事实审核与正式发布分别核对。来源已存档不表示提取完成，也不表示审核通过。</Notice>
  {(data?.reason_messages || []).map((reason:Row,i:number)=><Notice key={reason.code || i}>{reason.label_zh || '请核对固定成果、报告身份及持久回执。'}</Notice>)}
  {(data?.jobs || []).map((job:Row)=>{const execution=data?.execution?.jobs?.find((item:Row)=>item.job_id===job.job_id);return <article className="work-item" key={job.job_id}>
   <div><strong>{execution?.package_id?.split('.').at(-1)?.toUpperCase() || '条款成果'}</strong><p>{deliveryLabel(job.status)}</p>
    {execution&&<p>所选 {countLabel(execution.selected_metrics)} 条 · 已尝试 {countLabel(execution.attempted_metrics)} 条 · 尚未执行 {countLabel(execution.not_executed_metrics)} 条</p>}
    <p>SQL来源 {countLabel(job.counts?.archived_source_count)} 条 · 所选候选 {countLabel(job.counts?.selected_candidate_count)} 条</p></div>
   <Diagnostics value={job} title="固定版本、批次与回执依据"/></article>;})}
  <details><summary>会话与报告的存档范围</summary><h4>本会话全部已核对批次</h4><Counts counts={data?.session_counts}/>
   <h4>该报告当前PDF版本全部任务</h4><Counts counts={data?.report_counts}/><p className="muted">报告范围固定登记版本与PDF来源，包含其他提取任务，不能混作本会话完成数量。共享来源按真实快照去重。</p></details>
  <Diagnostics value={data} title="权威查询范围、未知原因及执行记录"/>
 </div>;
}
