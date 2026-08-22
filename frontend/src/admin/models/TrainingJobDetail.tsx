import { useQuery } from "@tanstack/react-query";
import type { TrainingJob, TrainingMetricSet } from "../../api/types";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { ModalDialog } from "../shared";
import { trainingApi } from "./trainingApi";

const percent = (value?: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

function MetricRow({ name, metrics }: { name: string; metrics: TrainingMetricSet }) {
  return <tr><td>{name}</td><td>{percent(metrics.precision)}</td><td>{percent(metrics.recall)}</td><td>{percent(metrics.map50)}</td><td>{percent(metrics.map50_95)}</td></tr>;
}

export function TrainingMetricsSummary({ job }: { job: TrainingJob }) {
  if (!job.metrics && job.map50 == null) return null;
  const classes = Object.entries(job.metrics?.classes ?? {});
  return <div className="training-metrics">
    <strong className={job.accepted ? "training-accepted" : "training-rejected"}>{job.accepted ? "已达标" : "未达标"}</strong>
    <dl><div><dt>mAP50</dt><dd>{percent(job.map50)}</dd></div><div><dt>mAP50-95</dt><dd>{percent(job.map50_95)}</dd></div><div><dt>未佩戴安全帽精确率</dt><dd>{percent(job.no_helmet_precision)}</dd></div><div><dt>未佩戴安全帽召回率</dt><dd>{percent(job.no_helmet_recall)}</dd></div></dl>
    {classes.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>类别</th><th>精确率</th><th>召回率</th><th>mAP50</th><th>mAP50-95</th></tr></thead><tbody>{classes.map(([name, metrics]) => <MetricRow key={name} name={name} metrics={metrics} />)}</tbody></table></div> : null}
  </div>;
}

export function TrainingJobDetail({ jobId, onClose }: { jobId: number; onClose: () => void }) {
  const query = useQuery({ queryKey: ["admin", "training-jobs", jobId], queryFn: () => trainingApi.detail(jobId) });
  const logUrl = query.data?.log_url ?? "";
  const log = useQuery({ queryKey: ["admin", "training-jobs", jobId, "log", logUrl], queryFn: () => trainingApi.log(logUrl), enabled: Boolean(logUrl) });
  return <ModalDialog title={`训练任务 #${jobId}`} onClose={onClose} className="training-detail-dialog">
    {query.isLoading ? <LoadingPanel label="正在加载训练详情" /> : query.isError || !query.data ? <ErrorPanel message="无法读取训练详情" onRetry={() => void query.refetch()} /> : <>
      <TrainingMetricsSummary job={query.data} />
      {query.data.confusion_matrix_url ? <img className="training-artifact" src={query.data.confusion_matrix_url} alt="测试集混淆矩阵" /> : null}
      <h3>最近日志</h3><pre className="training-log" aria-label="最近训练日志">{query.data.recent_log ?? query.data.log ?? (log.isLoading ? "正在加载日志" : log.isError ? "日志暂时不可用" : log.data ?? "暂无日志")}</pre>
    </>}
    <footer><button type="button" className="secondary-button" onClick={onClose}>关闭</button></footer>
  </ModalDialog>;
}
