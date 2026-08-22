import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { TrainingDataset } from "../../api/types";
import { boxFromPoints, imageToNormalized, screenToImage } from "./annotationGeometry";
import { AnnotationWorkspace } from "./AnnotationWorkspace";

const dataset: TrainingDataset = { id: 2, key: "dataset:2", name: "安全帽评测集", root_alias: "default", directory: "set-v2", train_split: "images/train", val_split: "images/val", test_split: "images/test", classes: ["person", "helmet", "no_helmet"], enabled: false, available: false, reason: "dataset_disabled", version: 2, status: "editing", parent_id: 1, root_dataset_id: 1, revision: 0, published_at: null, manifest_digest: null };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("maps a zoomed drag to normalized image coordinates", () => {
  const viewport = { scale: 2, offsetX: 40, offsetY: 20 };
  const start = screenToImage({ x: 240, y: 120 }, viewport);
  const end = screenToImage({ x: 440, y: 320 }, viewport);
  expect(imageToNormalized(boxFromPoints(start, end), { width: 1000, height: 500 })).toEqual({ class_id: 0, x_center: .15, y_center: .2, width: .1, height: .2 });
});

it("loads pending test images, draws a box and saves the annotation", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/images?split=test")) return Response.json({ items: [{ id: 9, dataset_id: 2, split: "test", image_key: "new.jpg", display_name: "new.jpg", width: 1000, height: 500, review_status: "pending_annotation", validation_code: null, revision: 0 }], total: 1, page: 1, page_size: 50 });
    if (path.endsWith("/images/9")) return Response.json({ id: 9, dataset_id: 2, split: "test", image_key: "new.jpg", display_name: "new.jpg", width: 1000, height: 500, review_status: "pending_annotation", validation_code: null, revision: 0, boxes: [] });
    if (path.endsWith("/images/9/annotations") && init?.method === "PUT") return Response.json({ id: 9, dataset_id: 2, split: "test", image_key: "new.jpg", display_name: "new.jpg", width: 1000, height: 500, review_status: "annotated", validation_code: null, revision: 1, boxes: JSON.parse(String(init.body)).boxes });
    throw new Error(`Unexpected ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("PointerEvent", MouseEvent);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AnnotationWorkspace dataset={dataset} onClose={() => undefined} /></QueryClientProvider>);
  expect(await screen.findByText("new.jpg")).toBeVisible();
  const canvas = await screen.findByLabelText("图片标注画布");
  Object.defineProperty(canvas, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500, x: 0, y: 0, toJSON: () => ({}) }) });
  fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent.pointerMove(canvas, { clientX: 300, clientY: 300, pointerId: 1 });
  fireEvent.pointerUp(canvas, { clientX: 300, clientY: 300, pointerId: 1 });
  await userEvent.click(screen.getByRole("button", { name: "保存标注" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/training/datasets/2/images/9/annotations", expect.objectContaining({ method: "PUT" })));
  expect(screen.getByRole("status")).toHaveTextContent("标注已保存");
});
