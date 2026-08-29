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
  config_schema_version: 1,
  profile_version: 1,
  training_profiles: [
    { id: "balanced", label: "均衡", description: "通用默认基线", recommended: { imgsz: 640, epochs: 100, patience: 50 }, overrides: {} },
    { id: "small_object", label: "小目标增强", description: "远处和小尺寸目标", recommended: { imgsz: 960, epochs: 100, patience: 50 }, overrides: {} },
    { id: "high_recall", label: "高召回", description: "优先降低漏检", recommended: { imgsz: 640, epochs: 150, patience: 75 }, overrides: { optimizer: "AdamW", lr0: 0.001 } },
    { id: "stable_finetune", label: "稳定微调", description: "低学习率微调", recommended: { imgsz: 640, epochs: 50, patience: 20 }, overrides: { optimizer: "AdamW", lr0: 0.001 } },
    { id: "custom", label: "自定义", description: "从均衡配置开始", recommended: { imgsz: 640, epochs: 100, patience: 50 }, overrides: {} },
  ],
  advanced_config_schema: {
    groups: [{ id: "optimization", label: "优化器与学习率", fields: ["optimizer", "lr0"] }, { id: "augmentation", label: "数据增强", fields: ["close_mosaic"] }, { id: "runtime", label: "运行与复现", fields: ["cache"] }],
    fields: {
      optimizer: { label: "优化器", type: "string", choices: ["auto", "SGD", "Adam", "AdamW"], default: "auto", help: "参数更新算法" },
      lr0: { label: "初始学习率", type: "number", min: 0.000001, max: 0.1, default: 0.01, help: "训练开始时的学习率" },
      close_mosaic: { label: "关闭 Mosaic 轮次", type: "integer", min: 0, max: 1000, default: 10, help: "最后若干轮关闭 Mosaic" },
      cache: { label: "图片缓存", type: "string", choices: ["false", "ram", "disk"], default: "false", help: "RAM 缓存需要较多内存" },
    },
  },
  acceptance_schema: {
    metrics: ["precision", "recall", "map50", "map50_95"],
    templates: [
      { id: "generic", label: "通用验收", required_classes: [], config: { schema_version: 1, overall: { precision: null, recall: null, map50: 0.5, map50_95: null }, classes: {} } },
      { id: "safety_helmet", label: "安全帽验收", required_classes: ["person", "helmet", "no_helmet"], config: { schema_version: 1, overall: { precision: null, recall: null, map50: 0.85, map50_95: null }, classes: { no_helmet: { precision: 0.85, recall: 0.9, map50: null, map50_95: null } } } },
    ],
  },
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
    await waitFor(() => expect(createBody).toMatchObject({ executor: "local", base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "auto", patience: 50, mode: "basic", profile: "balanced", advanced_config: {}, acceptance_config: { schema_version: 1, overall: { map50: 0.5 } } }));
  });

  it("builds an advanced request from the selected catalog preset and user overrides", async () => {
    let createBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs" && init?.method === "POST") { createBody = JSON.parse(String(init.body)); return json({ id: 30 }, 201); }
      if (path.startsWith("/api/training-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));

    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "新建训练" }));
    const dialog = screen.getByRole("dialog", { name: "新建本地训练" });
    await userEvent.click(within(dialog).getByRole("tab", { name: "高级模式" }));
    expect(within(dialog).getAllByLabelText("训练预设")[0]).toHaveValue("balanced");
    for (const label of ["均衡", "小目标增强", "高召回", "稳定微调", "自定义"]) expect(within(dialog).getByRole("option", { name: new RegExp(`^${label} —`) })).toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getAllByLabelText("训练预设")[0], "high_recall");
    await userEvent.clear(within(dialog).getByLabelText("初始学习率"));
    await userEvent.type(within(dialog).getByLabelText("初始学习率"), "0.002");
    await userEvent.click(within(dialog).getByText("最终配置摘要"));
    const summary = within(dialog).getByText("最终配置摘要").parentElement!;
    expect(within(summary).getByText("lr0")).toBeInTheDocument();
    expect(within(summary).getByText("0.002")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "开始训练" }));

    await waitFor(() => expect(createBody).toMatchObject({ mode: "advanced", profile: "high_recall", imgsz: 640, epochs: 150, patience: 75, advanced_config: { lr0: 0.002 }, acceptance_config: { overall: { map50: 0.5 } } }));
  });

  it("shows catalog range and combination errors and explains why submission is disabled", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path.startsWith("/api/training-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "新建训练" }));
    const dialog = screen.getByRole("dialog", { name: "新建本地训练" });
    await userEvent.click(within(dialog).getByRole("tab", { name: "高级模式" }));
    await userEvent.clear(within(dialog).getByLabelText("初始学习率"));
    await userEvent.type(within(dialog).getByLabelText("初始学习率"), "0.2");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("初始学习率必须在 0.000001 到 0.1 之间");
    expect(within(dialog).getByRole("button", { name: "开始训练" })).toBeDisabled();
    expect(within(dialog).getByText(/修正高级参数后才能开始训练/)).toBeInTheDocument();
  });

  it("edits acceptance thresholds for arbitrary dataset classes", async () => {
    const genericCatalog = { ...catalog, datasets: [{ ...catalog.datasets[0], classes: ["fire", "smoke"] }] };
    let createBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(genericCatalog);
      if (path === "/api/training-jobs" && init?.method === "POST") { createBody = JSON.parse(String(init.body)); return json({ id: 31 }, 201); }
      if (path.startsWith("/api/training-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "新建训练" }));
    const dialog = screen.getByRole("dialog", { name: "新建本地训练" });
    await userEvent.click(within(dialog).getByRole("tab", { name: "高级模式" }));
    expect(within(dialog).getByRole("option", { name: /安全帽验收/ })).toBeDisabled();
    await userEvent.click(within(dialog).getByLabelText("启用 fire 类别验收"));
    await userEvent.type(within(dialog).getByLabelText("fire 召回率"), "0.8");
    await userEvent.click(within(dialog).getByRole("button", { name: "开始训练" }));
    await waitFor(() => expect(createBody).toMatchObject({ acceptance_config: { classes: { fire: { recall: 0.8 } } } }));
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
    expect(screen.getByText("no_helmet").parentElement).toHaveTextContent("93.0%");
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
    const save = within(dialog).getByRole("button", { name: "保存训练集" });
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("请先选择训练集目录");
    await userEvent.click(within(dialog).getByRole("button", { name: "浏览服务器目录" }));
    const browser = await screen.findByRole("dialog", { name: "选择服务器训练目录" });
    await userEvent.dblClick(within(browser).getByRole("button", { name: "打开文件夹 site-a" }));
    expect(await within(browser).findByText("primary / site-a")).toBeInTheDocument();
    await userEvent.click(within(browser).getByRole("button", { name: "选择此文件夹" }));
    expect(within(dialog).getByDisplayValue("site-a")).toBeInTheDocument();
    await userEvent.click(save);
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

  it("shows dynamic class metrics, acceptance reasons, configuration, and evaluation artifacts", async () => {
    const job = { id: 41, executor: "local", dataset_key: "dataset:3", dataset_manifest_digest: "digest-a", dataset_classes: ["fire", "smoke"], base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "0", patience: 50, training_profile: "high_recall", config_schema_version: 1, status: "completed", current_epoch: 100, total_epochs: 100, has_last: true, has_best: true, accepted: false, map50: .72, map50_95: .41, metrics: { precision: .7, recall: .68, map50: .72, map50_95: .41, classes: { fire: { precision: .81, recall: .79, map50: .83, map50_95: .5 }, smoke: { precision: .6, recall: .55, map50: .61, map50_95: .32 } } }, error_code: null, error_message: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T01:00:00Z" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs/41") return json({ ...job, advanced_config: { optimizer: "AdamW", lr0: .002 }, acceptance_config: { schema_version: 1, overall: { map50: .7 }, classes: { fire: { recall: .85 } } }, acceptance_checks: [{ scope: "class", class_name: "fire", metric: "recall", threshold: .85, actual: null, passed: false, reason: "metric_missing" }], confusion_matrix_url: "/api/training-jobs/41/artifacts/confusion_matrix", results_curve_url: "/api/training-jobs/41/artifacts/results_curve", recent_log: "done" });
      if (path.startsWith("/api/training-jobs")) return json({ items: [job], total: 1, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const detail = await screen.findByRole("dialog", { name: "训练任务 #41" });
    expect(within(detail).getByRole("columnheader", { name: "类别" })).toBeInTheDocument();
    expect(within(detail).getByText("fire")).toBeInTheDocument();
    expect(within(detail).getByText("基础模型").parentElement).toHaveTextContent("yolo11m.pt");
    expect(within(detail).getByText("lr0").parentElement).toHaveTextContent("0.002");
    expect(within(detail).getByText("总体 map50").parentElement).toHaveTextContent("70.0%");
    expect(within(detail).getByText(/指标缺失/)).toBeInTheDocument();
    expect(within(detail).getByRole("img", { name: "测试集混淆矩阵" })).toBeInTheDocument();
    expect(within(detail).getByRole("img", { name: "训练结果曲线" })).toBeInTheDocument();
  });

  it("keeps jobs from a future configuration schema read-only", async () => {
    const job = { id: 43, executor: "local", dataset_key: "dataset:3", dataset_manifest_digest: "digest-a", base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "0", patience: 50, training_profile: "balanced", config_schema_version: 2, status: "failed", current_epoch: 30, total_epochs: 100, has_last: true, has_best: false, accepted: false, metrics: null, error_code: "training_failed", error_message: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T01:00:00Z" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path.startsWith("/api/training-jobs")) return json({ items: [job], total: 1, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    expect(await screen.findByRole("button", { name: "复制任务 43" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("配置版本 v2 高于当前支持的 v1，仅可查看");
    expect(screen.getByRole("button", { name: "恢复任务 43" })).toBeDisabled();
  });

  it("opens an advanced dialog prefilled from a safe clone template", async () => {
    const job = { id: 42, executor: "local", dataset_key: "dataset:3", dataset_manifest_digest: "digest-a", base_model: "yolo11m.pt", imgsz: 640, epochs: 150, batch: "auto", device: "0", patience: 75, training_profile: "high_recall", status: "completed", current_epoch: 150, total_epochs: 150, has_last: true, has_best: true, accepted: true, metrics: null, error_code: null, error_message: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T01:00:00Z" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs/42/clone-template") return json({ dataset_key: "dataset:3", executor: "local", base_model: "yolo11m.pt", imgsz: 640, epochs: 150, batch: "auto", device: "0", patience: 75, mode: "advanced", profile: "high_recall", advanced_config: { optimizer: "AdamW", lr0: .002, close_mosaic: 10, cache: "false" }, acceptance_config: { schema_version: 1, overall: { map50: .6 }, classes: {} } });
      if (path.startsWith("/api/training-jobs")) return json({ items: [job], total: 1, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByRole("button", { name: "复制任务 42" }));
    const dialog = await screen.findByRole("dialog", { name: "新建本地训练" });
    expect(within(dialog).getByRole("tab", { name: "高级模式" })).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).getByLabelText("训练预设")).toHaveValue("high_recall");
    expect(within(dialog).getByLabelText("初始学习率")).toHaveValue(.002);
    expect(within(dialog).getByLabelText("总体 mAP50")).toHaveValue(.6);
  });

  it("compares two to four jobs from the same dataset snapshot", async () => {
    const base = { executor: "local", dataset_key: "dataset:3", dataset_manifest_digest: "digest-a", dataset_classes: ["fire"], base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "0", patience: 50, training_profile: "balanced", status: "completed", current_epoch: 100, total_epochs: 100, has_last: true, has_best: true, accepted: true, error_code: null, error_message: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T01:00:00Z" };
    const jobs = [{ ...base, id: 51, map50: .71, metrics: { classes: { fire: { precision: .7, recall: .75, map50: .71, map50_95: .4 } } } }, { ...base, id: 52, training_profile: "high_recall", map50: .76, metrics: { classes: { fire: { precision: .72, recall: .82, map50: .76, map50_95: .45 } } } }];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path === "/api/training-jobs/51") return json({ ...jobs[0], advanced_config: { lr0: .01 }, acceptance_config: { schema_version: 1, overall: { map50: .5 }, classes: {} } });
      if (path === "/api/training-jobs/52") return json({ ...jobs[1], advanced_config: { lr0: .001 }, acceptance_config: { schema_version: 1, overall: { map50: .5 }, classes: { fire: { recall: .8 } } } });
      if (path.startsWith("/api/training-jobs")) return json({ items: jobs, total: 2, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByLabelText("选择任务 51"));
    await userEvent.click(await screen.findByLabelText("选择任务 52"));
    await userEvent.click(screen.getByRole("button", { name: "对比所选任务" }));
    const comparison = await screen.findByRole("dialog", { name: "训练实验对比" });
    expect(within(comparison).getByRole("columnheader", { name: "任务 #51" })).toBeInTheDocument();
    expect(within(comparison).getByRole("columnheader", { name: "任务 #52" })).toBeInTheDocument();
    expect(within(comparison).getByText("fire 召回率")).toBeInTheDocument();
    expect(within(comparison).getByText("82.0%")).toBeInTheDocument();
    expect(within(comparison).getByText("任务状态")).toBeInTheDocument();
    expect(within(comparison).getByText("验收 总体 map50")).toBeInTheDocument();
  });

  it("rejects comparison across different dataset snapshots with an accessible error", async () => {
    const base = { executor: "local", dataset_key: "dataset:3", base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "0", patience: 50, status: "completed", current_epoch: 100, total_epochs: 100, has_last: true, has_best: true, accepted: true, metrics: null, error_code: null, error_message: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T01:00:00Z" };
    const jobs = [{ ...base, id: 61, dataset_manifest_digest: "digest-a" }, { ...base, id: 62, dataset_manifest_digest: "digest-b" }];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/models") return json([]);
      if (path === "/api/training/catalog") return json(catalog);
      if (path.startsWith("/api/training-jobs")) return json({ items: jobs, total: 2, page: 1, page_size: 20 });
      throw new Error(`Unexpected request ${path}`);
    }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "训练任务" }));
    await userEvent.click(await screen.findByLabelText("选择任务 61"));
    await userEvent.click(await screen.findByLabelText("选择任务 62"));
    await userEvent.click(screen.getByRole("button", { name: "对比所选任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("训练集版本不同");
    expect(screen.queryByRole("dialog", { name: "训练实验对比" })).not.toBeInTheDocument();
  });
});
