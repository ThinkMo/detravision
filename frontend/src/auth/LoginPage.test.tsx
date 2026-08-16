import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../app/App";

const response = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

it("shows the configured name and a useful login error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/settings/site") {
        return response({ site_name: "厂区安全中心", logo_url: null });
      }
      if (path === "/api/auth/me") {
        return response({ detail: "authentication required" }, 401);
      }
      if (path === "/api/auth/login" && init?.method === "POST") {
        return response({ detail: "invalid username or password" }, 401);
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
    }),
  );
  window.history.replaceState({}, "", "/login");

  render(<App />);

  expect(await screen.findByText("厂区安全中心")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("用户名"), "admin");
  await userEvent.type(screen.getByLabelText("密码"), "wrong");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
});

it("uses the built-in brand when public settings cannot be loaded", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/settings/site") {
        throw new TypeError("network unavailable");
      }
      if (String(input) === "/api/auth/me") {
        return response({ detail: "authentication required" }, 401);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    }),
  );
  window.history.replaceState({}, "", "/login");

  render(<App />);

  expect(await screen.findByText("Safety Detect")).toBeInTheDocument();
  expect(document.title).toBe("Safety Detect");
});
