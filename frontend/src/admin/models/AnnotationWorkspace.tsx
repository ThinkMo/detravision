import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AnnotationBoxInput, TrainingDataset, TrainingDatasetImageDetail } from "../../api/types";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminNotice, errorMessage } from "../shared";
import { AnnotationCanvas } from "./AnnotationCanvas";
import { AnnotationSidebar } from "./AnnotationSidebar";
import { trainingApi } from "./trainingApi";

type Split = "train" | "val" | "test";
type Props = { dataset: TrainingDataset; onClose: () => void };

export function AnnotationWorkspace({ dataset, onClose }: Props) {
  const client = useQueryClient();
  const [split, setSplit] = useState<Split>("test");
  const [status, setStatus] = useState("");
  const [selectedId, setSelectedId] = useState<number>();
  const [boxes, setBoxes] = useState<AnnotationBoxInput[]>([]);
  const [history, setHistory] = useState<AnnotationBoxInput[][]>([]);
  const [activeClass, setActiveClass] = useState(Math.min(2, dataset.classes.length - 1));
  const [message, setMessage] = useState("");
  const [classes, setClasses] = useState(dataset.classes);
  const [datasetRevision, setDatasetRevision] = useState(dataset.revision);
  const [newClass, setNewClass] = useState("");
  const images = useQuery({ queryKey: ["admin", "training", "datasets", dataset.id, "images", split, status], queryFn: () => trainingApi.datasetImages(dataset.id, split, status || undefined) });
  useEffect(() => { setSelectedId((current) => current != null && images.data?.items.some((item) => item.id === current) ? current : images.data?.items[0]?.id); }, [images.data]);
  const detail = useQuery({ queryKey: ["admin", "training", "datasets", dataset.id, "images", selectedId], queryFn: () => trainingApi.datasetImage(dataset.id, selectedId!), enabled: selectedId != null });
  useEffect(() => { if (detail.data) { setBoxes(detail.data.boxes); setHistory([]); } }, [detail.data]);
  const changeBoxes = (next: AnnotationBoxInput[]) => { setHistory((current) => [...current, boxes]); setBoxes(next); setMessage(""); };
  const undo = () => {
    setHistory((current) => {
      const previous = current.at(-1);
      if (previous) setBoxes(previous);
      return current.slice(0, -1);
    });
  };
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z" || target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
      event.preventDefault(); undo();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });
  const refresh = () => { void client.invalidateQueries({ queryKey: ["admin", "training", "datasets", dataset.id, "images"] }); };
  const advance = (completedId: number) => {
    const items = images.data?.items ?? [];
    const index = items.findIndex((item) => item.id === completedId);
    const next = index >= 0 ? items[index + 1] : undefined;
    if (next) setSelectedId(next.id);
  };
  const save = useMutation({
    mutationFn: (submitted: AnnotationBoxInput[]) => trainingApi.saveAnnotations(dataset.id, detail.data!.id, detail.data!.revision, submitted),
    onSuccess: (value, submitted) => {
      setBoxes(submitted);
      setHistory([]);
      setMessage("标注已保存");
      client.setQueryData<TrainingDatasetImageDetail>(["admin", "training", "datasets", dataset.id, "images", value.id], (current) => current ? { ...current, ...value, boxes: submitted } : current);
      advance(value.id);
      refresh();
    },
  });
  const empty = useMutation({ mutationFn: () => trainingApi.confirmEmpty(dataset.id, detail.data!.id, detail.data!.revision), onSuccess: (value) => { setBoxes([]); setHistory([]); setMessage("已确认该图片无目标"); advance(value.id); refresh(); } });
  const addClass = useMutation({ mutationFn: () => trainingApi.addDatasetClass(dataset.id, datasetRevision, newClass), onSuccess: (value) => { setClasses(value.classes); setDatasetRevision(value.revision); setActiveClass(value.classes.length - 1); setNewClass(""); setMessage("类别已添加"); } });
  const readonly = dataset.status !== "editing";
  return <section className="annotation-workspace">
    <header className="annotation-toolbar"><div><button type="button" className="secondary-button" onClick={onClose}>返回训练集</button><h2>{dataset.name} · v{dataset.version}</h2></div><div><label>数据分区<select value={split} onChange={(event) => setSplit(event.target.value as Split)}><option value="train">训练集</option><option value="val">验证集</option><option value="test">测试集</option></select></label><label>状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option><option value="pending_annotation">待标注</option><option value="annotated">已标注</option><option value="confirmed_empty">已确认无目标</option></select></label></div></header>
    {!readonly ? <form className="annotation-class-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (newClass.trim()) addClass.mutate(); }}><label>新增标注类别<input value={newClass} maxLength={100} placeholder="例如 vest" onChange={(event) => setNewClass(event.target.value)} /></label><button type="submit" className="annotation-button annotation-button--primary" disabled={!newClass.trim() || addClass.isPending}>添加类别</button></form> : null}
    <AdminNotice message={message || (save.isError ? errorMessage(save.error, "标注保存失败") : empty.isError ? errorMessage(empty.error, "确认失败") : addClass.isError ? errorMessage(addClass.error, "类别添加失败") : "")} error={save.isError || empty.isError || addClass.isError} />
    {images.isLoading ? <LoadingPanel label="正在加载数据集图片" /> : images.isError || !images.data ? <ErrorPanel message="无法读取数据集图片" onRetry={() => void images.refetch()} /> : images.data.items.length === 0 ? <p className="annotation-empty">当前筛选条件下没有图片；请先将图片放入服务器目录并重新扫描。</p> : <div className="annotation-layout">
      <AnnotationSidebar images={images.data.items} selectedId={selectedId} boxes={boxes} classes={classes} activeClass={activeClass} onClass={setActiveClass} onImage={setSelectedId} onDeleteBox={(index) => changeBoxes(boxes.filter((_, boxIndex) => boxIndex !== index))} />
      <main className="annotation-stage">{detail.isLoading || !detail.data ? <LoadingPanel label="正在加载图片" /> : <>
        <AnnotationCanvas src={`/api/training/datasets/${dataset.id}/images/${detail.data.id}/content`} width={detail.data.width} height={detail.data.height} boxes={boxes} classes={classes} activeClass={activeClass} readOnly={readonly} onChange={changeBoxes} />
        <div className="annotation-actions"><span>{detail.data.display_name}</span>{readonly ? <strong>已发布版本仅供查看</strong> : <><button type="button" className="annotation-button annotation-button--secondary" disabled={!history.length} onClick={undo}>撤销 <kbd>Ctrl+Z</kbd></button><button type="button" className="annotation-button annotation-button--secondary" disabled={empty.isPending} onClick={() => empty.mutate()}>确认无目标</button><button type="button" className="annotation-button annotation-button--primary" disabled={!boxes.length || save.isPending} onClick={() => save.mutate(boxes)}>保存标注</button></>}</div>
      </>}</main>
    </div>}
  </section>;
}
