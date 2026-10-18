import {useState,type ReactNode} from 'react';
import {credentials,errorText,navigate,post,request,type Row,type Service} from '../api';
import {useWorkspace} from '../state';
const labels:Record<string,string>={evaluated:'已处理',processing:'正在处理',unfinished:'未完成',not_executed:'尚未执行',found:'发现披露',not_found:'未找到披露',ambiguous:'需要辨析',done:'已完成',not_assessed:'尚未评估',retired:'历史退役',source_validation_failed:'来源验证失败',queued:'等待执行',pending:'待开始',running:'正在执行',active:'活动',finished:'执行结束',completed:'已完成',partial:'部分成果',failed:'失败',cancelled:'已取消',cancelling:'正在取消',stopping:'等待执行退出',interrupted:'已中断',ready:'证据可用',ready_with_limits:'局部证据可用',needs_attention:'需要处理',blocked:'条件未满足',full:'完整',limited:'有限',not_ready:'未具备证据',not_evaluated:'尚未评估',needs_confirmation:'等待确认',usable:'成果可用',no_evidence_in_bound:'限定范围内无披露',approved:'已批准',rejected:'已退回',review_required:'待审核',candidate:'候选',published:'已发布',not_imported:'尚未交付',imported:'已导入',archived:'已归档',discarded:'已丢弃',purged:'已清除',unprocessed:'尚未处理',ocr_only:'识别完成，结构待构建',draft:'已编译草稿',planned:'待建设',supports:'支持',exact:'完全对应',not_selected:'未选择',not_extracted:'未提取',source_unreadable:'证据不可读',not_disclosed_in_reviewed_scope:'所查范围未发现披露',not_applicable:'不适用',reused:'复用兼容成果'};
export const label=(s:unknown)=>labels[String(s)] || String(s || '未记录');
export const dateLabel=(s:unknown)=>s?new Date(String(s)).toLocaleString('zh-CN',{hour12:false}):'未记录';
Object.assign(labels,{available:'已保存，可交付',not_published:'未发布',source_only_not_published:'来源存档，未发布',no_retained_facts:'暂无保留事实'});
export function Badge({value}:{value:unknown}){const s=String(value || 'unknown');return <span className={`badge ${s}`}>{label(s)}</span>;}
export function Notice({children,tone='info'}:{children:ReactNode,tone?:string}){return <div role={tone==='error'?'alert':'status'} className={`notice ${tone}`}>{children}</div>;}
export function ResourceState({error,loading,empty}:{error?:string,loading?:boolean,empty?:boolean}){return error?<Notice tone="error">读取失败：{error}。本次未取得最新状态。</Notice>:loading?<p className="muted">正在读取…</p>:empty?<div className="empty">暂无符合当前条件的记录。</div>:null;}
export function Section({title,children,action}:{title:string,children:ReactNode,action?:ReactNode}){return <section className="panel"><div className="section-title"><h2>{title}</h2>{action}</div>{children}</section>;}
export function Field({label:caption,children,hint}:{label:string,children:ReactNode,hint?:string}){return <label className="field"><span>{caption}</span>{children}{hint && <small>{hint}</small>}</label>;}
export function MetricValue({value,unit}:{value:unknown,unit?:unknown}){return <span className="metric-value">{value===null||value===undefined?'未披露数值':String(value)}{unit?<small> {String(unit)}</small>:null}</span>;}
const stageNames:Record<string,string>={queued:'等待执行',starting:'启动执行',running:'正在执行',stopping:'正在取消',completed:'执行完成',partial:'执行结束，有待处理成果',failed:'执行失败',cancelled:'已取消',interrupted:'执行中断',reconnected:'恢复观测已有任务',resource_lifecycle:'资源与执行时间检查',ocr:'文档识别',document_ir:'结构与证据构建',report_session:'报告定向提取',targeted_extraction:'条款提取',retrieval:'检索报告证据',search:'检索报告证据',source_understanding:'理解来源',interpret_source:'理解来源',mapping:'条款映射',map_metrics:'条款映射',metric_mapping:'条款映射',repair_format:'修复输出协议',materialization:'整理事实',export:'保存阶段成果',finalization:'检查并冻结成果',planning:'规划证据动作',quality:'检查证据可用范围',ir:'结构与证据构建'};
export const stageLabel=(code:unknown)=>stageNames[String(code)] || (/[\u4e00-\u9fff]/.test(String(code || ''))?String(code):'执行阶段待识别');
export function Progress({task}:{task:Row}){
 const p=task.progress,t=task.timing,call=p?.current_call;const current=p?.current ?? p?.completed,total=p?.total;
 const determinate=p?.determinate===true&&typeof current==='number'&&typeof total==='number'&&total>0&&current>=0&&current<=total;
 const units:Record<string,string>={pages:'页',objects:'个对象',requirements:'条款',steps:'步骤',metrics:'条款',clauses:'条款',checks:'项检查'};
 const callCode=call?.tool || call?.route || call?.action;
 const callCaption=call?.label || call?.stage_label || (callCode?stageLabel(callCode):'调用标签尚未提供');
 return <div className="progress-view"><div className="between"><strong>{p?.label || p?.stage_label || task.stage_label || task.label || stageLabel(p?.stage_code || task.stage)}</strong><Badge value={task.status || p?.status}/></div>{determinate?<><progress max={total} value={current}/><small>{current} / {total} {units[p.unit] || p.unit || '工作项'} · {['metrics','clauses','requirements'].includes(p.unit)?'已记录处理结论不代表有可用事实或发布':'已处理范围不代表成果获准发布'}</small></>:<p className="muted">{p?.message || (task.status==='reused'?'已复用通过兼容核查的成果':'当前阶段暂无可计算总量')}</p>}{call&&<><p>当前动作：{callCaption} {call.object_id || ''}</p><details><summary>本次模型调用</summary><dl className="facts"><dt>操作代码</dt><dd>{callCode || '未提供'}</dd><dt>模型</dt><dd>{call.model_id || call.model || '未提供'}</dd><dt>角色</dt><dd>{call.role_label || call.role || '未提供'}</dd><dt>调用阶段</dt><dd>{call.phase || '未提供'}</dd><dt>观测调用时间</dt><dd>{seconds(call.elapsed_seconds)}</dd></dl></details></>}{t&&<details><summary>执行时间</summary><dl className="facts"><dt>排队</dt><dd>{seconds(t.queue_wait_seconds)}</dd><dt>实际观测执行</dt><dd>{seconds(t.active_observed_seconds)}</dd><dt>未观测间隔</dt><dd>{seconds(t.unobserved_seconds)}（不推定为模型工作）</dd><dt>睡眠或时钟调整</dt><dd>{seconds(t.sleep_or_clock_adjustment_seconds)}（不计入已观测执行）</dd><dt>墙钟</dt><dd>{seconds(t.wall_elapsed_seconds)}</dd></dl></details>}</div>;
}
function seconds(value:unknown){return typeof value==='number'?`${Math.round(value)} 秒`:'未记录';}
export function Diagnostics({value,title='运行诊断'}:{value:unknown,title?:string}){return <details className="diagnostics"><summary>{title}</summary><pre>{JSON.stringify(value,null,2)}</pre></details>;}
export function preflightAllows(preflight:Row|undefined,action:Row){
 if(preflight?.allowed===false)return false;
 if(Array.isArray(preflight?.available_actions))return preflight.available_actions.some((candidate:Row)=>candidate.action===action.action&&candidate.enabled!==false);
 return true;
}
export function Actions({actions=[],service='document',revision,onDone}:{actions?:Row[],service?:Service,revision?:unknown,onDone?:()=>void}){
 const {refresh}=useWorkspace();const [busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[confirm,setConfirm]=useState<Row>(),[reason,setReason]=useState(''),[preflight,setPreflight]=useState<Row>();
 const allowed=!!confirm&&preflightAllows(preflight,confirm)&&actions.some(a=>a.action===confirm.action&&a.endpoint===confirm.endpoint&&a.enabled!==false);
 async function execute(action:Row){
  if(action.action==='start_extraction'){navigate(`/extractions/new?asset=${encodeURIComponent(action.asset_id || '')}&ir=${encodeURIComponent(action.ir_run_id || '')}`);return;}
  if(['inspect_evidence','review_evidence'].includes(action.action)){navigate(`/processing?ir=${encodeURIComponent(action.ir_run_id || '')}`);return;}
  if(action.action==='open_parent'){navigate(`/extractions/${action.scope?.session_id || ''}`);return;}
  if(action.action==='deliver'){
   const session=action.scope?.session_id;
   if(!session){setError('交付操作缺少所属报告会话，请刷新任务');return;}
   navigate(`/extractions/${encodeURIComponent(session)}?tab=deliver`);return;
  }
  if(action.requires_operator&&!credentials.token){setError('请先启用操作权限');return;}
  if(!action.endpoint){setError('该操作缺少后端执行地址，请查看系统诊断');return;}
  const confirmed=confirm?.action===action.action&&confirm?.endpoint===action.endpoint;
  setBusy(action.action);setError('');setNotice('');
  try{
   if(action.preflight_endpoint&&!confirmed){const value=await request(service,action.preflight_endpoint);const fresh=value.available_actions?.find((candidate:Row)=>candidate.action===action.action&&candidate.endpoint===action.endpoint&&candidate.method===action.method);setPreflight(value);setConfirm(fresh?{...action,...fresh}:action);return;}
   if((action.requires_reason||action.requires_confirmation||action.calls_model||action.incurs_cost)&&!confirmed){setPreflight(undefined);setReason('');setConfirm(action);return;}
   if(confirmed&&!allowed)throw new Error('最新预检或允许动作未批准此操作，请处理阻塞后重新核查。');
   const payload={...(action.payload || {}),...(action.accepts_revision!==false&&typeof revision==='number'&&!Object.hasOwn(action.payload || {},'expected_revision')?{expected_revision:revision}:{}),...(action.requires_reason?{reason}:{}),...(action.calls_model?{qiniu_api_key:credentials.secret('qiniu_api_key'),ocr_token:credentials.secret('ocr_token')}: {})};
   if(action.method==='GET'){const result=await request(service,action.endpoint);setPreflight(result);setConfirm(action);return;}
   const result=action.method==='DELETE'?await request(service,action.endpoint,{method:'DELETE',body:JSON.stringify(payload)}):await post(service,action.endpoint,payload);if(action.method==='DELETE'){if(result.status==='deletion_pending'&&result.deleted===false)setNotice(result.message || '正在停止关联任务，退出后自动删除。');else if(result.deleted!==true||result.status!=='deleted')throw new Error('服务未确认真正删除，请刷新核对');}setConfirm(undefined);setReason('');setPreflight(undefined);refresh();onDone?.();
  }catch(e){setError(errorText(e instanceof Error?e.message:e));}finally{setBusy('');}
 }
 return <div className="actions"><div className="button-row">{actions.map((a,i)=><button key={`${a.action}-${i}`} type="button" className={['delete','discard'].includes(a.action)?'danger':'secondary'} disabled={!!busy||a.enabled===false||a.requires_operator&&!credentials.token} title={a.requires_operator&&!credentials.token?'请先启用操作权限':a.disabled_reason || a.label} onClick={()=>execute(a)}>{busy===a.action?'正在处理…':a.label || label(a.action)}</button>)}</div>{error&&<Notice tone="error">{error}</Notice>}{notice&&<Notice>{notice}</Notice>}{confirm&&<div className="confirm-box"><strong>{confirm.confirmation_message || confirm.label}</strong>{confirm.calls_model&&<p>此操作将执行模型任务{confirm.incurs_cost?'，可能产生云服务费用':''}。</p>}{preflight&&<>{preflight.message&&<p>{preflight.message}</p>}{typeof preflight.ocr_count==='number'&&<p>关联页面读取版本：{preflight.ocr_count} 个</p>}<ul>{(preflight.blockers || []).map((b:Row,i:number)=><li key={i}>{b.message || b.reason || String(b)}</li>)}</ul>{!allowed&&<Notice tone="error">当前关联条件阻止此操作，不能继续执行。请处理阻塞后关闭并重新核查。</Notice>}<Diagnostics value={preflight} title="影响范围与关联对象"/></>}{confirm.requires_reason&&<Field label="操作原因"><textarea value={reason} onChange={e=>setReason(e.target.value)} required/></Field>}{confirm.method!=='GET'&&<button type="button" disabled={!!busy||!allowed||confirm.requires_reason&&!reason.trim()} onClick={()=>execute(confirm)}>确认执行</button>}<button className="ghost" onClick={()=>{setConfirm(undefined);setPreflight(undefined);setReason('');}}>关闭</button></div>}</div>;
}
