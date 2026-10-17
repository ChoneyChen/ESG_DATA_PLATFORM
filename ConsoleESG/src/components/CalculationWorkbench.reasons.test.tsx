import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { CalculationWorkbench, calculationReason } from './CalculationWorkbench';

const fixture = vi.hoisted(() => ({ reasons: [] as string[] }));
vi.mock('../state', () => ({
  useWorkspace: () => ({ refresh: vi.fn() }),
  useResource: (_service: string, path: string | null) => ({
    data: path === '/internal/v1/extensions/calculations'
      ? { items: [{ request_id: 'reason-test', extension_id: '范围1＋2合计', rule_id: '按地点法合计', status: 'blocked', reason_codes: fixture.reasons }] }
      : { items: [], releases: [] },
    loading: false,
    error: null,
  }),
}));
afterEach(() => { cleanup(); fixture.reasons = []; });

describe('计算工作台科学口径和来源原因', () => {
  it.each([
    ['shared_axis_mismatch_or_missing:entity_ref', '各操作数的测量主体缺失或不一致，不能按此公式合并'],
    ['shared_axis_mismatch_or_missing:gwp_basis', '各操作数的全球变暖潜势口径缺失或不一致，不能按此公式合并'],
    ['operand:scope2:selector_mismatch:scope_2_method', '操作数 范围2排放：范围2方法不满足公式对操作数的限定'],
    ['operand:scope1:not_in_input_dataset_or_context', '操作数 范围1排放：输入数据版本中不存在此观测或指定的审核口径'],
    ['missing_comparison_axis:amount_basis', '毛排放或净排放缺失或尚未确认'],
    ['comparison_axis_mismatch:water_measure', '水量类型不符合此指标的比较条件'],
    ['missing_required_context:measurement_method', '计量方法缺失或尚未确认'],
    ['fixed_dimension_mismatch:ghg_scope', '温室气体范围不符合指标定义的固定值或允许范围'],
  ])('明确解释真实后端原因 %s', (code, expected) => {
    expect(calculationReason(code)).toBe(expected);
  });

  it.each([
    'extension_release_snapshot_mismatch',
    'extension_definition_not_approved',
    'unsupported_or_unapproved_derivation_rule',
    'operand_selection_does_not_match_rule',
    'derived_target_identity_incomplete',
    'derived_value_outside_definition_bounds',
    'extension_not_in_published_dictionary',
    'frozen_calculation_receipt_differs',
    'calculation_pending',
    'calculation_rejected',
    'operand:scope1:approval_no_longer_active',
    'operand:scope2:not_currently_published',
    'operand:scope2:normalization_rule_snapshot_missing',
    'operand:scope1:target_unit_conversion_unavailable',
    'operand:scope1:non_terminating_conversion',
    'operand:scope1:unit_conversion_failed',
    'operand:scope1:invalid_extension_request',
  ])('将审核、合同、换算原因 %s 解释为中文', code => {
    expect(calculationReason(code)).toMatch(/[\u4e00-\u9fff]/);
    expect(calculationReason(code)).not.toContain(code);
  });

  it('实际请求卡显示中文原因，同时保留未知原码供诊断', () => {
    fixture.reasons = [
      'shared_axis_mismatch_or_missing:period_end',
      'operand:scope2:normalization_rule_snapshot_missing',
      'future_reason:scope_2_method',
      'operand:custom:future_conversion_code',
    ];
    render(<CalculationWorkbench />);
    expect(screen.getByText('各操作数的期间结束缺失或不一致，不能按此公式合并')).toBeTruthy();
    expect(screen.getByText('操作数 范围2排放：来源缺少匹配的冻结单位换算规则')).toBeTruthy();
    expect(screen.getByText('future_reason:scope_2_method')).toBeTruthy();
    expect(screen.getByText('operand:custom:future_conversion_code')).toBeTruthy();
  });

  it.each(['constructor', '__proto__', 'fact_constructor', 'operand:custom:constructor'])('未知原码 %s 始终保留为文本', code => {
    expect(calculationReason(code)).toBe(code);
  });
});
