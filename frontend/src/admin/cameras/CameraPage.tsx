import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { AlarmGroup, Camera } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminHeading, AdminNotice, ConfirmDialog, errorMessage } from "../shared";

type FormState = { id: number | null; name: string; rtsp: string; enabled: boolean; fps: string; groupId: string; classes: string; roi: number[][] };
const emptyForm: FormState = { id: null, name: "", rtsp: "", enabled: true, fps: "1", groupId: "", classes: "no_helmet", roi: [] };

export function CameraPage() {
  const queryClient = useQueryClient();
  const cameras = useQuery({ queryKey: ["admin", "cameras"], queryFn: () => api.request<Camera[]>("/api/cameras") });
  const groups = useQuery({ queryKey: ["admin", "alarm-groups", "options"], queryFn: () => api.request<AlarmGroup[]>("/api/alarm-groups") });
  const [form, setForm] = useState<FormState | null>(null);
  const [deleting, setDeleting] = useState<Camera | null>(null);
  const [message, setMessage] = useState("");
  const save = useMutation({
    mutationFn: async (value: FormState) => {
      const payload: Record<string, unknown> = { name: value.name.trim(), enabled: value.enabled, sample_fps: Number(value.fps), alarm_group_id: value.groupId ? Number(value.groupId) : null, roi: value.roi, detect_classes: value.classes.split(",").map((item) => item.trim()).filter(Boolean) };
      if (value.rtsp.trim()) payload.rtsp_url = value.rtsp.trim();
      return api.request<Camera>(value.id ? `/api/cameras/${value.id}` : "/api/cameras", { method: value.id ? "PATCH" : "POST", body: JSON.stringify(payload) });
    },
    onSuccess: () => { setForm(null); setMessage("摄像头已保存"); void queryClient.invalidateQueries({ queryKey: ["admin", "cameras"] }); },
  });
  const remove = useMutation({ mutationFn: (id: number) => api.request<void>(`/api/cameras/${id}`, { method: "DELETE" }), onSuccess: () => { setDeleting(null); setMessage("摄像头已删除"); void queryClient.invalidateQueries({ queryKey: ["admin", "cameras"] }); } });

  if (cameras.isLoading || groups.isLoading) return <LoadingPanel label="正在加载摄像头" />;
  if (cameras.isError || groups.isError || !cameras.data || !groups.data) return <ErrorPanel message="暂时无法读取摄像头" onRetry={() => { void cameras.refetch(); void groups.refetch(); }} />;
  const submit = (event: FormEvent) => { event.preventDefault(); if (!form?.name.trim() || (!form.id && !form.rtsp.trim()) || Number(form.fps) <= 0) return; save.mutate(form); };
  return <section className="admin-page"><AdminHeading eyebrow="CAMERAS" title="摄像头" description="配置视频源、采样和告警组" action={<button type="button" onClick={() => setForm(emptyForm)}>新增摄像头</button>} />
    <AdminNotice message={message || (save.isError ? errorMessage(save.error, "保存失败") : remove.isError ? errorMessage(remove.error, "删除失败") : "")} error={save.isError || remove.isError} />
    {form ? <form className="admin-form" onSubmit={submit}><h2>{form.id ? "编辑摄像头" : "新增摄像头"}</h2><label>名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label><label>{form.id ? "替换 RTSP 地址（留空则保持不变）" : "RTSP 地址"}<input required={!form.id} type="password" autoComplete="off" value={form.rtsp} onChange={(event) => setForm({ ...form, rtsp: event.target.value })} /></label><label>采样 FPS<input type="number" min="0.1" max="30" step="0.1" required value={form.fps} onChange={(event) => setForm({ ...form, fps: event.target.value })} /></label><label>检测类别（逗号分隔）<input required value={form.classes} onChange={(event) => setForm({ ...form, classes: event.target.value })} /></label><label>告警组<select value={form.groupId} onChange={(event) => setForm({ ...form, groupId: event.target.value })}><option value="">不关联</option>{groups.data.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label><label className="admin-check"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用</label><footer><button type="button" className="secondary-button" onClick={() => setForm(null)}>取消</button><button disabled={save.isPending} type="submit">保存</button></footer></form> : null}
    {cameras.data.length === 0 ? <EmptyState message="尚未配置摄像头" /> : <div className="admin-table-wrap" role="region" aria-label="摄像头列表" tabIndex={0}><table className="admin-table"><thead><tr><th>名称</th><th>视频源</th><th>状态</th><th>FPS</th><th>操作</th></tr></thead><tbody>{cameras.data.map((camera) => <tr key={camera.id}><td data-label="名称">{camera.name}</td><td data-label="视频源"><code>{camera.rtsp_configured ? "rtsp://••••••••" : "未配置"}</code></td><td data-label="状态">{camera.enabled ? "启用" : "停用"}</td><td data-label="FPS">{camera.sample_fps}</td><td data-label="操作"><button type="button" className="table-action" onClick={() => setForm({ id: camera.id, name: camera.name, rtsp: "", enabled: camera.enabled, fps: String(camera.sample_fps), groupId: camera.alarm_group_id == null ? "" : String(camera.alarm_group_id), classes: camera.detect_classes.join(", "), roi: camera.roi })}>编辑</button><button type="button" className="table-action table-action--danger" aria-label={`删除${camera.name}`} onClick={() => { remove.reset(); setDeleting(camera); }}>删除</button></td></tr>)}</tbody></table></div>}
    {deleting ? <ConfirmDialog title={`删除摄像头“${deleting.name}”`} confirmLabel="确认删除" pending={remove.isPending} error={remove.isError ? errorMessage(remove.error, "删除失败") : ""} onCancel={() => { remove.reset(); setDeleting(null); }} onConfirm={() => remove.mutate(deleting.id)}><p>删除后无法撤销，历史事件仍会保留。</p></ConfirmDialog> : null}
  </section>;
}
