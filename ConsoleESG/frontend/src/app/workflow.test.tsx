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
          : url === "/document/api/report-resources/folders"
            ? { folders: [] }
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
it("submits the ordered reports atomically and retries the same batch after losing the response", async () => {
  const ids = ["乙", "甲", ...Array.from({ length: 10 }, (_, index) => String(index + 3))];
  const replaces = "original-session";
  const posted: unknown[] = [];
  let firstFinished = false;
  const batch = () => ({
    batch_id: "batch-window",
    name: "实测滑动窗口",
    created_at: "2026-11-20T00:00:00Z",
    window_size: 10,
    active_count: 10,
    waiting_count: firstFinished ? 1 : 2,
    finished_count: firstFinished ? 1 : 0,
    total_count: ids.length,
    members: ids.map((id, index) => {
      const admitted = index < (firstFinished ? 11 : 10);
      return {
        session_id: "run-" + id,
        name: "报告 " + id,
        position: index + 1,
        status: index === 0 && firstFinished ? "completed" : admitted ? "queued" : "waiting_for_window",
        admitted_at: admitted ? "2026-11-20T00:00:00Z" : null,
        replaces_session_id: index === 0 ? replaces : null,
      };
    }),
  });
  const fetch = mock((url, init) => {
    if (url === "/document/api/report-resources")
      return { reports: ids.map(resource) };
    if (url === "/document/api/report-sessions/preview")
      return {
        targets: { digest: "d".repeat(64), clauses: { TEST_01: {} } },
        clause_count: 1,
        engine_fingerprint: "e".repeat(64),
        calls_model: false,
      };
    if (url === "/document/api/report-extraction-batches" && init.method === "POST") {
      const body = JSON.parse(init.body as string);
      posted.push(body);
      expect(body.name).toBe("实测滑动窗口");
      expect(body.sessions.map((member: { input: { asset_id: string } }) => member.input.asset_id))
        .toEqual(ids.map(id => "asset-" + id));
      expect(body.sessions.map((member: { replaces_session_id?: string }) => member.replaces_session_id))
        .toEqual([replaces, ...ids.slice(1).map(() => undefined)]);
      for (const member of body.sessions) {
        expect(member.expected_target_digest).toBe("d".repeat(64));
        expect(member.expected_engine_fingerprint).toBe("e".repeat(64));
      }
      if (posted.length === 1) throw new Error("Lost the response");
      return batch();
    }
    if (url === "/document/api/report-extraction-batches/batch-window") return batch();
    return undefined;
  });
  await open(`#/extractions/new?report=${encodeURIComponent(ids[0])}&replaces=${replaces}`);
  expect((await screen.findByRole("link", { name: "原任务" })).getAttribute("href")).toBe(`#/extractions/${replaces}`);
  for (const id of ids.slice(1)) fireEvent.click(await screen.findByText("报告 " + id));
  fireEvent.change(screen.getByLabelText("任务名称"), { target: { value: "实测滑动窗口" } });
  fireEvent.click(await screen.findByText("年度用量"));
  fireEvent.click(screen.getByText("核对提取目标"));
  await screen.findByText("确认本次选择");
  expect(fetch.mock.calls.filter(([url]) => url === "/document/api/report-sessions/preview")).toHaveLength(1);
  expect(posted).toHaveLength(0);
  fireEvent.click(screen.getByText("开始提取"));
  await screen.findByText(/服务尚未确认本次提交，可以用同一次提交重试/);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "返回修改" }).disabled).toBe(true);
  fireEvent.click(screen.getByText("重试提交"));
  await screen.findByRole("heading", { name: "实测滑动窗口" });
  expect(screen.getByRole("link", { name: "原任务" }).getAttribute("href")).toBe(`#/extractions/${replaces}`);
  expect(posted).toHaveLength(2);
  expect(posted[0]).toEqual(posted[1]);
  expect(fetch.mock.calls.filter(([url, init]) =>
    url === "/document/api/report-sessions" && init?.method === "POST",
  )).toHaveLength(0);
  const eleventh = ids[10];
  const memberRow = () => screen.getByRole("link", { name: "报告 " + eleventh }).closest("tr")!;
  expect(within(memberRow()).getByText("等待入窗")).toBeTruthy();
  firstFinished = true;
  await act(async () => invalidate("document", "/api/report-extraction-batches/batch-window"));
  await waitFor(() => expect(within(memberRow()).getByText("窗口内")).toBeTruthy());
  expect(within(memberRow()).getByText("等待执行")).toBeTruthy();
});
it.each([
  { provider: "deepseek", model: "deepseek-v4-pro", title: "DeepSeek" },
  { provider: "dashscope", model: "qwen3.8-omni-flash", title: "阿里云百炼" },
])("saves the $provider key separately and freezes the model choices", async ({provider, model, title}) => {
  const selection = { provider, model };
  const documentSelection = provider === "dashscope" ? selection : undefined;
  const value = "fixture-secret-value";
  let configured = false;
  const providerStatus = () => ({ providers: [
    { provider: "local", configured: true, models: [] },
    { provider: "deepseek", configured: provider === "deepseek" && configured, models: ["deepseek-flash", "deepseek-v4-pro"] },
    { provider: "dashscope", configured: provider === "dashscope" && configured, models: ["qwen3.8-omni-flash"] },
  ] });
  const submissions: Record<string, unknown>[] = [];
  const fetch = mock((url, init) => {
    if (url === "/extraction/api/model-providers") return providerStatus();
    if (url === `/extraction/api/model-providers/${provider}/credential`) {
      expect(init.method).toBe("PUT");
      expect(JSON.parse(init.body as string)).toEqual({ api_key: value });
      configured = true;
      return providerStatus();
    }
    if (url === "/document/api/report-sessions/preview") {
      submissions.push(JSON.parse(init.body as string));
      return {
        targets: { digest: "d".repeat(64), clauses: { TEST_01: {} } },
        clause_count: 1,
        engine_fingerprint: "e".repeat(64),
        calls_model: false,
        model_selection: selection,
        document_model_selection: documentSelection,
      };
    }
    if (url === "/document/api/report-sessions" && init.method === "POST") {
      submissions.push(JSON.parse(init.body as string));
      return { session_id: "cloud-session" };
    }
  });
  await open("#/extractions/new");
  fireEvent.click(await screen.findByText("报告 甲"));
  fireEvent.click(await screen.findByText("年度用量"));
  fireEvent.change(screen.getByLabelText("模型提供者"), { target: { value: provider } });
  if (documentSelection) fireEvent.change(screen.getByLabelText("文档定位"), { target: { value: provider } });
  await screen.findByText("未配置");
  if (provider === "deepseek") {
    fireEvent.change(screen.getByLabelText("DeepSeek 模型"), { target: { value: selection.model } });
  }
  const preview = screen.getByRole<HTMLButtonElement>("button", { name: "核对提取目标" });
  expect(preview.disabled).toBe(true);
  const credential = screen.getByLabelText<HTMLInputElement>(`${title} API 密钥`);
  fireEvent.change(credential, { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "保存密钥" }));
  await screen.findByText("已配置");
  expect(credential.value).toBe("");
  fireEvent.click(preview);
  await screen.findByText(`提取模型 · ${title} · ${model}`);
  fireEvent.click(screen.getByRole("button", { name: "开始提取" }));
  await waitFor(() => expect(submissions).toHaveLength(2));
  expect(submissions.map((entry) => entry.model_selection)).toEqual([selection, selection]);
  expect(submissions.map((entry) => entry.document_model_selection)).toEqual([documentSelection, documentSelection]);
  expect(JSON.stringify(submissions)).not.toContain(value);
  expect(fetch.mock.calls.filter(([, init]) => String(init?.body || "").includes(value))).toHaveLength(1);
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
  fireEvent.change(within(dialog).getByLabelText("企业（选填）"), {
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
