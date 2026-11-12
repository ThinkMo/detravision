import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { TrainingDataset } from "../../api/types";
import { AnnotationWorkspace } from "./AnnotationWorkspace";

const dataset: TrainingDataset = { id: 9, key: "dataset:9", name: "施工监控训练素材", root_alias: "default", directory: "manual", train_split: "images/train", val_split: "images/val", test_split: "images/test", classes: ["person", "helmet", "no_helmet"], enabled: true, available: false, reason: null, version: 1, status: "editing", parent_id: null, root_dataset_id: null, revision: 0, published_at: null, manifest_digest: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function setup({ failure = false, readonly = false, conflict = false } = {}) {
  let records = Array.from({ length: 51 }, (_, i) => ({ id: i + 1, dataset_id: 9, split: "train", image_key: `frame-${i + 1}.jpg`, display_name: `frame-${i + 1}.jpg`, width: 800, height: 600, review_status: "pending_annotation", validation_code: null, revision: 0 }));
  let revision = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://test.local");
    if (url.pathname === "/api/training/datasets/9/images/remove") {
      if (conflict) return Response.json({ code: "annotation_conflict", message: "The dataset annotation request failed." }, { status: 409 });
      if (failure) return Response.json({ code: "annotation_write_failed", message: "删除失败，请重试" }, { status: 503 });
      const body = JSON.parse(String(init?.body));
      if (body.revision !== revision) return Response.json({ code: "annotation_conflict" }, { status: 409 });
      const ids = body.images.map((r: { id: number }) => r.id);
      records = records.filter(r => !ids.includes(r.id));
      return Response.json({ removed: ids.length, image_ids: ids, dataset_revision: ++revision });
    }
    if (url.pathname === "/api/training/datasets/9/images") {
      const page = Number(url.searchParams.get("page") ?? 1);
      const filtered = url.searchParams.get("split") === "train" ? records : [];
      return Response.json({ items: filtered.slice((page - 1) * 50, page * 50), total: filtered.length, page, page_size: 50 });
    }
    const match = url.pathname.match(/\/images\/(\d+)$/);
    const image = match ? records.find(r => r.id === Number(match[1])) : undefined;
    if (image) return Response.json({ ...image, boxes: [] });
    if (url.pathname === "/api/training/datasets") return Response.json({ items: [{ ...dataset, revision }] });
    throw new Error(`Unexpected ${url.pathname}`);
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AnnotationWorkspace dataset={{ ...dataset, status: readonly ? "published" : "editing" }} onClose={() => undefined} /></QueryClientProvider>);
}
async function confirmRemoval() {
  const dialog = await screen.findByRole("dialog", { name: "从当前训练集删除图片" });
  await userEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
}

it("confirms deleting the current image, preserves cancellation, and supports consecutive deletions", async () => {
  setup();
  await screen.findByRole("button", { name: /^frame-1\.jpg/ });
  await userEvent.click(await screen.findByRole("button", { name: "删除当前图片" }));
  const dialog = await screen.findByRole("dialog", { name: "从当前训练集删除图片" });
  expect(dialog).toHaveTextContent("frame-1.jpg");
  await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("共 51 张");
  await userEvent.click(screen.getByRole("button", { name: "删除当前图片" }));
  await confirmRemoval();
  expect(await screen.findByRole("button", { name: /^frame-2\.jpg/ })).toHaveClass("is-active");
  expect(screen.queryByRole("button", { name: /^frame-1\.jpg/ })).not.toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("共 50 张");
  await userEvent.click(screen.getByRole("button", { name: "删除当前图片" }));
  await confirmRemoval();
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("共 49 张");
});

it("removes only checked images in a batch and refreshes the total", async () => {
  setup();
  await userEvent.click(await screen.findByRole("checkbox", { name: "选择图片 frame-1.jpg" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "选择图片 frame-2.jpg" }));
  await userEvent.click(screen.getByRole("button", { name: "删除所选（2）" }));
  expect(await screen.findByRole("dialog")).toHaveTextContent("2 张图片");
  await confirmRemoval();
  expect(screen.queryByRole("button", { name: /^frame-[12]\.jpg/ })).not.toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /^frame-3\.jpg/ })).toBeVisible();
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("共 49 张");
  expect(screen.getByRole("button", { name: "删除所选（0）" })).toBeDisabled();
});

it("returns to page one after deleting the sole image on the last page", async () => {
  setup();
  await screen.findByRole("button", { name: /^frame-1\.jpg/ });
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: /^frame-51\.jpg/ });
  await userEvent.click(await screen.findByRole("button", { name: "删除当前图片" }));
  await confirmRemoval();
  expect(await screen.findByRole("button", { name: /^frame-1\.jpg/ })).toHaveClass("is-active");
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("第 1 / 1 页");
});

it("clears page selections when navigating so bulk deletion cannot remove hidden previous-page items", async () => {
  setup();
  await userEvent.click(await screen.findByRole("checkbox", { name: "全选本页" }));
  expect(screen.getByRole("button", { name: "删除所选（50）" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: /^frame-51\.jpg/ });
  expect(screen.getByRole("button", { name: "删除所选（0）" })).toBeDisabled();
});

it("keeps the current image and dialog visible when deletion fails", async () => {
  setup({ failure: true });
  await userEvent.click(await screen.findByRole("button", { name: "删除当前图片" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect(await within(dialog).findByRole("alert")).toBeVisible();
  await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(await screen.findByRole("button", { name: /^frame-1\.jpg/ })).toBeVisible();
  expect(screen.getByRole("navigation", { name: "标注图片分页" })).toHaveTextContent("共 51 张");
});

it("does not offer image deletion or selection in a published version", async () => {
  setup({ readonly: true });
  await screen.findByRole("button", { name: /^frame-1\.jpg/ });
  expect(screen.queryByRole("button", { name: "删除当前图片" })).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: "全选本页" })).not.toBeInTheDocument();
});
it("explains a stale dataset revision in Chinese and keeps deletion uncommitted in the UI", async () => {
  setup({ conflict: true });
  await userEvent.click(await screen.findByRole("button", { name: "删除当前图片" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("图片或集合已更新，请刷新页面后重试");
  await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(await screen.findByRole("button", { name: /^frame-1\.jpg/ })).toBeVisible();
});