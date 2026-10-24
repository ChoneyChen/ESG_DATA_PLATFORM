import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {items,navigate,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Badge,Progress,ResourceState,stageLabel,Actions} from './ui';
import {useLibraryResource} from '../library-resource';

/**
 * The run monitor sits at the bottom of the sidebar as a single live line.
 * Clicking it opens a centred monitor dialog so the task list never squeezes
 * the workspace or covers an action the operator is about to take.
 */
export function SidebarMonitor(){
 const resource=useLibraryResource('status');
 const {connected}=useWorkspace();
 const [open,setOpen]=useState(false),[planId,setPlanId]=useState('');
 const plan=useResource('document',open&&planId?`/api/production-automations/${encodeURIComponent(planId)}`:null,true);
 useEffect(()=>{const show=(event:Event)=>{setOpen(true);setPlanId((event as CustomEvent).detail?.automationId || '');};window.addEventListener('show-processing-status',show);return()=>window.removeEventListener('show-processing-status',show);},[]);
 useEffect(()=>{if(!open)return;const key=(event:KeyboardEvent)=>{if(event.key==='Escape')setOpen(false);};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);},[open]);
 const active=items(resource.data,'active_tasks'),recent=items(resource.data,'recent_tasks');
 const running=resource.data?.running_count ?? active.filter(task=>task.status==='running').length;
 const queued=resource.data?.queued_count ?? active.filter(task=>task.status==='queued').length;
 const count=running+queued;
 const failures=recent.filter(task=>['failed','interrupted'].includes(String(task.status))).length;
 const current=active[0];
 const groups=groupTasks(active);
 return (
  <>
   <button type="button" className={`side-monitor ${count?'busy':''}`} aria-label="打开处理状态" aria-haspopup="dialog" onClick={()=>setOpen(true)}>
    <span className="side-monitor-title"><span className={`side-live ${connected?'on':''}`}/>运行监视</span>
    <span className="side-monitor-figures">
     <span><strong>{running}</strong>执行中</span>
     <span><strong>{queued}</strong>排队</span>
     <span className={failures?'warn':''}><strong>{failures}</strong>需处理</span>
    </span>
    <span className="side-monitor-line">{current?taskSummary(current):resource.error?'状态连接待恢复':'当前没有执行中的任务'}</span>
    {current&&<span className="side-monitor-meter" aria-hidden="true"><span style={{width:`${taskPercent(current)}%`}}/></span>}
   </button>
   {open&&createPortal((
    <div className="monitor-overlay" role="presentation" onClick={(event)=>{if(event.target===event.currentTarget)setOpen(false);}}>
     <div className="monitor-dialog" role="dialog" aria-modal="true" aria-label="处理状态">
      <header className="monitor-head">
       <div><strong>处理状态</strong><small>正在执行 {running} · 排队 {queued}{failures?` · 需处理 ${failures}`:''}</small></div>
       <button type="button" className="ghost" aria-label="关闭处理状态" onClick={()=>setOpen(false)}>✕</button>
      </header>
      <div className="monitor-body">
       <ResourceState error={resource.error} loading={resource.loading&&!resource.data}/>
       {plan.data&&(
        <section className="monitor-plan">
         <header><strong>{plan.data.name}</strong><Badge value={plan.data.status}/></header>
         {plan.data.pause_reason&&<p>{plan.data.pause_reason}</p>}
         {(plan.data.items||[]).map((item:Row)=>(
          <div className="monitor-item" key={item.item_id}>
           <b>{item.report_title||item.file_name}</b>
           <small>{item.stage_label} · {item.message}</small>
           {item.extraction_plan_id&&<button type="button" className="text-button" onClick={()=>{setOpen(false);navigate(`/extractions/${encodeURIComponent(item.extraction_plan_id)}`);}}>查看提取成果</button>}
          </div>
         ))}
        </section>
       )}
       <ResourceState error={plan.error}/>
       {groups.map(group=>(
        <article className="monitor-report" key={group.key}>
         <header><strong>{group.title}</strong><Badge value={group.current.status}/><small>{group.tasks.length>1?`${group.tasks.length} 个内部步骤`:''}</small></header>
         <Progress task={group.current} compact/>
         <div className="monitor-stage-chips">{group.tasks.map(task=><span key={task.task_id} className={`monitor-stage-chip ${String(task.status)}`}>{stageLabel(task.stage)}</span>)}</div>
         <Actions actions={(group.current.available_actions||[]).filter((action:Row)=>action.action==='cancel')}/>
        </article>
       ))}
       {!active.length&&resource.data&&<p className="monitor-empty">当前没有执行中的任务。</p>}
       {recent.length>0&&(
        <details className="monitor-recent">
         <summary>最近完成或失败</summary>
         {recent.map(task=>(<div key={task.task_id}><strong>{task.title}</strong><Badge value={task.status}/>{task.error_detail?.message&&<small>{task.error_detail.message}</small>}</div>))}
        </details>
       )}
      </div>
      <footer className="monitor-foot">
       <button type="button" className="text-button" onClick={()=>{setOpen(false);navigate('/extractions');}}>打开提取成果</button>
       <button type="button" className="text-button" onClick={()=>{setOpen(false);navigate('/automation');}}>打开批量生产</button>
      </footer>
     </div>
    </div>
   ),document.body)}
  </>
 );
}

function groupTasks(tasks:Row[]){
 const groups:Array<{key:string,title:string,tasks:Row[],current:Row}>=[];
 for(const task of tasks){
  const title=String(task.title||'').replace(/^(?:Document IR|报告提取|OCR)\s*·\s*/,'') || task.task_id;
  const group=groups.find(item=>item.title===title);
  if(group)group.tasks.push(task);else groups.push({key:title,title,tasks:[task],current:task});
 }
 for(const group of groups)group.current=group.tasks.find(task=>['running','queued'].includes(String(task.status))) || group.tasks[group.tasks.length-1];
 return groups;
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
