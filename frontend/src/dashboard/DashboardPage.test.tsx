import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, it, vi } from "vitest";
import { App } from "../app/App";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

it("shows live dashboard counts while hiding administrator navigation from users", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/settings/site") {
        return json({ site_name: "厂区安全中心", logo_url: null });
      }
      if (path === "/api/auth/me") {
        return json({
          id: 7,
          username: "operator",
          role: "user",
          must_change_password: false,
        });
      }
      if (path === "/api/dashboard/summary") {
        return json({
          camera_total: 4,
          camera_active: 3,
          events_today: 12,
          events_new: 2,
          recent_events: [
            {
              id: 9,
              camera_id: 3,
              event_type: "no_helmet",
              confidence: 0.92,
              bbox: [1, 2, 3, 4],
              snapshot_url: null,
              status: "new",
              ack_by: null,
              ack_user_id: null,
              created_at: "2026-08-16T08:30:00+00:00",
            },
          ],
          runtime: {
            enabled: true,
            active_cameras: [1, 2, 3],
            queued_frames: 0,
            dropped_frames: 1,
            processed_frames: 240,
            created_events: 12,
            capture_errors: {},
          },
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
  window.history.replaceState({}, "", "/dashboard");

  render(<App />);

  expect(await screen.findByRole("heading", { name: "安全态势" })).toBeInTheDocument();
  expect(screen.getByText("12")).toBeInTheDocument();
  expect(screen.getByText("3 / 4")).toBeInTheDocument();
  expect(screen.queryByText("系统设置")).not.toBeInTheDocument();
  expect(screen.getByText("operator")).toBeInTheDocument();
});
