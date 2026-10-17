import { describe, expect, it } from "vitest";
import { reasonLabel } from "./format";

describe("公开科学口径与发布原因", () => {
  it.each([
    ["missing_comparison_axis:gwp_basis", "全球变暖潜势口径待确认"],
    ["comparison_axis_mismatch:scope_2_method", "范围2方法不满足当前比较条件"],
    [
      "comparison_dimension_mismatch:water_measure",
      "水量类型不满足当前比较条件",
    ],
    ["missing_required_context:measurement_method", "计量方法待确认"],
    ["missing_required_dimension:pollutant", "污染物待确认"],
    ["fixed_dimension_mismatch:ghg_scope", "温室气体范围不符合指标定义"],
    ["dimension_value_mismatch:population", "人员范围不符合指标定义"],
    ["request_dimension_differs:period_start", "期间起始与本次筛选不同"],
    [
      "shared_axis_mismatch_or_missing:entity_ref",
      "参与计算的各来源测量主体缺失或不一致",
    ],
    [
      "operand:scope2:selector_mismatch:scope_2_method",
      "参与计算的来源范围2方法不满足公式要求",
    ],
  ])("将后端原因 %s 表达为明确中文", (code, expected) => {
    expect(reasonLabel(code)).toBe(expected);
  });

  it.each([
    "source_unit_unresolved",
    "reported_unit_disagrees_with_source",
    "unit_incompatible_or_unknown",
    "unresolved_reported_derived_conflict",
    "derived_target_identity_incomplete",
    "extension_definition_not_approved",
    "frozen_calculation_receipt_differs",
    "operand:scope1:approval_no_longer_active",
    "operand:scope2:not_currently_published",
    "operand:scope2:normalization_rule_snapshot_missing",
    "operand:scope1:target_unit_conversion_unavailable",
    "operand:scope1:non_terminating_conversion",
    "fact_review_required",
    "mapping_rejected",
    "calculation_pending",
  ])("单位、来源和确认原因 %s 不直接展示工程码", (code) => {
    expect(reasonLabel(code)).toMatch(/[\u4e00-\u9fff]/);
    expect(reasonLabel(code)).not.toContain(code);
  });

  it("未知原因与维度不泄漏 UUID、hash、JSON 或内部路径", () => {
    const internalId = "8f9d4a07-8a29-4b16-8671-128e0561a2df";
    for (const code of [
      `unknown_reason:${internalId}`,
      `来源失败：${internalId}`,
      `计算失败：${JSON.stringify({ task_id: internalId })}`,
      `missing_comparison_axis:${internalId}`,
      `operand:${internalId}:selector_mismatch:${internalId}`,
      `来源失败：${"a".repeat(64)}`,
      "来源失败：/Users/private/source.json",
      "constructor",
      "__proto__",
      "fact_constructor",
    ]) {
      const label = reasonLabel(code);
      expect(label).toMatch(/[\u4e00-\u9fff]/);
      expect(label).not.toContain(internalId);
      expect(label).not.toContain("task_id");
      expect(label).not.toContain("a".repeat(64));
      expect(label).not.toContain("/Users/");
      expect(label).not.toMatch(/[{}]/);
    }
    expect(reasonLabel("future_backend_reason")).toBe(
      "当前条件尚未满足，请进一步核对指标口径",
    );
  });

  it("保留冻结版本已经提供的公开中文覆盖说明", () => {
    const explanation = "该期间尚无正式观测，不能据此推断未披露";
    expect(reasonLabel(explanation)).toBe(explanation);
    expect(reasonLabel("equal_value_same_context_prefer_reported")).toBe(
      "同一口径下数值一致，优先展示报告披露值",
    );
  });
});
