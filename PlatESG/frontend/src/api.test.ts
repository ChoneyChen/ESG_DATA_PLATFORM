import { afterEach, describe, expect, it, vi } from "vitest";
import { apiUrl, request } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("versioned public API contract", () => {
  it("encodes opaque entity and dataset references without altering them", () => {
    const path = apiUrl("/v2/observations", {
      clause_key: "cn:甲/乙",
      cursor: "dataset:1+2",
      limit: 200,
      period: "",
    });
    const url = new URL(path, "https://example.test");
    expect(url.searchParams.get("clause_key")).toBe("cn:甲/乙");
    expect(url.searchParams.get("cursor")).toBe("dataset:1+2");
    expect(url.searchParams.has("period")).toBe(false);
  });
  it("rejects HTML proxy fallbacks rather than rendering invented empty data", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html/>", { headers: { "Content-Type": "text/html" } }),
        ),
    );
    await expect(request("/api/v2/metrics")).rejects.toThrow("未返回 JSON");
  });
  it("preserves structured service failure messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "数据版本不存在" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(request("/api/v2/entities")).rejects.toThrow("数据版本不存在");
  });
});
