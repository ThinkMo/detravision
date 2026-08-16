import { ImagePlus, Trash2, UploadCloud } from "lucide-react";
import { type ChangeEvent, type DragEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

const MAX_IMAGES = 20;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const allowed = new Map([
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
]);

type Preview = { file: File; url: string; key: string };

function fileKey(file: File) {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function UploadDropzone({
  pending,
  progress,
  onSubmit,
}: {
  pending: boolean;
  progress: number;
  onSubmit: (files: File[]) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const previews = useRef<Preview[]>([]);
  const [items, setItems] = useState<Preview[]>([]);
  const [message, setMessage] = useState("");

  useEffect(() => {
    previews.current = items;
  }, [items]);
  useEffect(() => () => previews.current.forEach((item) => URL.revokeObjectURL(item.url)), []);

  const addFiles = (incoming: File[]) => {
    setMessage("");
    if (items.length + incoming.length > MAX_IMAGES) {
      setMessage("单次最多上传 20 张图片");
      return;
    }
    const existing = new Set(items.map((item) => item.key));
    const acceptedFiles: Array<{ file: File; key: string }> = [];
    let duplicateCount = 0;
    for (const file of incoming) {
      const key = fileKey(file);
      if (existing.has(key)) { duplicateCount += 1; continue; }
      const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      const expectedMime = allowed.get(extension);
      if (!expectedMime || file.type !== expectedMime) {
        setMessage("仅支持扩展名与类型匹配的 PNG、JPEG 或 WebP 图片");
        return;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        setMessage(`${file.name} 超过每张 10 MB 的限制`);
        return;
      }
      existing.add(key);
      acceptedFiles.push({ file, key });
    }
    const accepted = acceptedFiles.map(({ file, key }) => ({ file, key, url: URL.createObjectURL(file) }));
    setItems((current) => [...current, ...accepted]);
    if (duplicateCount) setMessage(`已忽略 ${duplicateCount} 张重复图片`);
  };

  const change = (event: ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };
  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!pending) addFiles(Array.from(event.dataTransfer.files));
  };
  const activate = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!pending && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      input.current?.click();
    }
  };
  const remove = (key: string) => setItems((current) => current.filter((item) => {
    if (item.key !== key) return true;
    URL.revokeObjectURL(item.url);
    return false;
  }));
  const totalBytes = items.reduce((sum, item) => sum + item.file.size, 0);

  return (
    <section className="batch-panel batch-upload" aria-labelledby="batch-upload-title">
      <div className="batch-panel__heading"><div><p>NEW RECOGNITION</p><h2 id="batch-upload-title">上传图片识别</h2></div><span>{items.length} / 20</span></div>
      <div
        className="batch-dropzone"
        role="button"
        tabIndex={pending ? -1 : 0}
        aria-disabled={pending}
        onClick={() => !pending && input.current?.click()}
        onKeyDown={activate}
        onDragOver={(event) => event.preventDefault()}
        onDrop={drop}
      >
        <UploadCloud aria-hidden="true" size={28} />
        <strong>拖放多张图片，或点击选择</strong>
        <span>PNG、JPEG、WebP · 每张不超过 10 MB · 最多 20 张</span>
        <input ref={input} className="sr-only" aria-label="选择识别图片" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" multiple disabled={pending} onChange={change} />
      </div>
      {message ? <p className="batch-alert" role="alert">{message}</p> : null}
      {items.length ? (
        <>
          <ul className="batch-previews" aria-label="待上传图片">
            {items.map((item) => <li key={item.key}><img src={item.url} alt="" /><span><strong>{item.file.name}</strong><small>{formatBytes(item.file.size)}</small></span><button type="button" aria-label={`移除 ${item.file.name}`} disabled={pending} onClick={(event) => { event.stopPropagation(); remove(item.key); }}><Trash2 aria-hidden="true" size={16} /></button></li>)}
          </ul>
          <div className="batch-upload__footer"><span>总计 {formatBytes(totalBytes)}</span><button type="button" disabled={pending} onClick={() => onSubmit(items.map((item) => item.file))}><ImagePlus aria-hidden="true" size={17} />{pending ? "正在上传…" : "开始识别"}</button></div>
          {pending ? <div className="batch-progress" role="progressbar" aria-label="上传进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div> : null}
        </>
      ) : null}
    </section>
  );
}
