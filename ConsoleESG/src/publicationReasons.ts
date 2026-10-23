/** Publication gate reasons are distinct from comparison eligibility. */
const labels:Record<string,string>={
 fact_pending:'报告事实尚待审核',fact_candidate:'报告事实尚待审核',fact_review_required:'报告事实尚待审核',
 fact_needs_confirmation:'报告事实仍需确认',fact_rejected:'报告事实已被拒绝',fact_grounded:'报告事实有来源依据，仍需审核',fact_superseded:'报告事实已被更新事实替代',
 mapping_pending:'条款映射尚待审核',mapping_review_required:'条款映射尚待审核',mapping_rejected:'条款映射已被拒绝',
 measurement_context_required:'测量口径尚未确认',
 context_required:'主体、期间与口径未确认',reviewed_context_missing:'主体、期间与测量口径尚未审核确认',
 mapping_not_equivalent:'披露内容与指标定义不完全对应',unsupported_mapping_relation:'映射不能进入此指标',mapping_not_exact:'映射不是完全对应',
 missing_entity:'测量主体缺失',missing_period:'报告期间缺失',missing_boundary:'报告边界缺失',missing_required_dimensions:'缺少指标必要维度',
 physical_source_missing:'缺少绑定的原文证据',source_not_available:'来源不可读取',source_fact_superseded:'原披露已被更新事实替代',
 unsupported_unit:'报告单位不能安全换算',source_unit_unresolved:'报告原始单位尚未确定或不能安全换算',
 reported_unit_not_reviewed:'报告单位尚未审核确认',unit_incompatible_or_unknown:'单位未知或与指标标准单位不兼容',
 reported_unit_disagrees_with_source:'审核确认的单位与报告原始单位不一致',
 unknown_metric:'原编号尚未进入平台指标字典',not_platform_major:'当前平台未选择此条款',not_in_major_profile:'当前平台未选择此条款',
 metric_not_ready:'指标定义尚未达到发布条件',clause_identity_mismatch:'披露条款身份与指标定义不一致',
 extension_execution_snapshot_mismatch:'延伸条款的执行快照与批准版本不一致',standard_snapshot_mismatch:'提取所用标准快照与发布指标绑定版本不一致',
 statement_missing:'缺少报告原文陈述',numeric_value_missing:'缺少有效数值披露',non_finite_numeric_value:'数值格式不符合有限精确数值要求',
 ratio_out_of_range:'比例超出指标定义的允许范围',metric_value_out_of_range:'数值超出指标定义的允许范围',
 value_role_mismatch:'实际值、目标或重述属性与指标定义不一致',invalid_supersession_identity:'修订值与被替代观测的身份不一致',
 explicitly_superseded:'观测已被明确批准的修订替代',unresolved_source_conflict:'同一指标与口径存在尚未解决的来源冲突',
};
const fields:Record<string,string>={
 entity_ref:'测量主体',period_start:'期间起始日',period_end:'期间结束日',boundary_code:'报告边界',
 measurement_method:'测量方法',value_role:'实际、目标或重述属性',reported_unit:'报告单位',
 ghg_scope:'温室气体排放范围',scope:'排放范围',scope2_method:'范围2核算方法',gwp_basis:'全球变暖潜势依据',
 gross_net_basis:'排放总量或净额口径',emissions_basis:'排放核算口径',energy_type:'能源类型',energy_source:'能源来源',intensity_numerator_scope:'强度分子范围',intensity_denominator:'强度分母',board_definition:'董事会口径',
 renewable_basis:'可再生能源口径',pollutant:'污染物',pollutant_medium:'污染介质',medium:'污染介质',
 facility:'设施',site:'场址',geography:'地理范围',population:'人群范围',workforce_scope:'劳动力范围',
 water_measure:'水量类型',water_definition:'水量定义',water_risk_class:'水风险等级',
 risk_classification_method:'风险识别方法',estimation_method:'估算方法',denominator_scope:'分母口径',
 material_flow:'物质流向',classification_basis:'物质分类依据',waste_type:'废弃物类型',hazardous_class:'危险类别',
 activity:'活动范围',business_segment:'业务分部',reporting_period:'报告期间',reporting_boundary:'报告边界',
 accident_definition:'事故定义',accounting_standard:'核算标准',aggregation_role:'汇总角色',amount_basis:'金额口径',
 area_measurement_basis:'面积计量口径',averaging_basis:'平均值口径',business_scale_basis:'业务规模口径',
 category_coverage:'类别覆盖范围',channel_scope:'渠道范围',community_scope:'社区范围',complaint_definition:'投诉定义',
 concern_class:'关注物质类别',consolidation_approach:'合并核算方式',consumer_population:'消费者人群',
 count_basis:'计数口径',counting_basis:'计数依据',currency:'币种',denominator_unit:'分母单位',
 energy_accounting_basis:'能源核算口径',energy_flow:'能源流向',entity_kind:'主体类别',fatality_cause_scope:'死亡原因范围',
 fine_basis:'罚款口径',gender:'性别',hazard_class:'危险类别',hazard_classification:'危险分类依据',
 impact_scope:'影响范围',impact_type:'影响类型',incident_definition:'事件定义',incident_scope:'事件范围',
 invoice_scope:'发票范围',legal_scope:'法律适用范围',management_level_definition:'管理层级定义',
 material_scope:'材料范围',pay_definition:'薪酬定义',payment_start_definition:'付款起算定义',policy_scope:'政策范围',
 pollutant_definition:'污染物定义',pollution_medium:'污染介质',rate_multiplier:'比率倍数',release_boundary:'排放边界',
 remedy_scope:'补救范围',scope3_category:'范围3类别',sensitivity_class:'敏感性类别',site_definition:'场址定义',
 statistical_time_basis:'统计时间口径',substance:'物质',training_scope:'培训范围',turnover_definition:'员工流动定义',
 value_chain_stage:'价值链环节',waste_flow:'废弃物流向',waste_scope:'废弃物范围',worker_population:'劳动者人群',
};
const prefixes:Record<string,string>={
 missing_required_dimension:'缺少必要维度',missing_required_context:'缺少必要测量口径',
 dimension_value_mismatch:'维度不符合指标允许值',fixed_dimension_mismatch:'维度不符合指标固定口径',
};
export function blockerText(reason:string):string {
 if(labels[reason])return labels[reason];
 const colon=reason.indexOf(':');
 if(colon>0){const prefix=prefixes[reason.slice(0,colon)],field=fields[reason.slice(colon+1)];
  if(prefix)return `${prefix}：${field || '尚需核对的科学字段'}`;
 }
 return '尚需核对的发布条件';
}
