import { FormEvent, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TrainingDatasetInput } from "../../api/types";
import { ModalDialog } from "../shared";
import { ServerDirectoryPicker } from "./ServerDirectoryPicker";
import { trainingApi } from "./trainingApi";

const initial: TrainingDatasetInput = {
  name: "", root_alias: "", directory: ".",
  train_split: "images/train", val_split: "images/val", test_split: "images/test",
  classes: ["person", "helmet", "no_helmet"],
};

export function TrainingDatasetDialog({ pending, error, onClose, onSubmit }: { pending: boolean; error: string; onClose: () => void; onSubmit: (value: TrainingDatasetInput) => void }) {
  const roots = useQuery({ queryKey: ["admin", "training", "dataset-roots"], queryFn: trainingApi.datasetRoots });
  const [form, setForm] = useState(initial);
  const [showBrowser, setShowBrowser] = useState(false);
  const [localError, setLocalError] = useState("");
  useEffect(() => {
    const alias = roots.data?.items[0]?.alias;
    if (alias && !form.root_alias) setForm((current) => ({ ...current, root_alias: alias }));
  }, [form.root_alias, roots.data]);
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
      <label>授权数据根目录<select aria-label="数据根目录" required value={form.root_alias} onChange={(event) => { setForm({ ...form, root_alias: event.target.value, directory: "." }); setLocalError(""); }}>{roots.data?.items.map((root) => <option key={root.alias} value={root.alias}>{root.alias}</option>)}</select></label>
      <label>训练数据目录<div className="training-directory-field"><input readOnly value={form.directory === "." ? "尚未选择" : form.directory} /><button type="button" onClick={() => { if (!form.root_alias) { setLocalError("请先选择授权数据根目录。"); return; } setShowBrowser(true); setLocalError(""); }}>浏览服务器目录</button></div></label>
      <label>训练图片相对目录<input required value={form.train_split} onChange={(event) => setForm({ ...form, train_split: event.target.value })} /></label>
      <label>验证图片相对目录<input required value={form.val_split} onChange={(event) => setForm({ ...form, val_split: event.target.value })} /></label>
      <label>测试图片相对目录<input required value={form.test_split} onChange={(event) => setForm({ ...form, test_split: event.target.value })} /></label>
      <label>类别（每行一个）<textarea required rows={4} value={form.classes.join("\n")} onChange={(event) => setForm({ ...form, classes: event.target.value.split("\n") })} /></label>
      {roots.isError || error || localError ? <p className="admin-dialog__error" role="alert">{error || localError || "无法读取服务器目录，请检查授权根目录配置。"}</p> : null}
      <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="submit" disabled={pending}>{pending ? "正在保存" : "保存训练集"}</button></footer>
    </form>
    {showBrowser ? <ServerDirectoryPicker rootAlias={form.root_alias} initialPath={form.directory} onClose={() => setShowBrowser(false)} onSelect={(directory) => { setForm({ ...form, directory }); setShowBrowser(false); setLocalError(""); }} /> : null}
  </ModalDialog>;
}
