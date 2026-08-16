import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Camera as CameraIcon } from "lucide-react";
import { api } from "../api/client";
import type { Camera } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { LoadingPanel } from "../components/LoadingPanel";
import { StatusBadge } from "../components/StatusBadge";
import { LiveCameraCard } from "./LiveCameraCard";
import "./live.css";

export function LivePage() {
  const cameras = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api.request<Camera[]>("/api/cameras"),
    staleTime: 10_000,
  });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    if (selectedId === null && cameras.data?.length) {
      setSelectedId((cameras.data.find((camera) => camera.enabled) ?? cameras.data[0]).id);
    }
  }, [cameras.data, selectedId]);

  if (cameras.isLoading) return <LoadingPanel label="正在加载摄像头" />;
  if (cameras.isError || !cameras.data) {
    return <ErrorPanel message="暂时无法读取摄像头列表" onRetry={() => void cameras.refetch()} />;
  }
  if (cameras.data.length === 0) return <EmptyState message="尚未配置摄像头" />;

  const selected = cameras.data.find((camera) => camera.id === selectedId) ?? cameras.data[0];
  return (
    <section className="live-page" aria-labelledby="live-title">
      <header className="page-heading">
        <div><p>LIVE OPERATIONS</p><h1 id="live-title">实时监控</h1><span>选择摄像头查看实际连接画面与状态</span></div>
        <StatusBadge tone={cameras.data.some((camera) => camera.enabled) ? "safe" : "neutral"}>
          {cameras.data.filter((camera) => camera.enabled).length} / {cameras.data.length} 路已启用
        </StatusBadge>
      </header>
      <div className="live-layout">
        <aside className="camera-selector" aria-label="摄像头列表">
          <div className="camera-selector__title"><CameraIcon aria-hidden="true" size={18} /><strong>摄像头</strong></div>
          {cameras.data.map((camera) => (
            <button
              type="button"
              className={camera.id === selected.id ? "camera-option camera-option--active" : "camera-option"}
              key={camera.id}
              onClick={() => setSelectedId(camera.id)}
              aria-pressed={camera.id === selected.id}
              aria-label={`${camera.name}，${camera.enabled ? "已启用" : "未启用"}`}
            >
              <span><strong>{camera.name}</strong><small>摄像头 #{camera.id}</small></span>
              <StatusBadge tone={camera.enabled ? "safe" : "neutral"}>{camera.enabled ? "已启用" : "未启用"}</StatusBadge>
            </button>
          ))}
        </aside>
        <LiveCameraCard camera={selected} />
      </div>
    </section>
  );
}
