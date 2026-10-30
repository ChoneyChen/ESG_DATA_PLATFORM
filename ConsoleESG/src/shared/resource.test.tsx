import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { invalidate, useResource } from "./resource";

afterEach(() => {
  cleanup();
  invalidate();
  vi.unstubAllGlobals();
});
function Reader({ path }: { path: string }) {
  const result = useResource<{ name: string }>("document", path);
  return <p>{result.data?.name || result.error?.message || "读取中"}</p>;
}
const response = (name: string) => ({
  ok: true,
  status: 200,
  headers: new Headers(),
  json: async () => ({ name }),
});

it("shares one request between readers of the same resource", async () => {
  const fetch = vi.fn().mockResolvedValue(response("同一报告"));
  vi.stubGlobal("fetch", fetch);
  render(
    <>
      <Reader path="/probe/shared" />
      <Reader path="/probe/shared" />
    </>,
  );
  await waitFor(() => expect(screen.getAllByText("同一报告")).toHaveLength(2));
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("recovers after StrictMode cancels the initial subscription", async () => {
  const fetch = vi.fn().mockResolvedValue(response("报告已载入"));
  vi.stubGlobal("fetch", fetch);
  render(
    <StrictMode>
      <Reader path="/probe/strict" />
    </StrictMode>,
  );
  await screen.findByText("报告已载入");
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
});

it("does not present the previous report when the route changes during a request", async () => {
  let resolveFirst!: (value: unknown) => void;
  const fetch = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockResolvedValue(response("报告乙"));
  vi.stubGlobal("fetch", fetch);
  const view = render(<Reader path="/probe/report-a" />);
  view.rerender(<Reader path="/probe/report-b" />);
  await screen.findByText("报告乙");
  await act(async () => resolveFirst(response("报告甲")));
  expect(screen.queryByText("报告甲")).toBeNull();
  expect(screen.getByText("报告乙")).toBeTruthy();
});

it("reloads after a mutation invalidates an in-flight read", async () => {
  let resolveFirst!: (value: unknown) => void;
  const fetch = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockResolvedValue(response("新修订"));
  vi.stubGlobal("fetch", fetch);
  render(<Reader path="/probe/revision" />);
  act(() => invalidate("document", "/probe/revision"));
  await act(async () => resolveFirst(response("旧修订")));
  await screen.findByText("新修订");
  expect(screen.queryByText("旧修订")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(2);
});
