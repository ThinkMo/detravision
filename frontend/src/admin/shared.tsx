import type { ReactNode } from "react";

export function AdminHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <header className="admin-heading"><div><p>{eyebrow}</p><h1>{title}</h1><span>{description}</span></div>{action}</header>;
}

export function AdminNotice({ message, error = false }: { message: string; error?: boolean }) {
  return message ? <p className={`admin-notice${error ? " admin-notice--error" : ""}`} role={error ? "alert" : "status"}>{message}</p> : null;
}

export function ConfirmDialog({ title, children, confirmLabel, pending, onConfirm, onCancel }: { title: string; children: ReactNode; confirmLabel: string; pending?: boolean; onConfirm: () => void; onCancel: () => void }) {
  return <div className="admin-dialog-backdrop"><section className="admin-dialog" role="dialog" aria-modal="true" aria-label={title}><h2>{title}</h2><div>{children}</div><footer><button type="button" className="secondary-button" onClick={onCancel}>取消</button><button type="button" className="danger-button" disabled={pending} onClick={onConfirm}>{pending ? "正在提交" : confirmLabel}</button></footer></section></div>;
}

export function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
