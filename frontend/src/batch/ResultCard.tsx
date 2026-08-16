import { Download, Image as ImageIcon } from "lucide-react";
import { useState } from "react";
import type { BatchImageResult } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { safePreviewUrl, safeResultUrl } from "./batchApi";

export function ResultCard({ jobId, image }: { jobId: number; image: BatchImageResult }) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const [previewRetryKey, setPreviewRetryKey] = useState(0);
  const resultUrl = image.status === "completed" ? safeResultUrl(jobId, image.id, image.result_url) : null;
  const previewUrl = image.status === "completed" ? safePreviewUrl(jobId, image.id, image.preview_url) : null;
  const previewSrc = previewUrl && !previewFailed ? `${previewUrl}${previewRetryKey ? `?retry=${previewRetryKey}` : ""}` : null;
  const violations = image.detections.filter((item) => item.label === "no_helmet");
  const context = image.detections.filter((item) => item.label !== "no_helmet");
  const statusLabel = image.status === "completed" ? "识别完成" : image.status === "failed" ? "识别失败" : image.status === "processing" ? "处理中" : "等待处理";
  return (
    <article className={`batch-result batch-result--${image.status}`} aria-label={`${image.display_name} 识别结果`}>
      <div className="batch-result__image">
        {previewSrc ? <img src={previewSrc} alt={`${image.display_name} 标注结果`} onError={() => setPreviewFailed(true)} /> : previewUrl && previewFailed ? <div className="batch-result__preview-error"><span>预览加载失败</span><button type="button" onClick={() => { setPreviewFailed(false); setPreviewRetryKey((key) => key + 1); }}>重新加载预览</button></div> : <ImageIcon aria-hidden="true" size={32} />}
      </div>
      <div className="batch-result__body">
        <div className="batch-result__heading"><div><h3>{image.display_name}</h3><span>{image.model_version_id === null ? "模型待定" : `模型 #${image.model_version_id}`}</span></div><StatusBadge tone={image.status === "completed" ? "safe" : image.status === "failed" ? "danger" : "neutral"}>{statusLabel}</StatusBadge></div>
        {image.status === "failed" ? <p className="batch-result__error" role="alert">{image.error_message ?? "图片识别失败"}</p> : null}
        {image.status === "completed" ? <>
          <p className="batch-result__violation-summary">未佩戴安全帽：{violations.length} 人</p>
          {violations.length ? <ul className="batch-detections batch-detections--violation" aria-label="未佩戴安全帽检测结果">{violations.map((detection, index) => <li key={`${detection.label}-${index}`}><span>{detection.label}</span><strong>{Math.round(detection.confidence * 100)}%</strong></li>)}</ul> : null}
          {context.length ? <ul className="batch-detections batch-detections--context" aria-label="其他识别对象">{context.map((detection, index) => <li key={`${detection.label}-${index}`}><span>{detection.label}</span><strong>{Math.round(detection.confidence * 100)}%</strong></li>)}</ul> : null}
          {!image.detections.length ? <p className="batch-result__empty">未检测到目标</p> : null}
        </> : null}
        {resultUrl ? <a className="batch-download" href={resultUrl} download><Download aria-hidden="true" size={16} />下载 {image.display_name} 标注图</a> : null}
      </div>
    </article>
  );
}
