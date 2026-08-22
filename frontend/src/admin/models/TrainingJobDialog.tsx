import { FormEvent, useMemo, useState } from "react";
import type { TrainingCatalog, TrainingJobCreate } from "../../api/types";
import { ConfirmDialog, ModalDialog } from "../shared";

const DEFAULTS: TrainingJobCreate = { executor: "local", base_model: "yolo11m.pt", imgsz: 640, epochs: 100, batch: "auto", device: "auto", patience: 50 };

export function TrainingJobDialog({ catalog, pending, error, onClose, onSubmit }: { catalog: TrainingCatalog; pending: boolean; error: string; onClose: () => void; onSubmit: (value: TrainingJobCreate) => void }) {
  const firstModel = catalog.models[0]?.id ?? DEFAULTS.base_model;
  const defaultModel = catalog.models.some((model) => model.id === DEFAULTS.base_model) ? DEFAULTS.base_model : firstModel;
  const [form, setForm] = useState<TrainingJobCreate>({ ...DEFAULTS, base_model: defaultModel });
  const [confirmCpu, setConfirmCpu] = useState(false);
  const size = useMemo(() => form.base_model.replace(".pt", "").slice(-1), [form.base_model]);
  const needsCpuConfirmation = form.device === "cpu" && ["m", "l", "x"].includes(size);
  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (needsCpuConfirmation && !confirmCpu) setConfirmCpu(true);
    else onSubmit(form);
  };

  if (confirmCpu) return <ConfirmDialog title="确认使用 CPU 训练" confirmLabel="仍然开始" pending={pending} error={error} onCancel={() => setConfirmCpu(false)} onConfirm={() => onSubmit(form)}><p>中型及以上模型使用 CPU 训练可能需要很长时间，建议使用 GPU。</p></ConfirmDialog>;

  return <ModalDialog title="新建本地训练" onClose={onClose}>
    <form className="admin-form training-form" onSubmit={submit}>
      <label>运行位置<select aria-label="运行位置" value="local" onChange={() => undefined}>{catalog.executors.map((executor) => <option key={executor.id} value={executor.id} disabled={!executor.enabled}>{executor.id === "local" ? "本地训练" : "远程训练（暂未开放）"}</option>)}</select></label>
      <label>基础模型<select value={form.base_model} onChange={(event) => setForm({ ...form, base_model: event.target.value })}>{catalog.models.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}</select></label>
      <label>输入尺寸<select value={form.imgsz} onChange={(event) => setForm({ ...form, imgsz: Number(event.target.value) as 640 | 960 })}><option value={640}>640</option><option value={960}>960</option></select></label>
      <label>训练轮数<input type="number" min={1} max={1000} value={form.epochs} onChange={(event) => setForm({ ...form, epochs: Number(event.target.value) })} /></label>
      <label>批次<select value={form.batch} onChange={(event) => setForm({ ...form, batch: event.target.value as TrainingJobCreate["batch"] })}>{["auto", "1", "2", "4", "8"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>设备<select value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value as TrainingJobCreate["device"] })}><option value="auto">自动</option><option value="0">GPU 0</option><option value="cpu">CPU</option></select></label>
      <label>早停耐心值<input type="number" min={0} max={500} value={form.patience} onChange={(event) => setForm({ ...form, patience: Number(event.target.value) })} /></label>
      {form.imgsz === 960 || ["l", "x"].includes(size) ? <p className="training-warning">此组合更重，训练和部署速度会明显下降。</p> : null}
      {error ? <p className="admin-dialog__error" role="alert">{error}</p> : null}
      <footer><button type="button" className="secondary-button" disabled={pending} onClick={onClose}>取消</button><button type="submit" disabled={pending || !catalog.dataset.available}>{pending ? "正在创建" : "开始训练"}</button></footer>
    </form>
  </ModalDialog>;
}
