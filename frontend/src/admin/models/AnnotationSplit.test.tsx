import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { TrainingDataset } from "../../api/types";
import { AnnotationWorkspace } from "./AnnotationWorkspace";

const dataset: TrainingDataset = { id: 9, key: "dataset:9", name: "施工监控训练素材", root_alias: "default", directory: "helmet-manual-pool-v1", train_split: "images/train", val_split: "images/val", test_split: "images/test", classes: ["person", "helmet", "no_helmet"], enabled: true, available: false, reason: null, version: 1, status: "editing", parent_id: null, root_dataset_id: null, revision: 0, published_at: null, manifest_digest: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function renderTrainOnlyDataset() {
  const records = Array.from({ length: 3069 }, (_, index) => ({ id: index + 1, dataset_id: 9, split: "train", image_key: `CMOT-${index + 1}.png`, display_name: `CMOT-${index + 1}.png`, width: 1920, height: 1080, review_status: "pending_annotation", validation_code: null, revision: 0 }));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test.local");
    if (url.pathname === "/api/training/datasets/9/images") {
      const page = Number(url.searchParams.get("page") ?? 1);
      const size = Number(url.searchParams.get("page_size") ?? 50);
      const status = url.searchParams.get("status");
      const filtered = url.searchParams.get("split") === "train" ? records.filter(image => !status || image.review_status === status) : [];
      return Response.json({ items: filtered.slice((page - 1) * size, page * size), total: filtered.length, page, page_size: size });
    }
    const match = url.pathname.match(/^\/api\/training\/datasets\/9\/images\/(\d+)$/);
    if (match) return Response.json({ ...records[Number(match[1]) - 1], boxes: [] });
    throw new Error(`Unexpected request ${url.pathname}`);
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AnnotationWorkspace dataset={dataset} onClose={() => undefined} /></QueryClientProvider>);
}

it("opens a train-only 3069-image dataset with its images visible immediately", async () => {
  renderTrainOnlyDataset();
  expect(await screen.findByRole("button", { name: /^CMOT-1\.png/ })).toBeVisible();
  expect(screen.getByLabelText("数据分区")).toHaveValue("train");
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("训练集 · 共 3069 张");
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("第 1 / 62 页");
});

it("explains an intentionally selected empty split without claiming files are missing", async () => {
  renderTrainOnlyDataset();
  await userEvent.selectOptions(screen.getByLabelText("数据分区"), "test");
  expect(await screen.findByText(/测试集暂无图片/)).toBeVisible();
  expect(screen.queryByText(/放入服务器目录|重新扫描/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("数据分区")).toHaveValue("test");
  await userEvent.selectOptions(screen.getByLabelText("数据分区"), "train");
  expect(await screen.findByRole("button", { name: /^CMOT-1\.png/ })).toBeVisible();
});

it("distinguishes no matching annotation status from an empty image collection", async () => {
  renderTrainOnlyDataset();
  await userEvent.selectOptions(screen.getByLabelText("数据分区"), "train");
  await userEvent.selectOptions(screen.getByLabelText("状态"), "annotated");
  expect(await screen.findByText(/训练集中没有符合当前状态的图片/)).toBeVisible();
  expect(screen.queryByText(/放入服务器目录|重新扫描/)).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("状态"), "");
  expect(await screen.findByRole("button", { name: /^CMOT-1\.png/ })).toBeVisible();
});