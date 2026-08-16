import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BrandProvider } from "../brand/BrandProvider";
import { AlarmGroupsPage } from "./groups/AlarmGroupsPage";
import { CameraPage } from "./cameras/CameraPage";
import { ModelsPage } from "./models/ModelsPage";
import { UsersPage } from "./users/UsersPage";
import { SiteSettingsPage } from "./settings/SiteSettingsPage";
import { AuditPage } from "./audit/AuditPage";

const json = (value: unknown, status = 200) => Response.json(value, { status });

function renderPage(page: ReactNode, brand = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content = <QueryClientProvider client={client}>{page}</QueryClientProvider>;
  return render(brand ? <QueryClientProvider client={client}><BrandProvider>{page}</BrandProvider></QueryClientProvider> : content);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("masks stored RTSP credentials and requires a named delete confirmation", async () => {
  const requests: Array<{ path: string; body?: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    requests.push({ path, body: typeof init?.body === "string" ? init.body : undefined });
    if (path === "/api/cameras" && init?.method === "PATCH") return json([]);
    if (path === "/api/cameras/4" && init?.method === "DELETE") return new Response(null, { status: 204 });
    if (path === "/api/alarm-groups") return json([]);
    if (path === "/api/cameras") return json([{ id: 4, name: "北门", rtsp_url: "rtsp://admin:secret@example.invalid/live", enabled: true, sample_fps: 1, roi: [], detect_classes: ["no_helmet"], alarm_group_id: null, created_at: "2026-08-16T00:00:00Z" }]);
    throw new Error(`Unexpected request ${path}`);
  }));

  renderPage(<CameraPage />);
  expect(await screen.findByText("北门")).toBeInTheDocument();
  expect(screen.queryByText(/admin:secret/)).not.toBeInTheDocument();
  expect(screen.getByText("rtsp://••••••••")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "删除北门" }));
  const dialog = screen.getByRole("dialog", { name: "删除摄像头“北门”" });
  expect(within(dialog).getByText(/无法撤销/)).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(requests.some((item) => item.path === "/api/cameras/4")).toBe(true));
});

it("updates an alarm group with selected people", async () => {
  let patchBody = "";
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/persons") return json([{ id: 2, name: "王工", phone: null, feishu_id: null, dept: "安全部", created_at: "2026-08-16T00:00:00Z" }]);
    if (path === "/api/alarm-groups/8" && init?.method === "PATCH") {
      patchBody = String(init.body);
      return json({ id: 8, name: "夜班", created_at: "2026-08-16T00:00:00Z", members: [{ id: 2, name: "王工", phone: null, feishu_id: null, dept: "安全部", created_at: "2026-08-16T00:00:00Z" }] });
    }
    if (path === "/api/alarm-groups") return json([{ id: 8, name: "白班", created_at: "2026-08-16T00:00:00Z", members: [] }]);
    throw new Error(`Unexpected request ${path}`);
  }));

  renderPage(<AlarmGroupsPage />);
  await userEvent.click(await screen.findByRole("button", { name: "编辑白班" }));
  await userEvent.clear(screen.getByLabelText("告警组名称"));
  await userEvent.type(screen.getByLabelText("告警组名称"), "夜班");
  await userEvent.click(screen.getByRole("checkbox", { name: /王工/ }));
  await userEvent.click(screen.getByRole("button", { name: "保存告警组" }));

  await waitFor(() => expect(JSON.parse(patchBody)).toEqual({ name: "夜班", person_ids: [2] }));
});

it("shows model version and runtime before activation", async () => {
  let activated = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const model = { id: 6, name: "helmet-v3", file_path: "D:\\private\\helmet.onnx", runtime: "onnx-fp32", classes: ["helmet", "no_helmet"], map50: 0.91, is_active: false, created_at: "2026-08-16T00:00:00Z" };
    if (path === "/api/models/6/activate" && init?.method === "POST") { activated = true; return json({ ...model, is_active: true }); }
    if (path === "/api/models") return json([model]);
    throw new Error(`Unexpected request ${path}`);
  }));

  renderPage(<ModelsPage />);
  await userEvent.click(await screen.findByRole("button", { name: "激活helmet-v3" }));
  const dialog = screen.getByRole("dialog", { name: "激活模型“helmet-v3”" });
  expect(dialog).toHaveTextContent("onnx-fp32");
  expect(screen.queryByText(/D:\\private/)).not.toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "确认激活" }));
  await waitFor(() => expect(activated).toBe(true));
});

it("shows a reset password only after success and clears it on close", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/users/3/reset-password" && init?.method === "POST") return json({ temporary_password: "temp-Only-123" });
    if (path === "/api/users") return json([{ id: 3, username: "operator", role: "user", enabled: true, must_change_password: false, created_at: "2026-08-16T00:00:00Z", updated_at: "2026-08-16T00:00:00Z" }]);
    throw new Error(`Unexpected request ${path}`);
  }));

  renderPage(<UsersPage />);
  expect(screen.queryByText("temp-Only-123")).not.toBeInTheDocument();
  await userEvent.click(await screen.findByRole("button", { name: "重置operator的密码" }));
  await userEvent.click(within(screen.getByRole("dialog", { name: "重置用户“operator”的密码" })).getByRole("button", { name: "确认重置" }));
  expect(await screen.findByText("temp-Only-123")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "我已复制，关闭" }));
  expect(screen.queryByText("temp-Only-123")).not.toBeInTheDocument();
});

it("updates branding immediately and rejects an unsafe Logo locally", async () => {
  let siteName = "Safety Detect";
  let logoRequests = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/settings/site/logo") { logoRequests += 1; return json({ site_name: siteName, logo_url: "/api/settings/site/logo" }); }
    if (path === "/api/settings/site" && init?.method === "PATCH") { siteName = JSON.parse(String(init.body)).site_name; return json({ site_name: siteName, logo_url: null }); }
    if (path === "/api/settings/site") return json({ site_name: siteName, logo_url: null });
    throw new Error(`Unexpected request ${path}`);
  }));

  renderPage(<SiteSettingsPage />, true);
  await userEvent.clear(await screen.findByLabelText("网站名称"));
  await userEvent.type(screen.getByLabelText("网站名称"), "厂区安全中心");
  await userEvent.click(screen.getByRole("button", { name: "保存品牌设置" }));
  expect(await screen.findByText("厂区安全中心")).toBeInTheDocument();

  await userEvent.upload(screen.getByLabelText("上传 Logo"), new File([new Uint8Array(2 * 1024 * 1024 + 1)], "logo.png", { type: "image/png" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不能超过 2 MB");
  expect(logoRequests).toBe(0);
});

it("paginates audit records without rendering audit details", async () => {
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    paths.push(path);
    return json([{ id: 11, actor_id: 1, action: "update", resource_type: "camera", resource_id: "4", outcome: "success", details: "{\"rtsp_url\":\"[REDACTED]\"}", ip: "127.0.0.1", created_at: "2026-08-16T00:00:00Z" }]);
  }));

  renderPage(<AuditPage />);
  expect(await screen.findByText("camera #4")).toBeInTheDocument();
  expect(screen.queryByText(/rtsp_url/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(paths.some((path) => path.includes("offset=50"))).toBe(true));
});
