import { api } from "../../api/client";
import type { ModelVersion, TrainingCatalog, TrainingDataset, TrainingDatasetInput, TrainingJob, TrainingJobCreate, TrainingJobDetail, TrainingJobPage } from "../../api/types";

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
  log: (url: string) => api.requestText(url),
  datasetRoots: () => api.request<{ items: Array<{ alias: string }> }>("/api/training/dataset-roots"),
  datasetDirectories: (rootAlias: string, path: string) => api.request<{ items: Array<{ name: string; path: string }> }>(`/api/training/dataset-directories?root_alias=${encodeURIComponent(rootAlias)}&path=${encodeURIComponent(path)}`),
  datasets: () => api.request<{ items: TrainingDataset[] }>("/api/training/datasets"),
  createDataset: (value: TrainingDatasetInput) => api.request<TrainingDataset>("/api/training/datasets", { method: "POST", body: JSON.stringify(value) }),
  updateDataset: (id: number, value: Partial<TrainingDatasetInput> & { enabled?: boolean }) => api.request<TrainingDataset>(`/api/training/datasets/${id}`, { method: "PATCH", body: JSON.stringify(value) }),
  disableDataset: (id: number) => api.request<void>(`/api/training/datasets/${id}`, { method: "DELETE" }),
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
