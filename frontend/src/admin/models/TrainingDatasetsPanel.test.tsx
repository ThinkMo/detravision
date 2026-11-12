import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TrainingDatasetsPanel } from "./TrainingDatasetsPanel";
import type { TrainingDataset } from "../../api/types";

const published: TrainingDataset = {
  id: 1, key: "dataset:1", name: "工地安全帽", root_alias: "default",
  directory: "construction-ppe-helmet", train_split: "images/train",
  val_split: "images/val", test_split: "images/test",
  classes: ["person", "helmet", "no_helmet"], enabled: true,
  available: true, reason: null, version: 1, status: "published",
  parent_id: null, root_dataset_id: null, revision: 0,
  published_at: "2026-08-22T00:00:00Z", manifest_digest: null,
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><TrainingDatasetsPanel onAnnotate={() => undefined} /></QueryClientProvider>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("uses consistent styled actions and a safe version label", async () => {
  const legacy = { ...published, version: undefined } as unknown as TrainingDataset;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ items: [legacy] })));
  renderPanel();

  const createButton = await screen.findByRole("button", { name: "创建编辑版本" });
  const disableButton = screen.getByRole("button", { name: "停用" });
  expect(createButton).toHaveClass("dataset-action", "dataset-action--primary");
  expect(disableButton).toHaveClass("dataset-action", "dataset-action--danger");
  expect(screen.getByText("v1 · 已发布")).toBeVisible();
});

it("creates an editing version and exposes only editing actions", async () => {
  let items: TrainingDataset[] = [published];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/training/datasets/1/versions" && init?.method === "POST") {
      const editing: TrainingDataset = { ...published, id: 2, key: "dataset:2", version: 2, status: "editing", enabled: false, available: false, parent_id: 1, root_dataset_id: 1, published_at: null, revision: 0 };
      items = [...items, editing];
      return Response.json(editing, { status: 201 });
    }
    if (path === "/api/training/datasets") return Response.json({ items });
    throw new Error(`Unexpected request ${path}`);
  }));

  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "创建编辑版本" }));

  expect(await screen.findByText("v2 · 编辑中")).toBeVisible();
  expect(screen.getByRole("button", { name: "继续标注" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "重新扫描图片" })).toBeEnabled();
  expect(screen.getAllByRole("button", { name: "创建编辑版本" })).toHaveLength(1);
});

it("shows clone failures in an alert", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/training/datasets") return Response.json({ items: [published] });
    if (path === "/api/training/datasets/1/versions" && init?.method === "POST") {
      return Response.json({ code: "dataset_copy_failed", message: "复制失败" }, { status: 503 });
    }
    throw new Error(`Unexpected request ${path}`);
  }));

  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "创建编辑版本" }));

  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("复制失败"));
});

it("explains blocked publication with pending counts by split", async () => {
  const editing: TrainingDataset = { ...published, id: 2, key: "dataset:2", version: 2, status: "editing", enabled: false, available: false, revision: 0 };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/training/datasets") return Response.json({ items: [editing] });
    if (path === "/api/training/datasets/2/publish") return Response.json({ code: "dataset_validation_failed", message: "The dataset annotation request failed.", validation: { errors: [
      { code: "annotation_pending", severity: "error", split: "train", image_id: 9, message: "Annotation pending." },
      { code: "annotation_pending", severity: "error", split: "train", image_id: 10, message: "Annotation pending." },
      { code: "annotation_pending", severity: "error", split: "val", image_id: 11, message: "Annotation pending." },
    ], warnings: [], counts: {}, digest: null } }, { status: 409 });
    throw new Error(`Unexpected request ${path}`);
  }));
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "发布" }));
  await userEvent.click(screen.getByRole("button", { name: "校验并发布" }));
  expect(await screen.findByText("训练集：2 张待核验")).toBeVisible();
  expect(screen.getByText("验证集：1 张待核验")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("校验未通过");
  expect(screen.queryByText("The dataset annotation request failed.")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "继续核验" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "发布" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
it("publishes a valid version and refreshes its available actions", async () => {
  let item: TrainingDataset = { ...published, id: 2, key: "dataset:2", version: 2, status: "editing", enabled: false, available: false, revision: 0 };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === "/api/training/datasets") return Response.json({ items: [item] });
    if (String(input) === "/api/training/datasets/2/publish" && init?.method === "POST") {
      item = { ...item, status: "published", enabled: true, available: true, revision: 1, manifest_digest: "a".repeat(64) };
      return Response.json(item);
    }
    throw new Error(`Unexpected request ${input}`);
  }));
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "发布" }));
  await userEvent.click(screen.getByRole("button", { name: "校验并发布" }));
  expect(await screen.findByText("v2 · 已发布")).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建编辑版本" })).toBeVisible();
});