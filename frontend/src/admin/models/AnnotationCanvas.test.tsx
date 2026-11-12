import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AnnotationCanvas } from "./AnnotationCanvas";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function canvasFor(width: number, height: number) {
  vi.stubGlobal("PointerEvent", MouseEvent);
  const onChange = vi.fn();
  const { rerender } = render(<AnnotationCanvas src="/sample.jpg" width={width} height={height} boxes={[]} classes={["helmet", "no_helmet"]} activeClass={1} onChange={onChange} />);
  const canvas = screen.getByLabelText("图片标注画布");
  Object.defineProperty(canvas, "getBoundingClientRect", { value: () => ({ left: 80, top: 40, width: 600, height: 600, right: 680, bottom: 640, x: 80, y: 40, toJSON: () => ({}) }) });
  return { canvas, onChange, rerender };
}

it.each([
  { name: "top and bottom padding", width: 1000, height: 500, from: [140, 250], to: [260, 370], want: [.2, .4, .2, .4] },
  { name: "left and right padding", width: 500, height: 1000, from: [290, 100], to: [410, 220], want: [.4, .2, .4, .2] },
])("draws at the actual image position with $name", ({ width, height, from, to, want }) => {
  const { canvas, onChange } = canvasFor(width, height);
  fireEvent.pointerDown(canvas, { clientX: from[0], clientY: from[1], pointerId: 1 });
  fireEvent.pointerMove(canvas, { clientX: to[0], clientY: to[1], pointerId: 1 });
  fireEvent.pointerUp(canvas, { clientX: to[0], clientY: to[1], pointerId: 1 });
  expect(onChange).toHaveBeenCalledTimes(1);
  const [box] = onChange.mock.calls[0][0];
  expect(box.class_id).toBe(1);
  [box.x_center, box.y_center, box.width, box.height].forEach((value, i) => expect(value).toBeCloseTo(want[i], 8));
});

it("ignores a drag started in the canvas padding outside the image", () => {
  const { canvas, onChange } = canvasFor(500, 1000);
  fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent.pointerUp(canvas, { clientX: 410, clientY: 220, pointerId: 1 });
  expect(onChange).not.toHaveBeenCalled();
});

it("does not create a box after the pointer gesture is canceled", () => {
  const { canvas, onChange } = canvasFor(1000, 500);
  fireEvent.pointerDown(canvas, { clientX: 140, clientY: 250, pointerId: 1 });
  fireEvent.pointerCancel(canvas, { pointerId: 1 });
  fireEvent.pointerUp(canvas, { clientX: 260, clientY: 370, pointerId: 1 });
  expect(onChange).not.toHaveBeenCalled();
});

it("keeps the drag preview and committed box inside the same image bounds", () => {
  const { canvas, onChange } = canvasFor(1000, 500);
  fireEvent.pointerDown(canvas, { clientX: 140, clientY: 250, pointerId: 1 });
  fireEvent.pointerMove(canvas, { clientX: 800, clientY: 700, pointerId: 1 });
  const draft = canvas.querySelector(".annotation-box--draft")!;
  expect(Number(draft.getAttribute("x"))).toBeCloseTo(100);
  expect(Number(draft.getAttribute("y"))).toBeCloseTo(100);
  expect(Number(draft.getAttribute("width"))).toBeCloseTo(900);
  expect(Number(draft.getAttribute("height"))).toBeCloseTo(400);
  fireEvent.pointerUp(canvas, { clientX: 800, clientY: 700, pointerId: 1 });
  const [box] = onChange.mock.calls[0][0];
  expect(box).toMatchObject({ x_center: .55, y_center: .6, width: .9, height: .8 });
});

it("discards an unfinished gesture when switching to another image", () => {
  const { canvas, onChange, rerender } = canvasFor(1000, 500);
  fireEvent.pointerDown(canvas, { clientX: 140, clientY: 250, pointerId: 1 });
  rerender(<AnnotationCanvas src="/other.jpg" width={1000} height={500} boxes={[]} classes={["helmet", "no_helmet"]} activeClass={1} onChange={onChange} />);
  fireEvent.pointerUp(screen.getByLabelText("图片标注画布"), { clientX: 260, clientY: 370, pointerId: 1 });
  expect(onChange).not.toHaveBeenCalled();
});

it.each([
  { width: 1024, height: 683, want: [204.8, 136.6, 102.4, 68.3] },
  { width: 683, height: 1024, want: [136.6, 204.8, 68.3, 102.4] },
  { width: 2560, height: 1440, want: [512, 288, 256, 144] },
])("renders loaded boxes in the same original-image coordinates at $width x $height", ({ width, height, want }) => {
  const { container } = render(<AnnotationCanvas src="/saved.jpg" width={width} height={height} boxes={[
    { class_id: 1, x_center: .25, y_center: .25, width: .1, height: .1 },
  ]} classes={["helmet", "no_helmet"]} activeClass={1} readOnly onChange={() => undefined} />);
  const image = container.querySelector("image")!;
  expect(image.getAttribute("width")).toBe(String(width));
  expect(image.getAttribute("height")).toBe(String(height));
  const rect = container.querySelector(".annotation-box")!;
  ["x", "y", "width", "height"].forEach((attribute, i) => expect(Number(rect.getAttribute(attribute))).toBeCloseTo(want[i], 8));
});