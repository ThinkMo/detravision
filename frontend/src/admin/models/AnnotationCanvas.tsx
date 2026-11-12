import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { AnnotationBoxInput } from "../../api/types";
import { boxFromPoints, clampBox, imageToNormalized, normalizedToImage, type Point } from "./annotationGeometry";
import { annotationColor } from "./annotationColors";

type Props = { src: string; width: number; height: number; boxes: AnnotationBoxInput[]; classes: string[]; activeClass: number; readOnly?: boolean; onChange: (boxes: AnnotationBoxInput[]) => void };

export function AnnotationCanvas({ src, width, height, boxes, classes, activeClass, readOnly = false, onChange }: Props) {
  const surface = useRef<SVGSVGElement>(null);
  const [start, setStart] = useState<Point | null>(null);
  const [current, setCurrent] = useState<Point | null>(null);
  useEffect(() => { setStart(null); setCurrent(null); }, [src, width, height]);
  const point = (event: PointerEvent<SVGSVGElement>) => {
    const rect = surface.current!.getBoundingClientRect();
    // Match the SVG viewBox's centered, uniform scaling (including letterboxing).
    const scale = Math.min(rect.width / width, rect.height / height);
    const left = rect.left + (rect.width - width * scale) / 2;
    const top = rect.top + (rect.height - height * scale) / 2;
    return { x: (event.clientX - left) / scale, y: (event.clientY - top) / scale };
  };
  const pointerDown = (event: PointerEvent<SVGSVGElement>) => {
    if (readOnly) return;
    const next = point(event);
    if (!Number.isFinite(next.x) || !Number.isFinite(next.y) || next.x < 0 || next.x > width || next.y < 0 || next.y > height) return;
    setStart(next); setCurrent(next);
    surface.current?.setPointerCapture?.(event.pointerId);
  };
  const pointerMove = (event: PointerEvent<SVGSVGElement>) => { if (start) setCurrent(point(event)); };
  const pointerUp = (event: PointerEvent<SVGSVGElement>) => {
    if (!start) return;
    const candidate = clampBox(boxFromPoints(start, point(event)), { width, height });
    if (candidate) onChange([...boxes, imageToNormalized({ ...candidate, class_id: activeClass }, { width, height })]);
    setStart(null); setCurrent(null);
  };
  const draft = start && current ? clampBox(boxFromPoints(start, current), { width, height }, 0) : null;
  return <svg ref={surface} className="annotation-canvas" aria-label="图片标注画布" role="img" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={() => { setStart(null); setCurrent(null); }}>
    <image href={src} x="0" y="0" width={width} height={height} preserveAspectRatio="xMidYMid meet" />
    {boxes.map((box, index) => {
      const pixel = normalizedToImage(box, { width, height });
      const name = classes[box.class_id] ?? `类别 ${box.class_id}`;
      const color = annotationColor(box.class_id, classes);
      const labelWidth = Math.max(46, name.length * 9 + 12);
      return <g key={`${index}-${box.class_id}`}><rect className="annotation-box" x={pixel.x} y={pixel.y} width={pixel.width} height={pixel.height} fill={`${color}24`} stroke={color} /><rect className="annotation-box-label-bg" x={pixel.x} y={pixel.y} width={labelWidth} height={24} fill={color} /><text className="annotation-box-label" x={pixel.x + 6} y={pixel.y + 17}>{name}</text></g>;
    })}
    {draft ? <rect className="annotation-box annotation-box--draft" x={draft.x} y={draft.y} width={draft.width} height={draft.height} fill={`${annotationColor(activeClass, classes)}24`} stroke={annotationColor(activeClass, classes)} /> : null}
  </svg>;
}
