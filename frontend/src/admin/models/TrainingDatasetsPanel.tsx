import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { DatasetValidationReport, TrainingDataset, TrainingDatasetInput } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { AdminNotice, ConfirmDialog, errorMessage } from "../shared";
import { TrainingDatasetDialog } from "./TrainingDatasetDialog";
import { trainingApi } from "./trainingApi";
import { DatasetValidationDetails, datasetErrorMessage, datasetValidationSummary } from "./DatasetValidationDetails";
import { DatasetPublishDialog } from "./DatasetPublishDialog";

type Props = { onAnnotate?: (dataset: TrainingDataset) => void };

export function TrainingDatasetsPanel({ onAnnotate }: Props) {
  const client = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [disabling, setDisabling] = useState<TrainingDataset | null>(null);
  const [publishing, setPublishing] = useState<TrainingDataset | null>(null);
  const [validation, setValidation] = useState<DatasetValidationReport | null>(null);
  const query = useQuery({ queryKey: ["admin", "training", "datasets"], queryFn: trainingApi.datasets });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["admin", "training", "datasets"] });
    void client.invalidateQueries({ queryKey: ["admin", "training", "catalog"] });
  };
  const create = useMutation({ mutationFn: (value: TrainingDatasetInput) => trainingApi.createDataset(value), onSuccess: () => { setShowCreate(false); refresh(); } });
  const disable = useMutation({ mutationFn: (id: number) => trainingApi.disableDataset(id), onSuccess: () => { setDisabling(null); refresh(); } });
  const clone = useMutation({ mutationFn: (id: number) => trainingApi.cloneDataset(id), onSuccess: refresh });
  const rescan = useMutation({ mutationFn: (id: number) => trainingApi.rescanDataset(id), onSuccess: refresh });
  const validate = useMutation({ mutationFn: (id: number) => trainingApi.validateDataset(id), onSuccess: setValidation });
  const mutationError = clone.error ?? rescan.error ?? validate.error;

  return <section className="training-datasets">
    <div className="admin-subheading"><div><h2>训练集版本</h2><small>发布版本用于训练；编辑版本可增加图片、人工标注并校验发布</small></div><button type="button" onClick={() => { create.reset(); setShowCreate(true); }}>定义训练集</button></div>
    {mutationError ? <AdminNotice error message={datasetErrorMessage(mutationError, "训练集操作失败，请检查目录或重试。")} /> : null}
    {validation && !publishing ? <AdminNotice error={Boolean(validation.errors.length)} message={datasetValidationSummary(validation)} /> : null}
    {validation && !publishing ? <DatasetValidationDetails report={validation} /> : null}
    {query.data?.items.length ? <div className="dataset-version-grid">{query.data.items.map((item) => {
      const editing = item.status === "editing";
      return <article className="admin-card dataset-version-card" key={item.id}>
        <header><div><h3>{item.name}</h3><small>{item.root_alias} / {item.directory}</small></div><span className={`dataset-status dataset-status--${item.status}`}>v{item.version ?? 1} · {editing ? "编辑中" : "已发布"}</span></header>
        <p>{item.classes.join("、")}</p>
        <footer>
          {editing ? <>
            <button type="button" className="dataset-action dataset-action--primary" onClick={() => onAnnotate?.(item)}>继续标注</button>
            <button type="button" className="dataset-action dataset-action--secondary" disabled={rescan.isPending} onClick={() => rescan.mutate(item.id)}>重新扫描图片</button>
            <button type="button" className="dataset-action dataset-action--secondary" disabled={validate.isPending} onClick={() => validate.mutate(item.id)}>校验</button>
            <button type="button" className="dataset-action dataset-action--primary" onClick={() => { setValidation(null); setPublishing(item); }}>发布</button>
          </> : <>
            <button type="button" className="dataset-action dataset-action--primary" disabled={clone.isPending} onClick={() => clone.mutate(item.id)}>创建编辑版本</button>
            {item.enabled ? <button type="button" className="dataset-action dataset-action--danger" onClick={() => { disable.reset(); setDisabling(item); }}>停用</button> : null}
          </>}
        </footer>
      </article>;
    })}</div> : <EmptyState message="尚未定义服务器训练集" />}
    {showCreate ? <TrainingDatasetDialog pending={create.isPending} error={create.isError ? errorMessage(create.error, "训练集保存失败") : ""} onClose={() => setShowCreate(false)} onSubmit={(value) => create.mutate(value)} /> : null}
    {disabling ? <ConfirmDialog title="停用训练集" confirmLabel="确认停用" pending={disable.isPending} error={disable.isError ? errorMessage(disable.error, "训练集停用失败") : ""} onCancel={() => setDisabling(null)} onConfirm={() => disable.mutate(disabling.id)}><p>已有训练任务仍保留快照，新任务将不能再选择此训练集。</p></ConfirmDialog> : null}
    {publishing ? <DatasetPublishDialog dataset={publishing} onClose={() => setPublishing(null)} onPublished={() => { setPublishing(null); setValidation(null); }} onContinueReview={() => { const item = publishing; setPublishing(null); setValidation(null); onAnnotate?.(item); }} /> : null}
  </section>;
}
