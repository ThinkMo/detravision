import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelsPage } from "./ModelsPage";
import { trainingPollingInterval } from "./TrainingJobsPanel";
import { trainingErrorMessage } from "./trainingApi";

const json = (value: unknown, status = 200) => Response.json(value, { status });

const catalog = {
  models: ["11", "26"].flatMap((series) => ["n", "s", "m", "l", "x"].map((size) => ({ id: `yolo${series}${size}.pt`, series, size, available: series === "11" && ["n", "m"].includes(size) }))),
  executors: [{ id: "local", enabled: true }, { id: "remote", enabled: false }],
  dataset: { id: "configured", available: true, has_test_split: true, classes: ["helmet", "no_helmet"] },
  datasets: [{ id: 3, key: "dataset:3", name: "工地安全帽", root_alias: "primary", directory: "site-a", train_split: "images/train", val_split: "images/val", test_split: "images/test", classes: ["person", "helmet", "no_helmet"], enabled: true, available: true, reason: null }],
  default_dataset_key: "dataset:3",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><ModelsPage /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("model training console", () => {
  it("maps stable training failures to Chinese guidance", () => {
    expect(trainingErrorMessage("weights_unavailable")).toContain("基础模型");
    expect(trainingErrorMessage("dataset_invalid")).toContain("训练集");
    expect(trainingErrorMessage("cuda_oom")).toContain("显存");
    expect(trainingErrorMessage("unknown_code")).toContain("训练失败");
  });
  it("uses the catalog for all ten models, disables remote, and defaults to YOLO11m at 640", async () => {
    let createBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs" && init?.method === "POST") {
        createBody = JSON.parse(String(init.body));
        return json({ id: 1, ...createBody, status: "queued", current_epoch: 0, total_epochs: 100, created_at: "2026-08-22T00:00:00Z", updated_at: "2026-08-22T00:00:00Z" }, 201);
      }
      if (path.startsWith("/api/training-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));

    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "新建训练" }));

    const dialog = screen.getByRole("dialog", { name: "新建本地训练" });
    for (const model of catalog.models) expect(within(dialog).getByRole("option", { name: new RegExp(model.id.replace(".", "\\.")) })).toBeInTheDocument();
    expect(within(dialog).getByRole("option", { name: /远程/ })).toBeDisabled();
    expect(within(dialog).getByLabelText("基础模型")).toHaveValue("yolo11m.pt");
    expect(within(dialog).getByLabelText("输入尺寸")).toHaveValue("640");

    await userEvent.click(within(dialog).getByRole("button", { name: "开始训练" }));
    await waitFor(() => expect(createBody).toMatchObject({ executor: "local", base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "auto", patience: 50 }));
  });

  it("requires confirmation before running a medium or larger model on CPU", async () => {
    let posted = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs" && init?.method === "POST") { posted = true; return json({ id: 2 }, 201); }
      if (path.startsWith("/api/training-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));

    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "新建训练" }));
    const dialog = screen.getByRole("dialog", { name: "新建本地训练" });
    await userEvent.selectOptions(within(dialog).getByLabelText("基础模型"), "yolo11x.pt");
    await userEvent.selectOptions(within(dialog).getByLabelText("设备"), "cpu");
    await userEvent.click(within(dialog).getByRole("button", { name: "开始训练" }));

    expect(posted).toBe(false);
    const confirmation = screen.getByRole("dialog", { name: "确认使用 CPU 训练" });
    expect(confirmation).toHaveTextContent("可能需要很长时间");
    await userEvent.click(within(confirmation).getByRole("button", { name: "仍然开始" }));
    await waitFor(() => expect(posted).toBe(true));
  });

  it("shows held-out metrics, acceptance, progress, and action failures in an alert", async () => {
    const running = { id: 7, executor: "local", base_model: "yolo26l.pt", imgsz: 960, epochs: 100, batch: "auto", device: "0", patience: 50, status: "running", current_epoch: 32, total_epochs: 100, has_best: false, has_last: true, accepted: null, metrics: null, error_code: null, error_message: null, created_at: "2026-08-22T00:00:00Z", updated_at: "2026-08-22T00:05:00Z" };
    const completed = { ...running, id: 8, status: "completed", current_epoch: 100, has_best: true, accepted: true, map50: 0.91, map50_95: 0.62, precision: 0.9, recall: 0.92, no_helmet_precision: 0.88, no_helmet_recall: 0.93, metrics: { classes: { no_helmet: { precision: 0.88, recall: 0.93, map50: 0.9, map50_95: 0.61 } } } };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs/7/stop" && init?.method === "POST") return json({ message: "无法停止训练" }, 409);
      if (path === "/api/training-jobs/7") return json({ ...running, log_url: "/api/training-jobs/7/artifacts/log" });
      if (path === "/api/training-jobs/7/artifacts/log") return new Response("epoch 32", { headers: { "Content-Type": "text/plain" } });
      if (path.startsWith("/api/training-jobs")) return json({ items: [running, completed], total: 2, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));

    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    expect(await screen.findByText("已达标")).toBeInTheDocument();
    expect(screen.getByText("91.0%")).toBeInTheDocument();
    expect(screen.getByText("未佩戴安全帽召回率").parentElement).toHaveTextContent("93.0%");
    expect(screen.getByRole("progressbar", { name: "任务 7 训练进度" })).toHaveAttribute("aria-valuenow", "32");

    await userEvent.click(screen.getAllByRole("button", { name: "查看详情" })[0]);
    expect(await screen.findByText("epoch 32")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog", { name: "训练任务 #7" })).getByRole("button", { name: "关闭" }));

    await userEvent.click(screen.getByRole("button", { name: "停止任务 7" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法停止训练");
  });

  it("polls active job responses and stops polling terminal responses", () => {
    expect(trainingPollingInterval({ state: { data: { items: [{ status: "running" }] } } })).toBe(2000);
    expect(trainingPollingInterval({ state: { data: { items: [{ status: "completed" }, { status: "failed" }] } } })).toBe(false);
  });

  it("places datasets beside training jobs and keeps its action below the tabs", async () => {
    let created: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs") return json({ items: [], total: 0, page: 1, page_size: 20 });
      if (path === "/api/training/datasets" && init?.method === "POST") { created = JSON.parse(String(init.body)); return json(catalog.datasets[0], 201); }
      if (path === "/api/training/datasets") return json({ items: catalog.datasets });
      if (path === "/api/training/dataset-roots") return json({ items: [{ alias: "primary" }] });
      if (path.includes("/api/training/dataset-directories")) return json({ items: [{ name: "site-a", path: "site-a" }] });
      throw new Error(`Unexpected request ${path}`);
    }));

    renderPage();
    expect((await screen.findAllByRole("tab")).map((tab) => tab.textContent)).toEqual(["模型版本", "训练集", "训练任务"]);
    expect(screen.queryByRole("button", { name: "管理训练集" })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("tab", { name: "训练集" }));
    await userEvent.click(await screen.findByRole("button", { name: "定义训练集" }));
    const dialog = await screen.findByRole("dialog", { name: "定义服务器训练集" });
    await userEvent.type(within(dialog).getByLabelText("训练集名称"), "现场 A");
    await userEvent.selectOptions(within(dialog).getByLabelText("服务器目录"), "site-a");
    await userEvent.click(within(dialog).getByRole("button", { name: "进入目录" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "选择当前目录" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "保存训练集" }));
    await waitFor(() => expect(created).toMatchObject({ root_alias: "primary", directory: "site-a" }));
    expect(JSON.stringify(created)).not.toContain("D:\\");
  });

  it("announces resume and registration failures from the training section", async () => {
    const base = { executor: "local", base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "0", patience: 50, current_epoch: 10, total_epochs: 100, accepted: false, metrics: null, error_code: null, error_message: null, created_at: "2026-08-22T00:00:00Z", updated_at: "2026-08-22T00:05:00Z" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs/9/resume" && init?.method === "POST") return json({ message: "恢复点已损坏" }, 409);
      if (path === "/api/training-jobs/10/register-model" && init?.method === "POST") return json({ message: "模型文件校验失败" }, 409);
      if (path.startsWith("/api/training-jobs")) return json({ items: [{ ...base, id: 9, status: "failed", has_last: true, has_best: false }, { ...base, id: 10, status: "completed", has_last: true, has_best: true }], total: 2, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));

    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "恢复任务 9" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("恢复点已损坏");
    await userEvent.click(screen.getByRole("button", { name: "注册任务 10 模型" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("模型文件校验失败");
  });

  it("confirms deletion of a terminal training task", async () => {
    let deleted = false;
    const terminal = { id: 21, executor: "local", base_model: "yolo11n.pt", imgsz: 640, epochs: 1, batch: "1", device: "cpu", patience: 0, status: "failed", current_epoch: 0, total_epochs: 1, has_last: false, has_best: false, accepted: false, metrics: null, error_code: "training_failed", error_message: null, created_at: "2026-08-22T00:00:00Z", updated_at: "2026-08-22T00:00:00Z" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs/21" && init?.method === "DELETE") { deleted = true; return new Response(null, { status: 204 }); }
      if (path.startsWith("/api/training-jobs")) return json({ items: deleted ? [] : [terminal], total: deleted ? 0 : 1, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));

    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "删除训练任务 21" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "删除训练任务" })).getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleted).toBe(true));
  });
});
