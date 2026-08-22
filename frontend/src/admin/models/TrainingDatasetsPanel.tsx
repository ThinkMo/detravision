import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TrainingDataset, TrainingDatasetInput } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ConfirmDialog } from "../shared";
import { errorMessage } from "../shared";
import { TrainingDatasetDialog } from "./TrainingDatasetDialog";
import { trainingApi } from "./trainingApi";

export function TrainingDatasetsPanel() {
  const client = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [disabling, setDisabling] = useState<TrainingDataset | null>(null);
  const query = useQuery({ queryKey: ["admin", "training", "datasets"], queryFn: trainingApi.datasets });
  const refresh = () => { void client.invalidateQueries({ queryKey: ["admin", "training", "datasets"] }); void client.invalidateQueries({ queryKey: ["admin", "training", "catalog"] }); };
  const create = useMutation({ mutationFn: (value: TrainingDatasetInput) => trainingApi.createDataset(value), onSuccess: () => { setShowCreate(false); refresh(); } });
  const disable = useMutation({ mutationFn: (id: number) => trainingApi.disableDataset(id), onSuccess: () => { setDisabling(null); refresh(); } });
  return <section className="training-datasets"><div className="admin-subheading"><div><h2>训练集</h2><small>仅可从服务器授权目录中选择</small></div><button type="button" onClick={() => { create.reset(); setShowCreate(true); }}>定义训练集</button></div>
    {query.data?.items.length ? <div className="admin-card-grid">{query.data.items.map((item) => <article className="admin-card" key={item.id}><h3>{item.name}</h3><p>{item.root_alias} / {item.directory}</p><small>{item.classes.join("、")}</small><p>{item.available ? "可用于训练" : "不可用"}</p>{item.enabled ? <footer><button type="button" className="table-action table-action--danger" onClick={() => { disable.reset(); setDisabling(item); }}>停用</button></footer> : null}</article>)}</div> : <EmptyState message="尚未定义服务器训练集" />}
    {showCreate ? <TrainingDatasetDialog pending={create.isPending} error={create.isError ? errorMessage(create.error, "训练集保存失败") : ""} onClose={() => setShowCreate(false)} onSubmit={(value) => create.mutate(value)} /> : null}
    {disabling ? <ConfirmDialog title="停用训练集" confirmLabel="确认停用" pending={disable.isPending} error={disable.isError ? errorMessage(disable.error, "训练集停用失败") : ""} onCancel={() => setDisabling(null)} onConfirm={() => disable.mutate(disabling.id)}><p>已有训练任务仍保留自己的数据集快照，新任务将不能再选择此训练集。</p></ConfirmDialog> : null}
  </section>;
}
