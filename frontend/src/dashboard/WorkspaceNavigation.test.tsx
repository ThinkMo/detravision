import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { ModelsPage } from "../admin/models/ModelsPage";
import { DashboardPage } from "./DashboardPage";

const summary = {
  camera_total: 0, camera_active: 0, events_today: 0, events_new: 0, recent_events: [],
  runtime: { enabled: false, active_cameras: [], queued_frames: 0, dropped_frames: 0, processed_frames: 0, created_events: 0, capture_errors: {} },
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function LocationControls() {
  const location = useLocation();
  const navigate = useNavigate();
  return <><output aria-label="当前地址">{location.pathname}{location.search}</output><button onClick={() => navigate(-1)}>返回上一步</button></>;
}

function renderWorkspace(role: "admin" | "user") {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === "/api/auth/me") return Response.json({ id: 1, username: role, role, must_change_password: false });
    if (String(input) === "/api/dashboard/summary") return Response.json(summary);
    throw new Error(`Unexpected request: ${input}`);
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><AuthProvider><DashboardPage /></AuthProvider></MemoryRouter></QueryClientProvider>);
}

it("gives administrators direct entries into annotation, training and recognition", async () => {
  renderWorkspace("admin");
  const workflow = await screen.findByRole("navigation", { name: "视觉任务快捷入口" });
  expect(within(workflow).getByRole("link", { name: /标注数据/ })).toHaveAttribute("href", "/admin/models?view=datasets");
  expect(within(workflow).getByRole("link", { name: /训练模型/ })).toHaveAttribute("href", "/admin/models?view=training");
  expect(within(workflow).getByRole("link", { name: /识别图片/ })).toHaveAttribute("href", "/batch");
});

it("keeps restricted annotation and training actions out of the operator workflow", async () => {
  renderWorkspace("user");
  const workflow = await screen.findByRole("navigation", { name: "视觉任务快捷入口" });
  expect(within(workflow).queryByRole("link", { name: /标注数据|训练模型/ })).not.toBeInTheDocument();
  expect(within(workflow).getByRole("link", { name: /识别图片/ })).toHaveAttribute("href", "/batch");
  expect(within(workflow).getByRole("link", { name: /实时监控/ })).toHaveAttribute("href", "/live");
});

function renderModels(entry: string) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/models") return Response.json([]);
    if (path.startsWith("/api/training/datasets")) return Response.json({ items: [], total: 0, page: 1, page_size: 20 });
    if (path === "/api/training/catalog") return Response.json({ models: [], datasets: [], executors: [{ id: "local", enabled: true }], config_schema_version: 1, scheduler_state: "healthy" });
    if (path.startsWith("/api/training-jobs")) return Response.json({ items: [], total: 0, page: 1, page_size: 20 });
    throw new Error(`Unexpected request: ${path}`);
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><ModelsPage /><LocationControls /></MemoryRouter></QueryClientProvider>);
}

it("opens a dataset deep link and preserves view history and unrelated query parameters", async () => {
  renderModels("/admin/models?view=datasets&source=home");
  expect(await screen.findByRole("tab", { name: "训练集" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByRole("button", { name: "定义训练集" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "训练任务" }));
  expect(await screen.findByText("暂无训练任务")).toBeInTheDocument();
  expect(screen.getByLabelText("当前地址")).toHaveTextContent("/admin/models?view=training&source=home");
  await userEvent.click(screen.getByRole("button", { name: "返回上一步" }));
  expect(await screen.findByRole("tab", { name: "训练集" })).toHaveAttribute("aria-selected", "true");
});

it("falls back to model versions for an unknown view", async () => {
  renderModels("/admin/models?view=unknown");
  expect(await screen.findByRole("tab", { name: "模型版本" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("尚未登记模型")).toBeInTheDocument();
});


it("guides an unconfigured training workspace directly to dataset preparation", async () => {
  renderModels("/admin/models?view=training");
  await userEvent.click(await screen.findByRole("button", { name: "前往训练集" }));
  expect(await screen.findByRole("tab", { name: "训练集" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByRole("button", { name: "定义训练集" })).toBeInTheDocument();
});
