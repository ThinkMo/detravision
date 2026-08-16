import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { ApiErrorBody, BatchJob, BatchJobDetail, BatchJobPage } from "../api/types";

export interface BatchJobFilters {
  page?: number;
  pageSize?: number;
  status?: string;
  createdFrom?: string;
  createdTo?: string;
  userId?: string;
}

function endOfUtcDay(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? `${value}T23:59:59.999Z`
    : new Date(value).toISOString();
}

export function batchJobsPath(filters: BatchJobFilters) {
  const params = new URLSearchParams();
  params.set("page", String(filters.page ?? 1));
  params.set("page_size", String(filters.pageSize ?? 20));
  if (filters.status) params.set("status", filters.status);
  if (filters.createdFrom) params.set("created_from", new Date(filters.createdFrom).toISOString());
  if (filters.createdTo) params.set("created_to", endOfUtcDay(filters.createdTo));
  if (filters.userId) params.set("user_id", filters.userId);
  return `/api/batch-jobs?${params.toString()}`;
}

export function useBatchJobs(filters: BatchJobFilters) {
  return useQuery({
    queryKey: ["batch-jobs", filters],
    queryFn: () => api.request<BatchJobPage>(batchJobsPath(filters)),
  });
}

const activeStatuses = new Set(["queued", "processing"]);

export function useBatchJob(jobId: number | null) {
  return useQuery({
    queryKey: ["batch-job", jobId],
    enabled: jobId !== null,
    queryFn: () => api.request<BatchJobDetail>(`/api/batch-jobs/${jobId}`),
    refetchInterval: (query) => activeStatuses.has(query.state.data?.status ?? "") ? 1_000 : false,
  });
}

export function createBatchJob(files: File[], onProgress: (percent: number) => void): Promise<BatchJob> {
  const data = new FormData();
  files.forEach((file) => data.append("files", file, file.name));
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/batch-jobs");
    request.withCredentials = true;
    request.responseType = "json";
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    request.addEventListener("load", () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress(100);
        resolve(request.response as BatchJob);
        return;
      }
      const body = request.response && typeof request.response === "object" ? request.response as ApiErrorBody : {};
      reject(new ApiError(request.status, body));
    });
    request.addEventListener("error", () => reject(new Error("上传失败，请检查网络后重试")));
    request.addEventListener("abort", () => reject(new Error("上传已取消")));
    request.send(data);
  });
}

export function safeResultUrl(jobId: number, imageId: number, value: string) {
  const expected = `/api/batch-jobs/${jobId}/images/${imageId}/download`;
  return value === expected ? value : null;
}

export function safePreviewUrl(jobId: number, imageId: number, value: string) {
  const expected = `/api/batch-jobs/${jobId}/images/${imageId}/preview`;
  return value === expected ? value : null;
}
