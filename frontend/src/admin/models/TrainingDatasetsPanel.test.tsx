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
