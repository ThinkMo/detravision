import { type FormEvent, useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useBrand } from "../brand/BrandProvider";
import { useAuth } from "./AuthProvider";
import "./auth.css";

interface LoginFormElements extends HTMLFormControlsCollection {
  username: HTMLInputElement;
  password: HTMLInputElement;
}

interface LoginForm extends HTMLFormElement {
  readonly elements: LoginFormElements;
}

function destinationFromState(state: unknown): string {
  if (state && typeof state === "object" && "from" in state) {
    const from = (state as { from?: { pathname?: unknown } }).from;
    if (from && typeof from.pathname === "string" && from.pathname.startsWith("/")) {
      return from.pathname;
    }
  }
  return "/dashboard";
}

export function LoginPage() {
  const { siteName, logoUrl } = useBrand();
  const { user, loading, login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const formRef = useRef<LoginForm>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const destination = destinationFromState(location.state);

  useEffect(() => {
    if (!loading && user) {
      navigate(user.must_change_password ? "/change-password" : destination, {
        replace: true,
        state: user.must_change_password ? { from: location.state && (location.state as { from?: unknown }).from } : undefined,
      });
    }
  }, [destination, loading, location.state, navigate, user]);

  if (loading) {
    return <div role="status">正在验证登录状态</div>;
  }
  if (user) {
    return <Navigate to={user.must_change_password ? "/change-password" : destination} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget as LoginForm;
    setSubmitting(true);
    setError("");
    try {
      const current = await login(form.elements.username.value, form.elements.password.value);
      form.reset();
      navigate(current.must_change_password ? "/change-password" : destination, {
        replace: true,
        state: current.must_change_password ? { from: { pathname: destination } } : undefined,
      });
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 401
          ? "用户名或密码错误"
          : caught instanceof ApiError
            ? caught.message
            : "暂时无法登录，请稍后重试",
      );
    } finally {
      setSubmitting(false);
      formRef.current?.elements.password.focus();
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="brand-mark" aria-hidden={logoUrl ? undefined : true}>
          {logoUrl ? <img src={logoUrl} alt={`${siteName} Logo`} /> : <span>SD</span>}
        </div>
        <p className="auth-card__brand">{siteName}</p>
        <h1 id="login-title">登录管理控制台</h1>
        <p className="auth-card__intro">使用本地账号进入安全监测工作台</p>
        <form ref={formRef} onSubmit={handleSubmit} className="auth-form">
          <label htmlFor="username">用户名</label>
          <input id="username" name="username" autoComplete="username" required autoFocus />
          <label htmlFor="password">密码</label>
          <input id="password" name="password" type="password" autoComplete="current-password" required />
          <p className="auth-error" role="alert" aria-live="polite">{error}</p>
          <button type="submit" disabled={submitting}>
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
