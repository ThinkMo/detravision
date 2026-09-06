import { api } from "../../api/client";
import type { AnnotationBoxInput, DatasetValidationReport, ModelVersion, TrainingCatalog, TrainingDataset, TrainingDatasetImage, TrainingDatasetImageDetail, TrainingDatasetImagePage, TrainingDatasetInput, TrainingEffectAnalysis, TrainingJob, TrainingJobCreate, TrainingJobDetail, TrainingJobPage } from "../../api/types";

function idempotencyKey() {
  return globalThis.crypto.randomUUID();
}

const TRAINING_ERRORS: Record<string, string> = {
  weights_unavailable: "基础模型权重不可用，请先将对应 .pt 文件放入模型缓存目录。",
  cuda_oom: "GPU 显存不足，请降低批大小、输入尺寸或改用更小模型。",
  dataset_invalid: "训练集不可用，请检查训练、验证、测试目录及标签配置。",
  training_failed: "训练失败，请查看任务日志中的安全诊断信息。",
  evaluation_failed: "测试集评估失败，请检查测试集标注和任务日志。",
  interrupted: "训练进程已中断，可在检查环境后尝试恢复。",
  worker_exit: "训练进程异常退出，请检查 Python、PyTorch 和 Ultralytics 环境。",
  training_config_invalid: "高级训练参数无效，请根据字段提示修正后重试。",
  training_profile_invalid: "训练预设不可用，请重新选择预设。",
  acceptance_class_unknown: "验收规则包含训练集中不存在的类别。",
};

export function trainingErrorMessage(code?: string | null, fallback?: string | null) {
  if (code && TRAINING_ERRORS[code]) return TRAINING_ERRORS[code];
  return fallback && /[\u3400-\u9fff]/u.test(fallback)
    ? fallback
    : "训练失败，请查看任务日志或检查训练环境。";
}

export const trainingApi = {
  catalog: () => api.request<TrainingCatalog>("/api/training/catalog"),
  list: () => api.request<TrainingJobPage>("/api/training-jobs"),
  detail: (id: number) => api.request<TrainingJobDetail>(`/api/training-jobs/${id}`),
  cloneTemplate: (id: number) => api.request<TrainingJobCreate>(`/api/training-jobs/${id}/clone-template`),
  log: (url: string) => api.requestText(url),
  analysis: (url: string) => api.request<TrainingEffectAnalysis>(url),
  datasetRoots: () => api.request<{ items: Array<{ alias: string }> }>("/api/training/dataset-roots"),
  datasetDirectories: (rootAlias: string, path: string) => api.request<{ items: Array<{ name: string; path: string }> }>(`/api/training/dataset-directories?root_alias=${encodeURIComponent(rootAlias)}&path=${encodeURIComponent(path)}`),
  datasets: () => api.request<{ items: TrainingDataset[] }>("/api/training/datasets"),
  createDataset: (value: TrainingDatasetInput) => api.request<TrainingDataset>("/api/training/datasets", { method: "POST", body: JSON.stringify(value) }),
  updateDataset: (id: number, value: Partial<TrainingDatasetInput> & { enabled?: boolean }) => api.request<TrainingDataset>(`/api/training/datasets/${id}`, { method: "PATCH", body: JSON.stringify(value) }),
  disableDataset: (id: number) => api.request<void>(`/api/training/datasets/${id}`, { method: "DELETE" }),
  cloneDataset: (id: number) => api.request<TrainingDataset>(`/api/training/datasets/${id}/versions`, { method: "POST" }),
  datasetVersions: (id: number) => api.request<{ items: TrainingDataset[] }>(`/api/training/datasets/${id}/versions`),
  rescanDataset: (id: number, split: "train" | "val" | "test" | null = null) => api.request<{ created: number; updated: number; removed: number }>(`/api/training/datasets/${id}/rescan`, { method: "POST", body: JSON.stringify({ split }) }),
  datasetImages: (id: number, split: "train" | "val" | "test", status?: string, page = 1) => api.request<TrainingDatasetImagePage>(`/api/training/datasets/${id}/images?split=${split}&page=${page}&page_size=50${status ? `&status=${encodeURIComponent(status)}` : ""}`),
  datasetImage: (datasetId: number, imageId: number) => api.request<TrainingDatasetImageDetail>(`/api/training/datasets/${datasetId}/images/${imageId}`),
  saveAnnotations: (datasetId: number, imageId: number, revision: number, boxes: AnnotationBoxInput[]) => api.request<TrainingDatasetImage>(`/api/training/datasets/${datasetId}/images/${imageId}/annotations`, { method: "PUT", body: JSON.stringify({ revision, boxes }) }),
  confirmEmpty: (datasetId: number, imageId: number, revision: number) => api.request<TrainingDatasetImageDetail>(`/api/training/datasets/${datasetId}/images/${imageId}/confirm-empty`, { method: "POST", body: JSON.stringify({ revision }) }),
  validateDataset: (id: number) => api.request<DatasetValidationReport>(`/api/training/datasets/${id}/validate`, { method: "POST" }),
  publishDataset: (id: number, revision: number, confirmWarnings: boolean) => api.request<TrainingDataset>(`/api/training/datasets/${id}/publish`, { method: "POST", body: JSON.stringify({ revision, confirm_warnings: confirmWarnings }) }),
  addDatasetClass: (id: number, revision: number, name: string) => api.request<TrainingDataset>(`/api/training/datasets/${id}/classes`, { method: "POST", body: JSON.stringify({ revision, name }) }),
  create: (value: TrainingJobCreate, key = idempotencyKey()) => api.request<TrainingJob>("/api/training-jobs", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(value),
  }),
  stop: (id: number) => api.request<TrainingJob>(`/api/training-jobs/${id}/stop`, { method: "POST" }),
  delete: (id: number) => api.request<void>(`/api/training-jobs/${id}`, { method: "DELETE" }),
  resume: (id: number, key = idempotencyKey()) => api.request<TrainingJob>(`/api/training-jobs/${id}/resume`, { method: "POST", headers: { "Idempotency-Key": key } }),
  register: (id: number, name?: string) => api.request<ModelVersion>(`/api/training-jobs/${id}/register-model`, {
    method: "POST",
    body: JSON.stringify(name?.trim() ? { name: name.trim() } : {}),
  }),
};
