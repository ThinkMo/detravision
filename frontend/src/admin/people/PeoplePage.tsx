import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Person } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminHeading, AdminNotice, ConfirmDialog, errorMessage } from "../shared";

type FormState = { id: number | null; name: string; dept: string; phone: string; feishu: string };
const empty: FormState = { id: null, name: "", dept: "", phone: "", feishu: "" };

export function PeoplePage() {
  const client = useQueryClient(); const query = useQuery({ queryKey: ["admin", "people"], queryFn: () => api.request<Person[]>("/api/persons") });
  const [form, setForm] = useState<FormState | null>(null); const [deleting, setDeleting] = useState<Person | null>(null); const [message, setMessage] = useState("");
  const save = useMutation({ mutationFn: (value: FormState) => api.request<Person>(value.id ? `/api/persons/${value.id}` : "/api/persons", { method: value.id ? "PATCH" : "POST", body: JSON.stringify({ name: value.name.trim(), dept: value.dept.trim() || null, phone: value.phone.trim() || null, feishu_id: value.feishu.trim() || null }) }), onSuccess: () => { setForm(null); setMessage("联系人已保存"); void client.invalidateQueries({ queryKey: ["admin", "people"] }); } });
  const remove = useMutation({ mutationFn: (id: number) => api.request<void>(`/api/persons/${id}`, { method: "DELETE" }), onSuccess: () => { setDeleting(null); void client.invalidateQueries({ queryKey: ["admin", "people"] }); } });
  if (query.isLoading) return <LoadingPanel label="正在加载联系人" />; if (query.isError || !query.data) return <ErrorPanel message="暂时无法读取联系人" onRetry={() => void query.refetch()} />;
  return <section className="admin-page"><AdminHeading eyebrow="CONTACTS" title="告警联系人" description="管理告警通知人员" action={<button type="button" onClick={() => setForm(empty)}>新增联系人</button>} /><AdminNotice message={message || (save.isError ? errorMessage(save.error, "保存失败") : "")} error={save.isError} />
    {form ? <form className="admin-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (form.name.trim()) save.mutate(form); }}><h2>{form.id ? "编辑联系人" : "新增联系人"}</h2><label>姓名<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label><label>部门<input value={form.dept} onChange={(event) => setForm({ ...form, dept: event.target.value })} /></label><label>电话<input value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} /></label><label>飞书 ID<input value={form.feishu} onChange={(event) => setForm({ ...form, feishu: event.target.value })} /></label><footer><button type="button" className="secondary-button" onClick={() => setForm(null)}>取消</button><button type="submit" disabled={save.isPending}>保存</button></footer></form> : null}
    {query.data.length === 0 ? <EmptyState message="暂无告警联系人" /> : <div className="admin-card-grid">{query.data.map((person) => <article className="admin-card" key={person.id}><h3>{person.name}</h3><p>{person.dept || "未设置部门"}</p><small>{person.phone || "未设置电话"}</small><footer><button type="button" className="table-action" aria-label={`编辑${person.name}`} onClick={() => setForm({ id: person.id, name: person.name, dept: person.dept || "", phone: person.phone || "", feishu: person.feishu_id || "" })}>编辑</button><button type="button" className="table-action table-action--danger" aria-label={`删除${person.name}`} onClick={() => setDeleting(person)}>删除</button></footer></article>)}</div>}
    {deleting ? <ConfirmDialog title={`删除联系人“${deleting.name}”`} confirmLabel="确认删除" pending={remove.isPending} onCancel={() => setDeleting(null)} onConfirm={() => remove.mutate(deleting.id)}><p>删除后将从关联告警组中移除，且无法撤销。</p></ConfirmDialog> : null}</section>;
}
