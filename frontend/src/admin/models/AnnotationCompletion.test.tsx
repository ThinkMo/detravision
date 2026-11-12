import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { TrainingDataset } from "../../api/types";
import { AnnotationWorkspace } from "./AnnotationWorkspace";

const dataset: TrainingDataset = { id: 2, key: "dataset:2", name: "Helmet review", root_alias: "default", directory: "set-v2", train_split: "images/train", val_split: "images/val", test_split: "images/test", classes: ["helmet", "no_helmet"], enabled: false, available: false, reason: "dataset_disabled", version: 2, status: "editing", parent_id: 1, root_dataset_id: 1, revision: 0, published_at: null, manifest_digest: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function setup(options: { otherPending?: boolean; trainPending?: number; valPending?: number; testCount?: number; alreadyReviewed?: boolean; saveFailure?: boolean; checkFailure?: boolean; publishFailure?: boolean; many?: boolean } = {}) {
  let reviewed = Boolean(options.alreadyReviewed);
  let failedCheck = Boolean(options.checkFailure);
  const image = (id: number) => ({ id, dataset_id: 2, split: "test", image_key: `frame-${id}.jpg`, display_name: `frame-${id}.jpg`, width: 800, height: 600, review_status: id !== 51 || reviewed ? "annotated" : "pending_annotation", validation_code: null, revision: reviewed ? 1 : 0 });
  const onClose = vi.fn();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://test.local");
    if (url.pathname === "/api/training/datasets") return Response.json({ items: [{ ...dataset, revision: 7 }] });
    if (url.pathname === "/api/training/datasets/2/images") {
      const split = url.searchParams.get("split");
      const status = url.searchParams.get("status");
      const page = Number(url.searchParams.get("page") ?? 1);
      const pageSize = Number(url.searchParams.get("page_size") ?? 50);
      if (status === "pending_annotation" && failedCheck) return Response.json({ message: "Offline" }, { status: 503 });
      const all = split === "test" ? options.many || options.testCount ? Array.from({ length: options.testCount ?? 51 }, (_, i) => image(i + 1)) : [image(51)] : Array.from({ length: split === "train" ? options.trainPending ?? (options.otherPending ? 1 : 0) : options.valPending ?? 0 }, (_, i) => ({ ...image(i + 100), split, review_status: "pending_annotation" }));
      const filtered = all.filter(item => !status || item.review_status === status);
      return Response.json({ items: filtered.slice((page - 1) * pageSize, page * pageSize), total: filtered.length, page, page_size: pageSize });
    }
    if (/\/images\/\d+\/(annotations|confirm-empty)$/.test(url.pathname)) {
      if (options.saveFailure) return Response.json({ message: "Save failed" }, { status: 503 });
      reviewed = true;
      return Response.json({ ...image(Number(url.pathname.split("/").at(-2))), review_status: url.pathname.endsWith("confirm-empty") ? "confirmed_empty" : "annotated" });
    }
    if (/\/images\/\d+$/.test(url.pathname)) return Response.json({ ...image(Number(url.pathname.split("/").at(-1))), boxes: [{ class_id: 0, x_center: .5, y_center: .3, width: .1, height: .2 }] });
    if (url.pathname.endsWith("/publish")) {
      if (options.publishFailure) return Response.json({ code: "dataset_validation_failed", validation: { errors: [{ code: "file_changed", severity: "error", split: "test", image_id: 51 }], warnings: [], counts: {}, digest: null } }, { status: 409 });
      return Response.json({ ...dataset, revision: 8, status: "published", enabled: true });
    }
    throw new Error(`Unexpected ${url.pathname} ${init?.method}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AnnotationWorkspace dataset={dataset} onClose={onClose} /></QueryClientProvider>);
  await userEvent.selectOptions(screen.getByLabelText("数据分区"), "test" );
  return { fetchMock, onClose, recoverCheck: () => { failedCheck = false; } };
}

async function saveLast(label = "保存标注") {
  await screen.findByRole("button", { name: /^frame-51\.jpg/ });
  await waitFor(() => expect(screen.getByRole("button", { name: label })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: label }));
}

it("prompts after the final pending image across every split and publishes only after confirmation", async () => {
  const { fetchMock, onClose } = await setup({ many: true });
  await screen.findByRole("button", { name: /^frame-1\.jpg/ });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await saveLast();
  expect(await screen.findByRole("dialog", { name: "全部标注已完成，是否发布？" })).toBeVisible();
  for (const split of ["train", "val", "test"]) expect(fetchMock.mock.calls.some(([input]) => String(input).includes(`split=${split}`) && String(input).includes("status=pending_annotation"))).toBe(true);
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/publish"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "校验并发布" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(fetchMock).toHaveBeenCalledWith("/api/training/datasets/2/publish", expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 7, confirm_warnings: true }) }));
});

it("also prompts after confirming the final image empty and allows postponing publication", async () => {
  const { fetchMock } = await setup();
  await saveLast("确认无目标");
  expect(await screen.findByRole("dialog", { name: "全部标注已完成，是否发布？" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/publish"))).toBe(false);
  await saveLast();
  expect(await screen.findByRole("dialog", { name: "全部标注已完成，是否发布？" })).toBeVisible();
});

it("does not mistake completion of the current split for completion of the whole version", async () => {
  const { fetchMock } = await setup({ otherPending: true });
  await saveLast();
  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("status=pending_annotation"))).toHaveLength(3));
  expect(screen.queryByRole("dialog", { name: "全部标注已完成，是否发布？" })).not.toBeInTheDocument();
  expect(await screen.findByRole("dialog", { name: "测试集标注已完成" })).toHaveTextContent("整个版本还有 1 张待标注");
  await userEvent.click(screen.getByRole("button", { name: "前往训练集" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByLabelText("数据分区")).toHaveValue("train");
  expect(screen.getByLabelText("状态")).toHaveValue("pending_annotation");
});

it("checks again when saving an already reviewed last image after reopening the workspace", async () => {
  const { fetchMock } = await setup({ many: true, alreadyReviewed: true });
  await screen.findByRole("button", { name: /^frame-1\.jpg/ });
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await saveLast();
  expect(await screen.findByRole("dialog", { name: "全部标注已完成，是否发布？" })).toBeVisible();
  expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("status=pending_annotation"))).toHaveLength(3);
});

it("explains why publication is unavailable when resaving a completed test split", async () => {
  await setup({ alreadyReviewed: true, otherPending: true });
  await saveLast();
  expect(await screen.findByRole("dialog", { name: "测试集标注已完成" })).toHaveTextContent("整个版本还有 1 张待标注");
  expect(screen.queryByRole("button", { name: "校验并发布" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.getByRole("region", { name: "标注完成状态" })).toHaveTextContent("训练集：1 张待标注");
});

it("lets the user check completion without saving an annotation again", async () => {
  const { fetchMock } = await setup({ alreadyReviewed: true });
  await screen.findByRole("button", { name: /^frame-51\.jpg/ });
  await userEvent.click(screen.getByRole("button", { name: "检查完成状态" }));
  expect(await screen.findByRole("dialog", { name: "全部标注已完成，是否发布？" })).toBeVisible();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT" || init?.method === "POST")).toBe(false);
});

it("reproduces the completed 140-image test split while 1264 images remain in other splits", async () => {
  const { fetchMock } = await setup({ alreadyReviewed: true, testCount: 140, trainPending: 1121, valPending: 143 });
  await screen.findByRole("button", { name: /^frame-1\.jpg/ });
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: /^frame-51\.jpg/ });
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await userEvent.click(await screen.findByRole("button", { name: /^frame-140\.jpg/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: "保存标注" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "保存标注" }));
  const dialog = await screen.findByRole("dialog", { name: "测试集标注已完成" });
  expect(dialog).toHaveTextContent("整个版本还有 1264 张待标注");
  expect(dialog).toHaveTextContent("训练集：1121 张待标注");
  expect(dialog).toHaveTextContent("验证集：143 张待标注");
  expect(dialog).toHaveTextContent("测试集：0 张待标注");
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/publish"))).toBe(false);
});

it("never offers publication when the final annotation did not save", async () => {
  const { fetchMock } = await setup({ saveFailure: true });
  await saveLast();
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("status=pending_annotation"))).toBe(false);
});

it("keeps a successful save when the completion check fails and supports retry", async () => {
  const server = await setup({ checkFailure: true });
  await saveLast();
  expect(await screen.findByText(/标注已保存，但无法确认整个版本是否完成/)).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  server.recoverCheck();
  await userEvent.click(screen.getByRole("button", { name: "重新检查完成状态" }));
  expect(await screen.findByRole("dialog", { name: "全部标注已完成，是否发布？" })).toBeVisible();
});

it("keeps publication errors visible so the user can return to annotation", async () => {
  const { onClose } = await setup({ publishFailure: true });
  await saveLast();
  await screen.findByRole("dialog", { name: "全部标注已完成，是否发布？" });
  await userEvent.click(screen.getByRole("button", { name: "校验并发布" }));
  expect(await screen.findByText("图片内容或尺寸已变化：1 项")).toBeVisible();
  expect(onClose).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "继续核验" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
