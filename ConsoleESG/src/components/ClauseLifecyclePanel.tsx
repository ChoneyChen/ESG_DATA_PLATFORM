import {useState} from 'react';
import {credentials,post,request,type Row} from '../api';
import {Badge,Diagnostics,Field,Notice} from './ui';

const stateLabels:Record<string,[string,string]>={draft:['草稿','idle'],compiled:['已编译 · 未试提取','warning'],trialed:['试提取未通过','error'],accepted:['已验收 · 待业务批准','warning'],publishable:['可发布','ok'],retired:['已退役','idle']};
const reasonLabels:Record<string,string>={no_real_acceptance:'尚无真实验收',contract_changed_since_acceptance:'条款合同已变更，旧验收失效',business_approval_missing:'缺少业务批准',retired:'已退役'};
const checkLabels:Record<string,string>={task_executed:'条款任务已执行',facts_bound:'事实已绑定原值',evidence_anchored:'字段证据有物理锚点',unit_and_period:'单位与期间完整',gold_match:'对照期望一致',single_interpretation:'同一原值解释唯一'};
const failedLabels:Record<string,string>={task_executed:'条款任务未完成',facts_bound:'未绑定到印刷原值或缺失未被证明',evidence_anchored:'缺少物理锚点',unit_and_period:'单位或期间缺失',gold_match:'与对照期望不一致',single_interpretation:'同一原值存在多种解释'};
function reasonText(item:string){if(reasonLabels[item])return reasonLabels[item];const failed=item.match(/^acceptance_failed:(.+)$/);if(failed)return `未通过 · ${failedLabels[failed[1]] || failed[1]}`;return item;}

export function ClauseLifecyclePanel({packageId,version,rows,refresh}:{packageId:string;version:string;rows:Row[];refresh:()=>void}){
 const [reason,setReason]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const [detail,setDetail]=useState<string>(),[dependents,setDependents]=useState<Record<string,Row>>({});
 const counts=rows.reduce((acc,row)=>{acc[row.state]=(acc[row.state] || 0)+1;return acc;},{ } as Record<string,number>);
 async function act(body:Row){setBusy(true);setMessage('');try{const result=await post('document','/api/pipeline/clause-lifecycle/retirements',body) as Row;setMessage(`已退役 ${result?.retirement?.clause?.split(':').pop() || ''}，既有任务与已发布含义不变。`);refresh();}catch(error){setMessage((error as Error).message);}finally{setBusy(false);}}
 async function loadDependents(row:Row){setBusy(true);try{setDependents({...dependents,[row.label]:await request<Row>('document',`/api/pipeline/standards/${encodeURIComponent(packageId)}/${encodeURIComponent(version)}/dependents/${encodeURIComponent(row.label)}`)});}catch(error){setMessage((error as Error).message);}finally{setBusy(false);}}
 if(!rows.length) return null;
 return <>
  <div className="chips" aria-label="条款生命周期汇总">{['publishable','accepted','trialed','compiled','retired'].map(state=>counts[state]?<span className="clause-chip" key={state}>{stateLabels[state][0]} {counts[state]}</span>:null)}</div>
  <Notice>可发布必须同时满足：当前条款合同通过真实验收，且业务批准已记录。未读取的页面不能作为“未披露”的证据。</Notice>
  {message&&<Notice>{message}</Notice>}
  <div className="table-scroll"><table><thead><tr><th>条款</th><th>状态与原因</th><th>验收证据</th><th>操作</th></tr></thead><tbody>{rows.map(row=><tr key={row.clause}>
   <td><strong>{row.label}</strong></td>
   <td><Badge value={stateLabels[row.state]?.[0] || row.state}/><p className="muted">{(row.reasons || []).map((item:string)=>reasonText(item)).join('；') || '—'}</p></td>
   <td style={{maxWidth:260,wordBreak:'break-word'}}>{row.acceptance?<details><summary>{row.acceptance.outcome==='accepted'?'通过':'未通过'} · {row.acceptance.trial?.report_label || row.acceptance.trial?.run_ref}</summary><ul>{row.acceptance.checks.map((check:Row)=><li key={check.check}>{checkLabels[check.check] || check.check}：{check.outcome==='pass'?'通过':check.outcome==='fail'?'失败':'不适用'} · {check.detail}</li>)}</ul></details>:<span className="muted">尚无真实验收</span>}</td>
   <td style={{whiteSpace:'nowrap',verticalAlign:'top'}}><div className="button-row"><button className="text-button" disabled={busy} onClick={()=>void loadDependents(row)}>依赖影响</button>{row.state!=='retired'&&<button className="text-button" disabled={busy} onClick={()=>setDetail(row.label)}>退役</button>}</div>
    {dependents[row.label]&&<details open><summary>受影响条款</summary><ul>{(dependents[row.label].dependents || []).map((item:Row,index:number)=><li key={index}>{item.direction==='uses_this_clause'?'引用本条款':'本条款依赖'} · {item.relation_type} · {(item.clauses || []).join('、')}</li>)}{!(dependents[row.label].dependents || []).length&&<li>无其他条款依赖本条款。</li>}</ul></details>}</td>
  </tr>)}</tbody></table></div>
  {detail&&<article className="task-card"><h3>退役 {detail}</h3><p>退役只阻止新任务选择该条款；冻结任务与已发布观测保持原含义。</p><Field label="退役原因"><input value={reason} onChange={event=>setReason(event.target.value)} placeholder="例如：被 3.0 条款替代"/></Field><div className="button-row"><button disabled={busy||!reason.trim()} onClick={()=>{void act({package_id:packageId,package_version:version,source_datapoint_id:detail,reason,retired_by:credentials.operator || 'local-operator',dependent_extensions:(dependents[detail]?.dependents || []).filter((item:Row)=>item.relation_type==='project_extension').flatMap((item:Row)=>item.clauses || [])});setDetail(undefined);setReason('');}}>确认退役</button><button className="secondary" onClick={()=>setDetail(undefined)}>取消</button></div></article>}
 </>;
}
