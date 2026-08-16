import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function AdminHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <header className="admin-heading"><div><p>{eyebrow}</p><h1>{title}</h1><span>{description}</span></div>{action}</header>;
}

export function AdminNotice({ message, error = false }: { message: string; error?: boolean }) {
  return message ? <p className={`admin-notice${error ? " admin-notice--error" : ""}`} role={error ? "alert" : "status"}>{message}</p> : null;
}

const focusableSelector = "button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])";

export function ModalDialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const backdrop = dialogRef.current?.parentElement;
    const siblings = Array.from(document.body.children).filter((element) => element !== backdrop);
    const prior = siblings.map((element) => ({
      element,
      ariaHidden: element.getAttribute("aria-hidden"),
      inert: element.hasAttribute("inert"),
    }));
    siblings.forEach((element) => {
      element.setAttribute("inert", "");
      element.setAttribute("aria-hidden", "true");
    });
    const first = dialogRef.current?.querySelector<HTMLElement>(focusableSelector);
    first?.focus();
    return () => {
      prior.forEach(({ element, ariaHidden, inert }) => {
        if (ariaHidden == null) element.removeAttribute("aria-hidden");
        else element.setAttribute("aria-hidden", ariaHidden);
        if (!inert) element.removeAttribute("inert");
      });
      if (previousFocus.current?.isConnected) previousFocus.current.focus();
    };
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? []);
    if (focusable.length === 0) { event.preventDefault(); dialogRef.current?.focus(); return; }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  return createPortal(
    <div className="admin-dialog-backdrop">
      <section ref={dialogRef} className="admin-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} onKeyDown={onKeyDown}>
        <h2 id={titleId}>{title}</h2>
        {children}
      </section>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({ title, children, confirmLabel, pending, error, onConfirm, onCancel }: { title: string; children: ReactNode; confirmLabel: string; pending?: boolean; error?: string; onConfirm: () => void; onCancel: () => void }) {
  return <ModalDialog title={title} onClose={() => { if (!pending) onCancel(); }}><div>{children}</div>{error ? <p className="admin-dialog__error" role="alert" aria-live="assertive">{error}</p> : null}<footer><button type="button" className="secondary-button" disabled={pending} onClick={onCancel}>取消</button><button type="button" className="danger-button" disabled={pending} onClick={onConfirm}>{pending ? "正在提交" : confirmLabel}</button></footer></ModalDialog>;
}

export function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
