import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TrainingJob, TrainingJobCreate } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminNotice, ConfirmDialog, errorMessage } from "../shared";
import { TrainingJobDetail, TrainingMetricsSummary } from "./TrainingJobDetail";
import { TrainingComparisonDialog } from "./TrainingComparisonDialog";
import { trainingApi, trainingErrorMessage } from "./trainingApi";

const ACTIVE = new Set(["queued", "running", "evaluating", "stopping"]);
const RESUMABLE = new Set(["stopped", "interrupted", "failed"]);

export function trainingPollingInterval(query: { state: { data?: { items: Array<{ status: TrainingJob["status"] }> } } }) {
  return query.state.data?.items.some((job) => ACTIVE.has(job.status)) ? 2000 : false;
}

const statusLabel: Record<TrainingJob["status"], string> = { queued: "排队中", running: "训练中", evaluating: "评估中", stopping: "正在停止", stopped: "已停止", completed: "已完成", failed: "失败", interrupted: "已中断", cancelled: "已取消" };

export function TrainingJobsPanel({ onRegistered, onClone, supportedConfigVersion }: { onRegistered: () => void; onClone: (value: TrainingJobCreate) => void; supportedConfigVersion: number }) {
  const client = useQueryClient();
  const [detailId, setDetailId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState<TrainingJob | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [comparing, setComparing] = useState<TrainingJob[] | null>(null);
  const [compareError, setCompareError] = useState("");
  const query = useQuery({ queryKey: ["admin", "training-jobs"], queryFn: trainingApi.list, refetchInterval: trainingPollingInterval });
  const refresh = () => void client.invalidateQueries({ queryKey: ["admin", "training-jobs"] });
  const stop = useMutation({ mutationFn: trainingApi.stop, onSuccess: () => { setMessage("已提交停止请求"); refresh(); } });
  const resume = useMutation({ mutationFn: (id: number) => trainingApi.resume(id), onSuccess: () => { setMessage("已创建恢复任务"); refresh(); } });
  const register = useMutation({ mutationFn: (id: number) => trainingApi.register(id), onSuccess: () => { setMessage("候选模型已注册，可在模型版本中激活"); refresh(); onRegistered(); } });
  const remove = useMutation({ mutationFn: trainingApi.delete, onSuccess: () => { setDeleting(null); setMessage("训练任务已删除"); refresh(); } });
  const clone = useMutation({ mutationFn: trainingApi.cloneTemplate, onSuccess: (value) => onClone(value) });
  const resetActions = () => { stop.reset(); resume.reset(); register.reset(); remove.reset(); setMessage(""); };
  const error = stop.isError ? errorMessage(stop.error, "停止失败") : resume.isError ? errorMessage(resume.error, "恢复失败") : register.isError ? errorMessage(register.error, "注册失败") : clone.isError ? errorMessage(clone.error, "读取复制模板失败") : "";

  if (query.isLoading) return <LoadingPanel label="正在加载训练任务" />;
  if (query.isError || !query.data) return <ErrorPanel message="无法读取训练任务" onRetry={() => void query.refetch()} />;
  const selectedJobs = query.data.items.filter((job) => selected.includes(job.id));
  const compareSelected = () => {
    if (selectedJobs.length < 2) { setCompareError("请至少选择 2 个训练任务。"); return; }
    const digests = selectedJobs.map((job) => job.dataset_manifest_digest);
    const allDigests = digests.every((value): value is string => Boolean(value));
    const legacy = digests.every((value) => !value);
    const sameDigest = allDigests && new Set(digests).size === 1;
    const sameLegacyKey = legacy && selectedJobs.every((job) => job.dataset_key && job.dataset_key === selectedJobs[0].dataset_key);
    if (!sameDigest && !sameLegacyKey) { setCompareError("所选任务的训练集版本不同，不能直接对比。"); return; }
    setCompareError(""); setComparing(selectedJobs);
  };
  return <div className="training-jobs">
    <AdminNotice message={error || message} error={Boolean(error)} />
    {query.data.items.length === 0 ? <EmptyState message="暂无训练任务" /> : <><div className="training-comparison-toolbar"><span>已选择 {selected.length}/4 个任务</span><button type="button" className="dataset-action dataset-action--secondary" onClick={compareSelected}>对比所选任务</button></div>{compareError ? <p className="admin-dialog__error" role="alert">{compareError}</p> : null}<div className="training-job-list">{query.data.items.map((job) => {
      const maximum = Math.max(job.total_epochs, 1); const current = Math.min(job.current_epoch, maximum); const progress = Math.round(current / maximum * 100);
      const futureConfig = (job.config_schema_version ?? 1) > supportedConfigVersion;
      return <article className="training-job-card" key={job.id}>
        <header><label className="training-job-selector"><input type="checkbox" aria-label={`选择任务 ${job.id}`} checked={selected.includes(job.id)} disabled={!selected.includes(job.id) && selected.length >= 4} onChange={(event) => { setCompareError(""); setSelected(event.target.checked ? [...selected, job.id] : selected.filter((id) => id !== job.id)); }} /><span className="sr-only">选择用于实验对比</span></label><div><strong>任务 #{job.id}</strong><span>{job.base_model} · {job.imgsz}</span></div><span className={`training-status training-status--${job.status}`}>{statusLabel[job.status]}</span></header>
        <div className="training-progress"><progress aria-label={`任务 ${job.id} 训练进度`} value={current} max={maximum} aria-valuemin={0} aria-valuemax={maximum} aria-valuenow={current} /><span>{job.current_epoch}/{job.total_epochs}（{progress}%）</span></div>
        <TrainingMetricsSummary job={job} />
        {futureConfig ? <p className="training-schema-warning" role="status">配置版本 v{job.config_schema_version} 高于当前支持的 v{supportedConfigVersion}，仅可查看</p> : null}
        {job.runtime_state === "heartbeat_delayed" ? <p className="training-schema-warning" role="status">训练进程心跳延迟，请检查主机负载。</p> : null}
        {job.runtime_state === "disconnected" ? <p className="training-error" role="alert">训练进程已失联，调度器将在宽限期后安全中断任务。</p> : null}
        {job.error_code || job.error_message ? <p className="training-error">{trainingErrorMessage(job.error_code, job.error_message)}</p> : null}
        <footer>
          <button type="button" className="table-action" onClick={() => setDetailId(job.id)}>查看详情</button>
          <button type="button" className="table-action" aria-label={`复制任务 ${job.id}`} disabled={clone.isPending || futureConfig} title={futureConfig ? "配置版本过新，不能复制" : undefined} onClick={() => { clone.reset(); clone.mutate(job.id); }}>复制为新任务</button>
          {ACTIVE.has(job.status) ? <button type="button" className="table-action table-action--danger" disabled={stop.isPending} aria-label={`停止任务 ${job.id}`} onClick={() => { resetActions(); stop.mutate(job.id); }}>停止</button> : null}
          {RESUMABLE.has(job.status) && job.has_last ? <button type="button" className="table-action" aria-label={`恢复任务 ${job.id}`} disabled={resume.isPending || futureConfig} title={futureConfig ? "配置版本过新，不能恢复" : undefined} onClick={() => { resetActions(); resume.mutate(job.id); }}>恢复</button> : null}
          {job.status === "completed" && job.has_best ? <button type="button" className="table-action" aria-label={`注册任务 ${job.id} 模型`} disabled={register.isPending} onClick={() => { resetActions(); register.mutate(job.id); }}>注册候选模型</button> : null}
          {!ACTIVE.has(job.status) ? <button type="button" className="table-action table-action--danger" aria-label={`删除训练任务 ${job.id}`} onClick={() => { resetActions(); setDeleting(job); }}>删除</button> : null}
        </footer>
      </article>;
    })}</div></>}
    {detailId != null ? <TrainingJobDetail jobId={detailId} supportedConfigVersion={supportedConfigVersion} onClone={(id) => { setDetailId(null); clone.mutate(id); }} onClose={() => setDetailId(null)} /> : null}
    {comparing ? <TrainingComparisonDialog jobs={comparing} legacyFallback={comparing.every((job) => !job.dataset_manifest_digest)} onClose={() => setComparing(null)} /> : null}
    {deleting ? <ConfirmDialog title="删除训练任务" confirmLabel="确认删除" pending={remove.isPending} error={remove.isError ? errorMessage(remove.error, "删除失败") : ""} onCancel={() => setDeleting(null)} onConfirm={() => remove.mutate(deleting.id)}><p>任务 #{deleting.id} 的日志、权重和评估制品将被永久删除。已注册模型的任务不能删除。</p></ConfirmDialog> : null}
  </div>;
}
