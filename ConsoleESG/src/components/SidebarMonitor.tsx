import {useEffect,useState} from 'react';
import {items,navigate,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Badge,Progress,ResourceState,stageLabel,Actions} from './ui';
import {useLibraryResource} from '../library-resource';

/**
 * The run monitor lives where the operator already looks: the sidebar.
 * Its header is always visible (running / queued counts), and opening it
 * expands the full task list in place instead of floating over the workspace.
 */
export function SidebarMonitor(){
 const resource=useLibraryResource('status');
 const {connected}=useWorkspace();
 const [expanded,setExpanded]=useState(false),[planId,setPlanId]=useState('');
 const plan=useResource('document',expanded&&planId?`/api/production-automations/${encodeURIComponent(planId)}`:null,true);
 useEffect(()=>{const show=(event:Event)=>{setExpanded(true);setPlanId((event as CustomEvent).detail?.automationId || '');};window.addEventListener('show-processing-status',show);return()=>window.removeEventListener('show-processing-status',show);},[]);
 const active=items(resource.data,'active_tasks'),recent=items(resource.data,'recent_tasks');
 const running=resource.data?.running_count ?? active.filter(task=>task.status==='running').length;
 const queued=resource.data?.queued_count ?? active.filter(task=>task.status==='queued').length;
 const count=running+queued;
 const failures=recent.filter(task=>['failed','interrupted'].includes(String(task.status))).length;
 const current=active[0];
 return (
  <section className="side-monitor" aria-label="运行监视">
   <button type="button" className="side-monitor-head" aria-label="展开处理状态" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>
    <span className="side-monitor-title"><span className={`side-live ${connected?'on':''}`}/>运行监视</span>
    <span className={`side-monitor-count ${count?'busy':''}`}>{count}</span>
   </button>
   <div className="side-monitor-stats">
    <span><strong>{running}</strong>执行中</span>
    <span><strong>{queued}</strong>排队</span>
    <span><strong>{failures}</strong>需处理</span>
   </div>
   {current ? (
    <div className="side-monitor-current">
     <span className="side-monitor-stage">{stageLabel(current.progress?.current_call?.tool || current.stage)}</span>
     <strong title={current.title}>{current.title || '未命名任务'}</strong>
     <div className="side-monitor-meter" aria-hidden="true"><span style={{width:`${taskPercent(current)}%`}}/></div>
     <small>{taskSummary(current)}</small>
    </div>
   ) : (
    <p className="side-monitor-idle">{resource.error?'状态连接待恢复':'当前没有执行中的任务'}</p>
   )}
   {expanded && (
    <div className="side-monitor-expand" role="dialog" aria-label="处理状态">
     <header>
      <strong>处理状态</strong>
      <button type="button" className="ghost" aria-label="关闭处理状态" onClick={()=>setExpanded(false)}>✕</button>
     </header>
     <ResourceState error={resource.error} loading={resource.loading&&!resource.data}/>
     {resource.data&&<p className="side-monitor-caption">正在执行 {running} · 排队 {queued}</p>}
     {plan.data&&(
      <div className="side-monitor-plan">
       <strong>{plan.data.name}</strong>
       <Badge value={plan.data.status}/>
       {plan.data.pause_reason&&<p>{plan.data.pause_reason}</p>}
       {(plan.data.items || []).map((item:Row)=><div key={item.item_id}><b>{item.report_title || item.file_name}</b><small>{item.stage_label} · {item.message}</small>{item.extraction_plan_id&&<button type="button" className="text-button" onClick={()=>navigate(`/extractions/${encodeURIComponent(item.extraction_plan_id)}`)}>查看提取成果</button>}</div>)}
      </div>
     )}
     <ResourceState error={plan.error}/>
     {active.map(task=>(
      <article className="side-monitor-task" key={task.task_id}>
       <header><strong>{task.title}</strong><Badge value={task.status}/></header>
       <Progress task={task} compact/>
       <Actions actions={(task.available_actions || []).filter((action:Row)=>action.action==='cancel')}/>
      </article>
     ))}
     {!active.length&&resource.data&&<p className="side-monitor-caption">当前没有执行中的任务。</p>}
     {recent.length>0&&(
      <details className="side-monitor-recent">
       <summary>最近完成或失败</summary>
       {recent.map(task=>(<div key={task.task_id}><strong>{task.title}</strong><Badge value={task.status}/>{task.error_detail?.message&&<small>{task.error_detail.message}</small>}</div>))}
      </details>
     )}
     <button type="button" className="text-button" onClick={()=>navigate('/extractions')}>打开提取成果</button>
    </div>
   )}
  </section>
 );
}

function taskPercent(task:Row){
 const current=task.progress?.current,total=task.progress?.total;
 if(task.progress?.determinate===true&&typeof current==='number'&&typeof total==='number'&&total>0)return Math.min(100,Math.max(0,Math.round(current/total*100)));
 return task.status==='running'?38:8;
}
function taskSummary(task:Row){
 const progress=task.progress||{},call=progress.current_call;
 if(typeof progress.current==='number'&&typeof progress.total==='number'&&progress.total>0)return `${progress.current} / ${progress.total} ${progress.unit||''}`.trim();
 if(call?.label||call?.stage_label)return call.label||call.stage_label;
 return progress.message || stageLabel(task.stage) || '等待阶段信息';
}
