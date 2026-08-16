import { CheckCircle2, XCircle } from "lucide-react";
import type { Camera, Event } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

export const eventStatus = (status: Event["status"]) => status === "new"
  ? { label: "待处理", tone: "warning" as const }
  : status === "ack"
    ? { label: "已确认", tone: "safe" as const }
    : { label: "已关闭", tone: "neutral" as const };

export function EventDetail({
  event,
  camera,
  pending,
  onStatus,
}: {
  event: Event;
  camera?: Camera;
  pending: boolean;
  onStatus: (status: "ack" | "closed") => void;
}) {
  const status = eventStatus(event.status);
  return (
    <aside className="event-detail" aria-label={`事件 #${event.id} 详情`}>
      <header><div><p>事件 #{event.id}</p><h2>{event.event_type}</h2></div><StatusBadge tone={status.tone}>{status.label}</StatusBadge></header>
      {event.snapshot_path ? (
        <img src={`/api/events/${event.id}/snapshot`} alt={`事件 #${event.id} 抓拍`} />
      ) : (
        <div className="event-detail__no-image">该事件没有抓拍图片</div>
      )}
      <dl>
        <div><dt>摄像头</dt><dd>{camera?.name ?? `#${event.camera_id}`}</dd></div>
        <div><dt>置信度</dt><dd>{Math.round(event.confidence * 100)}%</dd></div>
        <div><dt>发生时间</dt><dd>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</dd></div>
      </dl>
      <div className="event-detail__actions">
        <button type="button" disabled={pending || event.status !== "new"} onClick={() => onStatus("ack")}><CheckCircle2 aria-hidden="true" size={17} />确认事件</button>
        <button type="button" disabled={pending || event.status === "closed"} onClick={() => onStatus("closed")}><XCircle aria-hidden="true" size={17} />关闭事件</button>
      </div>
    </aside>
  );
}
