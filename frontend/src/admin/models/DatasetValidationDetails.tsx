import { ApiError } from "../../api/client";
import type { DatasetValidationReport } from "../../api/types";

const ERROR_MESSAGES: Record<string, string> = {
  dataset_validation_failed: "校验未通过，请完成待核验图片并处理错误后重试。",
  dataset_warnings_unconfirmed: "存在尚未确认的校验警告，请检查后再发布。",
  dataset_conflict: "数据集版本已变化，请刷新训练集列表后重试。",
  annotation_conflict: "标注版本已变化，请重新加载当前图片后重试。",
  dataset_published: "该版本已经发布，请刷新列表查看。",
  dataset_not_found: "数据集不存在，请刷新列表后重试。",
  dataset_invalid: "数据集不可用，请检查目录与类别配置。",
  dataset_copy_failed: "训练集复制失败，请检查目录权限和剩余空间。",
  dataset_scan_failed: "图片扫描失败，请检查图片文件和目录权限。",
  annotation_invalid: "标注格式或审核状态有误，请检查图片标签。",
  annotation_write_failed: "标注保存失败，请检查目录写入权限后重试。",
};
const ISSUE_NAMES: Record<string, string> = {
  annotation_pending: "图片尚未核验", annotation_invalid: "标签格式或审核状态错误",
  file_missing: "图片文件缺失", file_changed: "图片内容或尺寸已变化",
  rescan_required: "图片尚未扫描入库", dataset_scan_failed: "数据集扫描失败",
  person_head_unlabeled: "人员头部可能漏标", helmet_state_overlap: "戴帽与未戴帽框重叠",
  class_sparse: "类别样本偏少", class_split_imbalance: "分区类别分布不均衡",
};

export function datasetErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError && error.code && ERROR_MESSAGES[error.code]) return ERROR_MESSAGES[error.code];
  return error instanceof Error && /[\u3400-\u9fff]/u.test(error.message) ? error.message : fallback;
}

export function validationFromError(error: unknown) {
  return error instanceof ApiError ? error.validation : null;
}

export function datasetValidationSummary(report: DatasetValidationReport) {
  const pending = report.errors.filter(issue => issue.code === "annotation_pending").length;
  return `待核验 ${pending} 张，其他错误 ${report.errors.length - pending} 项，警告 ${report.warnings.length} 项。`;
}
export function DatasetValidationDetails({ report }: { report: DatasetValidationReport }) {
  const pending = new Map<string, number>();
  const other = new Map<string, number>();
  const warnings = new Map<string, number>();
  for (const issue of report.errors) {
    if (issue.code === "annotation_pending") pending.set(issue.split ?? "unknown", (pending.get(issue.split ?? "unknown") ?? 0) + 1);
    else other.set(issue.code, (other.get(issue.code) ?? 0) + 1);
  }
  for (const issue of report.warnings) warnings.set(issue.code, (warnings.get(issue.code) ?? 0) + 1);
  const names: Record<string, string> = { train: "训练集", val: "验证集", test: "测试集", unknown: "未指定分区" };
  return <section className="dataset-validation-details" aria-label="数据集校验明细">
    <p>{datasetValidationSummary(report)}</p>
    {pending.size ? <><ul>{[...pending].map(([split, count]) => <li key={split}>{`${names[split] ?? split}：${count} 张待核验`}</li>)}</ul><p>在标注页切换到对应分区，并筛选“待标注”继续核验。</p></> : null}
    {other.size ? <ul>{[...other].map(([code, count]) => <li key={code}>{`${ISSUE_NAMES[code] ?? "标签校验错误"}：${count} 项`}</li>)}</ul> : null}
    {warnings.size ? <ul>{[...warnings].map(([code, count]) => <li key={code}>{`${ISSUE_NAMES[code] ?? "数据质量警告"}：${count} 项`}</li>)}</ul> : null}
  </section>;
}