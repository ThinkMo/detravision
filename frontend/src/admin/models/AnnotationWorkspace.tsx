import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AnnotationBoxInput, TrainingDataset, TrainingDatasetImage, TrainingDatasetImageDetail, TrainingDatasetImagePage } from "../../api/types";
import { ApiError } from "../../api/client";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminNotice, ConfirmDialog, errorMessage } from "../shared";
import { AnnotationCanvas } from "./AnnotationCanvas";
import { AnnotationSidebar } from "./AnnotationSidebar";
import { trainingApi } from "./trainingApi";
import { DatasetPublishDialog } from "./DatasetPublishDialog";

type Split = "train" | "val" | "test";
const splits: readonly Split[] = ["train", "val", "test"];
const splitNames: Record<Split, string> = { train: "训练集", val: "验证集", test: "测试集" };
type Props = { dataset: TrainingDataset; onClose: () => void };

export function AnnotationWorkspace({ dataset, onClose }: Props) {
  const client = useQueryClient();
  const [split, setSplit] = useState<Split>("train");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number>();
  const [checkedIds, setCheckedIds] = useState<number[]>([]);
  const [removal, setRemoval] = useState<TrainingDatasetImage[] | null>(null);
  useEffect(() => { setCheckedIds([]); }, [split, status, page]);
  const [boxes, setBoxes] = useState<AnnotationBoxInput[]>([]);
  const [history, setHistory] = useState<AnnotationBoxInput[][]>([]);
  const [activeClass, setActiveClass] = useState(Math.min(2, dataset.classes.length - 1));
  const [message, setMessage] = useState("");
  const [classes, setClasses] = useState(dataset.classes);
  const [datasetRevision, setDatasetRevision] = useState(dataset.revision);
  const [newClass, setNewClass] = useState("");
  const [showPublish, setShowPublish] = useState(false);
  const [finishedSplit, setFinishedSplit] = useState<Split | null>(null);
  const completion = useMutation({
    mutationFn: (_checkedSplit: Split) => Promise.all(splits.map(part => trainingApi.datasetImages(dataset.id, part, "pending_annotation", 1, 1))),
    onSuccess: (parts, checkedSplit) => {
      setShowPublish(false); setFinishedSplit(null);
      if (parts.every(part => part.total === 0)) setShowPublish(true);
      else if (parts[splits.indexOf(checkedSplit)].total === 0) setFinishedSplit(checkedSplit);
    },
  });
  const remaining = completion.data?.reduce((total, part) => total + part.total, 0) ?? 0;
  const nextSplit = splits.find((_, index) => (completion.data?.[index].total ?? 0) > 0);
  const images = useQuery({ queryKey: ["admin", "training", "datasets", dataset.id, "images", split, status, page], queryFn: () => trainingApi.datasetImages(dataset.id, split, status || undefined, page) });
  useEffect(() => { setSelectedId((current) => current != null && images.data?.items.some((item) => item.id === current) ? current : images.data?.items[0]?.id); }, [images.data]);
  useEffect(() => {
    if (!images.data) return;
    const pages = Math.max(1, Math.ceil(images.data.total / images.data.page_size));
    if (page > pages) { setPage(pages); setSelectedId(undefined); }
  }, [images.data, page]);
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
  const advance = (completedId: number, reviewStatus: string) => {
    const items = images.data?.items ?? [];
    const index = items.findIndex((item) => item.id === completedId);
    const next = index >= 0 ? items[index + 1] : undefined;
    if (next) setSelectedId(next.id);
    else if ((!status || status === reviewStatus) && images.data && page < Math.ceil(images.data.total / images.data.page_size)) {
      setSelectedId(undefined); setPage(page + 1);
    }
  };
  const save = useMutation({
    mutationFn: ({ image, submitted }: { image: TrainingDatasetImageDetail; submitted: AnnotationBoxInput[] }) => trainingApi.saveAnnotations(dataset.id, image.id, image.revision, submitted),
    onSuccess: (value, { image, submitted }) => {
      setBoxes(submitted);
      setHistory([]);
      setMessage("标注已保存");
      client.setQueryData<TrainingDatasetImageDetail>(["admin", "training", "datasets", dataset.id, "images", value.id], (current) => current ? { ...current, ...value, boxes: submitted } : current);
      advance(value.id, value.review_status);
      refresh();
      completion.mutate(image.split);
    },
  });
  const empty = useMutation({ mutationFn: (image: TrainingDatasetImageDetail) => trainingApi.confirmEmpty(dataset.id, image.id, image.revision), onSuccess: (value, image) => {
    setBoxes([]); setHistory([]); setMessage("已确认该图片无目标");
    client.setQueryData(["admin", "training", "datasets", dataset.id, "images", value.id], value);
    advance(value.id, value.review_status); refresh();
    completion.mutate(image.split);
  } });
  const addClass = useMutation({ mutationFn: () => trainingApi.addDatasetClass(dataset.id, datasetRevision, newClass), onSuccess: (value) => { setClasses(value.classes); setDatasetRevision(value.revision); setActiveClass(value.classes.length - 1); setNewClass(""); setMessage("类别已添加"); } });
  const remove = useMutation({
    mutationFn: (selected: TrainingDatasetImage[]) => trainingApi.removeDatasetImages(dataset.id, datasetRevision, selected.map(image => ({ id: image.id, revision: image.revision }))),
    onSuccess: (result) => {
      const removed = new Set(result.image_ids);
      const items = images.data?.items ?? [];
      const index = items.findIndex(image => image.id === selectedId);
      if (selectedId != null && removed.has(selectedId)) {
        const next = items.slice(index + 1).find(image => !removed.has(image.id)) ?? items.slice(0, index).reverse().find(image => !removed.has(image.id));
        setSelectedId(next?.id); setBoxes([]); setHistory([]);
      }
      client.setQueryData<TrainingDatasetImagePage>(["admin", "training", "datasets", dataset.id, "images", split, status, page], current => current ? { ...current, items: current.items.filter(image => !removed.has(image.id)), total: Math.max(0, current.total - result.removed) } : current);
      for (const id of removed) client.removeQueries({ queryKey: ["admin", "training", "datasets", dataset.id, "images", id], exact: true });
      setDatasetRevision(result.dataset_revision); setCheckedIds([]); setRemoval(null);
      setShowPublish(false); setFinishedSplit(null); completion.reset();
      setMessage(`已从当前训练集删除 ${result.removed} 张图片`);
      refresh(); void client.invalidateQueries({ queryKey: ["admin", "training", "datasets"] });
    },
  });
  const requestRemoval = (selected: TrainingDatasetImage[]) => { setMessage(""); remove.reset(); setRemoval(selected); };
  const busy = save.isPending || empty.isPending || remove.isPending;
  const readonly = dataset.status !== "editing";
  return <section className="annotation-workspace">
    <header className="annotation-heading">
      <button type="button" className="secondary-button" onClick={onClose}>返回训练集</button>
      <h2>{dataset.name} · v{dataset.version}</h2>
    </header>
    <div className="annotation-toolbar" role="group" aria-label="标注工具栏">
      <div className="annotation-filters">
        <label>数据分区<select value={split} onChange={(event) => { setSplit(event.target.value as Split); setPage(1); setSelectedId(undefined); }}><option value="train">训练集</option><option value="val">验证集</option><option value="test">测试集</option></select></label>
        <label>状态<select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); setSelectedId(undefined); }}><option value="">全部</option><option value="pending_annotation">待标注</option><option value="annotated">已标注</option><option value="confirmed_empty">已确认无目标</option></select></label>
      </div>
      {!readonly ? <>
        <form className="annotation-class-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (newClass.trim()) addClass.mutate(); }}>
          <label>新增标注类别<input value={newClass} maxLength={100} placeholder="例如 vest" onChange={(event) => setNewClass(event.target.value)} /></label>
          <button type="submit" className="annotation-button annotation-button--primary" disabled={!newClass.trim() || addClass.isPending || busy}>添加类别</button>
        </form>
        <button type="button" className="annotation-button annotation-button--secondary annotation-completion-button" disabled={busy || completion.isPending} onClick={() => completion.mutate(split)}>{completion.isPending ? "正在检查完成状态" : "检查完成状态"}</button>
      </> : null}
    </div>
    <AdminNotice message={message || (save.isError ? errorMessage(save.error, "标注保存失败") : empty.isError ? errorMessage(empty.error, "确认失败") : addClass.isError ? errorMessage(addClass.error, "类别添加失败") : "")} error={save.isError || empty.isError || addClass.isError} />
    {completion.isError ? <div><AdminNotice error message="标注已保存，但无法确认整个版本是否完成。请重新检查完成状态，或返回训练集列表校验。" /><button type="button" className="secondary-button" onClick={() => completion.mutate(split)}>重新检查完成状态</button></div> : null}
    {completion.isSuccess ? <section className="dataset-validation-details" aria-label="标注完成状态"><p>{remaining ? `整个版本还有 ${remaining} 张待标注，完成后才能发布。` : "整个版本已无待标注图片，可以校验并发布。"}</p><ul>{splits.map((part, index) => <li key={part}>{splitNames[part]}：{completion.data[index].total} 张待标注</li>)}</ul></section> : null}
    {(images.isLoading || (images.isFetching && !images.data?.items.length)) ? <LoadingPanel label="正在加载数据集图片" /> : images.isError || !images.data ? <ErrorPanel message="无法读取数据集图片" onRetry={() => void images.refetch()} /> : images.data.items.length === 0 ? <p className="annotation-empty">{status ? `${splitNames[split]}中没有符合当前状态的图片，请切换状态筛选。` : `${splitNames[split]}暂无图片，请切换数据分区查看其他图片。`}</p> : <div className="annotation-layout">
      <AnnotationSidebar pagination={{ label: ({ train: "训练集", val: "验证集", test: "测试集" })[split] + (status ? "（当前筛选）" : ""), pageSize: images.data.page_size, page, pages: Math.max(1, Math.ceil(images.data.total / images.data.page_size)), total: images.data.total, onPage: (next) => { setPage(next); setSelectedId(undefined); }, disabled: images.isFetching || busy }} selection={readonly ? undefined : { ids: checkedIds, disabled: images.isFetching || busy || completion.isPending, onToggle: id => setCheckedIds(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]), onAll: () => setCheckedIds(current => images.data.items.every(image => current.includes(image.id)) ? [] : images.data.items.map(image => image.id)), onRemove: () => requestRemoval(images.data.items.filter(image => checkedIds.includes(image.id))) }} images={images.data.items} selectedId={selectedId} boxes={boxes} classes={classes} activeClass={activeClass} onClass={setActiveClass} onImage={setSelectedId} onDeleteBox={(index) => changeBoxes(boxes.filter((_, boxIndex) => boxIndex !== index))} />
      <main className="annotation-stage">{detail.isLoading || !detail.data ? <LoadingPanel label="正在加载图片" /> : <>
        <AnnotationCanvas src={`/api/training/datasets/${dataset.id}/images/${detail.data.id}/content`} width={detail.data.width} height={detail.data.height} boxes={boxes} classes={classes} activeClass={activeClass} readOnly={readonly || remove.isPending} onChange={changeBoxes} />
        <div className="annotation-actions"><span>{detail.data.display_name}</span>{readonly ? <strong>已发布版本仅供查看</strong> : <><button type="button" className="annotation-button annotation-button--danger" disabled={busy || completion.isPending || detail.isFetching} onClick={() => requestRemoval([detail.data!])}>删除当前图片</button><button type="button" className="annotation-button annotation-button--secondary" disabled={!history.length || busy} onClick={undo}>撤销 <kbd>Ctrl+Z</kbd></button><button type="button" className="annotation-button annotation-button--secondary" disabled={busy || completion.isPending || detail.isFetching} onClick={() => empty.mutate(detail.data!)}>确认无目标</button><button type="button" className="annotation-button annotation-button--primary" disabled={!boxes.length || busy || completion.isPending || detail.isFetching} onClick={() => save.mutate({ image: detail.data!, submitted: boxes })}>保存标注</button></>}</div>
      </>}</main>
    </div>}
    {finishedSplit && nextSplit ? <ConfirmDialog title={`${splitNames[finishedSplit]}标注已完成`} confirmLabel={`前往${splitNames[nextSplit]}`} onCancel={() => setFinishedSplit(null)} onConfirm={() => { setFinishedSplit(null); setSplit(nextSplit); setStatus("pending_annotation"); setPage(1); setSelectedId(undefined); }}><p>{splitNames[finishedSplit]}已无待标注图片。整个版本还有 {remaining} 张待标注，完成后才能发布。</p><ul>{splits.map((part, index) => <li key={part}>{splitNames[part]}：{completion.data?.[index].total ?? 0} 张待标注</li>)}</ul></ConfirmDialog> : null}
    {removal ? <ConfirmDialog title="从当前训练集删除图片" confirmLabel="确认删除" pending={remove.isPending} error={remove.isError ? remove.error instanceof ApiError && remove.error.code === "annotation_conflict" ? "图片或集合已更新，请刷新页面后重试。" : "删除失败，请重试。" : undefined} onCancel={() => { if (!remove.isPending) setRemoval(null); }} onConfirm={() => remove.mutate(removal)}><p>将从当前训练集移除 {removal.length} 张图片及对应标注。源素材和其他训练集不受影响。</p><ul>{removal.map(image => <li key={image.id}>{image.display_name}</li>)}</ul>{history.length > 0 && removal.some(image => image.id === selectedId) ? <p>当前图片未保存的标注也将放弃。</p> : null}</ConfirmDialog> : null}
    {showPublish ? <DatasetPublishDialog dataset={{ ...dataset, classes, revision: datasetRevision }} completed onClose={() => setShowPublish(false)} onContinueReview={() => setShowPublish(false)} onPublished={onClose} /> : null}
  </section>;
}
