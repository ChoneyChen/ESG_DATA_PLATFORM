import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PreparePublicationPage } from "./create";
import { ReceivedDataPage } from "./data";
import { invalidate } from "../../shared/resource";

const projection = {
  projection_id: "projection-test",
  source: { title: "测试报告" },
  observations: 3,
  periods: { "2023": 1, "2024": 1, "2025": 1 },
};
const raw = {
  source_fact_id: "fact-one",
  source_row_label: "实测用量",
  value: {
    kind: "quantitative",
    value_raw: "0.000000000000000001",
    unit_raw: "吨",
  },
  identity: {
    reporting_period: { state: "stated", value: "2023", raw_text: "2023" },
    dimensions: {},
  },
  evidence: [{ page_index: 4, excerpt: "0.000000000000000001", role: "value" }],
};
const row = {
  ...raw,
  observation_id: "observation-one",
  title: "实测用量",
  period: { kind: "annual", year: 2023, raw: "2023" },
  relation: "match",
  source: { title: "测试报告" },
  comparison: { eligible: false },
  flags: [],
};
function mock(handler: (url: string, init: RequestInit) => unknown) {
  const fn = vi.fn(async (url: string, init: RequestInit = {}) => ({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => handler(url, init),
  }));
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => {
  cleanup();
  invalidate();
  vi.unstubAllGlobals();
  location.hash = "";
});

it("keeps received raw facts visible when projection fails", async () => {
  mock((url) =>
    url.startsWith("/document/")
      ? {
          session_id: "session-one",
          export_id: "export-one",
          stage_label: "投影失败 · 原值已保留",
          state: {
            export_id: "export-one",
            captured_facts: 1,
            capture_periods: [{ period: "2023", facts: 1 }],
            projection: { status: "failed" },
          },
        }
      : { items: [{ record: raw }], total: 1, returned: 1 },
  );
  render(<ReceivedDataPage sessionId="session-one" />);
  expect(await screen.findByText("0.000000000000000001")).toBeTruthy();
  expect(screen.getByText("投影失败 · 原值已保留")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看" }));
  expect(await screen.findByText("PDF第5页")).toBeTruthy();
  expect(screen.getByRole("button", { name: "重试投影" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "准备发布" })).toBeNull();
});

it("previews actual members and requires an explicit reason before publishing the same snapshot", async () => {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  mock((url, init) => {
    if (init.method === "POST")
      posts.push({ url, body: JSON.parse(String(init.body)) });
    if (url.startsWith("/database/v1/projections?"))
      return { items: [projection], total: 1, returned: 1 };
    if (url.startsWith("/database/v1/datasets/preview/observations"))
      return { items: [row], total: 1, returned: 1 };
    if (url === "/database/v1/datasets/preview")
      return {
        title: "三年数据",
        preview_digest: "a".repeat(64),
        selected: 1,
        total: 3,
        periods: { "2023": 1 },
      };
    if (url === "/database/v1/datasets")
      return { dataset_version_id: "dataset-new" };
    throw new Error("Unexpected route " + url);
  });
  render(<PreparePublicationPage initialProjection="projection-test" />);
  await screen.findByText("测试报告");
  fireEvent.change(screen.getByRole("textbox", { name: "数据集名称" }), {
    target: { value: "三年数据" },
  });
  fireEvent.click(screen.getByRole("button", { name: "核对数据" }));
  expect(await screen.findByText("0.000000000000000001")).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "发布此快照" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(posts.filter((p) => p.url === "/database/v1/datasets")).toHaveLength(
    0,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "发布说明" }), {
    target: { value: "已核对本次范围" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发布此快照" }));
  await waitFor(() =>
    expect(location.hash).toContain("/publication/dataset-new"),
  );
  const published = posts.find((p) => p.url === "/database/v1/datasets")!;
  expect(published.body).toMatchObject({
    title: "三年数据",
    projection_ids: ["projection-test"],
    preview_digest: "a".repeat(64),
    reason: "已核对本次范围",
    policy: { mode: "all_sources", relations: ["match"] },
  });
});
