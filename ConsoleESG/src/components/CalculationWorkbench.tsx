import {useCallback,useEffect,useState} from 'react';
import {items,navigate,post,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Badge,Diagnostics,Field,MetricValue,Notice,ResourceState,Section} from './ui';

export function operandChoices(rows:Row[],selector:Row){return rows.filter(row=>row.source_datapoint_id===selector.source_datapoint_id&&(row.value_origin || 'reported')==='reported'&&typeof row.value_numeric==='string'&&row.lower_bound==null&&row.upper_bound==null&&[null,undefined,'exact'].includes(row.precision_qualifier));}
export function operandReferences(detail:Row|undefined,observationId:string){
 const unique=new Map<string,Row>();
 for(const source of detail?.sources || [])if(source.context_id&&source.fact_review_decision_id&&source.mapping_review_decision_id)unique.set(source.context_id,{observation_id:observationId,context_id:source.context_id,fact_review_decision_id:source.fact_review_decision_id,mapping_review_decision_id:source.mapping_review_decision_id});
 return [...unique.values()];
}
const labels:Record<string,string>={
 candidate_created:'计算候选已保存',blocked:'计算被阻塞',pending:'等待计算审核',review_required:'需要计算审核',approved:'计算已批准',rejected:'计算已退回',published:'计算值已发布',
 calculation_pending:'计算结果尚未审核',calculation_review_required:'计算尚未批准',calculation_rejected:'计算审核已退回',calculation_approved:'计算审核已批准',
 input_dataset_not_sealed:'输入数据版本尚未冻结',unresolved_reported_derived_conflict:'报告披露与计算结果存在未解决的数值冲突',
 definition_not_approved:'延伸定义尚未获得有效批准',extension_definition_not_approved:'延伸定义尚未获得有效批准',target_extension_definition_mismatch:'平台指标与已登记延伸定义不一致',
 extension_release_snapshot_mismatch:'延伸定义发布版本与请求冻结快照不一致',unsupported_or_unapproved_derivation_rule:'计算规则不受支持或未满足已批准公式要求',
 operand_selection_does_not_match_rule:'操作数数量、角色或来源选择不符合公式，不能重复选择同一观测',derived_target_identity_incomplete:'计算结果缺少必要的主体、期间或口径',
 derived_value_outside_definition_bounds:'计算结果超出延伸指标定义允许的范围',extension_not_in_published_dictionary:'延伸指标尚未纳入当前正式指标字典',
 frozen_calculation_receipt_differs:'计算结果、测量口径或操作数血缘与冻结计算凭据不一致',
 not_in_input_dataset_or_context:'输入数据版本中不存在此观测或指定的审核口径',approval_no_longer_active:'事实或映射的当前批准已失效',not_currently_published:'当前正式数据已不包含此来源',
 fact_approval_reference_invalid:'事实批准引用与冻结来源不匹配',mapping_approval_reference_invalid:'映射批准引用与冻结来源不匹配',point_value_required:'需要精确点值，不能使用区间、上下限或近似值',
 frozen_standard_binding_mismatch:'原条款、标准包版本或标准定义与公式要求不一致',context_snapshot_differs:'主体、期间、边界、方法或维度与冻结口径不一致',
 normalization_rule_snapshot_missing:'来源缺少匹配的冻结单位换算规则',target_unit_conversion_unavailable:'没有将操作数换算为目标单位的已登记规则',non_terminating_conversion:'换算结果为无限循环小数，无法满足精确十进制计算',unit_conversion_failed:'操作数单位换算失败',invalid_extension_request:'计算请求或数值格式不符合合同要求',
 reviewed_context_missing:'缺少已审核的测量口径',mapping_not_equivalent:'映射未确认完全对应原条款',physical_source_missing:'缺少实际原文来源',source_fact_superseded:'来源事实已由新的批准披露替代',
 clause_identity_mismatch:'原条款或延伸条款身份与指标定义不一致',standard_snapshot_mismatch:'来源标准包与冻结标准定义不一致',extension_execution_snapshot_mismatch:'延伸条款执行快照与已登记定义不一致',
 source_unit_unresolved:'报告原始单位未知或无法解析',reported_unit_not_reviewed:'报告单位尚未获得明确审核',unit_incompatible_or_unknown:'审核单位未知或不兼容',reported_unit_disagrees_with_source:'审核确认的单位与报告来源单位不一致',
 statement_missing:'缺少报告原文陈述',numeric_value_missing:'缺少数值披露',non_finite_numeric_value:'数值不是有效的有限十进制',ratio_out_of_range:'比例超出允许范围',metric_value_out_of_range:'数值超出指标定义范围',
 value_role_mismatch:'实际值、重述值或上下文值角色不符合指标定义',invalid_supersession_identity:'修订值主体、期间或口径与被替代观测不同',unresolved_source_conflict:'相同口径的来源数值冲突尚未解决',
 explicitly_superseded:'观测已被明确修订替代',not_in_major_profile:'指标不在当前主要条款配置中',metric_not_ready:'指标定义尚未达到可发布状态',
};
const calculationAxes:Record<string,string>={entity_ref:'测量主体',entity_kind:'主体类型',period_start:'期间起始',period_end:'期间结束',period_basis:'期间口径',boundary_code:'报告边界',reporting_boundary:'报告边界',organizational_boundary:'组织边界',measurement_method:'计量方法',methodology:'计量方法',definition_version:'指标定义版本',canonical_unit:'标准单位',reported_unit:'报告单位',value_role:'数值角色',ghg_scope:'温室气体范围',scope:'排放范围',scope_2_method:'范围2方法',amount_basis:'毛排放或净排放',gwp_basis:'全球变暖潜势口径',consolidation_approach:'合并核算方法',accounting_standard:'核算标准',scope3_category:'范围3类别',scope3_categories:'范围3类别覆盖',category_coverage:'类别覆盖',energy_source:'能源来源',energy_flow:'能源流向',energy_accounting_basis:'能源核算基础',aggregation_role:'聚合口径',pollutant:'污染物',pollutant_name:'污染物',analyte:'分析物',pollution_medium:'排放介质',media:'排放介质',release_boundary:'排放边界',pollutant_definition:'污染物定义',substance:'物质',substance_category:'物质分类',concern_class:'关注物质分类',hazard_class:'危害分类',material_flow:'物质流向',classification_basis:'物质分类基础',water_measure:'水量类型',water_definition:'水量定义',water_flow:'水流向',water_risk_class:'水风险等级',water_risk_area:'水风险区域',water_risk_basis:'水风险识别口径',risk_classification_method:'风险识别方法',estimation_method:'估算方法',denominator_scope:'分母口径',population:'人员范围',geography:'地理范围',currency:'币种'};
function calculationAxisReason(prefix:string,axis:string){
 const name=Object.hasOwn(calculationAxes,axis)?calculationAxes[axis]:axis;
 if(['missing_comparison_axis','missing_comparison_dimension','missing_required_dimension','missing_required_context','missing_dimension'].includes(prefix))return `${name}缺失或尚未确认`;
 if(['comparison_axis_mismatch','comparison_dimension_mismatch'].includes(prefix))return `${name}不符合此指标的比较条件`;
 if(prefix==='shared_axis_mismatch_or_missing')return `各操作数的${name}缺失或不一致，不能按此公式合并`;
 if(prefix==='selector_mismatch')return `${name}不满足公式对操作数的限定`;
 if(['fixed_dimension_mismatch','dimension_value_mismatch'].includes(prefix))return `${name}不符合指标定义的固定值或允许范围`;
 if(prefix==='request_dimension_differs')return `${name}与本次筛选条件不同`;
 if(prefix==='dimension_mismatch')return `${name}不一致`;
 return null;
}
export function calculationReason(code:string){
 if(Object.hasOwn(labels,code))return labels[code];
 const parts=code.split(':');
 if(parts[0]==='operand'){
  const operand=({scope1:'范围1排放',scope2:'范围2排放'} as Record<string,string>)[parts[1]] || parts[1];
  const reason=parts[2]==='selector_mismatch'&&parts[3]?calculationAxisReason(parts[2],parts[3]):Object.hasOwn(labels,parts[2])?labels[parts[2]]:null;
  return reason?`操作数 ${operand}：${reason}`:code;
 }
 const axisReason=parts[1]?calculationAxisReason(parts[0],parts[1]):null;
 if(axisReason)return axisReason;
 const states:Record<string,string>={pending:'尚未审核',candidate:'尚未审核',review_required:'需要进一步审核',needs_confirmation:'需要进一步确认',rejected:'已退回',approved:'已批准'};
 for(const [prefix,subject]of[['fact_','来源事实'],['mapping_','指标映射']])if(code.startsWith(prefix)&&Object.hasOwn(states,code.slice(prefix.length)))return subject+states[code.slice(prefix.length)];
 return code;
}

