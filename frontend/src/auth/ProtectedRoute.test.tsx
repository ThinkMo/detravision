import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../app/App";

type User = {
  id: number;
  username: string;
  role: "admin" | "user";
  must_change_password: boolean;
};

const response = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function installApi(initialUser: User | null, loginUser?: User) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/settings/site") {
        return response({ site_name: "DetraVision", logo_url: null });
      }
      if (path === "/api/auth/me") {
        return initialUser
          ? response(initialUser)
          : response({ detail: "authentication required" }, 401);
      }
      if (path === "/api/auth/login" && init?.method === "POST" && loginUser) {
        return response(loginUser);
      }
      if (path === "/api/dashboard/summary") {
        return response({
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
        });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

it("redirects a forced-change user to the password page", async () => {
  installApi({
    id: 1,
    username: "admin",
    role: "admin",
    must_change_password: true,
  });
  window.history.replaceState({}, "", "/dashboard");

  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "修改初始密码" }),
  ).toBeInTheDocument();
});

it("returns a signed-in user to the protected destination", async () => {
  installApi(null, {
    id: 2,
    username: "operator",
    role: "user",
    must_change_password: false,
  });
  window.history.replaceState({}, "", "/dashboard");

  render(<App />);

  await userEvent.type(await screen.findByLabelText("用户名"), "operator");
  await userEvent.type(screen.getByLabelText("密码"), "correct-password");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(
    await screen.findByRole("heading", { name: "安全态势" }),
  ).toBeInTheDocument();
});

it("shows a 403 page when an ordinary user opens an admin route", async () => {
  installApi({
    id: 2,
    username: "operator",
    role: "user",
    must_change_password: false,
  });
  window.history.replaceState({}, "", "/admin");

  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "无权访问" }),
  ).toBeInTheDocument();
});
