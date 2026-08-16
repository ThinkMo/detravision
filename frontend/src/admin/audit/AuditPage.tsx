import { FormEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { AuditLog } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminHeading } from "../shared";

const PAGE_SIZE = 50;
type Filters = { actor: string; action: string; outcome: string; from: string; to: string };
const empty: Filters = { actor: "", action: "", outcome: "", from: "", to: "" };
function pathFor(page: number, filters: Filters) { const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) }); if (filters.actor) params.set("actor_id", filters.actor); if (filters.action.trim()) params.set("action", filters.action.trim()); if (filters.outcome) params.set("outcome", filters.outcome); if (filters.from) params.set("created_from", new Date(filters.from).toISOString()); if (filters.to) params.set("created_to", new Date(filters.to).toISOString()); return `/api/audit-logs?${params}`; }
export function AuditPage() {
  const [page, setPage] = useState(0); const [draft, setDraft] = useState(empty); const [filters, setFilters] = useState(empty); const path = useMemo(() => pathFor(page, filters), [page, filters]); const query = useQuery({ queryKey: ["admin", "audit", path], queryFn: () => api.request<AuditLog[]>(path) });
  if (query.isLoading) return <LoadingPanel label="正在加载审计记录" />; if (query.isError || !query.data) return <ErrorPanel message="暂时无法读取审计记录" onRetry={() => void query.refetch()} />;
  return <section className="admin-page"><AdminHeading eyebrow="AUDIT TRAIL" title="审计日志" description="追踪登录、配置和业务变更" /><form className="admin-filters" onSubmit={(event: FormEvent) => { event.preventDefault(); setPage(0); setFilters({ ...draft }); }}><label>操作者 ID<input type="number" min="1" value={draft.actor} onChange={(event) => setDraft({ ...draft, actor: event.target.value })} /></label><label>动作<input value={draft.action} onChange={(event) => setDraft({ ...draft, action: event.target.value })} /></label><label>结果<select value={draft.outcome} onChange={(event) => setDraft({ ...draft, outcome: event.target.value })}><option value="">全部</option><option value="success">成功</option><option value="rejected">拒绝</option></select></label><label>开始时间<input type="datetime-local" value={draft.from} onChange={(event) => setDraft({ ...draft, from: event.target.value })} /></label><label>结束时间<input type="datetime-local" value={draft.to} onChange={(event) => setDraft({ ...draft, to: event.target.value })} /></label><button type="submit">筛选</button></form>
    {query.data.length === 0 ? <EmptyState message="当前条件下没有审计记录" /> : <div className="admin-table-wrap" role="region" aria-label="审计日志列表" tabIndex={0}><table className="admin-table"><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>结果</th><th>来源 IP</th></tr></thead><tbody>{query.data.map((log) => <tr key={log.id}><td data-label="时间">{new Date(log.created_at).toLocaleString("zh-CN", { hour12: false })}</td><td data-label="操作者">{log.actor_id == null ? "系统/匿名" : `#${log.actor_id}`}</td><td data-label="动作">{log.action}</td><td data-label="资源">{log.resource_type}{log.resource_id ? ` #${log.resource_id}` : ""}</td><td data-label="结果">{log.outcome}</td><td data-label="来源 IP">{log.ip || "—"}</td></tr>)}</tbody></table></div>}
    <nav className="admin-pagination" aria-label="审计分页"><button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</button><span>第 {page + 1} 页</span><button type="button" className="secondary-button" onClick={() => setPage((value) => value + 1)}>下一页</button></nav>
  </section>;
}