export function CalculationWorkbench(){
 const {refresh}=useWorkspace(),registry=useResource('publication','/internal/v1/extensions',true),metrics=useResource('publication','/internal/v1/metrics',true),datasets=useResource('publication','/v1/datasets'),calculations=useResource('publication','/internal/v1/extensions/calculations',true);
 const [releaseDigest,setRelease]=useState(''),[ruleId,setRule]=useState(''),[dataset,setDataset]=useState(''),[operands,setOperands]=useState<Record<string,Row>>({}),[requestKey,setRequestKey]=useState(()=>crypto.randomUUID()),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const releases=items(registry.data,'releases').map(row=>row.release || row),release=releases.find(row=>row.release_digest===releaseDigest),rule=release?.definition.derivation_rules.find((row:Row)=>row.rule_id===ruleId),metric=items(metrics.data).find(row=>row.extension_binding?.release_digest===releaseDigest),observations=useResource('publication',dataset?`/v1/observations?limit=1000&dataset_version=${encodeURIComponent(dataset)}`:null);
 useEffect(()=>{setOperands({});setRequestKey(crypto.randomUUID());},[releaseDigest,ruleId,dataset]);
 const updateOperand=useCallback((id:string,value:Row|undefined)=>{setOperands(previous=>{const next={...previous};value?next[id]=value:delete next[id];return next;});setRequestKey(crypto.randomUUID());},[]);
 const ready=!!rule&&!!metric&&!!dataset&&rule.operands.every((selector:Row)=>!!operands[selector.operand_id])&&!observations.error&&!observations.loading;
 return <Section title="延伸条款计算与血缘审核"><Notice>选择冻结数据版本及已发布操作数。后端再次核验主体、期间、边界、方法、原编号和批准记录；计算结果单独审核后进入正式发布。</Notice><ResourceState error={registry.error || metrics.error || datasets.error || calculations.error}/>
 <form onSubmit={async event=>{event.preventDefault();if(!ready)return;setBusy(true);try{const result=await post('publication','/internal/v1/extensions/calculations',{idempotency_key:requestKey,input_dataset_version:Number(dataset),extension_id:release.definition.extension_id,definition_version:release.definition.definition_version,release_digest:releaseDigest,rule_id:ruleId,target_metric_id:metric.metric_id,target_definition_version:metric.definition_version,operands:rule.operands.map((selector:Row)=>({operand_id:selector.operand_id,...operands[selector.operand_id]}))});setMessage(result.status==='blocked'?`计算条件尚未满足：${(result.reason_codes || []).map(calculationReason).join('；')}`:'计算请求已持久保存，请在下方核对计算候选和完整血缘。');refresh();}catch(error){setMessage((error as Error).message);}finally{setBusy(false);}}}>
 <div className="form-grid"><Field label="已登记延伸定义"><select value={releaseDigest} onChange={event=>{setRelease(event.target.value);setRule('');}}><option value="">选择已登记版本</option>{releases.map(row=><option key={row.release_digest} value={row.release_digest}>{row.definition.labels.zh} · v{row.definition.definition_version}</option>)}</select></Field><Field label="计算规则"><select value={ruleId} onChange={event=>setRule(event.target.value)} disabled={!release}><option value="">明确选择方法</option>{release?.definition.derivation_rules.map((row:Row)=><option key={row.rule_id} value={row.rule_id}>{row.operands.map((operand:Row)=>operand.source_datapoint_id).join(' ＋ ')} · {row.target_axis_projections?.find((projection:Row)=>projection.target_axis==='scope_2_method')?.fixed_value || row.rule_id}</option>)}</select></Field><Field label="冻结输入数据版本"><select value={dataset} onChange={event=>setDataset(event.target.value)}><option value="">选择正式数据版本</option>{items(datasets.data).map(row=><option key={row.dataset_version} value={row.dataset_version}>版本 {row.dataset_version} · {row.observation_count} 条观测</option>)}</select></Field></div>
 {release&&!metric&&<Notice tone="error">该已登记定义尚未进入当前平台主要指标字典。先在标准中心创建所选延伸的字典版本。</Notice>}{rule&&<><p>公式：{rule.operands.map((operand:Row)=>operand.source_datapoint_id).join(' ＋ ')}；输出单位 {release.definition.canonical_unit}。</p><p>必须一致：{rule.shared_match_axes.join('、')}。</p>{dataset&&<ResourceState error={observations.error} loading={observations.loading&&!observations.data}/>}<div className="two-columns">{rule.operands.map((selector:Row)=><OperandSelector key={`${releaseDigest}:${ruleId}:${dataset}:${selector.operand_id}`} selector={selector} dataset={dataset} rows={items(observations.data)} onChange={updateOperand}/>)}</div>{observations.data?.next_cursor&&<Notice>当前读取前1000条正式观测；其余观测需先按企业、指标缩小查询范围，当前窗口不代表全部数据。</Notice>}</>}
 <button disabled={busy||!ready}>{busy?'核验并计算中…':'核验操作数并保存计算候选'}</button>{message&&<Notice>{message}</Notice>}</form>
 <h3>已保存计算请求</h3><ResourceState loading={calculations.loading&&!calculations.data} empty={!items(calculations.data).length}/>{items(calculations.data).map(row=><CalculationCard key={row.request_id} row={row}/>)}
 <button className="secondary" onClick={()=>navigate('/publication')}>前往发布中心处理批准后的事件</button>
 </Section>;
}

