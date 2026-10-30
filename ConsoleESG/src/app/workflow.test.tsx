import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { invalidate } from "../shared/resource";
const resource = (id: string) => ({
  schema_version: "report-resource-1.0",
  pdf_id: id,
  asset_id: "asset-" + id,
  sha256: "a".repeat(64),
  original_filename: id + ".pdf",
  title: "报告 " + id,
  company: { name: "企业 " + id },
  publication_year: 2026,
  reporting_year: 2025,
  industry: { name: "制造业" },
  revision: 1,
  asset_available: true,
  missing_metadata: [],
});
const c = {
  clause_key: "TEST_01",
  group_id: "TEST",
  title: "年度用量",
  data_kind: "quantitative",
  readiness: "ready",
  clause_digest: "b".repeat(64),
};
function mock(
  extra: (url: string, init: RequestInit) => unknown | undefined = () =>
    undefined,
) {
  const fn = vi.fn(async (url: string, init: RequestInit = {}) => {
    let body = extra(decodeURI(url), init);
    let status = 200;
    if (body instanceof Error) {
      status = 409;
      body = { detail: body.message };
    }
    if (body === undefined)
      body =
        url === "/document/api/report-resources"
          ? { reports: [resource("甲"), resource("乙")] }
          : url === "/document/api/report-sessions"
            ? []
            : url === "/extraction/api/clauses"
              ? { schema_version: "clause-catalog-3.0", clauses: [c] }
              : url === "/extraction/api/clause-groups"
                ? {
                    groups: [
                      {
                        group_id: "TEST",
                        title: "测试展示组",
                        clauses: ["TEST_01"],
                      },
                    ],
                  }
                : url === "/extraction/api/clause-selections"
                  ? { selections: { base32: ["TEST_01"] } }
                  : {};
    return {
      ok: status < 400,
      status,
      headers: new Headers(),
      json: async () => body,
    };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
async function open(hash: string) {
  location.hash = hash;
  await act(async () => {});
  render(<App />);
}
afterEach(() => {
  cleanup();
  invalidate();
  vi.unstubAllGlobals();
  location.hash = "";
});
vi.spyOn(window, "scrollTo").mockImplementation(() => {});
it("opens parent and child clause pages through real navigation without legacy controls", async () => {
  mock((url) =>
    url === "/extraction/api/clauses/TEST_01"
      ? {
          ...c,
          labels: { zh: c.title },
          definition: "本年度实际使用总量",
          exclusions: ["监管限值"],
          value: { canonical_unit: "t" },
          output_scopes: [{ scope_id: "current", label_zh: "本期" }],
          organization: { title: "结果", fields: [], curation: "sample" },
          reading: { instructions: ["读取原文"], rules: [] },
          retrieval: { positive_terms: ["用量"] },
          evidence: [],
        }
      : undefined,
  );
  await open("#/clauses");
  fireEvent.click(await screen.findByText("测试展示组"));
  await act(async () =>
    window.dispatchEvent(new HashChangeEvent("hashchange")),
  );
  fireEvent.click(await screen.findByText("年度用量"));
  await act(async () =>
    window.dispatchEvent(new HashChangeEvent("hashchange")),
  );
  expect(await screen.findByText("本年度实际使用总量")).toBeTruthy();
  expect(screen.getByText("← 测试展示组")).toBeTruthy();
  expect(screen.queryByText("版本差异")).toBeNull();
});
it("previews targets before execution and retries only the unsubmitted report in a batch", async () => {
  let failed = false;
  const posted: string[] = [];
  const fetch = mock((url, init) => {
    if (url === "/document/api/report-sessions/preview")
      return {
        targets: { digest: "d".repeat(64), clauses: { TEST_01: {} } },
        clause_count: 1,
        engine_fingerprint: "e".repeat(64),
        calls_model: false,
      };
    if (url === "/document/api/report-sessions" && init.method === "POST") {
      const body = JSON.parse(init.body as string);
      posted.push(body.input.asset_id);
      expect(body.expected_target_digest).toBe("d".repeat(64));
      expect(body.expected_engine_fingerprint).toBe("e".repeat(64));
      if (body.input.asset_id === "asset-乙" && !failed) {
        failed = true;
        return new Error("temporary rejection");
      }
      return { session_id: "run-" + body.input.asset_id };
    }
    return undefined;
  });
  await open("#/extractions/new");
  fireEvent.click(await screen.findByText("报告 甲"));
  fireEvent.click(screen.getByText("报告 乙"));
  fireEvent.click(await screen.findByText("年度用量"));
  fireEvent.click(screen.getByText("核对提取目标"));
  await screen.findByText("确认本次选择");
  expect(
    fetch.mock.calls.filter(
      ([url, init]) =>
        url === "/document/api/report-sessions" && init?.method === "POST",
    ),
  ).toHaveLength(0);
  fireEvent.click(screen.getByText("开始提取"));
  await screen.findByText("temporary rejection");
  fireEvent.click(screen.getByText("提交剩余报告"));
  await screen.findByText("已创建 2 个独立任务");
  expect(posted).toEqual(["asset-甲", "asset-乙", "asset-乙"]);
});
it("keeps report edits open on a revision conflict and submits only the original revision", async () => {
  const fetch = mock((url, init) =>
    url === "/document/api/report-resources/甲"
      ? init?.method === "PUT"
        ? new Error("Clause changed; reload before saving")
        : resource("甲")
      : undefined,
  );
  await open("#/reports/%E7%94%B2");
  fireEvent.click(await screen.findByText("编辑身份"));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("企业"), {
    target: { value: "改名" },
  });
  fireEvent.submit(dialog.querySelector("form")!);
  await screen.findByText("内容已变化，请重新载入并核对后提交。");
  expect(screen.getByRole("dialog")).toBeTruthy();
  const sent = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(sent[1]!.body as string).expected_revision).toBe(1);
  expect(JSON.parse(sent[1]!.body as string).resource.company.name).toBe(
    "改名",
  );
});
it("rejects malformed deep links without issuing a resource request", async () => {
  const fetch = mock();
  await open("#/extractions/%2Fetc");
  expect(screen.getByText("页面不存在")).toBeTruthy();
  expect(fetch.mock.calls.every(([url]) => url === "/document/api/console/monitor")).toBe(true);
});

it("reads real service readiness paths and never labels an unknown engine as needing restart", async () => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const fetch = vi.fn(async (url: string) => {
    if (
      url === "/document/health/compatibility" ||
      url === "/extraction/api/engine"
    )
      await pending;
    const known =
      url === "/document/health/compatibility" ||
      url === "/extraction/api/engine" ||
      url === "/document/api/operations/tasks" ||
      url === "/document/api/report-sessions";
    const body =
      url === "/document/health/compatibility"
        ? { readiness: { ready: true, blockers: [] } }
        : url === "/extraction/api/engine"
          ? { ready: true, version: "fixture" }
          : [];
    return {
      ok: known,
      status: known ? 200 : 404,
      headers: new Headers(),
      json: async () => body,
    };
  });
  vi.stubGlobal("fetch", fetch);
  await open("#/runtime");
  expect(screen.queryByText("待重启")).toBeNull();
  expect(screen.queryByText("未连接")).toBeNull();
  await act(async () => {
    release();
    await pending;
  });
  await waitFor(() => expect(screen.getAllByText("就绪")).toHaveLength(2));
});
