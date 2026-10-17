// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import type { Evidence, Observation, ReportedProperty } from "../types";
import EvidenceDrawer from "./EvidenceDrawer";
import { ObservationTable } from "./ObservationTable";
import {
  ReportedProperties,
  ReportedPropertySummary,
} from "./ReportedProperties";
import { Dimensions } from "./Ui";

const properties: ReportedProperty[] = [
  {
    field_key: "operating_site",
    label_zh: "生产场址",
    value_raw: "西南生产基地",
    value_type: "text",
    unit_raw: null,
    evidence_scope: "field",
    evidence: [{ page_number: 18, source_quote: "生产场址：西南生产基地。" }],
    report_title: "2024 年可持续发展报告",
  },
  {
    field_key: "reported_capacity",
    label_zh: "原表产能",
    value_raw: "001.2300",
    value_type: "number",
    unit_raw: "万吨／年",
    evidence_scope: "source",
    evidence: [{ page_number: 19, source_quote: "本节列示基地及产能信息。" }],
  },
  {
    field_key: "activities",
    label_zh: "业务活动",
    value_raw: ["原油加工", "产品储运", "产品储运"],
    value_type: "text_array",
    unit_raw: null,
    evidence_scope: "field",
    evidence: [],
  },
  {
    field_key: "is_certified",
    label_zh: "原文认证标记",
    value_raw: "false",
    value_type: "boolean",
    unit_raw: null,
    evidence_scope: "field",
    evidence: [{ page_number: null, source_quote: null }],
  },
];

