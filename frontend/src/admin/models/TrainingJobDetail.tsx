import { useQuery } from "@tanstack/react-query";
import type { TrainingAcceptanceCheck, TrainingJob, TrainingJobDetail as TrainingJobDetailType, TrainingMetricSet } from "../../api/types";
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
    <dl><div><dt>总体精确率</dt><dd>{percent(job.metrics?.precision ?? job.precision)}</dd></div><div><dt>总体召回率</dt><dd>{percent(job.metrics?.recall ?? job.recall)}</dd></div><div><dt>mAP50</dt><dd>{percent(job.metrics?.map50 ?? job.map50)}</dd></div><div><dt>mAP50-95</dt><dd>{percent(job.metrics?.map50_95 ?? job.map50_95)}</dd></div></dl>
    {classes.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>类别</th><th>精确率</th><th>召回率</th><th>mAP50</th><th>mAP50-95</th></tr></thead><tbody>{classes.map(([name, metrics]) => <MetricRow key={name} name={name} metrics={metrics} />)}</tbody></table></div> : null}
  </div>;
}

const checkReason = (check: TrainingAcceptanceCheck) => check.passed
  ? "通过"
  : check.reason === "metric_missing"
    ? "指标缺失"
    : check.reason === "threshold_not_met" || check.reason === "below_threshold"
      ? "低于门槛"
      : check.reason;

function JobConfiguration({ job }: { job: TrainingJobDetailType }) {
  const advanced = Object.entries(job.advanced_config ?? {});
  const checks = job.acceptance_checks ?? [];
  const acceptance = job.acceptance_config;
  const thresholds = [
    ...Object.entries(acceptance?.overall ?? {}).filter((entry) => entry[1] != null).map(([metric, value]) => ({ label: `总体 ${metric}`, value })),
    ...Object.entries(acceptance?.classes ?? {}).flatMap(([className, metrics]) => Object.entries(metrics).filter((entry) => entry[1] != null).map(([metric, value]) => ({ label: `${className} ${metric}`, value }))),
  ];
  return <div className="training-detail-config">
    <h3>训练配置</h3>
    <dl><div><dt>训练集</dt><dd>{job.dataset_key ?? "—"}</dd></div><div><dt>基础模型</dt><dd>{job.base_model}</dd></div><div><dt>输入尺寸</dt><dd>{job.imgsz}</dd></div><div><dt>轮次</dt><dd>{job.epochs}</dd></div><div><dt>批次</dt><dd>{job.batch}</dd></div><div><dt>设备</dt><dd>{job.device}</dd></div><div><dt>早停耐心</dt><dd>{job.patience}</dd></div><div><dt>预设</dt><dd>{job.training_profile === "legacy" || !job.training_profile ? "传统默认配置" : job.training_profile}</dd></div><div><dt>Schema</dt><dd>v{job.config_schema_version ?? 1}</dd></div>{advanced.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl>
    <h3>验收配置</h3>
    {thresholds.length ? <dl>{thresholds.map((threshold) => <div key={threshold.label}><dt>{threshold.label}</dt><dd>{percent(threshold.value)}</dd></div>)}</dl> : <p>未设置验收门槛。</p>}
    <h3>验收结果</h3>
    {checks.length ? <ul className="training-acceptance-checks">{checks.map((check, index) => <li key={`${check.scope}-${check.class_name}-${check.metric}-${index}`} className={check.passed ? "training-accepted" : "training-rejected"}><strong>{check.scope === "class" ? `${check.class_name} ` : "总体 "}{check.metric}</strong><span>门槛 {percent(check.threshold)} · 实际 {percent(check.actual)} · {checkReason(check)}</span></li>)}</ul> : <p>此任务没有逐项验收记录。</p>}
  </div>;
}

export function TrainingJobDetail({ jobId, onClone, onClose, supportedConfigVersion = 1 }: { jobId: number; onClone?: (id: number) => void; onClose: () => void; supportedConfigVersion?: number }) {
  const query = useQuery({ queryKey: ["admin", "training-jobs", jobId], queryFn: () => trainingApi.detail(jobId) });
  const logUrl = query.data?.log_url ?? "";
  const analysisUrl = query.data?.analysis_url ?? "";
  const log = useQuery({ queryKey: ["admin", "training-jobs", jobId, "log", logUrl], queryFn: () => trainingApi.log(logUrl), enabled: Boolean(logUrl) });
  const analysis = useQuery({ queryKey: ["admin", "training-jobs", jobId, "analysis", analysisUrl], queryFn: () => trainingApi.analysis(analysisUrl), enabled: Boolean(analysisUrl) });
  return <ModalDialog title={`训练任务 #${jobId}`} onClose={onClose} className="training-detail-dialog">
    {query.isLoading ? <LoadingPanel label="正在加载训练详情" /> : query.isError || !query.data ? <ErrorPanel message="无法读取训练详情" onRetry={() => void query.refetch()} /> : <>
      <TrainingMetricsSummary job={query.data} />
      <JobConfiguration job={query.data} />
      {analysis.data && Object.keys(analysis.data.classes).length ? <div className="training-effect-analysis"><h3>置信度与误检漏检分析</h3><div className="admin-table-wrap"><table className="admin-table" aria-label="置信度与误检漏检分析"><thead><tr><th>类别</th><th>建议置信度</th><th>最佳 F1</th><th>误检</th><th>漏检</th></tr></thead><tbody>{Object.entries(analysis.data.classes).map(([name, value]) => <tr key={name}><td>{name}</td><td>{percent(value.recommended_confidence)}</td><td>{percent(value.best_f1)}</td><td>{value.false_positives ?? "—"}</td><td>{value.false_negatives ?? "—"}</td></tr>)}</tbody></table></div><p className="training-form__status">建议置信度来自测试集 F1 峰值；上线阈值仍应结合漏检成本调整。</p></div> : null}
      {query.data.confusion_matrix_url ? <img className="training-artifact" src={query.data.confusion_matrix_url} alt="测试集混淆矩阵" /> : null}
      {query.data.results_curve_url ? <img className="training-artifact" src={query.data.results_curve_url} alt="训练结果曲线" /> : null}
      {Object.entries(query.data.evaluation_curve_urls ?? {}).map(([kind, url]) => url ? <img key={kind} className="training-artifact" src={url} alt={`${kind.toUpperCase()} 评估曲线`} /> : null)}
      <h3>最近日志</h3><pre className="training-log" aria-label="最近训练日志">{query.data.recent_log ?? query.data.log ?? (log.isLoading ? "正在加载日志" : log.isError ? "日志暂时不可用" : log.data ?? "暂无日志")}</pre>
    </>}
    <footer>{onClone ? <button type="button" disabled={(query.data?.config_schema_version ?? 1) > supportedConfigVersion} title={(query.data?.config_schema_version ?? 1) > supportedConfigVersion ? "配置版本过新，不能复制" : undefined} onClick={() => onClone(jobId)}>复制为新任务</button> : null}<button type="button" className="secondary-button" onClick={onClose}>关闭</button></footer>
  </ModalDialog>;
}