function OperandSelector({selector,dataset,rows,onChange}:{selector:Row,dataset:string,rows:Row[],onChange:(id:string,value:Row|undefined)=>void}){
 const [observationId,setObservation]=useState(''),[contextId,setContext]=useState('');const detail=useResource('publication',observationId&&dataset?`/internal/v1/observations/${encodeURIComponent(observationId)}?dataset_version=${encodeURIComponent(dataset)}`:null,true),references=operandReferences(detail.data,observationId),choice=references.find(row=>row.context_id===contextId),row=rows.find(row=>row.observation_id===observationId);
 useEffect(()=>{onChange(selector.operand_id,choice);},[selector.operand_id,choice?.observation_id,choice?.context_id,choice?.fact_review_decision_id,choice?.mapping_review_decision_id,onChange]);
 return <article className="task-card"><h4>{selector.source_datapoint_id}</h4><Field label={`选择 ${selector.source_datapoint_id} 已发布观测`}><select value={observationId} disabled={!dataset} onChange={event=>{setObservation(event.target.value);setContext('');}}><option value="">依据主体、期间与方法选择</option>{operandChoices(rows,selector).map(value=><option key={value.observation_id} value={value.observation_id}>{value.entity_name} · {value.period_start} — {value.period_end} · {value.value_numeric} {value.canonical_unit} · {value.dimensions?.scope_2_method || value.measurement_method || '方法待核对'}</option>)}</select></Field><ResourceState error={detail.error} loading={detail.loading&&!detail.data}/>{row&&<><MetricValue value={row.value_numeric} unit={row.canonical_unit}/><p>报告边界：{row.boundary_code}；已保存维度：{JSON.stringify(row.dimensions)}。</p><Field label={`确认 ${selector.source_datapoint_id} 来源批准记录`}><select value={contextId} onChange={event=>setContext(event.target.value)}><option value="">核对并选择固定来源</option>{references.map((reference,index)=><option key={reference.context_id} value={reference.context_id}>已批准来源 {index+1} · {reference.context_id}</option>)}</select></Field>{detail.data&&!references.length&&<Notice tone="error">该观测没有完整的冻结事实、映射批准与口径引用，不能构造计算操作数。</Notice>}{detail.data?.sources?.map((source:Row,index:number)=><blockquote key={index}>{source.source_quote}<footer>PDF 第 {source.page_number} 页</footer></blockquote>)}<Diagnostics value={{required_axes:selector.required_axes,references}} title="公式方法限制与固定审核身份"/></>}</article>;
}

