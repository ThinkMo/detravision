import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { TrainingDataset } from "../../api/types";
import { boxFromPoints, imageToNormalized, screenToImage } from "./annotationGeometry";
import { AnnotationWorkspace } from "./AnnotationWorkspace";
import { AnnotationCanvas } from "./AnnotationCanvas";

const dataset: TrainingDataset = { id: 2, key: "dataset:2", name: "安全帽评测集", root_alias: "default", directory: "set-v2", train_split: "images/train", val_split: "images/val", test_split: "images/test", classes: ["person", "helmet", "no_helmet"], enabled: false, available: false, reason: "dataset_disabled", version: 2, status: "editing", parent_id: 1, root_dataset_id: 1, revision: 0, published_at: null, manifest_digest: null };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("maps a zoomed drag to normalized image coordinates", () => {
  const viewport = { scale: 2, offsetX: 40, offsetY: 20 };
  const start = screenToImage({ x: 240, y: 120 }, viewport);
  const end = screenToImage({ x: 440, y: 320 }, viewport);
  expect(imageToNormalized(boxFromPoints(start, end), { width: 1000, height: 500 })).toEqual({ class_id: 0, x_center: .15, y_center: .2, width: .1, height: .2 });
});

it("renders each annotation class with its name and a distinct color", () => {
  const { container } = render(<AnnotationCanvas src="/sample.jpg" width={100} height={100} boxes={[
    { class_id: 0, x_center: .25, y_center: .5, width: .2, height: .4 },
    { class_id: 1, x_center: .75, y_center: .5, width: .2, height: .4 },
  ]} classes={["person", "helmet"]} activeClass={0} readOnly onChange={() => undefined} />);
  expect(screen.getByText("person")).toBeVisible();
  expect(screen.getByText("helmet")).toBeVisible();
  const rectangles = container.querySelectorAll("rect.annotation-box");
  expect(rectangles[0]).not.toHaveAttribute("stroke", rectangles[1].getAttribute("stroke"));
});

it("loads pending test images, draws a box and saves the annotation", async () => {
  let savedBoxes: Array<{ class_id: number; x_center: number; y_center: number; width: number; height: number }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/images?split=test")) return Response.json({ items: [
      { id: 9, dataset_id: 2, split: "test", image_key: "new.jpg", display_name: "new.jpg", width: 1000, height: 500, review_status: savedBoxes.length ? "annotated" : "pending_annotation", validation_code: null, revision: savedBoxes.length ? 1 : 0 },
      { id: 10, dataset_id: 2, split: "test", image_key: "next.jpg", display_name: "next.jpg", width: 800, height: 600, review_status: "pending_annotation", validation_code: null, revision: 0 },
    ], total: 2, page: 1, page_size: 50 });
    if (path.endsWith("/images/9")) return Response.json({ id: 9, dataset_id: 2, split: "test", image_key: "new.jpg", display_name: "new.jpg", width: 1000, height: 500, review_status: savedBoxes.length ? "annotated" : "pending_annotation", validation_code: null, revision: savedBoxes.length ? 1 : 0, boxes: savedBoxes });
    if (path.endsWith("/images/9/annotations") && init?.method === "PUT") {
      savedBoxes = JSON.parse(String(init.body)).boxes;
      return Response.json({ id: 9, dataset_id: 2, split: "test", image_key: "new.jpg", display_name: "new.jpg", width: 1000, height: 500, review_status: "annotated", validation_code: null, revision: 1 });
    }
    if (path.endsWith("/images/10")) return Response.json({ id: 10, dataset_id: 2, split: "test", image_key: "next.jpg", display_name: "next.jpg", width: 800, height: 600, review_status: "pending_annotation", validation_code: null, revision: 0, boxes: [] });
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
  expect(screen.getByText("标注框（1）")).toBeVisible();
  fireEvent.keyDown(window, { key: "z", ctrlKey: true });
  expect(screen.getByText("标注框（0）")).toBeVisible();
  fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, pointerId: 2 });
  fireEvent.pointerMove(canvas, { clientX: 300, clientY: 300, pointerId: 2 });
  fireEvent.pointerUp(canvas, { clientX: 300, clientY: 300, pointerId: 2 });
  expect(screen.getByRole("button", { name: "确认无目标" })).toHaveClass("annotation-button", "annotation-button--secondary");
  expect(screen.getByRole("button", { name: "保存标注" })).toHaveClass("annotation-button", "annotation-button--primary");
  expect(screen.getByRole("button", { name: "添加类别" })).toHaveClass("annotation-button", "annotation-button--primary");
  await userEvent.click(screen.getByRole("button", { name: "保存标注" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/training/datasets/2/images/9/annotations", expect.objectContaining({ method: "PUT" })));
  expect(screen.getByRole("status")).toHaveTextContent("标注已保存");
  await waitFor(() => expect(screen.getByRole("button", { name: /next.jpg/ })).toHaveClass("is-active"));
});
