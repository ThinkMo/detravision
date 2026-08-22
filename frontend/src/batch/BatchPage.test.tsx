import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import type { BatchJobDetail } from "../api/types";
import { batchJobsPath, createBatchJob } from "./batchApi";
import { BatchPage } from "./BatchPage";
import { ResultCard } from "./ResultCard";
import { UploadDropzone } from "./UploadDropzone";

vi.mock("./batchApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./batchApi")>();
  return { ...actual, createBatchJob: vi.fn() };
});

const currentUser = {
  id: 7,
  username: "operator",
  role: "user" as const,
  must_change_password: false,
};

const processingJob: BatchJobDetail = {
  id: 12,
  user_id: 7,
  status: "processing",
  image_total: 2,
  success_count: 1,
  failure_count: 0,
  expires_at: "2026-09-15T00:00:00Z",
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:01Z",
  images: [
    {
      id: 31,
      display_name: "north-gate.jpg",
      status: "completed",
      error_code: null,
      error_message: null,
      model_version_id: 4,
      preview_url: "/api/batch-jobs/12/images/31/preview",
      detections: [
        { label: "no_helmet", confidence: 0.91, bbox: [1, 2, 30, 40] },
        { label: "helmet_worn", confidence: 0.76, bbox: [50, 5, 70, 90] },
      ],
      result_url: "/api/batch-jobs/12/images/31/download",
      created_at: "2026-08-16T00:00:00Z",
      updated_at: "2026-08-16T00:00:01Z",
    },
    {
      id: 32,
      display_name: "broken.png",
      status: "processing",
      error_code: null,
      error_message: null,
      model_version_id: null,
      preview_url: "/api/batch-jobs/12/images/32/preview",
      detections: [],
      result_url: "/api/batch-jobs/12/images/32/download",
      created_at: "2026-08-16T00:00:00Z",
      updated_at: "2026-08-16T00:00:01Z",
    },
  ],
};

const partialFailedJob = {
  ...processingJob,
  status: "partial_failed",
  failure_count: 1,
  updated_at: "2026-08-16T00:00:02Z",
  images: [
    processingJob.images[0],
    {
      ...processingJob.images[1],
      status: "failed",
      error_code: "invalid_image",
      error_message: "图片无法解码",
    },
  ],
};

function json(value: unknown, status = 200) {
  return Response.json(value, { status });
}

function renderBatch(path = "/batch") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content: ReactNode = (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/batch" element={<BatchPage />} />
            <Route path="/batch/:jobId" element={<BatchPage />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
  return render(content);
}

function makeImages(count: number) {
  return Array.from({ length: count }, (_, index) =>
    new File([new Uint8Array([index])], `image-${index}.png`, {
      type: "image/png",
      lastModified: index,
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.mocked(createBatchJob).mockReset();
});

it("exposes determinate upload progress to assistive technology", async () => {
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:progress"),
    revokeObjectURL: vi.fn(),
  });
  const onSubmit = vi.fn();
  const view = render(<UploadDropzone pending={false} progress={0} onSubmit={onSubmit} />);
  await userEvent.upload(
    screen.getByLabelText("选择识别图片"),
    new File(["image"], "progress.png", { type: "image/png" }),
  );
  view.rerender(<UploadDropzone pending progress={37} onSubmit={onSubmit} />);

  expect(screen.getByRole("progressbar", { name: "上传进度" })).toHaveAttribute("aria-valuenow", "37");
});

it("opens the picker only from its explicit button and appends later selections", async () => {
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn((file: Blob) => `blob:${file.size}-${Math.random()}`),
    revokeObjectURL: vi.fn(),
  });
  const pickerClick = vi.spyOn(HTMLInputElement.prototype, "click");
  const onSubmit = vi.fn();
  render(<UploadDropzone pending={false} progress={0} onSubmit={onSubmit} />);
  const first = new File(["first"], "first.png", { type: "image/png" });
  const second = new File(["second"], "second.png", { type: "image/png" });
  const third = new File(["third"], "third.png", { type: "image/png" });

  await userEvent.click(screen.getByRole("button", { name: "选择图片" }));
  expect(pickerClick).toHaveBeenCalledTimes(1);
  const input = screen.getByLabelText("选择识别图片");
  expect(input).toHaveAttribute("multiple");

  await userEvent.upload(input, [first, second]);
  await userEvent.upload(input, [third]);
  expect(screen.getByText("3 / 20")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "开始识别" }));
  expect(onSubmit).toHaveBeenCalledWith([first, second, third]);
  expect(pickerClick).toHaveBeenCalledTimes(1);
});

