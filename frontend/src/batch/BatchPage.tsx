import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { createBatchJob, type BatchJobFilters, useBatchJob, useBatchJobs } from "./batchApi";
import { JobDetail } from "./JobDetail";
import { JobList } from "./JobList";
import { UploadDropzone } from "./UploadDropzone";
import "./batch.css";

function filtersFrom(search: URLSearchParams): BatchJobFilters {
  const page = Number(search.get("page") ?? 1);
  return {
    page: Number.isInteger(page) && page > 0 ? page : 1,
    pageSize: 20,
    status: search.get("status") || undefined,
    createdFrom: search.get("created_from") || undefined,
    createdTo: search.get("created_to") || undefined,
    userId: search.get("user_id") || undefined,
  };
}

function filtersToSearch(filters: BatchJobFilters) {
  const search = new URLSearchParams();
  if ((filters.page ?? 1) > 1) search.set("page", String(filters.page));
  if (filters.status) search.set("status", filters.status);
  if (filters.createdFrom) search.set("created_from", filters.createdFrom);
  if (filters.createdTo) search.set("created_to", filters.createdTo);
  if (filters.userId) search.set("user_id", filters.userId);
  return search;
}

export function BatchPage() {
  const { jobId: rawJobId } = useParams();
  const parsedJobId = rawJobId ? Number(rawJobId) : null;
  const jobId = parsedJobId !== null && Number.isInteger(parsedJobId) && parsedJobId > 0 ? parsedJobId : null;
  return rawJobId ? <BatchDetailView jobId={jobId} /> : <BatchWorkspace />;
}

function BatchDetailView({ jobId }: { jobId: number | null }) {
  const job = useBatchJob(jobId);
  return <JobDetail data={job.data} loading={job.isLoading && jobId !== null} error={job.isError || jobId === null} onRetry={() => void job.refetch()} />;
}

function BatchWorkspace() {
  const { user } = useAuth();
  const [search, setSearch] = useSearchParams();
  const filters = filtersFrom(search);
  const jobs = useBatchJobs(filters);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const upload = useMutation({
    mutationFn: (files: File[]) => {
      setError("");
      setProgress(0);
      return createBatchJob(files, setProgress);
    },
    onSuccess: async (job) => {
      await queryClient.invalidateQueries({ queryKey: ["batch-jobs"] });
      navigate(`/batch/${job.id}`);
    },
    onError: (reason) => setError(reason instanceof Error ? reason.message : "上传失败，请重试"),
  });
  const updateFilters = (next: BatchJobFilters) => setSearch(filtersToSearch(next), { replace: true });

  return (
    <div className="batch-page">
      <header className="page-heading"><div><p>MULTI-IMAGE INFERENCE</p><h1>多图识别</h1><span>一次提交多张现场图片，集中查看模型识别结果</span></div></header>
      <UploadDropzone pending={upload.isPending} progress={progress} onSubmit={(files) => upload.mutate(files)} />
      {error ? <p className="batch-alert" role="alert">{error}</p> : null}
      <JobList data={jobs.data} loading={jobs.isLoading} error={jobs.isError} admin={user?.role === "admin"} filters={filters} onFilters={updateFilters} onRetry={() => void jobs.refetch()} />
    </div>
  );
}
