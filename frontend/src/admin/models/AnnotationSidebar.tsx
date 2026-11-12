import type { AnnotationBoxInput, TrainingDatasetImage } from "../../api/types";
import { annotationColor } from "./annotationColors";

type Props = { pagination: { label: string; pageSize: number; page: number; pages: number; total: number; onPage: (page: number) => void; disabled: boolean }; images: TrainingDatasetImage[]; selectedId?: number; boxes: AnnotationBoxInput[]; classes: string[]; activeClass: number; onClass: (id: number) => void; onImage: (id: number) => void; onDeleteBox: (index: number) => void; selection?: { ids: number[]; disabled: boolean; onToggle: (id: number) => void; onAll: () => void; onRemove: () => void } };

export function AnnotationSidebar({ pagination, images, selectedId, boxes, classes, activeClass, onClass, onImage, onDeleteBox, selection }: Props) {
  return <aside className="annotation-sidebar">
    <label>当前类别<select value={activeClass} onChange={(event) => onClass(Number(event.target.value))}>{classes.map((name, id) => <option key={name} value={id}>{id}: {name}</option>)}</select></label>
    <div className="annotation-class-legend" aria-label="类别颜色">{classes.map((name, id) => <span key={name}><i style={{ backgroundColor: annotationColor(id, classes) }} />{name}</span>)}</div>
    <h3>图片</h3>
    {selection ? <div className="annotation-selection-toolbar"><label><input type="checkbox" checked={images.length > 0 && images.every(image => selection.ids.includes(image.id))} disabled={selection.disabled} onChange={selection.onAll} />全选本页</label><button type="button" className="annotation-delete-button" disabled={!selection.ids.length || selection.disabled} onClick={selection.onRemove}>删除所选（{selection.ids.length}）</button></div> : null}
    <div className="annotation-image-list">{images.map((image) => <div className={`annotation-image-row${selection ? " annotation-image-row--selectable" : ""}`} key={image.id}>
      {selection ? <input type="checkbox" aria-label={`选择图片 ${image.display_name}`} checked={selection.ids.includes(image.id)} disabled={selection.disabled} onChange={() => selection.onToggle(image.id)} /> : null}
      <button type="button" disabled={selection?.disabled} className={image.id === selectedId ? "is-active" : ""} onClick={() => onImage(image.id)}><span title={image.display_name}>{image.display_name}</span><small>{image.review_status === "pending_annotation" ? "待标注" : image.review_status === "confirmed_empty" ? "已确认无目标" : "已标注"}</small></button>
    </div>)}</div>
    <nav className="annotation-image-pagination" aria-label="标注图片分页">
      <span>{pagination.label} · 共 {pagination.total} 张</span><span>当前 {(pagination.page - 1) * pagination.pageSize + 1}–{Math.min(pagination.page * pagination.pageSize, pagination.total)} 张 · 第 {pagination.page} / {pagination.pages} 页</span>
      <div><button type="button" disabled={pagination.disabled || pagination.page <= 1} onClick={() => pagination.onPage(pagination.page - 1)}>上一页</button><button type="button" disabled={pagination.disabled || pagination.page >= pagination.pages} onClick={() => pagination.onPage(pagination.page + 1)}>下一页</button></div>
    </nav>
    <h3>标注框（{boxes.length}）</h3><ol className="annotation-box-list">{boxes.map((box, index) => <li key={index}><span><i className="annotation-class-dot" style={{ backgroundColor: annotationColor(box.class_id, classes) }} />{classes[box.class_id] ?? `类别 ${box.class_id}`}</span><button type="button" className="annotation-delete-button" onClick={() => onDeleteBox(index)}>删除</button></li>)}</ol>
  </aside>;
}