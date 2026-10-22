import {useEffect,useState} from 'react';
import {credentials,errorText,reportLabel,type Row} from '../api';
import {deleteManagedRecord,deletionBlockers,recordDeleteAction,type ManagedKind} from '../management-api';
import {useWorkspace} from '../state';
import {Notice} from './ui';

export function endedRecords(rows:Row[]){return rows.filter(row=>!['active','queued','running','cancelling','stopping','deleted'].includes(row.execution_status || row.status));}
export function BatchRecordDelete({rows,kind,idKey,label='删除已结束任务'}:{rows:Row[];kind:ManagedKind;idKey:string;label?:string}){
 const {refresh}=useWorkspace();const [confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const signature=rows.map(row=>`${row[idKey]}:${row.lifecycle?.revision ?? row.revision ?? ''}`).join('|');
 useEffect(()=>{setConfirm(false);},[signature]);
 const allowed=rows.filter(row=>{const action=recordDeleteAction(row,kind,String(row[idKey]));return action&&action.enabled!==false;});
 const blocked=rows.filter(row=>!allowed.includes(row));
 async function execute(){setBusy(true);const results:string[]=[];for(const row of allowed){const title=row.name || row.title || reportLabel(row);try{const result=await deleteManagedRecord(row,kind,String(row[idKey]));results.push(`${title}：${result.status==='deletion_pending'?'正在停止，退出后自动删除':'已删除'}`);}catch(e){results.push(`${title}：${errorText((e as Error).message)}`);}}setMessage(results.join('；'));setConfirm(false);setBusy(false);refresh();}
 return <div className="batch-management">
  <button type="button" className="secondary" title={!credentials.roles.includes('admin')?'数据平台未连接或当前身份没有管理权限':label} disabled={busy||!rows.length||rows.length>100||!credentials.roles.includes('admin')} onClick={()=>{setMessage('');setConfirm(true);}}>{busy?'正在删除…':`${label}（${rows.length}）`}</button>
  {confirm&&<div className="confirm-box"><strong>确认删除 {allowed.length} 条记录？删除后无法恢复。</strong>
   <ul>{allowed.map(row=><li key={row[idKey]}>{row.name || row.title || reportLabel(row)}</li>)}</ul>
   {blocked.length>0&&<p className="muted">不能删除：{blocked.map(row=>`${row.name || row.title || reportLabel(row)}（${recordDeleteAction(row,kind,String(row[idKey]))?.disabled_reason || deletionBlockers(row).join('；') || '仍有来源或任务引用'}）`).join('；')}</p>}
   <div className="button-row"><button type="button" className="danger" disabled={busy||!allowed.length} onClick={execute}>确认删除</button><button type="button" className="ghost" disabled={busy} onClick={()=>setConfirm(false)}>取消</button></div>
  </div>}
  {rows.length>100&&<p className="muted">每次最多删除 100 条，请缩小选择范围。</p>}
  {message&&<Notice>{message}</Notice>}
 </div>;
}
