import { ChevronDown, KeyRound, LogOut, Menu } from "lucide-react";
import type { RefObject } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { StatusBadge } from "../components/StatusBadge";
import type { RuntimeStatus } from "../api/types";

export function Header({
  title,
  runtime,
  runtimeLoading,
  onOpenNavigation,
  menuButtonRef,
}: {
  title: string;
  runtime?: RuntimeStatus;
  runtimeLoading: boolean;
  onOpenNavigation: () => void;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const runtimeLabel = runtimeLoading
    ? "状态检查中"
    : runtime?.enabled
      ? "推理服务运行中"
      : "推理服务未启用";

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <header className="app-header">
      <div className="app-header__title">
        <button
          ref={menuButtonRef}
          type="button"
          className="app-header__menu"
          aria-label="打开导航"
          onClick={onOpenNavigation}
        >
          <Menu aria-hidden="true" size={21} />
        </button>
        <div>
          <p>运营中心</p>
          <strong>{title}</strong>
        </div>
      </div>
      <div className="app-header__actions">
        <StatusBadge tone={runtime?.enabled ? "safe" : "neutral"}>
          {runtimeLabel}
        </StatusBadge>
        <details className="user-menu">
          <summary>
            <span className="user-menu__avatar" aria-hidden="true">
              {user?.username.slice(0, 1).toUpperCase()}
            </span>
            <span className="user-menu__identity">
              <strong>{user?.username}</strong>
              <small>{user?.role === "admin" ? "管理员" : "操作员"}</small>
            </span>
            <ChevronDown aria-hidden="true" size={16} />
          </summary>
          <div className="user-menu__panel">
            <Link to="/change-password">
              <KeyRound aria-hidden="true" size={16} />修改密码
            </Link>
            <button type="button" onClick={() => void handleLogout()}>
              <LogOut aria-hidden="true" size={16} />退出登录
            </button>
          </div>
        </details>
      </div>
    </header>
  );
}
