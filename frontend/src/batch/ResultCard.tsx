import { Image as ImageIcon, X } from "lucide-react";
import { useState } from "react";
import type { BatchImageResult } from "../api/types";
import { ModalDialog } from "../admin/shared";
import { StatusBadge } from "../components/StatusBadge";
import { safePreviewUrl } from "./batchApi";

const detectionLabels: Record<string, string> = {
  helmet_worn: "已佩戴安全帽",
  no_helmet: "未佩戴安全帽",
};

function detectionLabel(label: string) {
  return detectionLabels[label] ?? label;
}

export function ResultCard({ jobId, image }: { jobId: number; image: BatchImageResult }) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const [previewRetryKey, setPreviewRetryKey] = useState(0);
  const [enlarged, setEnlarged] = useState(false);
  const previewUrl = image.status === "completed" ? safePreviewUrl(jobId, image.id, image.preview_url) : null;
  const previewSrc = previewUrl && !previewFailed ? `${previewUrl}${previewRetryKey ? `?retry=${previewRetryKey}` : ""}` : null;
  const violations = image.detections.filter((item) => item.label === "no_helmet");
  const context = image.detections.filter((item) => item.label !== "no_helmet");
  const statusLabel = image.status === "completed" ? "识别完成" : image.status === "failed" ? "识别失败" : image.status === "processing" ? "处理中" : "等待处理";
  return (
    <article className={`batch-result batch-result--${image.status}`} aria-label={`${image.display_name} 识别结果`}>
      <div className="batch-result__image">
        {previewSrc ? <button className="batch-result__preview-trigger" type="button" aria-label={`放大预览 ${image.display_name} 标注结果`} onClick={() => setEnlarged(true)}><img src={previewSrc} alt={`${image.display_name} 标注结果`} onError={() => setPreviewFailed(true)} /></button> : previewUrl && previewFailed ? <div className="batch-result__preview-error"><span>预览加载失败</span><button type="button" onClick={() => { setPreviewFailed(false); setPreviewRetryKey((key) => key + 1); }}>重新加载预览</button></div> : <ImageIcon aria-hidden="true" size={32} />}
      </div>
      <div className="batch-result__body">
        <div className="batch-result__heading"><div><h3>{image.display_name}</h3><span>{image.model_version_id === null ? "模型待定" : `模型 #${image.model_version_id}`}</span></div><StatusBadge tone={image.status === "completed" ? "safe" : image.status === "failed" ? "danger" : "neutral"}>{statusLabel}</StatusBadge></div>
        {image.status === "failed" ? <p className="batch-result__error" role="alert">{image.error_message ?? "图片识别失败"}</p> : null}
        {image.status === "completed" ? <>
          <p className="batch-result__violation-summary">未佩戴安全帽：{violations.length} 人</p>
          {violations.length ? <ul className="batch-detections batch-detections--violation" aria-label="未佩戴安全帽检测结果">{violations.map((detection, index) => <li key={`${detection.label}-${index}`}><span>{detectionLabel(detection.label)}</span><strong>{Math.round(detection.confidence * 100)}%</strong></li>)}</ul> : null}
          {context.length ? <ul className="batch-detections batch-detections--context" aria-label="其他识别对象">{context.map((detection, index) => <li key={`${detection.label}-${index}`}><span>{detectionLabel(detection.label)}</span><strong>{Math.round(detection.confidence * 100)}%</strong></li>)}</ul> : null}
          {!image.detections.length ? <p className="batch-result__empty">未检测到目标</p> : null}
        </> : null}
      </div>
      {enlarged && previewSrc ? <ModalDialog title={`${image.display_name} 标注结果预览`} className="batch-preview-dialog" onClose={() => setEnlarged(false)}><button className="batch-preview-dialog__close" type="button" aria-label="关闭预览" onClick={() => setEnlarged(false)}><X aria-hidden="true" size={20} /></button><img className="batch-preview-dialog__image" src={previewSrc} alt={`${image.display_name} 放大标注结果`} /></ModalDialog> : null}
    </article>
  );
}
