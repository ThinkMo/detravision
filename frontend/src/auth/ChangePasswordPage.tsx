import { type FormEvent, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useBrand } from "../brand/BrandProvider";
import { useAuth } from "./AuthProvider";

interface PasswordFormElements extends HTMLFormControlsCollection {
  currentPassword: HTMLInputElement;
  newPassword: HTMLInputElement;
  confirmPassword: HTMLInputElement;
}

interface PasswordForm extends HTMLFormElement {
  readonly elements: PasswordFormElements;
}

export function ChangePasswordPage() {
  const { siteName } = useBrand();
  const { user, login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const formRef = useRef<PasswordForm>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    const form = event.currentTarget as PasswordForm;
    const currentPassword = form.elements.currentPassword.value;
    const newPassword = form.elements.newPassword.value;
    if (newPassword !== form.elements.confirmPassword.value) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await api.request<void>("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      await login(user.username, newPassword);
      form.reset();
      const from = (location.state as { from?: { pathname?: string } } | null)?.from;
      navigate(from?.pathname?.startsWith("/") ? from.pathname : "/dashboard", { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "密码修改失败，请稍后重试");
    } finally {
      setSubmitting(false);
      formRef.current?.elements.currentPassword.focus();
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="change-password-title">
        <p className="auth-card__brand">{siteName}</p>
        <h1 id="change-password-title">
          {user?.must_change_password ? "修改初始密码" : "修改密码"}
        </h1>
        <p className="auth-card__intro">新密码至少需要 10 个字符，修改后将重新登录。</p>
        <form ref={formRef} onSubmit={handleSubmit} className="auth-form">
          <label htmlFor="current-password">当前密码</label>
          <input id="current-password" name="currentPassword" type="password" autoComplete="current-password" required autoFocus />
          <label htmlFor="new-password">新密码</label>
          <input id="new-password" name="newPassword" type="password" autoComplete="new-password" minLength={10} required />
          <label htmlFor="confirm-password">确认新密码</label>
          <input id="confirm-password" name="confirmPassword" type="password" autoComplete="new-password" minLength={10} required />
          <p className="auth-error" role="alert" aria-live="polite">{error}</p>
          <button type="submit" disabled={submitting}>
            {submitting ? "保存中…" : "保存新密码"}
          </button>
        </form>
      </section>
    </main>
  );
}
