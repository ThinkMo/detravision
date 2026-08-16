import { Download, Image as ImageIcon } from "lucide-react";
import type { BatchImageResult } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { safeResultUrl } from "./batchApi";

export function ResultCard({ jobId, image }: { jobId: number; image: BatchImageResult }) {
  const resultUrl = image.status === "completed" ? safeResultUrl(jobId, image.id, image.result_url) : null;
  const statusLabel = image.status === "completed" ? "识别完成" : image.status === "failed" ? "识别失败" : image.status === "processing" ? "处理中" : "等待处理";
  return (
    <article className={`batch-result batch-result--${image.status}`} aria-label={`${image.display_name} 识别结果`}>
      <div className="batch-result__image">{resultUrl ? <img src={resultUrl} alt={`${image.display_name} 标注结果`} /> : <ImageIcon aria-hidden="true" size={32} />}</div>
      <div className="batch-result__body">
        <div className="batch-result__heading"><div><h3>{image.display_name}</h3><span>{image.model_version_id === null ? "模型待定" : `模型 #${image.model_version_id}`}</span></div><StatusBadge tone={image.status === "completed" ? "safe" : image.status === "failed" ? "danger" : "neutral"}>{statusLabel}</StatusBadge></div>
        {image.status === "failed" ? <p className="batch-result__error" role="alert">{image.error_message ?? "图片识别失败"}</p> : null}
        {image.status === "completed" ? <><p className="batch-result__summary">检测到 {image.detections.length} 个目标</p>{image.detections.length ? <ul className="batch-detections">{image.detections.map((detection, index) => <li key={`${detection.label}-${index}`}><span>{detection.label}</span><strong>{Math.round(detection.confidence * 100)}%</strong></li>)}</ul> : <p className="batch-result__empty">未检测到目标</p>}</> : null}
        {resultUrl ? <a className="batch-download" href={resultUrl} download><Download aria-hidden="true" size={16} />下载 {image.display_name} 标注图</a> : null}
      </div>
    </article>
  );
}
