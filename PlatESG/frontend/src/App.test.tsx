// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import App from "./App";

const dataset = {
  dataset_version_id: "dataset-one",
  title: "全年份测试集",
  status: "published",
  observations: 3,
  periods: { "2023": 1, "2024": 1, unknown: 1 },
  published_at: "2026-10-31T00:00:00Z",
  policy: { mode: "all_sources", relations: ["match"] },
};
const observation = (period = "2023", raw = "0.00000000000000000001") => ({
  observation_id: "observation-" + period,
  source_fact_id: "fact-" + period,
  title: "实测排放",
  source_row_label: "氮氧化物",
  relation: "match",
  period: {
    kind: period === "unknown" ? "unknown" : "annual",
    raw: period,
    year: period === "unknown" ? null : Number(period),
  },
  value: { kind: "quantitative", value_raw: raw, unit_raw: "吨" },
  identity: { dimensions: {} },
  comparison: { eligible: false, reasons: ["identity_unconfirmed"] },
  source: { title: "测试报告", pdf_sha256: "a".repeat(64) },
  evidence: [{ page_index: 72, excerpt: raw, role: "value" }],
});
let calls: URL[];
let empty = false;
let withdrawn = false;
beforeEach(() => {
  calls = [];
  empty = false;
  withdrawn = false;
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      const url = new URL(path, "http://localhost");
      calls.push(url);
      let body: unknown;
      let status = 200;
      if (url.pathname === "/api/v2/datasets")
        body = {
          items: empty ? [] : [dataset],
          total: empty ? 0 : 1,
          returned: empty ? 0 : 1,
        };
      else if (withdrawn && url.pathname === "/api/v2/datasets/dataset-one") {
        body = { detail: "此发布已撤下" };
        status = 404;
      } else if (url.pathname === "/api/v2/datasets/dataset-one")
        body = dataset;
      else if (url.pathname.endsWith("/definitions"))
        body = [
          {
            clause_key: "TEST",
            clause_digest: "b".repeat(64),
            title: "实测排放",
            contract: { definition: "保留报告披露的所有期间。" },
          },
        ];
      else if (url.pathname.endsWith("/sources")) body = [observation().source];
      else if (url.pathname.endsWith("/observations")) {
        const period = url.searchParams.get("period"),
          cursor = url.searchParams.get("cursor");
        const rows = period
          ? [observation(period, "0")]
          : cursor
            ? [observation("unknown", "7")]
            : [observation(), observation("2024", "-1.25")];
        body = {
          items: rows,
          total: period ? 1 : 3,
          returned: rows.length,
          next_cursor: !period && !cursor ? "page-two" : null,
        };
      } else if (url.pathname.includes("/observations/")) body = observation();
      else
        throw new Error("Unexpected current product endpoint: " + url.pathname);
      return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("opens the dataset hierarchy, keeps all periods and exact sparse values, paginates and filters downloads", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("link", { name: /全年份测试集/ }));
  expect(await screen.findByText("0.00000000000000000001")).toBeTruthy();
  expect(screen.getByText("-1.25")).toBeTruthy();
  expect(
    screen.getByRole("combobox", { name: "期间" }).getAttribute("value"),
  ).toBeNull();
  expect(screen.getAllByText("口径未确认").length).toBe(2);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  expect(await screen.findByText("7")).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "期间" }), {
    target: { value: "2024" },
  });
  expect(await screen.findByText("0")).toBeTruthy();
  await waitFor(() =>
    expect(calls.at(-1)?.searchParams.get("period")).toBe("2024"),
  );
  expect(calls.at(-1)?.searchParams.has("cursor")).toBe(false);
  expect(
    screen.getByRole("link", { name: "CSV 长表" }).getAttribute("href"),
  ).toContain("period=2024");
  fireEvent.click(screen.getByRole("button", { name: "来源" }));
  expect(await screen.findByRole("link", { name: /查看原PDF/ })).toBeTruthy();
  expect(calls.every((url) => url.pathname.startsWith("/api/v2/"))).toBe(true);
});

it("opens a fact as a child page with physical evidence and returns to its dataset", async () => {
  window.history.replaceState({}, "", "/?dataset_id=dataset-one");
  render(<App />);
  fireEvent.click((await screen.findAllByRole("link", { name: "查看 →" }))[0]);
  expect(await screen.findByRole("heading", { name: "原文证据" })).toBeTruthy();
  expect(screen.getByText("PDF第73页")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /查看原PDF/ }).getAttribute("href"),
  ).toContain("#page=73");
  fireEvent.click(screen.getByRole("link", { name: "← 全年份测试集" }));
  expect(await screen.findByRole("combobox", { name: "期间" })).toBeTruthy();
});

it("shows an empty publication catalog without legacy metric placeholders", async () => {
  empty = true;
  render(<App />);
  expect(await screen.findByText("尚无已发布数据")).toBeTruthy();
  expect(screen.queryByText("指标浏览")).toBeNull();
});

it("does not render withdrawn data when the API refuses a saved dataset URL", async () => {
  withdrawn = true;
  window.history.replaceState({}, "", "/?dataset_id=dataset-one");
  render(<App />);
  expect(await screen.findByText("此发布已撤下")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "期间" })).toBeNull();
});
