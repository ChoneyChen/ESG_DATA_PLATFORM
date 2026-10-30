import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunMonitor } from "./monitor";
import { invalidate } from "../../shared/resource";
afterEach(() => {cleanup();invalidate();vi.unstubAllGlobals();});
it("keeps the monitor available, exposes real counts and returns focus on Escape", async () => {
  vi.stubGlobal("fetch",vi.fn(async () => ({ok:true,status:200,headers:new Headers(),json:async () => ({active_count:1,runs:[{session_id:"one",name:"本期排放量",status:"running",phase_label:"阅读条款证据",current:1,total:2,facts:3,model_calls:1,observation:"fresh"}]})})));
  render(<RunMonitor/>);
  const trigger=screen.getByRole("button",{name:/运行监视/});
  fireEvent.click(trigger);
  expect(await screen.findByText("本期排放量")).toBeTruthy();
  expect(screen.getByText("3 条原值")).toBeTruthy();
  expect(screen.queryByText(/%/)).toBeNull();
  fireEvent.keyDown(window,{key:"Escape"});
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
