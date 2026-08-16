import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Search } from "lucide-react";
import { api } from "../api/client";
import type { Camera, Event } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { LoadingPanel } from "../components/LoadingPanel";
import { EventDetail, eventStatus } from "./EventDetail";
import "./events.css";

type Filters = { cameraId: string; eventType: string; status: string; createdFrom: string; createdTo: string };
const emptyFilters: Filters = { cameraId: "", eventType: "", status: "", createdFrom: "", createdTo: "" };

function queryPath(filters: Filters) {
  const params = new URLSearchParams();
  if (filters.cameraId) params.set("camera_id", filters.cameraId);
  if (filters.eventType.trim()) params.set("event_type", filters.eventType.trim());
  if (filters.status) params.set("status", filters.status);
  if (filters.createdFrom) params.set("created_from", new Date(filters.createdFrom).toISOString());
  if (filters.createdTo) params.set("created_to", new Date(filters.createdTo).toISOString());
  const query = params.toString();
  return query ? `/api/events?${query}` : "/api/events";
}

export function EventsPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(emptyFilters);
  const [filters, setFilters] = useState(emptyFilters);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const eventQueryKey = useMemo(() => ["events", filters] as const, [filters]);
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api.request<Camera[]>("/api/cameras") });
  const events = useQuery({ queryKey: eventQueryKey, queryFn: () => api.request<Event[]>(queryPath(filters)) });
  const mutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: "ack" | "closed" }) => api.request<Event>(`/api/events/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
    onMutate: async ({ id, status }) => {
      setMessage("");
      await queryClient.cancelQueries({ queryKey: eventQueryKey });
      const previous = queryClient.getQueryData<Event[]>(eventQueryKey);
      queryClient.setQueryData<Event[]>(eventQueryKey, (current = []) => current.map((item) => item.id === id ? { ...item, status } : item));
      return { previous };
    },
    onSuccess: (updated) => {
      queryClient.setQueryData<Event[]>(eventQueryKey, (current = []) => current.map((item) => item.id === updated.id ? updated : item));
    },
    onError: (error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(eventQueryKey, context.previous);
      setMessage(error instanceof Error ? error.message : "事件状态更新失败");
    },
  });

  const selected = events.data?.find((event) => event.id === selectedId) ?? null;
  const cameraById = new Map((cameras.data ?? []).map((camera) => [camera.id, camera]));
  const submit = (event: FormEvent) => { event.preventDefault(); setFilters({ ...draft }); };

  if (events.isLoading || cameras.isLoading) return <LoadingPanel label="正在加载事件" />;
  if (events.isError || cameras.isError || !events.data || !cameras.data) {
    return <ErrorPanel message="暂时无法读取事件记录" onRetry={() => { void events.refetch(); void cameras.refetch(); }} />;
  }

  return (
    <section className="events-page" aria-labelledby="events-title">
      <header className="page-heading"><div><p>INCIDENT CENTER</p><h1 id="events-title">事件中心</h1><span>筛选、核查并处置检测事件</span></div><button className="secondary-button" type="button" onClick={() => void events.refetch()}><RefreshCw aria-hidden="true" size={16} />刷新</button></header>
      <form className="event-filters" onSubmit={submit}>
        <label>摄像头<select value={draft.cameraId} onChange={(e) => setDraft({ ...draft, cameraId: e.target.value })}><option value="">全部</option>{cameras.data.map((camera) => <option key={camera.id} value={camera.id}>{camera.name}</option>)}</select></label>
        <label>类型<input value={draft.eventType} onChange={(e) => setDraft({ ...draft, eventType: e.target.value })} placeholder="例如 no_helmet" /></label>
        <label>状态<select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}><option value="">全部</option><option value="new">待处理</option><option value="ack">已确认</option><option value="closed">已关闭</option></select></label>
        <label>开始时间<input type="datetime-local" value={draft.createdFrom} onChange={(e) => setDraft({ ...draft, createdFrom: e.target.value })} /></label>
        <label>结束时间<input type="datetime-local" value={draft.createdTo} onChange={(e) => setDraft({ ...draft, createdTo: e.target.value })} /></label>
        <button type="submit"><Search aria-hidden="true" size={17} />筛选</button>
      </form>
      {message ? <div className="event-message" role="status" aria-live="polite">{message}</div> : null}
      {events.data.length === 0 ? <EmptyState message="当前筛选条件下没有事件" /> : (
        <div className="events-layout">
          <div className="events-table-wrap">
            <table className="events-table"><thead><tr><th>事件</th><th>摄像头</th><th>置信度</th><th>时间</th><th>状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>
              {events.data.map((event) => { const status = eventStatus(event.status); return <tr key={event.id} className={event.id === selectedId ? "events-table__selected" : ""}>
                <td><strong>{event.event_type}</strong><small>#{event.id}</small></td><td>{cameraById.get(event.camera_id)?.name ?? `#${event.camera_id}`}</td><td>{Math.round(event.confidence * 100)}%</td><td><time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</time></td><td><span className={`event-status event-status--${status.tone}`}>{status.label}</span></td><td><button type="button" className="table-action" aria-label={`查看事件 #${event.id}`} onClick={() => setSelectedId(event.id)}>查看</button></td>
              </tr>; })}
            </tbody></table>
          </div>
          {selected ? <EventDetail event={selected} camera={cameraById.get(selected.camera_id)} pending={mutation.isPending} onStatus={(status) => mutation.mutate({ id: selected.id, status })} /> : <aside className="event-detail event-detail--empty"><p>选择一条事件查看抓拍与处置操作</p></aside>}
        </div>
      )}
    </section>
  );
}
