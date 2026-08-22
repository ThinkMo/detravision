import type { AnnotationBoxInput } from "../../api/types";

export type Point = { x: number; y: number };
export type ImageSize = { width: number; height: number };
export type Viewport = { scale: number; offsetX: number; offsetY: number };
export type PixelBox = { x: number; y: number; width: number; height: number; class_id?: number };

export function screenToImage(point: Point, viewport: Viewport): Point {
  return { x: (point.x - viewport.offsetX) / viewport.scale, y: (point.y - viewport.offsetY) / viewport.scale };
}

export function boxFromPoints(first: Point, second: Point): PixelBox {
  return { x: Math.min(first.x, second.x), y: Math.min(first.y, second.y), width: Math.abs(second.x - first.x), height: Math.abs(second.y - first.y) };
}

export function clampBox(box: PixelBox, size: ImageSize, minimumPixels = 4): PixelBox | null {
  const x = Math.max(0, Math.min(size.width, box.x));
  const y = Math.max(0, Math.min(size.height, box.y));
  const right = Math.max(0, Math.min(size.width, box.x + box.width));
  const bottom = Math.max(0, Math.min(size.height, box.y + box.height));
  const result = { ...box, x: Math.min(x, right), y: Math.min(y, bottom), width: Math.abs(right - x), height: Math.abs(bottom - y) };
  return result.width >= minimumPixels && result.height >= minimumPixels ? result : null;
}

export function imageToNormalized(box: PixelBox, size: ImageSize): AnnotationBoxInput {
  return { class_id: box.class_id ?? 0, x_center: (box.x + box.width / 2) / size.width, y_center: (box.y + box.height / 2) / size.height, width: box.width / size.width, height: box.height / size.height };
}

export function normalizedToImage(box: AnnotationBoxInput, size: ImageSize): PixelBox {
  const width = box.width * size.width;
  const height = box.height * size.height;
  return { class_id: box.class_id, x: box.x_center * size.width - width / 2, y: box.y_center * size.height - height / 2, width, height };
}

export function hitTest(point: Point, boxes: PixelBox[], handleRadius = 8): number | null {
  for (let index = boxes.length - 1; index >= 0; index -= 1) {
    const box = boxes[index];
    if (point.x >= box.x - handleRadius && point.x <= box.x + box.width + handleRadius && point.y >= box.y - handleRadius && point.y <= box.y + box.height + handleRadius) return index;
  }
  return null;
}