function CalculationCard({row}:{row:Row}){const {refresh}=useWorkspace(),[reason,setReason]=useState(''),[decision,setDecision]=useState('approved'),[busy,setBusy]=useState(false),[message,setMessage]=useState('');return <article className="task-card"><h4>{row.extension_id} · {row.rule_id}</h4><div className="chips"><span>{labels[row.status] || row.status}</span>{row.candidate_id&&<Badge value={row.review_status}/>}<Badge value={row.publication_status || 'not_published'}/></div>{row.candidate_id&&<MetricValue value={row.numeric_value} unit={row.canonical_unit}/>}<ul>{[...(row.reason_codes || []),...(row.publication_reasons || [])].map((code:string,index:number)=><li key={index}>{calculationReason(code)}</li>)}</ul>{row.lineage_snapshot&&<><p>输入数据版本 {row.input_dataset_version}；精确十进制相加。</p>{row.lineage_snapshot.operands?.map((operand:Row)=><article className="work-item" key={operand.operand_id}><strong>{operand.source_datapoint_id}</strong><MetricValue value={operand.numeric_value} unit={operand.canonical_unit}/><span>{operand.reviewed_axes?.entity_ref} · {operand.reviewed_axes?.period_start} — {operand.reviewed_axes?.period_end}</span></article>)}<Diagnostics value={row.lineage_snapshot} title="冻结定义、公式、操作数、换算和批准血缘"/></>}{row.candidate_id?<form onSubmit={async event=>{event.preventDefault();setBusy(true);try{await post('publication','/internal/v1/extensions/calculation-reviews',{candidate_id:row.candidate_id,decision,reason});setMessage('计算审核已保存；批准后由统一发布事务再次检查来源有效性。');refresh();}catch(error){setMessage((error as Error).message);}finally{setBusy(false);}}}><Field label="计算审核决定"><select value={decision} onChange={event=>setDecision(event.target.value)}><option value="approved">批准计算与血缘</option><option value="rejected">退回计算</option><option value="review_required">保留待审核</option></select></Field><Field label="计算核对依据"><textarea required value={reason} onChange={event=>setReason(event.target.value)} placeholder="核对所有操作数口径、精确换算及结果"/></Field><button disabled={busy||!reason.trim()}>保存计算审核</button></form>:<button className="secondary" disabled={busy} onClick={async()=>{setBusy(true);try{await post('publication',`/internal/v1/extensions/calculations/${row.request_id}/retry`,{});refresh();}catch(error){setMessage((error as Error).message);}finally{setBusy(false);}}}>条件修正后重试此请求</button>}{message&&<Notice>{message}</Notice>}</article>;}