it("keeps selected images and retry action after batch creation fails", async () => {
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn((file: Blob) => `blob:${file.size}-${Math.random()}`),
    revokeObjectURL: vi.fn(),
  });
  vi.mocked(createBatchJob).mockRejectedValueOnce(new Error("上传失败，请重试"));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(currentUser);
    if (path.startsWith("/api/batch-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
    throw new Error(`Unexpected request ${path}`);
  }));
  renderBatch();
  const first = new File(["first"], "first.png", { type: "image/png" });
  const second = new File(["second"], "second.png", { type: "image/png" });

  await userEvent.upload(await screen.findByLabelText("选择识别图片"), [first, second]);
  await userEvent.click(screen.getByRole("button", { name: "开始识别" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("上传失败，请重试");
  expect(screen.getByText("first.png")).toBeInTheDocument();
  expect(screen.getByText("second.png")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始识别" })).toBeEnabled();
});

it("treats the end-date filter as the inclusive whole UTC day", () => {
  const path = batchJobsPath({ createdFrom: "2026-08-15", createdTo: "2026-08-16" });
  const query = new URL(path, "http://testserver").searchParams;

  expect(query.get("created_from")).toBe("2026-08-15T00:00:00.000Z");
  expect(query.get("created_to")).toBe("2026-08-16T23:59:59.999Z");
});

it("rejects a twenty-first file before upload", async () => {
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    requests.push(path);
    if (path === "/api/auth/me") return json(currentUser);
    if (path.startsWith("/api/batch-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
    throw new Error(`Unexpected request ${path}`);
  }));

  renderBatch();
  await userEvent.upload(await screen.findByLabelText("选择识别图片"), makeImages(21));

  expect(screen.getByRole("alert")).toHaveTextContent("单次最多上传 20 张图片");
  expect(requests.filter((path) => path === "/api/batch-jobs")).toHaveLength(0);
});

it("removes duplicate files and revokes every preview URL", async () => {
  const createObjectURL = vi.fn((file: Blob) => `blob:${file.size}-${Math.random()}`);
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(currentUser);
    if (path.startsWith("/api/batch-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
    throw new Error(`Unexpected request ${path}`);
  }));
  const duplicateA = new File(["a"], "same.png", { type: "image/png", lastModified: 1 });
  const duplicateB = new File(["b"], "same.png", { type: "image/png", lastModified: 1 });

  const view = renderBatch();
  await userEvent.upload(await screen.findByLabelText("选择识别图片"), [duplicateA, duplicateB]);

  expect(screen.getByRole("alert")).toHaveTextContent("已忽略 1 张重复图片");
  expect(screen.getAllByText("same.png")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "移除 same.png" }));
  expect(revokeObjectURL).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(revokeObjectURL).toHaveBeenCalledTimes(1);
});

it("does not allocate previews when any selected file fails local validation", async () => {
  const createObjectURL = vi.fn(() => "blob:unused");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(currentUser);
    if (path.startsWith("/api/batch-jobs")) return json({ items: [], total: 0, page: 1, page_size: 20 });
    throw new Error(`Unexpected request ${path}`);
  }));

  renderBatch();
  const files = [
    new File(["valid"], "valid.png", { type: "image/png" }),
    new File(["invalid"], "invalid.txt", { type: "text/plain" }),
  ];
  const dropzone = (await screen.findByLabelText("选择识别图片")).closest(".batch-dropzone");
  fireEvent.drop(dropzone!, { dataTransfer: { files } });

  expect(screen.getByRole("alert")).toHaveTextContent("仅支持扩展名与类型匹配");
  expect(screen.queryByText("valid.png")).not.toBeInTheDocument();
  expect(createObjectURL).not.toHaveBeenCalled();
});

it("polls until a partially failed task reaches a terminal state", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let detailCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(currentUser);
    if (path === "/api/batch-jobs/12") {
      detailCalls += 1;
      return json(detailCalls === 1 ? processingJob : partialFailedJob);
    }
    throw new Error(`Unexpected request ${path}`);
  }));

  renderBatch("/batch/12");
  expect(await screen.findByText("处理中 1 / 2")).toBeInTheDocument();
  await act(() => vi.advanceTimersByTimeAsync(1_050));
  expect(await screen.findByText("部分失败")).toBeInTheDocument();
  expect(screen.getByText("图片无法解码")).toBeInTheDocument();
  expect(detailCalls).toBe(2);
  await act(() => vi.advanceTimersByTimeAsync(2_100));
  expect(detailCalls).toBe(2);
});

