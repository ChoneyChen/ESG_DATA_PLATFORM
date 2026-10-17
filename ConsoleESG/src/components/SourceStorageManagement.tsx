import {useEffect,useState} from 'react';
import {navigate,post,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Badge,Diagnostics,Field,Notice,ResourceState,Section} from './ui';

const keyOf=(row:Row)=>`${row.kind}:${row.run_id || row.target_id}`;
export function storageRuns(inventory:Row|undefined,sourceSha256?:string):Row[]{
 const result=new Map<string,Row>();
 for(const document of inventory?.documents || []){
  for(const ocr of document.ocr_runs || []){
   if(sourceSha256&&ocr.source_pdf_sha256!==sourceSha256)continue;
   const row={...ocr,kind:'ocr'};result.set(keyOf(row),row);
   for(const ir of ocr.ir_runs || []){
    if(sourceSha256&&ir.ocr_run_id!==ocr.run_id)continue;
    const linked={...ir,kind:'ir'};result.set(keyOf(linked),linked);
   }
  }
  for(const ir of document.unlinked_ir_runs || []){
   if(sourceSha256&&ir.source_pdf_sha256!==sourceSha256)continue;
   const row={...ir,kind:'ir'};result.set(keyOf(row),row);
  }
 }
 return [...result.values()];
}
const size=(value:number)=>`${(value/1024/1024).toFixed(2)} MiB`;
export function StoragePlan({inventory,sourceSha256}:{inventory?:Row,sourceSha256?:string}){
 const {refresh}=useWorkspace();const [chosen,setChosen]=useState<Set<string>>(new Set()),[plan,setPlan]=useState<Row>(),[reason,setReason]=useState('回收已结束且未被引用的报告处理文件'),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const runs=storageRuns(inventory,sourceSha256);
 useEffect(()=>{setChosen(new Set());setPlan(undefined);},[sourceSha256]);
 const executable=plan?.can_execute===true&&typeof plan.expires_at==='string'&&Date.parse(plan.expires_at)>Date.now();
 async function preflight(){setBusy(true);setMessage('');try{const value=await post('document','/api/storage/deletion-plans',{targets:[...chosen].map(key=>{const row=runs.find(run=>keyOf(run)===key);if(!row)throw new Error('所选成果已不在当前清单中，请刷新后选择。');return {kind:row.kind,target_id:row.run_id || row.target_id};}),cascade:false});setPlan(value);}catch(e){setPlan(undefined);setMessage((e as Error).message);}finally{setBusy(false);}}
 async function execute(){if(!plan||!executable)return;setBusy(true);try{const result=await post('document',`/api/storage/deletion-plans/${encodeURIComponent(plan.plan_id)}/execute`,{reason:reason.trim()});setMessage(`所选处理文件已移入回收区，保留期内可以恢复：${result.cleanup_id}。`);setPlan(undefined);setChosen(new Set());refresh();}catch(e){setMessage((e as Error).message);}finally{setBusy(false);}}
 return <div>
  {runs.length?<><div className="table-scroll"><table><thead><tr><th>选择</th><th>处理成果</th><th>状态</th><th>文件规模</th><th>来源引用</th></tr></thead><tbody>{runs.map(row=>{const key=keyOf(row);return <tr key={key}><td><input type="checkbox" aria-label={`选择${row.kind==='ocr'?'识别':'结构证据'}成果 ${row.run_id || row.target_id}`} checked={chosen.has(key)} disabled={busy||row.active===true} onChange={e=>{const next=new Set(chosen);e.target.checked?next.add(key):next.delete(key);setChosen(next);setPlan(undefined);}}/></td><td>{row.document_label || '报告身份待确认'}<small className="table-secondary">{row.kind==='ocr'?'文档识别':'结构与证据'} · {row.run_id || row.target_id}</small></td><td><Badge value={row.status || row.readiness}/>{row.active&&<small className="table-secondary">执行中，暂不可回收</small>}</td><td>{row.file_count ?? '—'} 个文件 · {size(row.size_bytes || 0)}</td><td>{row.deletion_blockers?.join('；') || row.blockers?.map((b:Row)=>b.message || b).join('；') || '选择后核对任务、审核和发布引用'}</td></tr>;})}</tbody></table></div><button className="secondary" type="button" disabled={busy||!chosen.size} onClick={preflight}>{busy?'正在核查…':`核查所选文件的回收条件（${chosen.size}）`}</button></>:<p className="muted">当前没有明确关联的可管理处理成果。历史来源无法关联时，请在系统存储清单单独核查。</p>}
  {plan&&<div className="confirm-box"><h3>{plan.can_execute?'所选文件可回收':'所选文件暂不可回收'}</h3><p>涉及 {plan.selected_runs?.length ?? '—'} 项成果、{plan.file_count ?? '—'} 个文件、{size(plan.size_bytes || 0)}。{plan.expires_at&&`检查结果有效至 ${new Date(plan.expires_at).toLocaleTimeString('zh-CN')}。`}</p>
   {(plan.blockers || []).map((blocker:Row,index:number)=><Notice tone="error" key={index}>{blocker.message || blocker.reason || '来源条件未满足'}</Notice>)}
   {(plan.warnings || []).map((warning:Row,index:number)=><Notice key={index}>{warning.message || warning.reason}</Notice>)}
   {plan.can_execute===true&&!executable&&<Notice>本次计划已过期或有效期无法确认，请重新核查。</Notice>}
   <Field label="文件回收依据"><textarea value={reason} onChange={e=>setReason(e.target.value)}/></Field>
   <div className="button-row"><button type="button" disabled={busy||!reason.trim()||!executable} onClick={execute}>确认移入可恢复回收区</button><button type="button" className="ghost" onClick={()=>setPlan(undefined)}>关闭检查结果</button></div><Diagnostics value={plan} title="逐项引用与固定回收范围"/>
  </div>}
  {message&&<Notice>{message}</Notice>}
 </div>;
}
export function ReportStorageManagement({sourceSha256}:{sourceSha256:string}){
 const inventory=useResource('document','/api/storage/inventory');
 return <Section title="本报告的处理文件与回收"><Notice>删除任务记录与回收来源文件分别操作。审核、已入库来源和进行中的任务仍引用的文件会保留；回收计划逐项说明原因。</Notice><ResourceState error={inventory.error} loading={inventory.loading&&!inventory.data}/><StoragePlan inventory={inventory.data} sourceSha256={sourceSha256}/><button type="button" className="text-button" onClick={()=>navigate('/runtime?tab=history')}>查看来源文件回收区与恢复入口</button></Section>;
}
