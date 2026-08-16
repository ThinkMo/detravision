import { ArrowLeft, Download, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import type { BatchJobDetail } from "../api/types";
import { ErrorPanel } from "../components/ErrorPanel";
import { LoadingPanel } from "../components/LoadingPanel";
import { StatusBadge } from "../components/StatusBadge";
import { batchStatus } from "./JobList";
import { ResultCard } from "./ResultCard";

export function JobDetail({ data, loading, error, onRetry }: { data?: BatchJobDetail; loading: boolean; error: boolean; onRetry: () => void }) {
  if (loading) return <LoadingPanel label="正在加载识别结果" />;
  if (error || !data) return <ErrorPanel message="暂时无法读取识别结果，任务可能不存在或无权访问" onRetry={onRetry} />;
  const status = batchStatus(data.status);
  const processed = data.success_count + data.failure_count;
  const active = data.status === "queued" || data.status === "processing";
  return (
    <section className="batch-detail" aria-labelledby="batch-detail-title">
      <header className="page-heading batch-detail__heading"><div><Link className="batch-back" to="/batch"><ArrowLeft aria-hidden="true" size={16} />返回任务列表</Link><p>BATCH RESULT</p><h1 id="batch-detail-title">任务 #{data.id}</h1><span>{active ? `处理中 ${processed} / ${data.image_total}` : `${processed} / ${data.image_total} 张已处理`}</span></div><div className="batch-detail__actions"><StatusBadge tone={status.tone}>{status.label}</StatusBadge>{active ? <span className="batch-polling"><RefreshCw aria-hidden="true" size={14} />每秒自动刷新</span> : null}<a className="secondary-button" href={`/api/batch-jobs/${data.id}/download`} download><Download aria-hidden="true" size={16} />下载全部结果 ZIP</a></div></header>
      <dl className="batch-detail__meta"><div><dt>创建时间</dt><dd>{new Date(data.created_at).toLocaleString("zh-CN", { hour12: false })}</dd></div><div><dt>成功</dt><dd>{data.success_count}</dd></div><div><dt>失败</dt><dd>{data.failure_count}</dd></div><div><dt>保留至</dt><dd>{new Date(data.expires_at).toLocaleString("zh-CN", { hour12: false })}</dd></div></dl>
      <div className="batch-results">{data.images.map((image) => <ResultCard key={image.id} jobId={data.id} image={image} />)}</div>
    </section>
  );
}
