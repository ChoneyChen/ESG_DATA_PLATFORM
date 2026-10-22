import {useEffect,useState} from 'react';
import {credentials,errorText,items,type Row} from '../api';
import {executeIntermediatePlan,intermediatePlan,isArchived,nativeSessionId,restoreIntermediateReceipt} from '../management-api';
import {useResource,useWorkspace} from '../state';
import {Badge,dateLabel,Field,Notice,ResourceState,Section} from './ui';

const bytes=(value:number)=>value>=1024*1024?`${(value/1024/1024).toFixed(2)} MiB`:`${value.toLocaleString()} 字节`;
const recoveryLabel=(value:string)=>({quarantined:'缓存已回收',moving:'正在移动缓存',rolled_back:'已恢复原路径',rollback_pending:'移动未完成，待恢复',restoring:'正在恢复',restore_pending:'恢复未完成',restored:'已恢复'} as Record<string,string>)[value] || value;
export function IntermediateCleanup({session}:{session:Row}){
 const {refresh}=useWorkspace(),native=nativeSessionId(session),archived=isArchived(session);
 const receipts=useResource('targeted',native?`/api/report-sessions/${encodeURIComponent(native)}/intermediates/receipts`:null,true);
 const [plan,setPlan]=useState<Row>(),[selected,setSelected]=useState<Row>(),[reason,setReason]=useState('整理已结束任务的未引用中间缓存'),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{setPlan(undefined);setSelected(undefined);setMessage('');},[native,session.lifecycle?.revision]);
 const boundReceipts=receipts.data?.session_id===native&&Number.isSafeInteger(receipts.data?.expected_revision)?receipts.data:undefined;
 async function check(){if(!native)return;setBusy(true);setMessage('');setSelected(undefined);try{const value=await intermediatePlan(native);if(value.session_id!==native||!Number.isSafeInteger(value.expected_revision)||!Array.isArray(value.candidates)||value.reference_check?.verified!==true||value.calls_model!==false||value.source_artifacts_removed!==false||value.permanent_deletion!==false)throw new Error('清理计划的所属会话、修订或来源保全无法确认，请刷新后重试。');setPlan(value);}catch(e){setPlan(undefined);setMessage(errorText((e as Error).message));}finally{setBusy(false);}}
 async function execute(){if(!native||!plan)return;setBusy(true);setMessage('');try{const result=await executeIntermediatePlan(native,plan,reason.trim());setMessage(`已移入可恢复回收区：${result.moved?.length ?? plan.candidates.length} 个中间文件。原始报告、证据和已保存来源保留。`);setPlan(undefined);refresh();}catch(e){setMessage(errorText((e as Error).message));}finally{setBusy(false);}}
 async function restore(){if(!native||!selected||!boundReceipts)return;setBusy(true);setMessage('');try{await restoreIntermediateReceipt(native,selected,boundReceipts.expected_revision,reason.trim());setMessage('中间文件已恢复；如需重新显示任务，请再恢复任务记录。');setSelected(undefined);refresh();}catch(e){setMessage(errorText((e as Error).message));}finally{setBusy(false);}}
 return <Section title="任务残留与中间缓存">
  <p>任务结束后可先删除记录，再核查未引用的缓存。这里只回收安全的中间文件，不会删除 PDF、OCR、结构证据、来源事实或已交付成果。</p>
  {!native&&<Notice>此任务尚无可确认的原生提取会话；可先整理任务记录，准备阶段成果在报告处理中管理。</Notice>}
  {!credentials.token&&<Notice>数据平台未连接，缓存管理暂不可用。</Notice>}
  {native&&!archived&&<Notice>先取消进行中的任务，等待执行退出，再使用“删除记录（可恢复）”。归档后可以核查中间缓存。</Notice>}
  <button type="button" className="secondary" disabled={busy||!native||!archived||!credentials.token} onClick={check}>{busy?'正在核查…':'核查可清理的中间缓存'}</button>
  {plan&&<div className="confirm-box"><h3>本次可回收 {plan.candidates.length} 个文件 · {bytes(plan.total_size_bytes || 0)}</h3>
   {plan.candidates.length===0?<Notice>当前没有可安全回收的未引用中间文件。</Notice>:<ul>{plan.candidates.map((candidate:Row)=><li key={`${candidate.job_id}:${candidate.relative_path}`}>{candidate.relative_path} · {bytes(candidate.size_bytes || 0)}<small className="table-secondary">所属任务：{candidate.job_id}</small></li>)}</ul>}
   {plan.excluded?.length>0&&<p className="muted">{plan.excluded.length} 个文件因来源、决定或冻结成果引用而保留。</p>}
   <Field label="整理依据"><textarea value={reason} onChange={e=>setReason(e.target.value)}/></Field>
   <div className="button-row"><button type="button" disabled={busy||!reason.trim()||!plan.candidates.length} onClick={execute}>确认移入可恢复回收区</button><button type="button" className="ghost" onClick={()=>setPlan(undefined)}>关闭本次计划</button></div>
  </div>}
  {native&&credentials.token&&<><h3>本任务缓存回收记录</h3><ResourceState error={receipts.error} loading={receipts.loading&&!receipts.data} empty={!!boundReceipts&&!items(boundReceipts).length}/>
   {receipts.data&&!boundReceipts&&<Notice tone="error">回收目录的会话绑定或修订无法确认，恢复操作被阻止。</Notice>}
   {items(boundReceipts).map(receipt=>{const restoreAction=receipt.available_actions?.find((action:Row)=>action.action==='restore'),restoreAllowed=restoreAction&&restoreAction.enabled!==false;return <article className="receipt" key={receipt.cleanup_id}><div className="between"><strong>已记录移动 {receipt.moved_count ?? '—'} 个文件 · 计划缓存规模 {bytes(receipt.bytes_size || 0)}</strong><Badge value={recoveryLabel(receipt.state)}/></div><p>{receipt.reason} · {dateLabel(receipt.created_at)}</p>{restoreAllowed?<button type="button" className="secondary" disabled={busy} onClick={()=>{setSelected(receipt);setPlan(undefined);setReason('恢复此任务已回收的中间文件');}}>恢复这些中间文件…</button>:<p className="muted">{restoreAction?.disabled_reason || receipt.disabled_reason || '此回收记录当前无需恢复，或恢复条件未满足。'}</p>}</article>;})}
  </>}
  {selected&&<div className="confirm-box"><strong>恢复 {selected.moved_count ?? '所选'} 个中间文件</strong><Field label="恢复依据"><textarea value={reason} onChange={e=>setReason(e.target.value)}/></Field><div className="button-row"><button type="button" disabled={busy||!reason.trim()||!boundReceipts} onClick={restore}>确认恢复中间文件</button><button type="button" className="ghost" onClick={()=>setSelected(undefined)}>关闭</button></div></div>}
  {message&&<Notice>{message}</Notice>}
 </Section>;
}
