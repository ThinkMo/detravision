import type { ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";

export function ProtectedRoute({ children }: { children?: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div role="status">正在验证登录状态</div>;
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  if (user.must_change_password && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace state={{ from: location }} />;
  }
  return children ?? <Outlet />;
}

function ForbiddenPage() {
  return (
    <main className="status-page">
      <p className="status-page__code">403</p>
      <h1>无权访问</h1>
      <p>当前账号没有访问此页面的权限。</p>
    </main>
  );
}

function AdminGate({ children }: { children?: ReactNode }) {
  const { user } = useAuth();
  if (user?.role !== "admin") {
    return <ForbiddenPage />;
  }
  return children ?? <Outlet />;
}

export function AdminRoute({ children }: { children?: ReactNode }) {
  return (
    <ProtectedRoute>
      <AdminGate>{children}</AdminGate>
    </ProtectedRoute>
  );
}
