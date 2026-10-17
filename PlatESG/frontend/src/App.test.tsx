// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import App from "./App";
import type { Entity, Metric, Observation } from "./types";

const ids = [
  "E1-5_01",
  "E1-5_05",
  "E1-5_09",
  "E1-6_07",
  "E1-6_09",
  "E1-6_10",
  "E1-6_11",
  "E2-4_02",
  "E2-4_03",
  "E2-4_09",
  "E2-5_02",
  "E3-4_01",
  "E3-4_02",
  "E3-4_03",
  "E3-4_11",
  "E3-4_12",
  "E4.IRO-1_14",
  "E4.SBM-3_04",
  "E4-5_01",
  "E4-5_02",
  "E5-4_02",
  "E5-4_05",
  "E5-5_07",
  "E5-5_11",
  "E5-5_15",
  "S1-6_02",
  "S1-6_12",
  "S1-9_02",
  "S1-13_03",
  "S1-14_02",
  "S1-14_05",
  "S1-16_01",
  "S2-1_05",
  "S2-3_02",
  "S2-3_06",
  "S2-4_11",
  "S3-1_01",
  "S3-3_11",
  "S3-4_02",
  "S3-4_11",
  "S4-1_01",
  "S4-3_02",
  "S4-3_13",
  "S4-4_11",
  "G1-1_03",
  "G1-4_01",
  "G1-4_02",
  "G1-4_04",
  "G1-6_01",
];
const metrics: Metric[] = ids.map((id, index) => ({
  metric_id: `metric-${id}`,
  source_datapoint_id: id,
  label_zh: index === 0 ? "总能源消耗" : `测试指标 ${id}`,
  topic: id.slice(0, 2),
  canonical_unit: "MWh",
  value_type: "decimal",
  description: "明确主体、期间、边界后保存报告披露。",
  required_dimensions: [],
  comparison_conditions: ["matching_boundary"],
  definition_version: "1.0.0",
}));
const entities: Entity[] = [
  {
    entity_ref: "demo.alpha",
    name_zh: "演示企业甲",
    entity_kind: "consolidated_group",
    industry_code: "示例制造",
    country_code: "CN",
    coverage_count: 1,
  },
  {
    entity_ref: "demo.beta",
    name_zh: "演示企业乙",
    entity_kind: "subsidiary",
    industry_code: "示例制造",
    country_code: "CN",
    coverage_count: 0,
  },
];
const observation: Observation = {
  observation_id: "demo-observation",
  entity_ref: "demo.alpha",
  entity_name: "演示企业甲",
  metric_id: "metric-E1-5_01",
  source_datapoint_id: "E1-5_01",
  label_zh: "总能源消耗",
  period_start: "2024-01-01",
  period_end: "2024-12-31",
  boundary_code: "consolidated_group",
  measurement_method: "reported",
  dimensions: {},
  canonical_unit: "MWh",
  value_numeric: "0.000001",
  lower_bound: null,
  upper_bound: null,
  statement_text: null,
  raw_unit: "MWh",
  raw_value_text: "0.000001",
  comparison_eligibility: "comparable",
  comparison_reasons: [],
  publication_status: "published",
  definition_version: "1.0.0",
};
let calls: { url: URL; options?: RequestInit }[];
let empty = false;
let numericVersions = false;
let periodCoverage = false;
let withCohorts = false;
let percentageConversion = false;
function installApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      const url = new URL(path, "http://localhost");
      calls.push({ url, options });
      const dataset_version = url.searchParams.get("dataset_version") || "d2";
      let body: unknown;
      const currentObservation = percentageConversion
        ? {
            ...observation,
            metric_id: "metric-E1-5_09",
            source_datapoint_id: "E1-5_09",
            label_zh: "可再生能源消费占比",
            canonical_unit: "ratio",
            value_numeric: "0.25125",
            raw_value_text: "25.125",
            raw_unit: "%",
            normalization_rule_version: "normalization-v1",
          }
        : observation;
      if (url.pathname === "/api/v1/datasets")
        body = {
          latest: empty ? null : numericVersions ? 2 : "d2",
          items: empty
            ? []
            : [
                {
                  dataset_version: numericVersions ? 2 : "d2",
                  created_at: "2026-10-04",
                  observation_count: numericVersions ? 64 : 1,
                },
                {
                  dataset_version: numericVersions ? 1 : "d1",
                  created_at: "2026-10-03",
                  observation_count: 0,
                },
              ],
        };
      else if (url.pathname === "/api/v1/entities")
        body = { dataset_version, items: empty ? [] : entities };
      else if (url.pathname === "/api/v1/metrics")
        body = { dataset_version, dictionary_version: "1.0.0", items: metrics };
      else if (url.pathname === "/api/v1/observations")
        body = {
          dataset_version,
          items: dataset_version === "d1" ? [] : [currentObservation],
          next_cursor: null,
        };
      else if (url.pathname.startsWith("/api/v1/observations/"))
        body = {
          ...currentObservation,
          dataset_version,
          sources: [
            {
              page_number: 12,
              source_quote: "示例原文：总能源消耗为 0.000001 MWh。",
              source_node_id: "source:demo",
              raw_value_text: currentObservation.raw_value_text,
              raw_unit: currentObservation.raw_unit,
              unit_conversion: percentageConversion
                ? {
                    rule_version: "esg-units-1.1.0",
                    rule_digest: "demo-frozen-rule-digest",
                    source_unit: "%",
                    reviewed_unit: "percent",
                    canonical_unit: "ratio",
                    multiplier: "0.01",
                    divisor: "1",
                    arithmetic: "exact_decimal_ratio",
                  }
                : undefined,
            },
          ],
          report: {
            report_title_snapshot: "演示报告",
            source_sha256: "demo-sha256",
            source_artifact_ref: "demo:report",
          },
          review: {
            fact_review_status: "approved",
            mapping_review_status: "approved",
            context_review_status: "approved",
          },
          history: [],
        };
      else if (url.pathname === "/api/v1/sources")
        body = {
          dataset_version,
          items: [
            {
              source_id: "source-1",
              report_title: "演示报告",
              report_year: 2024,
              publisher: "演示企业甲",
              entity_refs: ["demo.alpha"],
              observation_count: 1,
              is_demo: true,
              public_url: "https://example.org/report.pdf",
            },
          ],
        };
      else if (url.pathname === "/api/v1/coverage")
        body = {
          dataset_version,
          coverage_as_of: periodCoverage ? "2026-10-04T07:00:00Z" : null,
          items: metrics.map((metric, index) => ({
            entity_ref: "demo.alpha",
            metric_id: metric.metric_id,
            source_datapoint_id: metric.source_datapoint_id,
            status:
              index >= 16
                ? "package_planned"
                : dataset_version === "d2" && index === 0
                  ? "published"
                  : "not_extracted",
            reason: null,
            published_count: index === 0 ? 1 : 0,
            periods:
              periodCoverage && index === 0
                ? [
                    {
                      period_start: "2024-01-01",
                      period_end: "2024-12-31",
                      status: "published",
                      published_count: 1,
                      reason: null,
                    },
                    {
                      period_start: "2025-01-01",
                      period_end: "2025-12-31",
                      status: "review_required",
                      published_count: 0,
                      reason: null,
                    },
                  ]
                : [],
          })),
        };
      else if (url.pathname === "/api/v1/comparisons")
        body = {
          dataset_version: JSON.parse(options!.body as string).dataset_version,
          metric_id: "metric-E1-5_01",
          rows: [observation],
          excluded: [
            { entity_ref: "demo.beta", reasons: ["boundary_mismatch"] },
          ],
          contract_version: "1.0.0",
          available_cohorts: withCohorts
            ? [
                {
                  dimensions: {
                    period_start: "2024-01-01",
                    period_end: "2024-12-31",
                    boundary_code: "consolidated_group",
                    measurement_method: "reported",
                    entity_kind: "consolidated_group",
                    definition_version: "1.0.0",
                  },
                  observation_count: 1,
                  entity_refs: ["demo.alpha"],
                },
              ]
            : [],
        };
      else throw new Error(`Unhandled test API: ${url.pathname}`);
      return new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}
