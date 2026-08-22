import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Folder, FolderOpen } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import { LoadingPanel } from "../../components/LoadingPanel";
import { ModalDialog } from "../shared";
import { trainingApi } from "./trainingApi";

export function ServerDirectoryPicker({ rootAlias, initialPath, onClose, onSelect }: {
  rootAlias: string;
  initialPath: string;
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const [path, setPath] = useState(initialPath === "." ? "" : initialPath);
  const [message, setMessage] = useState("");
  const directories = useQuery({
    queryKey: ["admin", "training", "dataset-directories", rootAlias, path],
    queryFn: () => trainingApi.datasetDirectories(rootAlias, path),
  });
  const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";

  return <ModalDialog title="选择服务器训练目录" className="server-directory-dialog" onClose={onClose}>
    <div className="server-directory-toolbar">
      <button type="button" className="secondary-button" onClick={() => {
        if (!path) { setMessage("当前已在授权根目录。"); return; }
        setPath(parent);
        setMessage("");
      }}><ArrowLeft aria-hidden="true" size={16} />返回上级</button>
      <strong className="server-directory-breadcrumb">{rootAlias} / {path || "根目录"}</strong>
    </div>
    <div className="server-directory-list" role="list" aria-label="服务器文件夹">
      {directories.isLoading ? <LoadingPanel label="正在读取服务器目录" /> : directories.isError || !directories.data ? <p className="admin-dialog__error" role="alert">无法读取服务器目录，请重试。</p> : directories.data.items.length === 0 ? <EmptyState message="当前目录没有下级文件夹" /> : directories.data.items.map((item) => <div role="listitem" key={item.path}><button type="button" aria-label={`打开文件夹 ${item.name}`} onClick={() => { setPath(item.path); setMessage(""); }}><Folder aria-hidden="true" size={22} /><span>{item.name}</span></button></div>)}
    </div>
    {message ? <p className="admin-dialog__error" role="alert">{message}</p> : null}
    <footer>
      <button type="button" className="secondary-button" onClick={onClose}>取消</button>
      <button type="button" onClick={() => {
        if (!path) { setMessage("授权根目录不能直接作为训练集，请选择一个数据集文件夹。"); return; }
        onSelect(path);
      }}><FolderOpen aria-hidden="true" size={16} />选择此文件夹</button>
    </footer>
  </ModalDialog>;
}
