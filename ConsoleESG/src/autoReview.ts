import type {Row} from './api';

const AXIS_NAMES:Record<string,string>={
  consolidation_approach:'合并口径',intensity_denominator:'强度分母',intensity_numerator_scope:'强度分子范围',
  statistical_time_basis:'统计时点',turnover_definition:'流失口径',population:'人群',accident_definition:'事故定义',
  waste_scope:'废弃物范围',waste_flow:'废弃物流向',material_scope:'材料范围',material_flow:'材料流向',
  aggregation_role:'聚合层级',denominator_scope:'分母范围',ghg_scope:'排放范围',training_scope:'培训范围',
  denominator_unit:'分母单位',rate_multiplier:'比率倍数',measurement_method:'测量方法',
};
const axis = (key:string)=>AXIS_NAMES[key] || key;

/** Plain-language reason a candidate still needs a person, from its proposal evidence. */
export function autoReviewReason(proposal:Row):string{
  const blockers:string[]=proposal.blockers || [];
  if(blockers.includes('single_source_mapping')) return '同一来源事实被映射到多个互斥指标';
  if(blockers.includes('consistent_value')) return '同一期间与边界下出现多个不同值';
  if(blockers.includes('mapping_exact')) return proposal.mapping_relation==='narrower'
    ? '概念匹配但边界更窄，需确认按该边界发布'
    : `映射为 ${proposal.mapping_relation || '未判定'}/${proposal.satisfaction_status || '未判定'}，需确认概念等价`;
  if(blockers.includes('dimensions_complete')){
    const missing:string[]=proposal.missing || [];
    if(missing.length) return `缺少测量轴：${missing.map(axis).join('、')}`;
    const fixed:string[]=proposal.fixed_dimension_mismatch || [];
    if(fixed.length) return `原文与固定定义冲突：${fixed.map(axis).join('、')}`;
    const mismatched:string[]=proposal.mismatched_dimensions || [];
    if(mismatched.length) return `维度值超出定义范围：${mismatched.map(axis).join('、')}`;
    return '测量维度待人工确认';
  }
  if(blockers.includes('unit_supported')) return `单位 ${proposal.raw_unit || '未记录'} 无法按 ${proposal.canonical_unit || '标准单位'} 换算`;
  if(blockers.includes('period_present')) return '报告期未确定';
  if(blockers.includes('entity_registered')) return '报告主体未登记';
  if(blockers.includes('boundary_resolved')) return '报告边界未确定';
  if(blockers.includes('physical_source')) return '缺少原文来源';
  if(blockers.includes('row_label')) return '缺少可核对的原始行标签';
  if(blockers.includes('value_numeric')) return '缺少可核对数值';
  if(blockers.includes('metric_ready')) return '指标定义未就绪';
  return '其它发布条件待确认';
}

export function autoReviewReasonCounts(proposals:Row[],reportRef:string):[string,number][]{
  const counts=new Map<string,number>();
  for(const proposal of proposals){
    if(proposal.already_approved || proposal.resolvable) continue;
    if(reportRef && String(proposal.report_ref || '')!==reportRef) continue;
    const reason=autoReviewReason(proposal);
    counts.set(reason,(counts.get(reason) || 0)+1);
  }
  return [...counts.entries()].sort((a,b)=>b[1]-a[1] || a[0].localeCompare(b[0],'zh-CN'));
}
