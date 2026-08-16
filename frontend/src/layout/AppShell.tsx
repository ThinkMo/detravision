import { useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useDashboardSummary } from "../dashboard/DashboardPage";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import "./layout.css";

const titles: Record<string, string> = {
  "/dashboard": "安全态势",
  "/live": "实时监控",
  "/events": "事件中心",
  "/batch": "多图识别",
  "/admin/devices": "设备与告警",
  "/admin/models": "模型管理",
  "/admin/settings": "系统设置",
};

export function AppShell() {
  const location = useLocation();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
  const summary = useDashboardSummary();

  const closeNavigation = () => {
    setNavigationOpen(false);
    requestAnimationFrame(() => menuTrigger.current?.focus());
  };

  useEffect(() => {
    if (!navigationOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNavigation();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigationOpen]);

  return (
    <div className="app-shell">
      <aside className="app-shell__sidebar">
        <Sidebar />
      </aside>
      {navigationOpen ? (
        <div className="mobile-navigation" role="dialog" aria-modal="true" aria-label="移动导航">
          <button type="button" className="mobile-navigation__backdrop" aria-label="关闭导航" onClick={closeNavigation} />
          <aside className="mobile-navigation__panel">
            <Sidebar onNavigate={closeNavigation} />
          </aside>
        </div>
      ) : null}
      <div className="app-shell__main">
        <Header
          title={titles[location.pathname] ?? "安全运营"}
          runtime={summary.data?.runtime}
          runtimeLoading={summary.isLoading}
          onOpenNavigation={() => setNavigationOpen(true)}
          menuButtonRef={menuTrigger}
        />
        <main className="app-shell__content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
