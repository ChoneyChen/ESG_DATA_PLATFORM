import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Modal } from "./ui";

afterEach(cleanup);
it("closes with Escape, restores focus and releases background scroll", () => {
  function Example() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>编辑报告</button>
        {open && (
          <Modal title="报告身份" onClose={() => setOpen(false)}>
            <input aria-label="企业" />
            <button>保存</button>
          </Modal>
        )}
      </>
    );
  }
  render(<Example />);
  const trigger = screen.getByText("编辑报告");
  trigger.focus();
  fireEvent.click(trigger);
  expect(document.body.style.overflow).toBe("hidden");
  expect(screen.getByRole("dialog")).toBe(document.activeElement);
  fireEvent.keyDown(document, { key: "Tab" });
  expect(document.activeElement).toBe(screen.getByLabelText("关闭窗口"));
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(screen.getByText("保存"));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(document.body.style.overflow).not.toBe("hidden");
});

it("sends Escape only to the top dialog", () => {
  const parent = vi.fn(),
    child = vi.fn();
  render(
    <>
      <Modal title="父窗口" onClose={parent}>
        父
      </Modal>
      <Modal title="子窗口" onClose={child}>
        子
      </Modal>
    </>,
  );
  fireEvent.keyDown(document, { key: "Escape" });
  expect(child).toHaveBeenCalledTimes(1);
  expect(parent).not.toHaveBeenCalled();
});
