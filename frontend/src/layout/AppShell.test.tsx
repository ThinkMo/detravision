import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, it, vi } from "vitest";
import { App } from "../app/App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

it("returns focus to the navigation trigger after Escape closes the drawer", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/settings/site") return Response.json({ site_name: "Safety Detect", logo_url: null });
    if (path === "/api/auth/me") return Response.json({ id: 1, username: "admin", role: "admin", must_change_password: false });
    if (path === "/api/dashboard/summary") return Response.json({
      camera_total: 0, camera_active: 0, events_today: 0, events_new: 0, recent_events: [],
      runtime: { enabled: false, active_cameras: [], queued_frames: 0, dropped_frames: 0, processed_frames: 0, created_events: 0, capture_errors: {} },
    });
    throw new Error(`Unexpected request: ${path}`);
  }));
  window.history.replaceState({}, "", "/dashboard");
  const user = userEvent.setup();

  render(<App />);
  const trigger = await screen.findByLabelText("打开导航");
  trigger.style.display = "inline-flex";
  await user.click(trigger);
  expect(screen.getByLabelText("移动导航")).toBeInTheDocument();
  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog", { name: "移动导航" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
