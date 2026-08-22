import { Maximize2, Scan, X, ZoomIn, ZoomOut } from "lucide-react";
import { useRef, useState, type MouseEvent, type WheelEvent } from "react";
import { ModalDialog } from "../admin/shared";

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;

type ZoomMode = "fit" | "original" | number;

function numericZoom(mode: ZoomMode) {
  return typeof mode === "number" ? mode : 1;
}

export function ImagePreviewDialog({ src, name, onClose }: { src: string; name: string; onClose: () => void }) {
  const [zoom, setZoom] = useState<ZoomMode>("fit");
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  const changeZoom = (delta: number) => {
    setZoom((current) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, numericZoom(current) + delta)));
  };

  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    changeZoom(event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP);
  };

  const onMouseDown = (event: MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    dragStart.current = {
      x: event.clientX,
      y: event.clientY,
      left: event.currentTarget.scrollLeft,
      top: event.currentTarget.scrollTop,
    };
    setDragging(true);
    event.preventDefault();
  };

  const onMouseMove = (event: MouseEvent<HTMLDivElement>) => {
    const start = dragStart.current;
    if (!start) return;
    event.currentTarget.scrollLeft = start.left - (event.clientX - start.x);
    event.currentTarget.scrollTop = start.top - (event.clientY - start.y);
  };

  const stopDragging = () => {
    dragStart.current = null;
    setDragging(false);
  };

  const zoomLabel = zoom === "fit" ? "适应窗口" : `${Math.round(numericZoom(zoom) * 100)}%`;
  const imageStyle = typeof zoom === "number" ? { width: `${zoom * 100}%` } : undefined;

  return (
    <ModalDialog title={`${name} 标注结果预览`} className="batch-preview-dialog" onClose={onClose}>
      <div className="batch-preview-dialog__toolbar" aria-label="图片缩放工具">
        <button type="button" aria-label="缩小图片" disabled={typeof zoom === "number" && zoom <= MIN_ZOOM} onClick={() => changeZoom(-ZOOM_STEP)}><ZoomOut aria-hidden="true" size={18} /></button>
        <span role="status" aria-live="polite">{zoomLabel}</span>
        <button type="button" aria-label="放大图片" disabled={typeof zoom === "number" && zoom >= MAX_ZOOM} onClick={() => changeZoom(ZOOM_STEP)}><ZoomIn aria-hidden="true" size={18} /></button>
        <button type="button" aria-label="适应窗口" onClick={() => setZoom("fit")}><Maximize2 aria-hidden="true" size={18} /></button>
        <button type="button" aria-label="原始尺寸" onClick={() => setZoom("original")}><Scan aria-hidden="true" size={18} /></button>
        <button className="batch-preview-dialog__close" type="button" aria-label="关闭预览" onClick={onClose}><X aria-hidden="true" size={20} /></button>
      </div>
      <div
        className="batch-preview-dialog__viewport"
        role="region"
        aria-label="可缩放图片预览"
        data-dragging={dragging || undefined}
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={stopDragging}
        onMouseLeave={stopDragging}
      >
        <img
          className={`batch-preview-dialog__image batch-preview-dialog__image--${typeof zoom === "number" ? "scaled" : zoom}`}
          style={imageStyle}
          src={src}
          alt={`${name} 放大标注结果`}
          draggable={false}
        />
      </div>
    </ModalDialog>
  );
}
