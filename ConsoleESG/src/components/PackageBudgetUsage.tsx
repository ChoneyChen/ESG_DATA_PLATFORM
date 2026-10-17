import type {Row} from '../api';
import {Notice} from './ui';

const number=(value:unknown)=>Number.isSafeInteger(value)&&Number(value)>=0?Number(value).toLocaleString('zh-CN'):'未记录';
const axes=[['model_calls','max_model_calls','模型调用'],['format_repairs','max_format_repairs','协议修复'],['input_tokens','max_input_tokens','输入 token'],['output_tokens','max_output_tokens','输出 token']] as const;
const basisNames:Record<string,string>={provider_reported:'服务实测',provider_prompt_tokens:'服务输入实测',provider_input_tokens:'服务输入实测',provider_generation_tokens:'服务输出实测',provider_output_tokens:'服务输出实测',runtime_prepared_input_ids:'实际模型输入',prepared_engine_input_ids:'实际模型输入',prepared_engine_input_tokens:'实际模型输入',rendered_text_tokens:'实际文本输入',runtime_generated_tokens:'实际模型输出',estimated_prompt_tokens:'输入估算',utf8_text_reservation:'历史字节估算',utf8_output_estimate:'输出字节估算',generation_not_started:'尚未开始生成',configured_output_reservation:'调用预留',legacy_unclassified:'历史未分类',caller_reservation:'调用前预留',caller_reported:'历史调用记录'};
function Basis({accounting}:{accounting?:Row}){
 if(!accounting)return null;
 return <details><summary>计数依据</summary>{(['input','output'] as const).map(axis=><p key={axis}>{axis==='input'?'输入':'输出'}：{Object.entries(accounting[axis] || {}).map(([basis,count])=>`${basisNames[basis] || basis} ${number(count)}`).join('；') || '暂无记录'}</p>)}</details>;
}

export function PackageBudgetUsage({budget}:{budget?:Row}){
 if(!budget)return <Notice>尚未取得各标准包的模型用量记录。</Notice>;
 if(budget.scope!=='report_summary'||budget.policy!=='independent-standard-package-v1'||budget.enforces_report_limit!==false)return <Notice>此历史记录采用旧预算口径。原始用量保留在诊断详情中，不能据此计算新版各包剩余额度。</Notice>;
 const packages:Row[]=Array.isArray(budget.packages)?budget.packages:[];
 const unassigned=budget.legacy_unattributed_usage;
 return <section aria-label="每包独立模型预算">
  <h3>每包独立模型预算</h3>
  <p>每个标准包拥有完整的调用与 token 额度；包内理解、抽取及修复共同计数。报告只汇总用量，各包额度互不扣减。</p>
  <div className="chips" aria-label="报告模型用量汇总">{axes.map(([key,,label])=><span key={key}>{label}累计 {number(budget.usage?.[key])}</span>)}</div>
  {Number(budget.usage?.unfinished_calls)>0&&<Notice>含 {number(budget.usage.unfinished_calls)} 次未完成调用的预留用量，尚非最终实测值。</Notice>}
  {packages.length>0?<div className="table-scroll"><table><caption>各包已用 / 各包上限</caption><thead><tr><th>标准包</th>{axes.map(([key,,label])=><th key={key}>{label}</th>)}</tr></thead><tbody>{packages.map(item=><tr key={item.job_id}><th scope="row">{item.package_id}<small className="table-secondary">{item.job_id}</small>{item.legacy_attributed&&<small className="table-secondary">由历史调用记录归属</small>}</th>{axes.map(([key,limit])=><td key={key}>{number(item.usage?.[key])} / {number(item.limits?.[limit])}{key==='format_repairs'&&item.limits?.[limit]===0?<small className="table-secondary">未启用修复</small>:Number.isSafeInteger(item.limits?.[limit])&&Number(item.usage?.[key])>=Number(item.limits[limit])&&<small className="table-secondary">达到本包上限</small>}</td>)}</tr>)}</tbody></table></div>:<p className="muted">尚无标准包用量明细。</p>}
  {Number(unassigned?.model_calls)>0&&<Notice>另有 {number(unassigned.model_calls)} 次历史调用尚未归属到标准包，已计入报告累计用量。</Notice>}
  <Basis accounting={budget.accounting}/>
 </section>;
}
