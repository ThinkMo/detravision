import { useState } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { ConfirmDialog } from "./shared";

afterEach(cleanup);

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return <div><button type="button" onClick={() => setOpen(true)}>删除记录</button><button type="button">背景操作</button>{open ? <ConfirmDialog title="删除记录" confirmLabel="确认删除" onCancel={() => setOpen(false)} onConfirm={() => undefined}><p>删除后无法恢复。</p></ConfirmDialog> : null}</div>;
}

it("traps keyboard focus, closes on Escape, and restores the trigger", async () => {
  render(<DialogHarness />);
  const trigger = screen.getByRole("button", { name: "删除记录" });
  const backgroundAction = screen.getByRole("button", { name: "背景操作" });
  await userEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "删除记录" });
  const cancel = screen.getByRole("button", { name: "取消" });
  const confirm = screen.getByRole("button", { name: "确认删除" });

  await waitFor(() => expect(cancel).toHaveFocus());
  expect(backgroundAction.closest("[inert]")).not.toBeNull();

  confirm.focus();
  await userEvent.keyboard("{Tab}");
  expect(cancel).toHaveFocus();
  await userEvent.keyboard("{Shift>}{Tab}{/Shift}");
  expect(confirm).toHaveFocus();

  await userEvent.keyboard("{Escape}");
  expect(dialog).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
