import { FormEvent, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { ModelVersion, TrainingDataset, TrainingJobCreate } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminHeading, AdminNotice, ConfirmDialog, errorMessage } from "../shared";
import { TrainingJobDialog } from "./TrainingJobDialog";
import { TrainingJobsPanel } from "./TrainingJobsPanel";
import { TrainingDatasetsPanel } from "./TrainingDatasetsPanel";
import { AnnotationWorkspace } from "./AnnotationWorkspace";
import { trainingApi } from "./trainingApi";

type ModelForm = { name: string; path: string; runtime: ModelVersion["runtime"]; classes: string; map50: string };
const empty: ModelForm = { name: "", path: "", runtime: "onnx-fp32", classes: "helmet, no_helmet", map50: "" };

function ModelVersions({ message, setMessage }: { message: string; setMessage: (value: string) => void }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin", "models"], queryFn: () => api.request<ModelVersion[]>("/api/models") });
  const [form, setForm] = useState<ModelForm | null>(null);
  const [activating, setActivating] = useState<ModelVersion | null>(null);
  const create = useMutation({ mutationFn: (value: ModelForm) => api.request<ModelVersion>("/api/models", { method: "POST", body: JSON.stringify({ name: value.name.trim(), file_path: value.path.trim(), runtime: value.runtime, classes: value.classes.split(",").map((item) => item.trim()).filter(Boolean), map50: value.map50 ? Number(value.map50) : null }) }), onSuccess: () => { setForm(null); setMessage("模型版本已登记"); void client.invalidateQueries({ queryKey: ["admin", "models"] }); } });
  const activate = useMutation({ mutationFn: (id: number) => api.request<ModelVersion>(`/api/models/${id}/activate`, { method: "POST" }), onSuccess: () => { setActivating(null); setMessage("模型已激活，新的识别任务将使用该模型"); void client.invalidateQueries({ queryKey: ["admin", "models"] }); } });
  if (query.isLoading) return <LoadingPanel label="正在加载模型" />;
  if (query.isError || !query.data) return <ErrorPanel message="暂时无法读取模型" onRetry={() => void query.refetch()} />;
  return <>
    <div className="admin-subheading"><h2>模型版本</h2><button type="button" onClick={() => setForm(empty)}>登记模型</button></div>
    <AdminNotice message={message || (create.isError ? errorMessage(create.error, "登记失败") : activate.isError ? errorMessage(activate.error, "激活失败") : "")} error={create.isError || activate.isError} />
    {form ? <form className="admin-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (form.name.trim() && form.path.trim() && form.classes.trim()) create.mutate(form); }}><h2>登记模型</h2><label>版本名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label><label>服务器模型路径<input required type="password" autoComplete="off" value={form.path} onChange={(event) => setForm({ ...form, path: event.target.value })} /></label><label>运行时<select value={form.runtime} onChange={(event) => setForm({ ...form, runtime: event.target.value as ModelForm["runtime"] })}><option value="pt">PyTorch</option><option value="onnx-fp32">ONNX FP32</option><option value="tensorrt-fp16">TensorRT FP16</option></select></label><label>类别（逗号分隔）<input required value={form.classes} onChange={(event) => setForm({ ...form, classes: event.target.value })} /></label><label>mAP50<input type="number" min="0" max="1" step="0.001" value={form.map50} onChange={(event) => setForm({ ...form, map50: event.target.value })} /></label><footer><button type="button" className="secondary-button" onClick={() => setForm(null)}>取消</button><button type="submit" disabled={create.isPending}>登记</button></footer></form> : null}
    {query.data.length === 0 ? <EmptyState message="尚未登记模型" /> : <div className="admin-table-wrap" role="region" aria-label="模型列表" tabIndex={0}><table className="admin-table"><thead><tr><th>版本</th><th>运行时</th><th>类别</th><th>mAP50</th><th>来源</th><th>状态</th><th>操作</th></tr></thead><tbody>{query.data.map((model) => <tr key={model.id}><td data-label="版本"><strong>{model.name}</strong></td><td data-label="运行时">{model.runtime}</td><td data-label="类别">{model.classes.join("、")}</td><td data-label="mAP50">{model.map50 == null ? "—" : model.map50.toFixed(3)}</td><td data-label="来源">{model.source === "training" ? `训练任务 #${model.training_job_id}` : "手工登记"}</td><td data-label="状态">{model.is_active ? "当前激活" : "未激活"}</td><td data-label="操作">{model.is_active ? null : <button type="button" className="table-action" aria-label={`激活${model.name}`} onClick={() => { activate.reset(); setActivating(model); }}>激活</button>}</td></tr>)}</tbody></table></div>}
    {activating ? <ConfirmDialog title={`激活模型“${activating.name}”`} confirmLabel="确认激活" pending={activate.isPending} error={activate.isError ? errorMessage(activate.error, "激活失败") : ""} onCancel={() => { activate.reset(); setActivating(null); }} onConfirm={() => activate.mutate(activating.id)}><p>版本：<strong>{activating.name}</strong></p><p>运行时：<strong>{activating.runtime}</strong></p>{activating.source === "training" && activating.metrics?.map50 != null && activating.metrics.map50 < .85 ? <p className="training-warning">该训练模型未达到验收标准，请确认后再激活。</p> : null}<p>激活后，实时监控和多图识别的新任务将使用此版本。</p></ConfirmDialog> : null}
  </>;
}

export function ModelsPage() {
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view");
  const tab = view === "datasets" || view === "training" ? view : "models";
  const setTab = (next: "models" | "datasets" | "training") => setSearchParams((previous) => {
    const updated = new URLSearchParams(previous);
    updated.set("view", next);
    return updated;
  });
  const [showTraining, setShowTraining] = useState(false);
  const [trainingInitial, setTrainingInitial] = useState<TrainingJobCreate | null>(null);
  const [message, setMessage] = useState("");
  const [annotating, setAnnotating] = useState<TrainingDataset | null>(null);
  const catalog = useQuery({ queryKey: ["admin", "training", "catalog"], queryFn: trainingApi.catalog, enabled: tab === "training" });
  const createTraining = useMutation({ mutationFn: (value: TrainingJobCreate) => trainingApi.create(value), onSuccess: () => { setShowTraining(false); setTrainingInitial(null); setMessage("训练任务已加入队列"); void client.invalidateQueries({ queryKey: ["admin", "training-jobs"] }); } });
  const hasDataset = Boolean(catalog.data?.datasets.some((dataset) => dataset.available));
  const hasModel = Boolean(catalog.data?.models.some((model) => model.available));

  return <section className="admin-page">
    <AdminHeading eyebrow="MODEL & TRAINING" title="模型管理" description="在一个工作台完成数据标注、YOLO 训练、效果评估与模型发布" />
    <ol className="model-workflow" aria-label="模型构建流程">
      <li><span>01</span><div><strong>准备与标注</strong><small>定义训练集，标注目标并校验发布</small></div></li>
      <li><span>02</span><div><strong>训练与评估</strong><small>从基础模式开始，比较指标与案例</small></div></li>
      <li><span>03</span><div><strong>注册与识别</strong><small>注册候选模型，激活后验证现场效果</small></div></li>
    </ol>
    <div className="admin-tabs" role="tablist" aria-label="模型管理视图">
      <button type="button" role="tab" aria-selected={tab === "datasets"} className={tab === "datasets" ? "admin-tabs__active" : ""} onClick={() => setTab("datasets")}>训练集</button>
      <button type="button" role="tab" aria-selected={tab === "training"} className={tab === "training" ? "admin-tabs__active" : ""} onClick={() => setTab("training")}>训练任务</button>
      <button type="button" role="tab" aria-selected={tab === "models"} className={tab === "models" ? "admin-tabs__active" : ""} onClick={() => setTab("models")}>模型版本</button>
    </div>
    {tab === "models" ? <ModelVersions message={message} setMessage={setMessage} /> : null}
    {tab === "datasets" ? annotating ? <AnnotationWorkspace dataset={annotating} onClose={() => setAnnotating(null)} /> : <TrainingDatasetsPanel onAnnotate={setAnnotating} /> : null}
    {tab === "training" ? catalog.isLoading ? <LoadingPanel label="正在加载训练配置" /> : catalog.isError || !catalog.data ? <ErrorPanel message="无法读取训练配置" onRetry={() => void catalog.refetch()} /> : <>
      <div className="admin-subheading">
        <div><h2>训练任务</h2><small>创建、查看并管理本地模型训练</small></div>
        <button type="button" disabled={!hasDataset || !hasModel} onClick={() => { createTraining.reset(); setTrainingInitial(null); setShowTraining(true); }}>新建训练</button>
      </div>
      {catalog.data.scheduler_state === "unavailable" || catalog.data.scheduler_state === "unknown" ? <AdminNotice message="训练调度服务当前未运行；任务仍可创建并排队，服务恢复后会自动处理。" error /> : null}
      <AdminNotice message={message || (!hasDataset ? "请先定义并发布训练集，再开始训练。" : !hasModel ? "请先准备基础模型权重，再刷新模型目录。" : "")} />
      {!hasDataset ? <div className="training-setup-action"><button type="button" className="secondary-button" onClick={() => setTab("datasets")}>前往训练集</button><span>定义目录、完成标注并校验发布后，即可在这里选择。</span></div> : null}
      <TrainingJobsPanel supportedConfigVersion={catalog.data.config_schema_version ?? 1} onRegistered={() => void client.invalidateQueries({ queryKey: ["admin", "models"] })} onClone={(value) => { createTraining.reset(); setTrainingInitial(value); setShowTraining(true); }} />
      {showTraining ? <TrainingJobDialog catalog={catalog.data} initialRequest={trainingInitial} pending={createTraining.isPending} error={createTraining.isError ? errorMessage(createTraining.error, "创建训练任务失败") : ""} onClose={() => { setShowTraining(false); setTrainingInitial(null); }} onSubmit={(value) => createTraining.mutate(value)} /> : null}
    </> : null}
  </section>;
}
