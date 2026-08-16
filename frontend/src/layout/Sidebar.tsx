import {
  BellRing,
  Boxes,
  Camera,
  ChartNoAxesCombined,
  ImageUp,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useBrand } from "../brand/BrandProvider";

type NavItem = {
  label: string;
  to: string;
  icon: LucideIcon;
  adminOnly?: boolean;
};

const navigation: NavItem[] = [
  { label: "安全态势", to: "/dashboard", icon: ChartNoAxesCombined },
  { label: "实时监控", to: "/live", icon: Camera },
  { label: "事件中心", to: "/events", icon: BellRing },
  { label: "多图识别", to: "/batch", icon: ImageUp },
  { label: "设备与告警", to: "/admin/devices", icon: SlidersHorizontal, adminOnly: true },
  { label: "模型管理", to: "/admin/models", icon: Boxes, adminOnly: true },
  { label: "系统设置", to: "/admin/settings", icon: Settings, adminOnly: true },
];

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const { siteName, logoUrl } = useBrand();
  return (
    <div className="sidebar__inner">
      <div className="sidebar__brand">
        <span className="sidebar__brand-mark">
          {logoUrl ? <img src={logoUrl} alt="" /> : <ShieldCheck aria-hidden="true" size={24} />}
        </span>
        <span>
          <strong>{siteName}</strong>
          <small>安全运营控制台</small>
        </span>
      </div>
      <nav aria-label="主导航" className="sidebar__nav">
        {navigation
          .filter((item) => !item.adminOnly || user?.role === "admin")
          .map(({ label, to, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onNavigate}
              className={({ isActive }) => `sidebar__link${isActive ? " sidebar__link--active" : ""}`}
            >
              <Icon aria-hidden="true" size={19} />
              <span>{label}</span>
            </NavLink>
          ))}
      </nav>
      <div className="sidebar__footer">
        <span className="sidebar__pulse" aria-hidden="true" />
        本地安全服务
      </div>
    </div>
  );
}
