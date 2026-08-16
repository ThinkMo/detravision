import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { batchJobsPath } from "./batchApi";
import { BatchPage } from "./BatchPage";
import { UploadDropzone } from "./UploadDropzone";

const currentUser = {
  id: 7,
  username: "operator",
  role: "user" as const,
  must_change_password: false,
};

const processingJob = {
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
      detections: [{ label: "helmet", confidence: 0.93, bbox: [1, 2, 3, 4] }],
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
  const dropzone = (await screen.findByLabelText("选择识别图片")).closest('[role="button"]');
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

it("renders authenticated result and ZIP download links without exposing storage paths", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(currentUser);
    if (path === "/api/batch-jobs/12") return json(partialFailedJob);
    throw new Error(`Unexpected request ${path}`);
  }));

  renderBatch("/batch/12");
  const card = await screen.findByRole("article", { name: "north-gate.jpg 识别结果" });
  expect(card).toHaveTextContent("模型 #4");
  const detection = within(card).getByText("helmet").closest("li");
  expect(detection).toHaveTextContent("helmet");
  expect(detection).toHaveTextContent("93%");
  expect(within(card).getByRole("link", { name: "下载 north-gate.jpg 标注图" })).toHaveAttribute(
    "href",
    "/api/batch-jobs/12/images/31/download",
  );
  expect(screen.getByRole("link", { name: "下载全部结果 ZIP" })).toHaveAttribute("href", "/api/batch-jobs/12/download");
  expect(document.body).not.toHaveTextContent("batch-source");
});
