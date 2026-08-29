import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { UsersPage } from "../admin/users/UsersPage";
import { App } from "./App";

const dashboardSummary = {
  camera_total: 0,
  camera_active: 0,
  events_today: 0,
  events_new: 0,
  recent_events: [],
  runtime: {
    enabled: false,
    active_cameras: [],
    queued_frames: 0,
    dropped_frames: 0,
    processed_frames: 0,
    created_events: 0,
    capture_errors: {},
  },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

it("traps keyboard focus in the mobile navigation and restores the trigger", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/settings/site") return Response.json({ site_name: "Safety Detect", logo_url: null });
    if (path === "/api/auth/me") return Response.json({ id: 1, username: "admin", role: "admin", must_change_password: false });
    if (path === "/api/dashboard/summary") return Response.json(dashboardSummary);
    throw new Error(`Unexpected request: ${path}`);
  }));
  window.history.replaceState({}, "", "/dashboard");
  const user = userEvent.setup();

  render(<App />);
  const trigger = await screen.findByLabelText("打开导航");
  trigger.style.display = "inline-flex";
  await user.click(trigger);
  const dialog = screen.getByLabelText("移动导航");
  dialog.style.display = "block";
  const navigation = within(dialog).getByRole("navigation", { name: "主导航" });
  const firstLink = within(navigation).getByRole("link", { name: "安全态势" });
  const lastLink = within(navigation).getByRole("link", { name: "系统设置" });

  expect(firstLink).toHaveFocus();
  await user.tab({ shift: true });
  expect(lastLink).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(dialog).not.toBeInTheDocument();
  await waitFor(() => expect(trigger).toHaveFocus());
});

it("gives scrollable user data a keyboard-accessible name and mobile labels", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([{
    id: 3,
    username: "operator",
    role: "user",
    enabled: true,
    must_change_password: false,
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:00:00Z",
  }])));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={client}><UsersPage /></QueryClientProvider>);
  const tableRegion = await screen.findByRole("region", { name: "用户列表" });
  expect(tableRegion).toHaveAttribute("tabindex", "0");
  const row = within(tableRegion).getByRole("row", { name: /operator/ });
  expect(within(row).getByText("operator")).toHaveAttribute("data-label", "用户名");
  expect(within(row).getByText("启用")).toHaveAttribute("data-label", "状态");
});
