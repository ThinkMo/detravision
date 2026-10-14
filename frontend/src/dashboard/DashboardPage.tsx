import { useQuery } from "@tanstack/react-query";
import { Activity, BellRing, Camera, CircleGauge, RadioTower } from "lucide-react";
import { api } from "../api/client";
import type { DashboardSummary } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { LoadingPanel } from "../components/LoadingPanel";
import { StatusBadge } from "../components/StatusBadge";
import { WorkspaceActions } from "./WorkspaceActions";
import "./dashboard.css";

export function useDashboardSummary() {
  return useQuery({
    queryKey: ["dashboard", "summary"],
    queryFn: () => api.request<DashboardSummary>("/api/dashboard/summary"),
    staleTime: 15_000,
  });
}

function eventStatus(status: string) {
  if (status === "new") return { label: "待处理", tone: "warning" as const };
  if (status === "ack") return { label: "已确认", tone: "safe" as const };
  return { label: "已关闭", tone: "neutral" as const };
}

export function DashboardPage() {
  const summary = useDashboardSummary();

  if (summary.isLoading) return <LoadingPanel label="正在汇总安全态势" />;
  if (summary.isError || !summary.data) {
    return <ErrorPanel message="暂时无法读取运营数据" onRetry={() => void summary.refetch()} />;
  }

  const data = summary.data;
  const cameraHealth = data.camera_total === 0
    ? 0
    : Math.round((data.camera_active / data.camera_total) * 100);

  return (
    <section className="dashboard" aria-labelledby="dashboard-title">
      <div className="dashboard__intro">
        <div>
          <p className="dashboard__eyebrow">OPERATIONS OVERVIEW</p>
          <h1 id="dashboard-title">安全态势</h1>
          <p>集中查看摄像头、事件与推理服务的当前运行状态。</p>
        </div>
        <StatusBadge tone={data.events_new > 0 ? "warning" : "safe"}>
          {data.events_new > 0 ? `${data.events_new} 项待处理` : "当前无待处理事件"}
        </StatusBadge>
      </div>

      <WorkspaceActions />

      <div className="metric-grid">
        <article className="metric-card">
          <span className="metric-card__icon"><Camera aria-hidden="true" size={20} /></span>
          <p>在线摄像头</p><strong>{data.camera_active} / {data.camera_total}</strong>
          <small>当前启用 / 已配置</small>
        </article>
        <article className="metric-card">
          <span className="metric-card__icon metric-card__icon--warning"><BellRing aria-hidden="true" size={20} /></span>
          <p>今日事件</p><strong>{data.events_today}</strong>
          <small>UTC 当日累计</small>
        </article>
        <article className="metric-card">
          <span className="metric-card__icon metric-card__icon--danger"><Activity aria-hidden="true" size={20} /></span>
          <p>待处理事件</p><strong>{data.events_new}</strong>
          <small>需要确认或关闭</small>
        </article>
        <article className="metric-card">
          <span className="metric-card__icon"><CircleGauge aria-hidden="true" size={20} /></span>
          <p>已处理帧</p><strong>{data.runtime.processed_frames.toLocaleString("zh-CN")}</strong>
          <small>当前运行周期</small>
        </article>
      </div>

      <div className="dashboard__columns">
        <article className="dashboard-panel runtime-panel">
          <div className="dashboard-panel__heading">
            <div><p>运行状态</p><h2>推理服务</h2></div>
            <RadioTower aria-hidden="true" size={22} />
          </div>
          <div className="runtime-panel__status">
            <StatusBadge tone={data.runtime.enabled ? "safe" : "neutral"}>
              {data.runtime.enabled ? "服务已启用" : "服务未启用"}
            </StatusBadge>
            <strong>{data.runtime.active_cameras.length}</strong>
            <span>路实时采集</span>
          </div>
          <dl className="runtime-panel__details">
            <div><dt>队列帧</dt><dd>{data.runtime.queued_frames}</dd></div>
            <div><dt>丢弃帧</dt><dd>{data.runtime.dropped_frames}</dd></div>
            <div><dt>采集异常</dt><dd>{Object.keys(data.runtime.capture_errors).length}</dd></div>
          </dl>
        </article>

        <article className="dashboard-panel camera-health">
          <div className="dashboard-panel__heading"><div><p>设备概览</p><h2>摄像头健康度</h2></div></div>
          {data.camera_total === 0 ? (
            <EmptyState message="尚未配置摄像头" />
          ) : (
            <>
              <div className="camera-health__score"><strong>{cameraHealth}%</strong><span>启用率</span></div>
              <div className="camera-health__track" aria-label={`摄像头启用率 ${cameraHealth}%`}>
                <span style={{ width: `${cameraHealth}%` }} />
              </div>
              <p>{data.camera_active} 路已启用，{data.camera_total - data.camera_active} 路未启用</p>
            </>
          )}
        </article>
      </div>

      <article className="dashboard-panel recent-events">
        <div className="dashboard-panel__heading"><div><p>最新动态</p><h2>近期事件</h2></div></div>
        {data.recent_events.length === 0 ? (
          <EmptyState message="暂无事件记录" />
        ) : (
          <ul>
            {data.recent_events.map((event) => {
              const status = eventStatus(event.status);
              return (
                <li key={event.id}>
                  <span className="recent-events__marker" aria-hidden="true" />
                  <div><strong>{event.event_type}</strong><small>摄像头 #{event.camera_id}</small></div>
                  <span className="recent-events__confidence">{Math.round(event.confidence * 100)}%</span>
                  <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</time>
                  <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                </li>
              );
            })}
          </ul>
        )}
      </article>
    </section>
  );
}
