import { FormEvent, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TrainingDatasetInput } from "../../api/types";
import { ModalDialog } from "../shared";
import { trainingApi } from "./trainingApi";

const initial: TrainingDatasetInput = {
  name: "", root_alias: "", directory: ".",
  train_split: "images/train", val_split: "images/val", test_split: "images/test",
  classes: ["person", "helmet", "no_helmet"],
};

export function TrainingDatasetDialog({ pending, error, onClose, onSubmit }: { pending: boolean; error: string; onClose: () => void; onSubmit: (value: TrainingDatasetInput) => void }) {
  const roots = useQuery({ queryKey: ["admin", "training", "dataset-roots"], queryFn: trainingApi.datasetRoots });
  const [form, setForm] = useState(initial);
  const [browsePath, setBrowsePath] = useState("");
  const [selected, setSelected] = useState("");
  const [localError, setLocalError] = useState("");
  useEffect(() => {
    const alias = roots.data?.items[0]?.alias;
    if (alias && !form.root_alias) setForm((current) => ({ ...current, root_alias: alias }));
  }, [form.root_alias, roots.data]);
  const directories = useQuery({
    queryKey: ["admin", "training", "dataset-directories", form.root_alias, browsePath],
    queryFn: () => trainingApi.datasetDirectories(form.root_alias, browsePath),
    enabled: Boolean(form.root_alias),
  });
  const parent = browsePath.includes("/") ? browsePath.slice(0, browsePath.lastIndexOf("/")) : "";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (form.directory === ".") {
      setLocalError("请先选择训练集目录。");
      return;
    }
    setLocalError("");
    onSubmit({ ...form, classes: form.classes.map((item) => item.trim()).filter(Boolean) });
  };
  return <ModalDialog title="定义服务器训练集" className="training-dataset-dialog" onClose={onClose}>
    <form className="admin-form" onSubmit={submit}>
      <label>训练集名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label>授权数据根目录<select aria-label="数据根目录" required value={form.root_alias} onChange={(event) => { setForm({ ...form, root_alias: event.target.value, directory: "." }); setBrowsePath(""); setSelected(""); setLocalError(""); }}>{roots.data?.items.map((root) => <option key={root.alias} value={root.alias}>{root.alias}</option>)}</select></label>
      <fieldset><legend>选择数据集目录</legend><p className="training-directory-path">当前位置：{browsePath || "根目录"}</p><label>下级目录<select aria-label="服务器目录" value={selected} onChange={(event) => { const value = event.target.value; setSelected(value); if (value) { setForm({ ...form, directory: value }); setLocalError(""); } }}><option value="">请选择</option>{directories.data?.items.map((item) => <option key={item.path} value={item.path}>{item.name}</option>)}</select></label><div className="training-directory-actions"><button type="button" className="secondary-button" onClick={() => { if (!browsePath) { setLocalError("当前已在授权根目录，没有可返回的上级目录。"); return; } setBrowsePath(parent); setSelected(""); setLocalError(""); }}>返回上级</button><button type="button" className="secondary-button" onClick={() => { if (!selected) { setLocalError("请先选择要进入的下级目录。"); return; } setBrowsePath(selected); setSelected(""); setLocalError(""); }}>进入目录</button><button type="button" onClick={() => { if (!browsePath) { setLocalError("根目录不能作为训练集，请先选择下级目录。"); return; } setForm({ ...form, directory: browsePath }); setLocalError(""); }}>选择当前目录</button></div><p>已选：{form.directory === "." ? "尚未选择" : form.directory}</p></fieldset>
      <label>训练图片相对目录<input required value={form.train_split} onChange={(event) => setForm({ ...form, train_split: event.target.value })} /></label>
      <label>验证图片相对目录<input required value={form.val_split} onChange={(event) => setForm({ ...form, val_split: event.target.value })} /></label>
      <label>测试图片相对目录<input required value={form.test_split} onChange={(event) => setForm({ ...form, test_split: event.target.value })} /></label>
      <label>类别（每行一个）<textarea required rows={4} value={form.classes.join("\n")} onChange={(event) => setForm({ ...form, classes: event.target.value.split("\n") })} /></label>
      {roots.isError || directories.isError || error || localError ? <p className="admin-dialog__error" role="alert">{error || localError || "无法读取服务器目录，请检查授权根目录配置。"}</p> : null}
      <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="submit" disabled={pending}>{pending ? "正在保存" : "保存训练集"}</button></footer>
    </form>
  </ModalDialog>;
}