const observation: Observation = {
  observation_id: "property-fixture",
  entity_ref: "fixture-company",
  entity_name: "测试企业",
  metric_id: "energy.total_consumption",
  source_datapoint_id: "E1-5_01",
  value_origin: "reported",
  label_zh: "能源消耗总量",
  period_start: "2024-01-01",
  period_end: "2024-12-31",
  boundary_code: "own_operations",
  measurement_method: "reported",
  dimensions: { energy_flow: "consumption" },
  reported_properties: properties,
  canonical_unit: "MWh",
  value_numeric: "100",
  lower_bound: null,
  upper_bound: null,
  statement_text: null,
  raw_unit: "MWh",
  raw_value_text: "100.000",
  comparison_eligibility: "comparable",
  comparison_reasons: [],
  publication_status: "published",
  definition_version: "1.0.0",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("公开报告补充信息", () => {
  it("计算属性标明各操作数来源，同一报告的描述不合并且不显示内部角色码", () => {
    const { container } = render(
      <ReportedProperties
        properties={[
          {
            ...properties[0],
            source_role: "operand",
            source_label: "范围1排放",
          },
          {
            ...properties[0],
            source_role: "operand",
            source_label: "范围2地点法排放",
          },
          { ...properties[1], source_role: "operand", source_label: null },
          {
            ...properties[3],
            source_role: "private_engine_role",
            source_label: "private-engine-label",
          },
        ]}
      />,
    );
    expect(screen.getByText("计算来源：范围1排放")).toBeTruthy();
    expect(screen.getByText("计算来源：范围2地点法排放")).toBeTruthy();
    expect(screen.getByText("计算来源：计算操作数")).toBeTruthy();
    expect(screen.getAllByText("来源报告：2024 年可持续发展报告")).toHaveLength(
      2,
    );
    expect(container.textContent).not.toContain("private_engine_role");
    expect(container.textContent).not.toContain("private-engine-label");
    expect(container.querySelectorAll(".reported-property-card")).toHaveLength(
      4,
    );
  });

  it("可展开原值和列表，保留数值精度、原单位、布尔原表示与重复项", () => {
    const { container } = render(
      <ReportedProperties properties={properties} />,
    );
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    fireEvent.click(screen.getByText("查看 4 项报告补充信息"));
    expect(details.open).toBe(true);
    expect(screen.getByText("001.2300")).toBeTruthy();
    expect(screen.getByText("原单位：万吨／年")).toBeTruthy();
    expect(screen.getByText("false")).toBeTruthy();
    expect(screen.getAllByText("产品储运")).toHaveLength(2);
    expect(screen.getByText("原油加工")).toBeTruthy();
    expect(container.textContent).not.toContain("1.23 万吨");
  });

  it("来源级说明不冒充字段精确证据，页码与引用按各字段保留", () => {
    const { container } = render(
      <ReportedProperties properties={properties} />,
    );
    const cards = container.querySelectorAll(".reported-property-card");
    expect(within(cards[0] as HTMLElement).getByText("字段证据")).toBeTruthy();
    expect(
      within(cards[0] as HTMLElement).getByText("字段引用 · 文档页码 18"),
    ).toBeTruthy();
    expect(
      within(cards[1] as HTMLElement).getByText("来源级说明"),
    ).toBeTruthy();
    expect(within(cards[1] as HTMLElement).queryByText("字段证据")).toBeNull();
    expect(
      within(cards[1] as HTMLElement).getByText(
        "以下为报告来源的说明，未提供此字段的单独引用。",
      ),
    ).toBeTruthy();
    expect(
      within(cards[1] as HTMLElement).getByText("来源说明 · 文档页码 19"),
    ).toBeTruthy();
    expect(screen.getByText("来源报告：2024 年可持续发展报告")).toBeTruthy();
    expect(screen.getByText("本节列示基地及产能信息。")).toBeTruthy();
    expect(screen.getByText("字段引用 · 未提供页码")).toBeTruthy();
    expect(screen.getByText("未提供引用片段。")).toBeTruthy();
  });

  it("表格摘要只列少量中文标签和项数，不堆叠原始内容或字段码", () => {
    const { container } = render(
      <ReportedPropertySummary properties={properties} />,
    );
    expect(container.textContent).toBe(
      "报告补充信息 · 4 项：生产场址、原表产能等",
    );
    expect(container.textContent).not.toContain("operating_site");
    expect(container.textContent).not.toContain("001.2300");
    expect(container.textContent).not.toContain("false");
  });

  it("未返回补充信息时不凭空生成展示，旧 list 类型仍保留原列表", () => {
    const { rerender, container } = render(<ReportedProperties />);
    expect(container.textContent).toBe("");
    rerender(<ReportedProperties properties={[]} />);
    expect(container.textContent).toBe("");
    rerender(
      <ReportedProperties
        properties={[{ ...properties[2], value_type: "list" }]}
      />,
    );
    expect(screen.getAllByText("产品储运")).toHaveLength(2);
  });

  it("空原值和空列表明确表达，单位缺失时不自动推断", () => {
    const { container } = render(
      <ReportedProperties
        properties={[
          { ...properties[0], value_raw: "", unit_raw: null },
          { ...properties[2], value_raw: [] },
        ]}
      />,
    );
    expect(screen.getByText("原字段为空")).toBeTruthy();
    expect(screen.getByText("原报告记录为空列表")).toBeTruthy();
    expect(container.querySelector(".reported-property-unit")).toBeNull();
  });

  it("错误对象结构不裸显 JSON 或内部属性，引用中的 HTML 按文本显示", () => {
    const malformed = {
      ...properties[0],
      field_key: "private_actor_field",
      value_raw: {
        reviewer_ref: "private-reviewer",
        source_path: "/private/path",
      },
      evidence: [
        {
          page_number: 18,
          source_quote: "<img src=x onerror=alert(1)>",
          bbox: { private_token: "not-public" },
        },
      ],
    } as unknown as ReportedProperty;
    const { container } = render(
      <ReportedProperties properties={[malformed]} />,
    );
    expect(
      screen.getByText("此项内容尚未结构化，请查看报告原文。"),
    ).toBeTruthy();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
    for (const hidden of [
      "private_actor_field",
      "private-reviewer",
      "/private/path",
      "private_token",
      "not-public",
    ])
      expect(container.innerHTML).not.toContain(hidden);
  });

  it("真实表格组件接通补充项摘要，同时保留指标原编号和主值", () => {
    render(<ObservationTable rows={[observation]} onEvidence={vi.fn()} />);
    expect(
      screen.getByText("报告补充信息 · 4 项：生产场址、原表产能等"),
    ).toBeTruthy();
    expect(screen.getByText("E1-5_01")).toBeTruthy();
    expect(screen.getByText("100")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "查看能源消耗总量原文证据" }),
    ).toBeTruthy();
  });

  it("实际来源抽屉读取固定版本的公开补充属性，不呈现后台字段", async () => {
    const evidence: Evidence = {
      ...observation,
      dataset_version: 3,
      report: { report_title_snapshot: "2024 年可持续发展报告" },
      sources: [],
    };
    const fetchMock = vi.fn(
      async (_path: string) =>
        new Response(
          JSON.stringify({
            ...evidence,
            reviewer_ref: "private-reviewer",
            source_path: "/private/path",
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <EvidenceDrawer
        observationId="property-fixture"
        dataset="3"
        close={vi.fn()}
      />,
    );
    expect(
      await screen.findByRole("heading", { name: "报告补充信息" }),
    ).toBeTruthy();
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/observations/property-fixture?dataset_version=3",
    );
    fireEvent.click(screen.getByText("查看 4 项报告补充信息"));
    expect(screen.getByText("001.2300")).toBeTruthy();
    expect(screen.getByText("来源级说明")).toBeTruthy();
    expect(container.innerHTML).not.toContain("private-reviewer");
    expect(container.innerHTML).not.toContain("/private/path");
  });
});

describe("科学维度的友好降级", () => {
  it("已知维度中文化，未知标量保留值且使用友好标签，不曝 raw-code title", () => {
    const { container } = render(
      <Dimensions
        values={{
          energy_flow: "consumption",
          unfamiliar_site: "南部炼厂",
          自定义地区: "西南地区",
          missing_axis: null,
          numeric_axis: 0,
        }}
      />,
    );
    expect(screen.getByText("能源流向：消耗")).toBeTruthy();
    expect(screen.getByText("补充口径：南部炼厂")).toBeTruthy();
    expect(screen.getByText("自定义地区：西南地区")).toBeTruthy();
    expect(screen.getByText("补充口径：未说明")).toBeTruthy();
    expect(screen.getByText("补充口径：0")).toBeTruthy();
    expect(container.innerHTML).not.toContain("unfamiliar_site");
    expect(container.querySelector("[title]")).toBeNull();
  });

  it("对象、数组和原型属性不泄漏工程 JSON 或使组件崩溃", () => {
    const { container } = render(
      <Dimensions
        values={{
          unknown_object: { reviewer_ref: "private-reviewer" },
          unknown_array: ["private-item"],
          constructor: "constructor",
        }}
      />,
    );
    expect(
      screen.getAllByText("补充口径：未结构化信息，请查看报告原文"),
    ).toHaveLength(2);
    expect(screen.getByText("补充口径：constructor")).toBeTruthy();
    expect(container.innerHTML).not.toContain("private-reviewer");
    expect(container.innerHTML).not.toContain("private-item");
    expect(container.innerHTML).not.toContain("reviewer_ref");
  });
});
