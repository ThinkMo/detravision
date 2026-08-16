import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "../auth/AuthProvider";
import { ChangePasswordPage } from "../auth/ChangePasswordPage";
import { LoginPage } from "../auth/LoginPage";
import { AdminRoute, ProtectedRoute } from "../auth/ProtectedRoute";
import { BrandProvider } from "../brand/BrandProvider";
import { DashboardPage } from "../dashboard/DashboardPage";
import { EventsPage } from "../events/EventsPage";
import { AppShell } from "../layout/AppShell";
import { LivePage } from "../live/LivePage";
import { BatchPage } from "../batch/BatchPage";
import { DevicesPage } from "../admin/devices/DevicesPage";
import { ModelsPage } from "../admin/models/ModelsPage";
import { SettingsHubPage } from "../admin/settings/SettingsHubPage";
import "../admin/admin.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

function NotFoundPage() {
  return <main className="status-page"><p className="status-page__code">404</p><h1>页面不存在</h1></main>;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrandProvider>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route element={<ProtectedRoute />}>
                <Route path="/change-password" element={<ChangePasswordPage />} />
                <Route element={<AppShell />}>
                  <Route path="/dashboard" element={<DashboardPage />} />
                  <Route path="/live" element={<LivePage />} />
                  <Route path="/events" element={<EventsPage />} />
                  <Route path="/batch" element={<BatchPage />} />
                  <Route path="/batch/:jobId" element={<BatchPage />} />
                  <Route element={<AdminRoute />}>
                    <Route path="/admin" element={<Navigate to="/admin/devices" replace />} />
                    <Route path="/admin/devices" element={<DevicesPage />} />
                    <Route path="/admin/models" element={<ModelsPage />} />
                    <Route path="/admin/settings" element={<SettingsHubPage />} />
                  </Route>
                </Route>
              </Route>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </BrandProvider>
    </QueryClientProvider>
  );
}
