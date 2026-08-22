import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TrainingJob } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminNotice, ConfirmDialog, errorMessage } from "../shared";
import { TrainingJobDetail, TrainingMetricsSummary } from "./TrainingJobDetail";
import { trainingApi, trainingErrorMessage } from "./trainingApi";

const ACTIVE = new Set(["queued", "running", "evaluating", "stopping"]);
const RESUMABLE = new Set(["stopped", "interrupted", "failed"]);

export function trainingPollingInterval(query: { state: { data?: { items: Array<{ status: TrainingJob["status"] }> } } }) {
  return query.state.data?.items.some((job) => ACTIVE.has(job.status)) ? 2000 : false;
}

const statusLabel: Record<TrainingJob["status"], string> = { queued: "排队中", running: "训练中", evaluating: "评估中", stopping: "正在停止", stopped: "已停止", completed: "已完成", failed: "失败", interrupted: "已中断", cancelled: "已取消" };

export function TrainingJobsPanel({ onRegistered }: { onRegistered: () => void }) {
  const client = useQueryClient();
  const [detailId, setDetailId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState<TrainingJob | null>(null);
  const query = useQuery({ queryKey: ["admin", "training-jobs"], queryFn: trainingApi.list, refetchInterval: trainingPollingInterval });
  const refresh = () => void client.invalidateQueries({ queryKey: ["admin", "training-jobs"] });
  const stop = useMutation({ mutationFn: trainingApi.stop, onSuccess: () => { setMessage("已提交停止请求"); refresh(); } });
  const resume = useMutation({ mutationFn: (id: number) => trainingApi.resume(id), onSuccess: () => { setMessage("已创建恢复任务"); refresh(); } });
  const register = useMutation({ mutationFn: (id: number) => trainingApi.register(id), onSuccess: () => { setMessage("候选模型已注册，可在模型版本中激活"); refresh(); onRegistered(); } });
  const remove = useMutation({ mutationFn: trainingApi.delete, onSuccess: () => { setDeleting(null); setMessage("训练任务已删除"); refresh(); } });
  const resetActions = () => { stop.reset(); resume.reset(); register.reset(); remove.reset(); setMessage(""); };
  const error = stop.isError ? errorMessage(stop.error, "停止失败") : resume.isError ? errorMessage(resume.error, "恢复失败") : register.isError ? errorMessage(register.error, "注册失败") : "";

  if (query.isLoading) return <LoadingPanel label="正在加载训练任务" />;
  if (query.isError || !query.data) return <ErrorPanel message="无法读取训练任务" onRetry={() => void query.refetch()} />;
  return <div className="training-jobs">
    <AdminNotice message={error || message} error={Boolean(error)} />
    {query.data.items.length === 0 ? <EmptyState message="暂无训练任务" /> : <div className="training-job-list">{query.data.items.map((job) => {
      const maximum = Math.max(job.total_epochs, 1); const current = Math.min(job.current_epoch, maximum); const progress = Math.round(current / maximum * 100);
      return <article className="training-job-card" key={job.id}>
        <header><div><strong>任务 #{job.id}</strong><span>{job.base_model} · {job.imgsz}</span></div><span className={`training-status training-status--${job.status}`}>{statusLabel[job.status]}</span></header>
        <div className="training-progress"><progress aria-label={`任务 ${job.id} 训练进度`} value={current} max={maximum} aria-valuemin={0} aria-valuemax={maximum} aria-valuenow={current} /><span>{job.current_epoch}/{job.total_epochs}（{progress}%）</span></div>
        <TrainingMetricsSummary job={job} />
        {job.error_code || job.error_message ? <p className="training-error">{trainingErrorMessage(job.error_code, job.error_message)}</p> : null}
        <footer>
          <button type="button" className="table-action" onClick={() => setDetailId(job.id)}>查看详情</button>
          {ACTIVE.has(job.status) ? <button type="button" className="table-action table-action--danger" disabled={stop.isPending} aria-label={`停止任务 ${job.id}`} onClick={() => { resetActions(); stop.mutate(job.id); }}>停止</button> : null}
          {RESUMABLE.has(job.status) && job.has_last ? <button type="button" className="table-action" aria-label={`恢复任务 ${job.id}`} disabled={resume.isPending} onClick={() => { resetActions(); resume.mutate(job.id); }}>恢复</button> : null}
          {job.status === "completed" && job.has_best ? <button type="button" className="table-action" aria-label={`注册任务 ${job.id} 模型`} disabled={register.isPending} onClick={() => { resetActions(); register.mutate(job.id); }}>注册候选模型</button> : null}
          {!ACTIVE.has(job.status) ? <button type="button" className="table-action table-action--danger" aria-label={`删除训练任务 ${job.id}`} onClick={() => { resetActions(); setDeleting(job); }}>删除</button> : null}
        </footer>
      </article>;
    })}</div>}
    {detailId != null ? <TrainingJobDetail jobId={detailId} onClose={() => setDetailId(null)} /> : null}
    {deleting ? <ConfirmDialog title="删除训练任务" confirmLabel="确认删除" pending={remove.isPending} error={remove.isError ? errorMessage(remove.error, "删除失败") : ""} onCancel={() => setDeleting(null)} onConfirm={() => remove.mutate(deleting.id)}><p>任务 #{deleting.id} 的日志、权重和评估制品将被永久删除。已注册模型的任务不能删除。</p></ConfirmDialog> : null}
  </div>;
}
