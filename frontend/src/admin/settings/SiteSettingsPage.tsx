import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { SiteSettings } from "../../api/types";
import { useBrand } from "../../brand/BrandProvider";
import { ErrorPanel } from "../../components/ErrorPanel";
import { LoadingPanel } from "../../components/LoadingPanel";
import { AdminHeading, AdminNotice, errorMessage } from "../shared";

const logoTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
export function SiteSettingsPage() {
  const client = useQueryClient(); const { siteName, logoUrl, refresh } = useBrand(); const settings = useQuery({ queryKey: ["admin", "site-settings"], queryFn: () => api.request<SiteSettings>("/api/settings/site") }); const [name, setName] = useState(siteName); const [logoError, setLogoError] = useState(""); const [message, setMessage] = useState("");
  useEffect(() => { if (settings.data) setName(settings.data.site_name); }, [settings.data]);
  const save = useMutation({ mutationFn: () => api.request<SiteSettings>("/api/settings/site", { method: "PATCH", body: JSON.stringify({ site_name: name.trim() }) }), onSuccess: async () => { await refresh(); setMessage("品牌设置已保存"); void client.invalidateQueries({ queryKey: ["admin", "site-settings"] }); } });
  const upload = useMutation({ mutationFn: (file: File) => { const body = new FormData(); body.set("file", file); return api.request<SiteSettings>("/api/settings/site/logo", { method: "POST", body }); }, onSuccess: async () => { await refresh(); setMessage("Logo 已更新"); setLogoError(""); void client.invalidateQueries({ queryKey: ["admin", "site-settings"] }); } });
  if (settings.isLoading) return <LoadingPanel label="正在加载品牌设置" />; if (settings.isError) return <ErrorPanel message="暂时无法读取品牌设置" onRetry={() => void settings.refetch()} />;
  const chooseLogo = (file?: File) => { setLogoError(""); if (!file) return; if (!logoTypes.has(file.type)) { setLogoError("Logo 仅支持 PNG、JPEG 或 WebP 图片"); return; } if (file.size > 2 * 1024 * 1024) { setLogoError("Logo 文件不能超过 2 MB"); return; } upload.mutate(file); };
  return <section className="admin-page"><AdminHeading eyebrow="BRANDING" title="品牌设置" description="配置全站名称与 Logo" /><AdminNotice message={logoError || message || (save.isError ? errorMessage(save.error, "保存失败") : upload.isError ? errorMessage(upload.error, "上传失败") : "")} error={Boolean(logoError || save.isError || upload.isError)} />
    <div className="brand-preview"><span>{logoUrl ? <img src={logoUrl} alt="当前 Logo" /> : siteName.slice(0, 1).toUpperCase()}</span><div><small>当前品牌</small><strong>{siteName}</strong></div></div>
    <form className="admin-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (name.trim()) save.mutate(); }}><label>网站名称<input required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label><footer><button type="submit" disabled={save.isPending}>保存品牌设置</button></footer></form>
    <div className="admin-form"><h2>网站 Logo</h2><p>支持 PNG、JPEG 或 WebP，最大 2 MB；服务器还会验证真实图片内容和尺寸。</p><label className="file-button">上传 Logo<input type="file" accept="image/png,image/jpeg,image/webp" aria-label="上传 Logo" onChange={(event) => chooseLogo(event.target.files?.[0])} /></label>{upload.isPending ? <p role="status">正在上传 Logo</p> : null}</div>
  </section>;
}
