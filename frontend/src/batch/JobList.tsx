import { ChevronLeft, ChevronRight, Clock3, Images, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { BatchJob, BatchJobPage, BatchJobStatus } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { LoadingPanel } from "../components/LoadingPanel";
import { StatusBadge } from "../components/StatusBadge";
import type { BatchJobFilters } from "./batchApi";

const labels: Record<BatchJobStatus, string> = {
  queued: "等待处理",
  processing: "处理中",
  completed: "已完成",
  partial_failed: "部分失败",
  failed: "失败",
};

export function batchStatus(status: BatchJobStatus) {
  return {
    label: labels[status] ?? status,
    tone: status === "completed" ? "safe" as const : status === "failed" ? "danger" as const : status === "partial_failed" ? "warning" as const : "neutral" as const,
  };
}

export function JobList({
  data,
  loading,
  error,
  admin,
  filters,
  onFilters,
  onRetry,
  onDelete,
}: {
  data?: BatchJobPage;
  loading: boolean;
  error: boolean;
  admin: boolean;
  filters: BatchJobFilters;
  onFilters: (filters: BatchJobFilters) => void;
  onRetry: () => void;
  onDelete: (job: BatchJob) => void;
}) {
  if (loading) return <LoadingPanel label="正在加载识别任务" />;
  if (error || !data) return <ErrorPanel message="暂时无法读取识别任务" onRetry={onRetry} />;
  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));

  return (
    <section className="batch-panel batch-history" aria-labelledby="batch-history-title">
      <div className="batch-panel__heading"><div><p>JOB HISTORY</p><h2 id="batch-history-title">识别记录</h2></div><span>{data.total} 个任务</span></div>
      <form className="batch-filters" onSubmit={(event) => event.preventDefault()}>
        <label>状态<select value={filters.status ?? ""} onChange={(event) => onFilters({ ...filters, status: event.target.value || undefined, page: 1 })}><option value="">全部</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>开始日期<input type="date" value={filters.createdFrom ?? ""} onChange={(event) => onFilters({ ...filters, createdFrom: event.target.value || undefined, page: 1 })} /></label>
        <label>结束日期<input type="date" value={filters.createdTo ?? ""} onChange={(event) => onFilters({ ...filters, createdTo: event.target.value || undefined, page: 1 })} /></label>
        {admin ? <label>创建用户 ID<input inputMode="numeric" value={filters.userId ?? ""} onChange={(event) => onFilters({ ...filters, userId: event.target.value.replace(/\D/g, "") || undefined, page: 1 })} placeholder="全部用户" /></label> : null}
      </form>
      {data.items.length === 0 ? <EmptyState message="当前筛选条件下没有识别任务" /> : <ul className="batch-jobs">{data.items.map((job) => <JobRow key={job.id} job={job} admin={admin} onDelete={onDelete} />)}</ul>}
      <nav className="batch-pagination" aria-label="识别任务分页">
        <button type="button" disabled={data.page <= 1} onClick={() => onFilters({ ...filters, page: data.page - 1 })}><ChevronLeft aria-hidden="true" size={16} />上一页</button>
        <span>第 {data.page} / {totalPages} 页</span>
        <button type="button" disabled={data.page >= totalPages} onClick={() => onFilters({ ...filters, page: data.page + 1 })}>下一页<ChevronRight aria-hidden="true" size={16} /></button>
      </nav>
    </section>
  );
}

function JobRow({ job, admin, onDelete }: { job: BatchJob; admin: boolean; onDelete: (job: BatchJob) => void }) {
  const status = batchStatus(job.status);
  const terminal = !["queued", "processing"].includes(job.status);
  return <li><Link to={`/batch/${job.id}`} aria-label={`查看识别任务 #${job.id}`}><span className="batch-job__icon"><Images aria-hidden="true" size={19} /></span><span className="batch-job__identity"><strong>任务 #{job.id}</strong><small>{admin ? `用户 #${job.user_id} · ` : ""}<time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString("zh-CN", { hour12: false })}</time></small></span><span className="batch-job__counts">{job.success_count + job.failure_count} / {job.image_total}<small>已处理</small></span><span className="batch-job__expiry"><Clock3 aria-hidden="true" size={14} />{new Date(job.expires_at).toLocaleDateString("zh-CN")} 到期</span></Link><span className="batch-job__actions" role="group" aria-label={`任务 #${job.id} 状态与操作`}><StatusBadge tone={status.tone}>{status.label}</StatusBadge>{terminal ? <button type="button" className="batch-job__delete" aria-label={`删除识别任务 #${job.id}`} onClick={() => onDelete(job)}><Trash2 aria-hidden="true" size={14} />删除</button> : null}</span></li>;
}
