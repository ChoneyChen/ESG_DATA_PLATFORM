import { afterEach, expect, it, vi } from "vitest";
import { request } from "./api";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
it("bounds a stalled request and does not automatically replay a mutation", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(
    (_url, init) =>
      new Promise((_resolve, reject) =>
        init.signal.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  const pending = request("document", "/api/report-sessions", {
    method: "POST",
    body: "{}",
  });
  const check = expect(pending).rejects.toThrow("请求等待超时");
  await vi.advanceTimersByTimeAsync(45000);
  await check;
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("does not expose server diagnostics that contain credential-shaped text", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({
        ok: false,
        status: 409,
        headers: new Headers(),
        json: async () => ({ detail: "api_key=fixture-private" }),
      }),
  );
  await expect(request("document", "/api/test")).rejects.toThrow(
    "请求未通过服务检查",
  );
});
