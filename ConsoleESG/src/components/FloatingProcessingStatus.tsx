import {useEffect,useState} from 'react';
import {items,navigate,type Row} from '../api';
import {useResource} from '../state';
import {Actions,Badge,Progress,ResourceState} from './ui';
import '../library.css';
import {useLibraryResource} from '../library-resource';

export function FloatingProcessingStatus(){
 const [open,setOpen]=useState(false),[planId,setPlanId]=useState('');
 const resource=useLibraryResource('status');
 const plan=useResource('document',open&&planId?`/api/production-automations/${encodeURIComponent(planId)}`:null,true);
 useEffect(()=>{const show=(event:Event)=>{setOpen(true);setPlanId((event as CustomEvent).detail?.automationId || '');};window.addEventListener('show-processing-status',show);return()=>window.removeEventListener('show-processing-status',show);},[]);
 const active=items(resource.data,'active_tasks'),recent=items(resource.data,'recent_tasks'),count=resource.data?(resource.data.running_count || 0)+(resource.data.queued_count || 0):undefined;
 return <div className="floating-processing">{open&&<section className="floating-processing-panel" role="dialog" aria-label="处理状态"><header><h2>处理状态</h2><button className="ghost" aria-label="关闭处理状态" onClick={()=>setOpen(false)}>✕</button></header><ResourceState error={resource.error} loading={resource.loading&&!resource.data}/>{resource.data&&<p className="muted">正在执行 {resource.data.running_count} · 排队 {resource.data.queued_count}</p>}{plan.data&&<div className="floating-plan"><strong>{plan.data.name}</strong><Badge value={plan.data.status}/>{plan.data.pause_reason&&<p>{plan.data.pause_reason}</p>}{(plan.data.items || []).map((item:Row)=><div key={item.item_id}><strong>{item.report_title || item.file_name}</strong><p>{item.stage_label} · {item.message}</p>{item.extraction_plan_id&&<button className="text-button" onClick={()=>navigate(`/extractions/${encodeURIComponent(item.extraction_plan_id)}`)}>查看提取成果</button>}</div>)}</div>}<ResourceState error={plan.error}/>{active.map(task=><article key={task.task_id}><h3>{task.title}</h3><Progress task={task}/><Actions actions={(task.available_actions || []).filter((a:Row)=>a.action==='cancel')}/></article>)}{!active.length&&resource.data&&<p>当前没有执行中的任务。</p>}{recent.length>0&&<details><summary>最近完成或失败</summary>{recent.map(task=><article key={task.task_id}><strong>{task.title}</strong><Badge value={task.status}/>{task.error_detail?.message&&<p>{task.error_detail.message}</p>}</article>)}</details>}<button className="text-button" onClick={()=>navigate('/extractions')}>打开提取成果</button></section>}<button className={`processing-fab ${count?'busy':''}`} aria-expanded={open} aria-label="展开处理状态" onClick={()=>setOpen(!open)}><span>{count?'◌':'✓'}</span>处理状态{count!==undefined&&count>0&&<b>{count}</b>}{resource.error&&<small>连接待恢复</small>}</button></div>;
}
