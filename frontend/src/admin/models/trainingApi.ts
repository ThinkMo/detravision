import { api } from "../../api/client";
import type { ModelVersion, TrainingCatalog, TrainingJob, TrainingJobCreate, TrainingJobDetail, TrainingJobPage } from "../../api/types";

function idempotencyKey() {
  return globalThis.crypto.randomUUID();
}

export const trainingApi = {
  catalog: () => api.request<TrainingCatalog>("/api/training/catalog"),
  list: () => api.request<TrainingJobPage>("/api/training-jobs"),
  detail: (id: number) => api.request<TrainingJobDetail>(`/api/training-jobs/${id}`),
  log: (url: string) => api.requestText(url),
  create: (value: TrainingJobCreate, key = idempotencyKey()) => api.request<TrainingJob>("/api/training-jobs", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(value),
  }),
  stop: (id: number) => api.request<TrainingJob>(`/api/training-jobs/${id}/stop`, { method: "POST" }),
  resume: (id: number, key = idempotencyKey()) => api.request<TrainingJob>(`/api/training-jobs/${id}/resume`, { method: "POST", headers: { "Idempotency-Key": key } }),
  register: (id: number, name?: string) => api.request<ModelVersion>(`/api/training-jobs/${id}/register-model`, {
    method: "POST",
    body: JSON.stringify(name?.trim() ? { name: name.trim() } : {}),
  }),
};
