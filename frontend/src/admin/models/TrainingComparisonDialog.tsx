import { useQueries } from "@tanstack/react-query";
import type { TrainingAcceptanceMetric, TrainingJob, TrainingJobDetail, TrainingMetricSet } from "../../api/types";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { ModalDialog } from "../shared";
import { trainingApi } from "./trainingApi";

const percent = (value?: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

const metricLabel = (key: keyof TrainingMetricSet) => key === "precision"
  ? "精确率"
  : key === "recall"
    ? "召回率"
    : key === "map50"
      ? "mAP50"
      : "mAP50-95";

function commonClasses(jobs: TrainingJobDetail[]) {
  if (!jobs.length) return [];
  const first = Object.keys(jobs[0].metrics?.classes ?? {});
  return first.filter((name) => jobs.every((job) => Object.hasOwn(job.metrics?.classes ?? {}, name)));
}

function classMetric(job: TrainingJobDetail, className: string, key: keyof TrainingMetricSet) {
  return percent(job.metrics?.classes?.[className]?.[key]);
}

type AcceptanceRow = { key: string; label: string; scope: "overall" | "class"; className?: string; metric: TrainingAcceptanceMetric };

function acceptanceRows(jobs: TrainingJobDetail[]): AcceptanceRow[] {
  const rows = new Map<string, AcceptanceRow>();
  jobs.forEach((job) => {
    Object.keys(job.acceptance_config?.overall ?? {}).forEach((metric) => rows.set(`overall:${metric}`, {
      key: `overall:${metric}`, label: `验收 总体 ${metric}`, scope: "overall", metric: metric as TrainingAcceptanceMetric,
    }));
    Object.entries(job.acceptance_config?.classes ?? {}).forEach(([className, thresholds]) => {
      Object.keys(thresholds).forEach((metric) => rows.set(`class:${className}:${metric}`, {
        key: `class:${className}:${metric}`, label: `验收 ${className} ${metric}`, scope: "class", className, metric: metric as TrainingAcceptanceMetric,
      }));
    });
  });
  return Array.from(rows.values()).sort((left, right) => left.key.localeCompare(right.key));
}

function acceptanceValue(job: TrainingJobDetail, row: AcceptanceRow) {
  const value = row.scope === "overall"
    ? job.acceptance_config?.overall?.[row.metric]
    : job.acceptance_config?.classes?.[row.className ?? ""]?.[row.metric];
  return percent(value);
}

export function TrainingComparisonDialog({ jobs, legacyFallback, onClose }: { jobs: TrainingJob[]; legacyFallback: boolean; onClose: () => void }) {
  const queries = useQueries({ queries: jobs.map((job) => ({ queryKey: ["admin", "training-jobs", job.id], queryFn: () => trainingApi.detail(job.id) })) });
  const details = queries.map((query) => query.data).filter((job): job is TrainingJobDetail => Boolean(job));
  const loading = queries.some((query) => query.isLoading);
  const failed = queries.some((query) => query.isError);
  const classes = commonClasses(details);
  const configKeys = Array.from(new Set(details.flatMap((job) => Object.keys(job.advanced_config ?? {})))).sort();
  const acceptance = acceptanceRows(details);
  const metricKeys: Array<keyof TrainingMetricSet> = ["precision", "recall", "map50", "map50_95"];

  return <ModalDialog title="训练实验对比" onClose={onClose} className="training-comparison-dialog">
    {legacyFallback ? <p className="training-warning" role="status">历史任务缺少训练集摘要，本次仅按相同训练集标识进行对比。</p> : null}
    {loading ? <LoadingPanel label="正在加载实验详情" /> : failed || details.length !== jobs.length
      ? <ErrorPanel message="无法读取全部实验详情" onRetry={() => queries.forEach((query) => void query.refetch())} />
      : <div className="admin-table-wrap" role="region" aria-label="训练实验指标对比" tabIndex={0}>
        <table className="admin-table training-comparison-table">
          <thead><tr><th>指标</th>{details.map((job) => <th key={job.id}>任务 #{job.id}</th>)}</tr></thead>
          <tbody>
            <tr><th>基础模型</th>{details.map((job) => <td key={job.id}>{job.base_model}</td>)}</tr>
            <tr><th>输入尺寸</th>{details.map((job) => <td key={job.id}>{job.imgsz}</td>)}</tr>
            <tr><th>训练轮次</th>{details.map((job) => <td key={job.id}>{job.epochs}</td>)}</tr>
            <tr><th>批次</th>{details.map((job) => <td key={job.id}>{job.batch}</td>)}</tr>
            <tr><th>任务状态</th>{details.map((job) => <td key={job.id}>{job.status} · {job.accepted ? "已达标" : "未达标"}</td>)}</tr>
            <tr><th>训练预设</th>{details.map((job) => <td key={job.id}>{job.training_profile ?? "legacy"}</td>)}</tr>
            {metricKeys.map((key) => <tr key={`overall-${key}`}><th>总体 {metricLabel(key)}</th>{details.map((job) => <td key={job.id}>{percent(job.metrics?.[key] ?? job[key])}</td>)}</tr>)}
            {classes.flatMap((className) => metricKeys.map((key) => <tr key={`${className}-${key}`}><th>{className} {metricLabel(key)}</th>{details.map((job) => <td key={job.id}>{classMetric(job, className, key)}</td>)}</tr>))}
            {configKeys.map((key) => <tr key={`config-${key}`}><th>参数 {key}</th>{details.map((job) => <td key={job.id}>{String(job.advanced_config?.[key] ?? "—")}</td>)}</tr>)}
            {acceptance.map((row) => <tr key={row.key}><th>{row.label}</th>{details.map((job) => <td key={job.id}>{acceptanceValue(job, row)}</td>)}</tr>)}
          </tbody>
        </table>
      </div>}
    <footer><button type="button" className="secondary-button" onClick={onClose}>关闭</button></footer>
  </ModalDialog>;
}
