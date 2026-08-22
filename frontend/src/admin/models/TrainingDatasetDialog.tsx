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
    onSubmit({ ...form, classes: form.classes.map((item) => item.trim()).filter(Boolean) });
  };
  return <ModalDialog title="定义服务器训练集" className="training-dataset-dialog" onClose={onClose}>
    <form className="admin-form" onSubmit={submit}>
      <label>训练集名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label>授权数据根目录<select aria-label="数据根目录" required value={form.root_alias} onChange={(event) => { setForm({ ...form, root_alias: event.target.value, directory: "." }); setBrowsePath(""); setSelected(""); }}>{roots.data?.items.map((root) => <option key={root.alias} value={root.alias}>{root.alias}</option>)}</select></label>
      <fieldset><legend>选择数据集目录</legend><p className="training-directory-path">当前位置：{browsePath || "根目录"}</p><label>下级目录<select aria-label="服务器目录" value={selected} onChange={(event) => setSelected(event.target.value)}><option value="">请选择</option>{directories.data?.items.map((item) => <option key={item.path} value={item.path}>{item.name}</option>)}</select></label><div className="training-directory-actions"><button type="button" className="secondary-button" disabled={!browsePath} onClick={() => { setBrowsePath(parent); setSelected(""); }}>返回上级</button><button type="button" className="secondary-button" disabled={!selected} onClick={() => { setBrowsePath(selected); setSelected(""); }}>进入目录</button><button type="button" disabled={!browsePath} onClick={() => setForm({ ...form, directory: browsePath })}>选择当前目录</button></div><p>已选：{form.directory === "." ? "尚未选择" : form.directory}</p></fieldset>
      <label>训练图片相对目录<input required value={form.train_split} onChange={(event) => setForm({ ...form, train_split: event.target.value })} /></label>
      <label>验证图片相对目录<input required value={form.val_split} onChange={(event) => setForm({ ...form, val_split: event.target.value })} /></label>
      <label>测试图片相对目录<input required value={form.test_split} onChange={(event) => setForm({ ...form, test_split: event.target.value })} /></label>
      <label>类别（每行一个）<textarea required rows={4} value={form.classes.join("\n")} onChange={(event) => setForm({ ...form, classes: event.target.value.split("\n") })} /></label>
      {roots.isError || directories.isError || error ? <p className="admin-dialog__error" role="alert">{error || "无法读取服务器目录，请检查授权根目录配置。"}</p> : null}
      <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="submit" disabled={pending || !form.root_alias || form.directory === "."}>{pending ? "正在保存" : "保存训练集"}</button></footer>
    </form>
  </ModalDialog>;
}
