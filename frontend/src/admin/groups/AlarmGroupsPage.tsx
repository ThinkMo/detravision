import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { AlarmGroup, Person } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminHeading, AdminNotice, ConfirmDialog, errorMessage } from "../shared";

type FormState = { id: number | null; name: string; personIds: number[] };
export function AlarmGroupsPage() {
  const client = useQueryClient(); const groups = useQuery({ queryKey: ["admin", "alarm-groups"], queryFn: () => api.request<AlarmGroup[]>("/api/alarm-groups") }); const people = useQuery({ queryKey: ["admin", "people", "options"], queryFn: () => api.request<Person[]>("/api/persons") });
  const [form, setForm] = useState<FormState | null>(null); const [deleting, setDeleting] = useState<AlarmGroup | null>(null); const [message, setMessage] = useState("");
  const save = useMutation({ mutationFn: (value: FormState) => api.request<AlarmGroup>(value.id ? `/api/alarm-groups/${value.id}` : "/api/alarm-groups", { method: value.id ? "PATCH" : "POST", body: JSON.stringify({ name: value.name.trim(), person_ids: value.personIds }) }), onSuccess: () => { setForm(null); setMessage("告警组已保存"); void client.invalidateQueries({ queryKey: ["admin", "alarm-groups"] }); } });
  const remove = useMutation({ mutationFn: (id: number) => api.request<void>(`/api/alarm-groups/${id}`, { method: "DELETE" }), onSuccess: () => { setDeleting(null); void client.invalidateQueries({ queryKey: ["admin", "alarm-groups"] }); } });
  if (groups.isLoading || people.isLoading) return <LoadingPanel label="正在加载告警组" />; if (groups.isError || people.isError || !groups.data || !people.data) return <ErrorPanel message="暂时无法读取告警组" onRetry={() => { void groups.refetch(); void people.refetch(); }} />;
  const toggle = (id: number) => form && setForm({ ...form, personIds: form.personIds.includes(id) ? form.personIds.filter((item) => item !== id) : [...form.personIds, id] });
  return <section className="admin-page"><AdminHeading eyebrow="ALARM GROUPS" title="告警组" description="按班组配置通知联系人" action={<button type="button" onClick={() => setForm({ id: null, name: "", personIds: [] })}>新增告警组</button>} /><AdminNotice message={message || (save.isError ? errorMessage(save.error, "保存失败") : remove.isError ? errorMessage(remove.error, "删除失败") : "")} error={save.isError || remove.isError} />
    {form ? <form className="admin-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (form.name.trim()) save.mutate(form); }}><h2>{form.id ? "编辑告警组" : "新增告警组"}</h2><label>告警组名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label><fieldset><legend>联系人</legend>{people.data.map((person) => <label className="admin-check" key={person.id}><input type="checkbox" checked={form.personIds.includes(person.id)} onChange={() => toggle(person.id)} />{person.name}{person.dept ? ` · ${person.dept}` : ""}</label>)}</fieldset><footer><button type="button" className="secondary-button" onClick={() => setForm(null)}>取消</button><button type="submit" disabled={save.isPending}>保存告警组</button></footer></form> : null}
    {groups.data.length === 0 ? <EmptyState message="暂无告警组" /> : <div className="admin-card-grid">{groups.data.map((group) => <article className="admin-card" key={group.id}><h3>{group.name}</h3><p>{group.members.length ? group.members.map((member) => member.name).join("、") : "暂未添加联系人"}</p><footer><button type="button" className="table-action" aria-label={`编辑${group.name}`} onClick={() => setForm({ id: group.id, name: group.name, personIds: group.members.map((member) => member.id) })}>编辑</button><button type="button" className="table-action table-action--danger" aria-label={`删除${group.name}`} onClick={() => { remove.reset(); setDeleting(group); }}>删除</button></footer></article>)}</div>}
    {deleting ? <ConfirmDialog title={`删除告警组“${deleting.name}”`} confirmLabel="确认删除" pending={remove.isPending} error={remove.isError ? errorMessage(remove.error, "删除失败") : ""} onCancel={() => { remove.reset(); setDeleting(null); }} onConfirm={() => remove.mutate(deleting.id)}><p>正在被摄像头使用的告警组无法删除。</p></ConfirmDialog> : null}</section>;
}
