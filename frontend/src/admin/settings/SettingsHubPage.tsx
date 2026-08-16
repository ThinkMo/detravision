import { useState } from "react";
import { AuditPage } from "../audit/AuditPage";
import { UsersPage } from "../users/UsersPage";
import { SiteSettingsPage } from "./SiteSettingsPage";

type Tab = "brand" | "users" | "audit";
export function SettingsHubPage() {
  const [tab, setTab] = useState<Tab>("brand");
  return <div><nav className="admin-tabs" aria-label="系统设置分类"><button className={tab === "brand" ? "admin-tabs__active" : ""} type="button" onClick={() => setTab("brand")}>品牌</button><button className={tab === "users" ? "admin-tabs__active" : ""} type="button" onClick={() => setTab("users")}>用户</button><button className={tab === "audit" ? "admin-tabs__active" : ""} type="button" onClick={() => setTab("audit")}>审计</button></nav>{tab === "brand" ? <SiteSettingsPage /> : tab === "users" ? <UsersPage /> : <AuditPage />}</div>;
}