beforeEach(() => {
  calls = [];
  empty = false;
  numericVersions = false;
  periodCoverage = false;
  withCohorts = false;
  percentageConversion = false;
  window.history.replaceState({}, "", "/");
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  installApi();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("independent public data platform", () => {
  it("opens a website home with public navigation and explicit synthetic-data labeling", async () => {
    const { container } = render(<App />);
    await screen.findByRole("heading", { name: /看清数据。\s*理解企业。/ });
    const nav = screen.getByRole("navigation", { name: "主要导航" });
    expect(within(nav).getAllByRole("link")).toHaveLength(6);
    expect(container.querySelector(".sidebar")).toBeNull();
    expect(screen.getByRole("note").textContent).toContain("合成演示数据");
    expect(screen.queryByText("标准包已建指标")).toBeNull();
    expect(screen.queryByText("当前审核状态")).toBeNull();
  });
  it("shares directory filters in the URL and honors a browser navigation event", async () => {
    window.history.replaceState(
      {},
      "",
      "/?view=entities&dataset_version=d2&q=企业甲&industry=示例制造",
    );
    render(<App />);
    await screen.findByRole("heading", { name: "企业目录" });
    expect((screen.getByLabelText("搜索企业") as HTMLInputElement).value).toBe(
      "企业甲",
    );
    expect(screen.queryByRole("link", { name: /演示企业乙/ })).toBeNull();
    fireEvent.change(screen.getByLabelText("搜索企业"), {
      target: { value: "企业乙" },
    });
    expect(new URLSearchParams(window.location.search).get("q")).toBe("企业乙");
    window.history.replaceState(
      {},
      "",
      "/?view=metrics&dataset_version=d2&topic=E3",
    );
    fireEvent(window, new PopStateEvent("popstate"));
    await screen.findByRole("heading", { name: "指标浏览" });
    expect(screen.getAllByText(/^E3-4_/)).toHaveLength(5);
  });
  it("opens an indicator detail from a shareable URL and fixes its export to the same definition and dataset", async () => {
    window.history.replaceState(
      {},
      "",
      "/?view=metric&metric=metric-E1-5_01&dataset_version=d2&display=table",
    );
    render(<App />);
    await screen.findByRole("heading", { name: "理解这一指标" });
    const link = screen.getByRole("link", { name: "下载该指标数据" });
    const url = new URL(link.getAttribute("href")!, "http://localhost");
    expect(url.searchParams.get("metric_id")).toBe("metric-E1-5_01");
    expect(url.searchParams.get("dataset_version")).toBe("d2");
    expect(
      screen
        .getByRole("button", { name: "数据表" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });
  it("reads the frozen public source directory and links each data row to evidence", async () => {
    window.history.replaceState(
      {},
      "",
      "/?view=sources&dataset_version=d2&source_entity=demo.alpha",
    );
    render(<App />);
    await screen.findByRole("heading", { name: "演示报告" });
    expect(
      screen.getByRole("link", { name: "查看原始报告" }).getAttribute("href"),
    ).toBe("https://example.org/report.pdf");
    expect(
      calls
        .find((call) => call.url.pathname === "/api/v1/sources")
        ?.url.searchParams.get("dataset_version"),
    ).toBe("d2");
    fireEvent.click(
      screen.getByRole("button", { name: "查看总能源消耗原文证据" }),
    );
    await screen.findByRole("dialog");
    expect(new URLSearchParams(window.location.search).get("evidence")).toBe(
      "demo-observation",
    );
  });
  it("restores a shared comparison as a read query without losing its selected companies or pollutant filters", async () => {
    const params = new URLSearchParams({
      view: "compare",
      dataset_version: "d2",
      metric: "metric-E1-5_01",
      companies: JSON.stringify(["demo.alpha", "demo.beta"]),
      dimensions: JSON.stringify({ pollutant: "SOx", empty: "" }),
      compared: "1",
    });
    window.history.replaceState({}, "", "/?" + params);
    render(<App />);
    await screen.findByRole("heading", { name: "比较结果" });
    const posts = calls.filter((call) => call.options?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].options!.body as string)).toMatchObject({
      entity_refs: ["demo.alpha", "demo.beta"],
      dataset_version: "d2",
      dimensions: { pollutant: "SOx" },
    });
    expect(
      JSON.parse(posts[0].options!.body as string).dimensions.empty,
    ).toBeUndefined();
    expect(screen.getByText(/不代表 ESG 优劣排名/)).toBeTruthy();
  });
  it("does not silently replace an unavailable shared release with latest data", async () => {
    window.history.replaceState(
      {},
      "",
      "/?view=entity&entity=demo.alpha&dataset_version=unavailable",
    );
    render(<App />);
    await screen.findByText("此发布版本不可用");
    expect(
      new URLSearchParams(window.location.search).get("dataset_version"),
    ).toBe("unavailable");
    expect(
      calls.some((call) => call.url.pathname === "/api/v1/observations"),
    ).toBe(false);
  });
  it("keeps numeric dataset versions stable between selects and query parameters", async () => {
    numericVersions = true;
    render(<App />);
    await screen.findByText("64");
    expect(
      new URLSearchParams(window.location.search).get("dataset_version"),
    ).toBe("2");
    fireEvent.change(screen.getByRole("combobox", { name: "选择数据集版本" }), {
      target: { value: "2" },
    });
    await waitFor(() => expect(screen.getByText("64")).toBeTruthy());
    expect(
      calls
        .filter((call) => call.url.pathname === "/api/v1/entities")
        .every((call) => call.url.searchParams.get("dataset_version") === "2"),
    ).toBe(true);
  });
  it("shows published period coverage without exposing live candidate timestamps", async () => {
    periodCoverage = true;
    window.history.replaceState(
      {},
      "",
      "/?view=entity&entity=demo.alpha&dataset=d2",
    );
    const { container } = render(<App />);
    fireEvent.click(await screen.findByText("2 个已记录期间"));
    const periodView = container.querySelector(
      ".coverage-periods",
    )! as HTMLElement;
    expect(within(periodView).getByText("2024 年度")).toBeTruthy();
    expect(within(periodView).getByText("2025 年度")).toBeTruthy();
    expect(within(periodView).getByText("此版本暂无数据")).toBeTruthy();
    expect(screen.queryByText(/候选与人工覆盖记录截至/)).toBeNull();
  });
  it("lets the user explicitly choose a comparison cohort before submitting its identity filters", async () => {
    withCohorts = true;
    window.history.replaceState({}, "", "/?view=compare&dataset=d2");
    render(<App />);
    const checkboxes = await screen.findAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    fireEvent.click(screen.getByRole("button", { name: "生成比较表" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /2024 年度.*1 家企业/ }),
    );
    expect((screen.getByLabelText("期间起始") as HTMLInputElement).value).toBe(
      "2024-01-01",
    );
    expect((screen.getByLabelText("期间结束") as HTMLInputElement).value).toBe(
      "2024-12-31",
    );
    expect((screen.getByLabelText("披露边界") as HTMLInputElement).value).toBe(
      "consolidated_group",
    );
    fireEvent.click(screen.getByRole("button", { name: "生成比较表" }));
    await screen.findByRole("heading", { name: "比较结果" });
    const posts = calls.filter((call) => call.options?.method === "POST");
    const body = JSON.parse(posts[1].options!.body as string);
    expect(body.period_start).toBe("2024-01-01");
    expect(body.dimensions).toEqual({ entity_kind: "consolidated_group" });
    expect(body.dimensions.definition_version).toBeUndefined();
  });
  it("shows all 49 original major clause IDs while keeping package construction in the console", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("link", { name: "浏览全部指标" }));
    await screen.findByRole("heading", { name: "指标浏览" });
    for (const id of ids) expect(screen.getByText(id)).toBeTruthy();
    expect(screen.queryByText("标准包已建")).toBeNull();
    expect(screen.queryByText("标准包待建设")).toBeNull();
  });
  it("freezes the resolved dataset in links and all entity queries", async () => {
    render(<App />);
    const entityButton = await screen.findByRole("link", {
      name: /演示企业甲 演示数据/,
    });
    fireEvent.click(entityButton);
    await screen.findByRole("heading", { name: "已发布数据" });
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.pathname === "/api/v1/observations"),
      ).toBe(true),
    );
    expect(
      new URLSearchParams(window.location.search).get("dataset_version"),
    ).toBe("d2");
    for (const call of calls.filter((item) =>
      ["entities", "metrics", "observations", "coverage"].some((name) =>
        item.url.pathname.endsWith(`/${name}`),
      ),
    ))
      expect(call.url.searchParams.get("dataset_version")).toBe("d2");
    fireEvent.change(screen.getByRole("combobox", { name: "选择数据集版本" }), {
      target: { value: "d1" },
    });
    await screen.findByText("此版本暂无已发布观测");
    expect(screen.queryByText("0.000001")).toBeNull();
    expect(
      new URLSearchParams(window.location.search).get("dataset_version"),
    ).toBe("d1");
  });
  it("displays actual source evidence and precise values through the read-only detail endpoint", async () => {
    window.history.replaceState(
      {},
      "",
      "/?view=entity&entity=demo.alpha&dataset=d2",
    );
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "查看总能源消耗原文证据" }),
    );
    await screen.findByText("示例原文：总能源消耗为 0.000001 MWh。");
    expect(
      screen.getByRole("dialog", { name: "原文证据与修订详情" }),
    ).toBeTruthy();
    expect(
      calls
        .find((call) => call.url.pathname.endsWith("/demo-observation"))
        ?.url.searchParams.get("dataset_version"),
    ).toBe("d2");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("explains public unit conversion without exposing internal rule digests or live audit decisions", async () => {
    percentageConversion = true;
    window.history.replaceState(
      {},
      "",
      "/?view=entity&entity=demo.alpha&dataset=d2",
    );
    const { container } = render(<App />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "查看可再生能源消费占比原文证据",
      }),
    );
    await screen.findByText("单位换算");
    const conversion = container.querySelector(
      ".source-conversion",
    )! as HTMLElement;
    expect(
      within(conversion).getByText("25.125 % → 0.25125 比例（小数）"),
    ).toBeTruthy();
    expect(within(conversion).getByText("原值 × 0.01 ÷ 1")).toBeTruthy();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("25.125 %")).toBeTruthy();
    expect(within(dialog).getByText("E1-5_09")).toBeTruthy();
    expect(within(dialog).queryByText("来源标识")).toBeNull();
    expect(within(dialog).queryByText("esg-units-1.1.0")).toBeNull();
    expect(within(dialog).queryByText("demo-frozen-rule-digest")).toBeNull();
    expect(within(dialog).queryByText("当前审核状态")).toBeNull();
  });
  it("keeps excluded companies and reasons visible in comparison results", async () => {
    window.history.replaceState({}, "", "/?view=compare&dataset=d2");
    render(<App />);
    const checkboxes = await screen.findAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);
    fireEvent.click(screen.getByRole("button", { name: "生成比较表" }));
    await screen.findByRole("heading", { name: "比较结果" });
    expect(screen.getByText("披露边界不同")).toBeTruthy();
    const body = JSON.parse(
      calls.find((call) => call.options?.method === "POST")!.options!
        .body as string,
    );
    expect(body.entity_refs).toEqual(["demo.alpha", "demo.beta"]);
    expect(body.dataset_version).toBe("d2");
    expect(
      calls
        .filter((call) => call.options?.method && call.options.method !== "GET")
        .map((call) => call.url.pathname),
    ).toEqual(["/api/v1/comparisons"]);
  });
  it("explains an empty database without inserting a demo or fabricating published values", async () => {
    empty = true;
    render(<App />);
    await screen.findByText("等待首批数据发布");
    expect(screen.queryByText("演示企业甲")).toBeNull();
    expect(
      screen.getByText("尚无已发布数据版本 · 保留来源与公开修订"),
    ).toBeTruthy();
    expect(
      calls.every(
        (call) => !call.options?.method || call.options.method === "GET",
      ),
    ).toBe(true);
  });
});
