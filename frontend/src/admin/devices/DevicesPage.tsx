import { useState } from "react";
import { CameraPage } from "../cameras/CameraPage";
import { AlarmGroupsPage } from "../groups/AlarmGroupsPage";
import { PeoplePage } from "../people/PeoplePage";

type Tab = "cameras" | "people" | "groups";
export function DevicesPage() {
  const [tab, setTab] = useState<Tab>("cameras");
  return <div><nav className="admin-tabs" aria-label="设备与告警分类"><button className={tab === "cameras" ? "admin-tabs__active" : ""} type="button" onClick={() => setTab("cameras")}>摄像头</button><button className={tab === "people" ? "admin-tabs__active" : ""} type="button" onClick={() => setTab("people")}>联系人</button><button className={tab === "groups" ? "admin-tabs__active" : ""} type="button" onClick={() => setTab("groups")}>告警组</button></nav>{tab === "cameras" ? <CameraPage /> : tab === "people" ? <PeoplePage /> : <AlarmGroupsPage />}</div>;
}