it("opens an enlarged annotated preview and does not offer downloads", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(currentUser);
    if (path === "/api/batch-jobs/12") return json(partialFailedJob);
    throw new Error(`Unexpected request ${path}`);
  }));

  renderBatch("/batch/12");
  const card = await screen.findByRole("article", { name: "north-gate.jpg 识别结果" });
  expect(card).toHaveTextContent("模型 #4");
  expect(within(card).getByRole("img", { name: "north-gate.jpg 标注结果" })).toHaveAttribute(
    "src",
    "/api/batch-jobs/12/images/31/preview",
  );
  const violation = within(card).getByText("未佩戴安全帽：1 人");
  const contextualDetection = within(card).getByText("已佩戴安全帽");
  expect(violation.compareDocumentPosition(contextualDetection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(card).getByText("未佩戴安全帽").closest("li")).toHaveTextContent("91%");
  expect(within(card).getByText("已佩戴安全帽").closest("li")).toHaveTextContent("76%");
  const previewTrigger = within(card).getByRole("button", { name: "放大预览 north-gate.jpg 标注结果" });
  await userEvent.click(previewTrigger);
  const dialog = screen.getByRole("dialog", { name: "north-gate.jpg 标注结果预览" });
  expect(dialog).toHaveStyle({
    position: "fixed",
    inset: "0",
    width: "100vw",
    height: "100vh",
    maxWidth: "none",
    maxHeight: "none",
    borderRadius: "0",
  });
  expect(within(dialog).getByRole("img", { name: "north-gate.jpg 放大标注结果" })).toHaveAttribute(
    "src",
    "/api/batch-jobs/12/images/31/preview",
  );
  const viewport = within(dialog).getByRole("region", { name: "可缩放图片预览" });
  expect(within(dialog).getByRole("status")).toHaveTextContent("适应窗口");
  await userEvent.click(within(dialog).getByRole("button", { name: "放大图片" }));
  expect(within(dialog).getByRole("status")).toHaveTextContent("125%");
  fireEvent.wheel(viewport, { deltaY: -100 });
  expect(within(dialog).getByRole("status")).toHaveTextContent("150%");
  Object.defineProperties(viewport, {
    scrollLeft: { configurable: true, writable: true, value: 100 },
    scrollTop: { configurable: true, writable: true, value: 80 },
  });
  fireEvent.mouseDown(viewport, { clientX: 100, clientY: 100 });
  fireEvent.mouseMove(viewport, { clientX: 40, clientY: 30 });
  fireEvent.mouseUp(viewport);
  expect(viewport.scrollLeft).toBe(160);
  expect(viewport.scrollTop).toBe(150);
  await userEvent.click(within(dialog).getByRole("button", { name: "原始尺寸" }));
  expect(within(dialog).getByRole("status")).toHaveTextContent("100%");
  await userEvent.click(within(dialog).getByRole("button", { name: "适应窗口" }));
  expect(within(dialog).getByRole("status")).toHaveTextContent("适应窗口");
  expect(screen.queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
  fireEvent.mouseDown(dialog.parentElement!);
  expect(dialog).not.toBeInTheDocument();
  expect(previewTrigger).toHaveFocus();
  await userEvent.click(previewTrigger);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "north-gate.jpg 标注结果预览" })).not.toBeInTheDocument();
  expect(previewTrigger).toHaveFocus();
  expect(document.body).not.toHaveTextContent("batch-source");
});

it("confirms and deletes a terminal recognition task", async () => {
  let deleted = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(currentUser);
    if (path.startsWith("/api/batch-jobs?") && !deleted) return json({ items: [partialFailedJob], total: 1, page: 1, page_size: 20 });
    if (path.startsWith("/api/batch-jobs?") && deleted) return json({ items: [], total: 0, page: 1, page_size: 20 });
    if (path === "/api/batch-jobs/12" && init?.method === "DELETE") { deleted = true; return new Response(null, { status: 204 }); }
    throw new Error(`Unexpected request ${path}`);
  }));

  renderBatch();
  const actions = await screen.findByRole("group", { name: "任务 #12 状态与操作" });
  expect(within(actions).getByText("部分失败")).toBeInTheDocument();
  await userEvent.click(within(actions).getByRole("button", { name: "删除识别任务 #12" }));
  const dialog = screen.getByRole("dialog", { name: "删除识别任务" });
  await userEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  await screen.findByText("当前筛选条件下没有识别任务");
  expect(deleted).toBe(true);
});

it("does not render an image for a non-canonical preview URL", () => {
  render(
    <ResultCard
      jobId={12}
      image={{
        ...processingJob.images[0],
        preview_url: "https://example.test/api/batch-jobs/12/images/31/preview",
      }}
    />,
  );

  const card = screen.getByRole("article", { name: "north-gate.jpg 识别结果" });
  expect(within(card).queryByRole("img")).not.toBeInTheDocument();
});

it("retries a failed canonical preview without using the download URL", async () => {
  render(<ResultCard jobId={12} image={processingJob.images[0]} />);
  const preview = screen.getByRole("img", { name: "north-gate.jpg 标注结果" });

  fireEvent.error(preview);
  expect(screen.getByText("预览加载失败")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "重新加载预览" }));

  expect(screen.getByRole("img", { name: "north-gate.jpg 标注结果" })).toHaveAttribute(
    "src",
    "/api/batch-jobs/12/images/31/preview?retry=1",
  );
});
