import type { AnnotationBoxInput, TrainingDatasetImage } from "../../api/types";
import { annotationColor } from "./annotationColors";

type Props = { images: TrainingDatasetImage[]; selectedId?: number; boxes: AnnotationBoxInput[]; classes: string[]; activeClass: number; onClass: (id: number) => void; onImage: (id: number) => void; onDeleteBox: (index: number) => void };

export function AnnotationSidebar({ images, selectedId, boxes, classes, activeClass, onClass, onImage, onDeleteBox }: Props) {
  return <aside className="annotation-sidebar">
    <label>当前类别<select value={activeClass} onChange={(event) => onClass(Number(event.target.value))}>{classes.map((name, id) => <option key={name} value={id}>{id}: {name}</option>)}</select></label>
    <div className="annotation-class-legend" aria-label="类别颜色">{classes.map((name, id) => <span key={name}><i style={{ backgroundColor: annotationColor(id, classes) }} />{name}</span>)}</div>
    <h3>图片</h3><div className="annotation-image-list">{images.map((image) => <button type="button" className={image.id === selectedId ? "is-active" : ""} key={image.id} onClick={() => onImage(image.id)}><span>{image.display_name}</span><small>{image.review_status === "pending_annotation" ? "待标注" : image.review_status === "confirmed_empty" ? "已确认无目标" : "已标注"}</small></button>)}</div>
    <h3>标注框（{boxes.length}）</h3><ol className="annotation-box-list">{boxes.map((box, index) => <li key={index}><span><i className="annotation-class-dot" style={{ backgroundColor: annotationColor(box.class_id, classes) }} />{classes[box.class_id] ?? `类别 ${box.class_id}`}</span><button type="button" className="annotation-delete-button" onClick={() => onDeleteBox(index)}>删除</button></li>)}</ol>
  </aside>;
}
