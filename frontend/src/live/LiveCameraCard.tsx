import { Radio, RefreshCw, VideoOff } from "lucide-react";
import type { Camera } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { useJpegStream } from "./useJpegStream";

const streamLabels = {
  idle: "未连接",
  connecting: "正在连接",
  connected: "实时传输",
  reconnecting: "正在重连",
} as const;

export function LiveCameraCard({ camera }: { camera: Camera }) {
  const stream = useJpegStream(camera.id, camera.enabled);

  return (
    <article className="live-surface" aria-label={`${camera.name}监控画面`}>
      <header className="live-surface__header">
        <div>
          <p>当前画面</p>
          <h2>{camera.name}</h2>
        </div>
        <StatusBadge tone={stream.state === "connected" ? "safe" : stream.state === "reconnecting" ? "warning" : "neutral"}>
          {streamLabels[stream.state]}
        </StatusBadge>
      </header>
      <div className="live-surface__viewport">
        {!camera.enabled ? (
          <div className="live-surface__placeholder"><VideoOff aria-hidden="true" /><p>该摄像头尚未启用</p></div>
        ) : stream.frameUrl ? (
          <img src={stream.frameUrl} alt={`${camera.name}实时画面`} />
        ) : (
          <div className="live-surface__placeholder">
            {stream.state === "reconnecting" ? <RefreshCw aria-hidden="true" /> : <Radio aria-hidden="true" />}
            <p>{streamLabels[stream.state]}</p>
            <small>等待摄像头返回首帧，不显示模拟画面</small>
          </div>
        )}
      </div>
    </article>
  );
}
