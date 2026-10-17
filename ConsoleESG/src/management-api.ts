import {post,request,type Row} from './api';

export type ManagedKind='asset'|'ocr'|'ir'|'processing'|'session'|'job'|'queue';
const prefixes:Record<ManagedKind,string>={asset:'/api/report-assets',ocr:'/api/ocr-library',ir:'/api/document-ir/jobs',processing:'/api/report-processing/runs',session:'/api/report-sessions',job:'/api/jobs',queue:'/api/pipeline/tasks'};
export const managementPath=(kind:ManagedKind,id:string)=>`${prefixes[kind]}/${encodeURIComponent(id)}`;
export const lifecyclePath=(kind:ManagedKind,id:string)=>kind==='queue'?managementPath(kind,id):`${managementPath(kind,id)}/lifecycle`;
export function lifecycleState(row:Row){return row.lifecycle?.state || (row.archived_at?'archived':['discarded','purged'].includes(row.status)?row.status:'active');}
export const isArchived=(row:Row)=>lifecycleState(row)!=='active';
const hiddenManagement=new Set(['archive','restore','discard','purge','release-references','restore-references']);
export const lifecycleActions=(actions:Row[]=[])=>actions.filter(a=>a.action==='delete'||a.action==='cancel');
export const workActions=(actions:Row[]=[])=>actions.filter(a=>a.action!=='delete'&&a.action!=='cancel'&&!hiddenManagement.has(a.action));
export function managementActions(actions:Row[]=[],kind:ManagedKind='session',id?:string):Row[]{
 return lifecycleActions(actions).map(action=>{
  const valid=action.action!=='delete'||action.method==='DELETE'&&(!id||action.endpoint===managementPath(kind,id))&&action.calls_model!==true&&action.incurs_cost!==true;
  return {...action,label:action.action==='delete'?'删除':'取消执行',enabled:valid&&action.enabled!==false,
   disabled_reason:valid?action.disabled_reason:'删除接口尚未就绪',requires_operator:action.action==='delete'||action.requires_operator,requires_reason:false,requires_confirmation:true,
   confirmation_message:action.action==='delete'?(action.confirmation_message || '确认删除？删除后无法恢复。'):'确认取消当前执行？已保存的成果保留。'};
 });
}
export function recordActions(record:Row):Row[]{return Array.isArray(record.available_actions)?record.available_actions:record.lifecycle?.available_actions || [];}
export function deletionBlockers(record:Row):string[]{
 const values=[...(record.delete_blockers || []),...(record.blockers || []),...(record.lifecycle?.blockers || [])];
 return [...new Set(values.map((value:Row|string)=>typeof value==='string'?value:value.message || value.reason || '').filter(Boolean))];
}
export function recordDeleteAction(record:Row,kind:ManagedKind,id:string):Row|undefined{return managementActions(recordActions(record),kind,id).find(action=>action.action==='delete');}
export async function deleteManagedRecord(record:Row,kind:ManagedKind,id:string):Promise<Row>{
 const action=recordDeleteAction(record,kind,id);
 if(!action||action.enabled===false)throw new Error(action?.disabled_reason || deletionBlockers(record).join('；') || '当前不可删除');
 const payload={...(action.payload || {})};delete payload.reason;
 const revision=record.lifecycle?.revision ?? record.revision;
 if(kind!=='ocr'&&!Object.hasOwn(payload,'expected_revision')&&Number.isSafeInteger(revision)&&revision>=0)payload.expected_revision=revision;
 if(Object.hasOwn(payload,'expected_revision')&&(!Number.isSafeInteger(payload.expected_revision)||payload.expected_revision<0))throw new Error('记录已变化，请刷新后再删除');
 if(!Object.hasOwn(payload,'expected_revision')&&!(typeof payload.identity_sha256==='string'&&/^[a-f0-9]{64}$/.test(payload.identity_sha256)))throw new Error('文件身份已变化，请刷新后再删除');
 const result=await request('document',action.endpoint,{method:'DELETE',body:JSON.stringify(payload)});
 if(result.status==='deletion_pending'&&result.deleted===false)return result;
 if(result.deleted!==true||result.status!=='deleted')throw new Error('后端未确认真正删除，请刷新核对');
 return result;
}
export function nativeSessionId(session:Row):string|undefined {
 if(session.native_unavailable)return;
 if(typeof session.native_session_id==='string'&&session.native_session_id)return session.native_session_id;
 if(session.schema_version==='report-session-v1'&&typeof session.session_id==='string')return session.session_id;
}
const intermediatesPath=(sessionId:string)=>`${managementPath('session',sessionId)}/intermediates`;
export const intermediatePlan=(sessionId:string)=>request('targeted',`${intermediatesPath(sessionId)}/plan`);
export const intermediateReceipts=(sessionId:string)=>request('targeted',`${intermediatesPath(sessionId)}/receipts`);
export const executeIntermediatePlan=(sessionId:string,plan:Row,reason:string)=>post('targeted',`${intermediatesPath(sessionId)}/execute`,{plan_id:plan.plan_id,expected_revision:plan.expected_revision,reason});
export const restoreIntermediateReceipt=(sessionId:string,receipt:Row,expectedRevision:number,reason:string)=>post('targeted',`${intermediatesPath(sessionId)}/restore`,{cleanup_id:receipt.cleanup_id,expected_revision:expectedRevision,reason});
