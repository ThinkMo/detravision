# Management Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Deliver a modern React management console for login, dashboard monitoring, live camera viewing, events, administration, users, models, audit logs, and site branding on the same origin as FastAPI.

**Architecture:** A Vite-built React single-page application uses a typed fetch client and same-origin session Cookie. TanStack Query owns server state, React Router owns navigation, and focused feature folders own pages and forms. FastAPI serves the production assets after API and WebSocket routes, while Vite proxies those routes during development.

**Tech Stack:** Node.js 22.12 or newer, React 19, TypeScript, Vite 7, React Router, TanStack Query, React Hook Form, Zod, Lucide React, Vitest, Testing Library, MSW, CSS modules/global design tokens

## Global Constraints

- This plan starts only after 2026-08-15-platform-auth-foundation.md is complete.
- The browser and API share one origin in production.
- The website name and Logo come from GET /api/settings/site and update without a reload.
- Use a dark navigation rail, light content canvas, teal/green safe states, and orange/red alert states.
- Desktop monitoring is primary; all core flows remain usable on tablet and mobile.
- Do not use model-authored SVG artwork; use Lucide icon components, typography, and CSS shapes.
- Never store session tokens or passwords in localStorage, sessionStorage, URLs, or JavaScript state.
- Frontend route hiding is usability only; backend role checks remain authoritative.
- Chinese is the default interface language.

---

### Task 1: Frontend Project and Test Harness

**Files:**
- Create: frontend/package.json
- Create: frontend/package-lock.json
- Create: frontend/index.html
- Create: frontend/tsconfig.json
- Create: frontend/tsconfig.app.json
- Create: frontend/vite.config.ts
- Create: frontend/src/main.tsx
- Create: frontend/src/app/App.tsx
- Create: frontend/src/styles/tokens.css
- Create: frontend/src/styles/global.css
- Create: frontend/src/test/setup.ts
- Create: frontend/src/app/App.test.tsx
- Modify: .gitignore

**Interfaces:**
- Produces: npm scripts dev, build, test, test:run, typecheck
- Produces: frontend/src/app/App.tsx as the application composition root
- Produces design tokens consumed by every later page

- [ ] **Step 1: Verify the Node prerequisite**

Run: node --version

Expected: v22.12.0 or newer. If the current version is older, install the current Node.js LTS before creating the lockfile, then reopen the shell and verify again.

- [ ] **Step 2: Create the failing application smoke test**

~~~tsx
import { render, screen } from "@testing-library/react";
import { App } from "./App";

it("renders the Safety Detect application root", () => {
  render(<App />);
  expect(screen.getByText("正在加载站点配置")).toBeInTheDocument();
});
~~~

- [ ] **Step 3: Install the declared runtime and test dependencies**

frontend/package.json must declare React, React DOM, React Router, TanStack Query, React Hook Form, Zod, Lucide React, TypeScript, Vite, Vitest, jsdom, MSW, and Testing Library. Generate package-lock.json with:

Run: npm install

Working directory: frontend

- [ ] **Step 4: Add Vite and test configuration**

~~~typescript
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
~~~

- [ ] **Step 5: Implement the minimal application root and global tokens**

Define tokens for surface, sidebar, text, muted text, safe, warning, danger, border, focus ring, radius, shadow, and spacing. Add a plain loading state with role=status and no untouched starter content.

- [ ] **Step 6: Run the tests and build**

Run: npm run test:run

Run: npm run build

Working directory: frontend

Expected: both commands PASS and frontend/dist exists.

- [ ] **Step 7: Commit**

~~~powershell
git add frontend .gitignore
git commit -m "feat: scaffold management console"
~~~

---

### Task 2: Same-Origin Static Hosting and SPA Fallback

**Files:**
- Create: backend/web.py
- Modify: backend/main.py
- Create: tests/test_web_hosting.py

**Interfaces:**
- Consumes: frontend/dist output from Task 1
- Produces: mount_frontend(app: FastAPI, dist_dir: Path) -> None
- Preserves: /api/*, /ws/*, /docs, /openapi.json before SPA fallback

- [ ] **Step 1: Write hosting order tests**

~~~python
def test_root_serves_frontend(client_with_dist):
    response = client_with_dist.get("/")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_spa_route_falls_back_but_unknown_api_does_not(client_with_dist):
    assert client_with_dist.get("/events").status_code == 200
    assert client_with_dist.get("/api/not-a-route").status_code == 404
~~~

- [ ] **Step 2: Run the tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_web_hosting.py -v

Expected: FAIL because the root currently returns 404.

- [ ] **Step 3: Implement static and fallback routing**

~~~python
def mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    assets = dist_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str) -> FileResponse:
        if spa_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404)
        index = dist_dir / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail="frontend build is unavailable")
        return FileResponse(index)
~~~

Register this function only after REST, WebSocket, and documentation routes.

- [ ] **Step 4: Run tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_web_hosting.py -v

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add backend/web.py backend/main.py tests/test_web_hosting.py
git commit -m "feat: serve console from FastAPI"
~~~

---

### Task 3: Typed API Client, Brand Provider, and Authentication Flow

**Files:**
- Create: frontend/src/api/client.ts
- Create: frontend/src/api/types.ts
- Create: frontend/src/brand/BrandProvider.tsx
- Create: frontend/src/auth/AuthProvider.tsx
- Create: frontend/src/auth/ProtectedRoute.tsx
- Create: frontend/src/auth/LoginPage.tsx
- Create: frontend/src/auth/ChangePasswordPage.tsx
- Create: frontend/src/auth/auth.css
- Modify: frontend/src/app/App.tsx
- Create: frontend/src/auth/LoginPage.test.tsx
- Create: frontend/src/auth/ProtectedRoute.test.tsx

**Interfaces:**
- Produces: api.request<T>(path: string, init?: RequestInit) -> Promise<T>
- Produces: useBrand() -> { siteName: string; logoUrl: string | null; refresh(): Promise<void> }
- Produces: useAuth() -> { user: CurrentUser | null; loading: boolean; login(username: string, password: string): Promise<CurrentUser>; logout(): Promise<void>; refresh(): Promise<void> }
- Produces: ProtectedRoute and AdminRoute components

- [ ] **Step 1: Write login and forced-password-change tests**

~~~tsx
it("redirects a forced-change user to the password page", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({
    id: 1, username: "admin", role: "admin", must_change_password: true,
  })));
  renderAppAt("/dashboard");
  expect(await screen.findByRole("heading", { name: "修改初始密码" })).toBeInTheDocument();
});


it("shows the configured name and login error", async () => {
  renderAppAt("/login");
  expect(await screen.findByText("厂区安全中心")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("用户名"), "admin");
  await userEvent.type(screen.getByLabelText("密码"), "wrong");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
});
~~~

- [ ] **Step 2: Run tests and verify failure**

Run: npm run test:run -- src/auth

Working directory: frontend

Expected: FAIL because auth and brand providers do not exist.

- [ ] **Step 3: Implement the fetch boundary**

api.request always sends credentials=same-origin, parses the documented error body, maps 401 to an AuthExpiredError, and never logs request bodies. Define exact TypeScript response types for SiteSettings, CurrentUser, Camera, Event, Person, AlarmGroup, ModelVersion, and AuditLog.

- [ ] **Step 4: Implement providers and route guards**

BrandProvider fetches public settings before rendering brand-dependent UI and updates document.title. AuthProvider calls /api/auth/me, handles login/logout, and clears cached protected queries after logout. ProtectedRoute redirects unauthenticated users to /login with the original route in navigation state; AdminRoute renders a 403 page for ordinary users.

- [ ] **Step 5: Implement login and forced-change pages**

Use labeled fields, visible keyboard focus, disabled submit during requests, aria-live error text, and no password persistence. Successful login routes forced-change users to /change-password and all others to the preserved destination or /dashboard.

- [ ] **Step 6: Run tests**

Run: npm run test:run -- src/auth

Run: npm run typecheck

Working directory: frontend

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add frontend/src/api frontend/src/brand frontend/src/auth frontend/src/app/App.tsx
git commit -m "feat: add branded console login"
~~~

---

### Task 4: Application Shell and Operations Dashboard

**Files:**
- Create: frontend/src/layout/AppShell.tsx
- Create: frontend/src/layout/Sidebar.tsx
- Create: frontend/src/layout/Header.tsx
- Create: frontend/src/layout/layout.css
- Create: frontend/src/components/StatusBadge.tsx
- Create: frontend/src/components/EmptyState.tsx
- Create: frontend/src/components/LoadingPanel.tsx
- Create: frontend/src/components/ErrorPanel.tsx
- Create: frontend/src/dashboard/DashboardPage.tsx
- Create: frontend/src/dashboard/dashboard.css
- Create: backend/api/dashboard_routes.py
- Modify: backend/db/repositories.py
- Create: tests/test_dashboard_api.py
- Create: frontend/src/dashboard/DashboardPage.test.tsx

**Interfaces:**
- Produces: GET /api/dashboard/summary
- Produces: DashboardSummary with camera_total, camera_active, events_today, events_new, recent_events, runtime
- Produces: AppShell containing role-aware navigation and responsive mobile drawer

- [ ] **Step 1: Write the dashboard API test**

~~~python
def test_dashboard_summary_counts_visible_state(authenticated_client, repository):
    seed_dashboard(repository)
    response = authenticated_client("user").get("/api/dashboard/summary")
    assert response.status_code == 200
    assert response.json()["camera_total"] == 2
    assert response.json()["events_new"] == 1
    assert len(response.json()["recent_events"]) <= 8
~~~

- [ ] **Step 2: Implement one aggregate query per summary section**

Repository methods accept a UTC day boundary rather than database-specific date functions. The route combines repository counts with RuntimeManager.status and returns a typed schema.

- [ ] **Step 3: Write the shell and page tests**

~~~tsx
it("hides administrator navigation from ordinary users", async () => {
  renderAuthenticatedApp({ role: "user" }, "/dashboard");
  expect(await screen.findByText("安全态势")).toBeInTheDocument();
  expect(screen.queryByText("系统设置")).not.toBeInTheDocument();
});
~~~

- [ ] **Step 4: Implement the shell**

Desktop uses a fixed 248 px sidebar; tablet and mobile use a modal navigation drawer with focus return. The header shows the current page, runtime health, username, change-password, and logout. Navigation includes workbench, live monitoring, events, batch recognition, devices and alarms, models, and system settings according to role.

- [ ] **Step 5: Implement the dashboard**

Render four metric cards, runtime status, camera health, and a recent-event list with real API data. Use text plus icons for status so color is never the only signal. Provide loading, empty, error, and retry states.

- [ ] **Step 6: Run backend and frontend tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_dashboard_api.py -v

Run: npm run test:run -- src/dashboard src/layout

Working directory for npm commands: frontend

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add backend/api/dashboard_routes.py backend/db/repositories.py tests/test_dashboard_api.py frontend/src/layout frontend/src/components frontend/src/dashboard
git commit -m "feat: add operations dashboard"
~~~

---

### Task 5: Live Monitoring and Event Center

**Files:**
- Create: frontend/src/live/LivePage.tsx
- Create: frontend/src/live/LiveCameraCard.tsx
- Create: frontend/src/live/useJpegStream.ts
- Create: frontend/src/live/live.css
- Create: frontend/src/events/EventsPage.tsx
- Create: frontend/src/events/EventDetail.tsx
- Create: frontend/src/events/events.css
- Create: backend/api/media_routes.py
- Modify: backend/api/routes.py
- Modify: backend/db/repositories.py
- Create: tests/test_event_queries.py
- Create: frontend/src/live/LivePage.test.tsx
- Create: frontend/src/events/EventsPage.test.tsx

**Interfaces:**
- Produces: authenticated GET /api/events/{id}/snapshot
- Extends: GET /api/events with created_from and created_to UTC filters
- Produces: useJpegStream(cameraId: number, enabled: boolean) with object URL cleanup and reconnect state

- [ ] **Step 1: Write event filter and snapshot authorization tests**

~~~python
def test_events_filter_by_utc_range(user_client, repository):
    seed_events(repository)
    response = user_client.get("/api/events?created_from=2026-08-15T00:00:00Z&created_to=2026-08-16T00:00:00Z")
    assert [item["id"] for item in response.json()] == [2]


def test_snapshot_rejects_path_outside_snapshot_root(user_client, repository):
    event = repository.create_event(event_with_snapshot("../../secret.txt"))
    assert user_client.get("/api/events/" + str(event["id"]) + "/snapshot").status_code == 404
~~~

- [ ] **Step 2: Implement UTC filtering and safe media serving**

Resolve snapshot storage keys under the configured snapshot directory, confirm the event exists, and return only supported image files. Repository filtering uses column comparisons with parsed UTC datetimes.

- [ ] **Step 3: Write UI tests**

~~~tsx
it("reconnects a camera stream and revokes old object URLs", async () => {
  const { unmount } = render(<LiveCameraCard camera={enabledCamera} />);
  await emitJpegFrame(enabledCamera.id, jpegBlob);
  expect(screen.getByRole("img", { name: "东门实时画面" })).toBeInTheDocument();
  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalled();
});
~~~

- [ ] **Step 4: Implement live monitoring**

List enabled and disabled cameras with explicit connection states. Open ws/live/{camera_id} only for visible enabled cards, convert binary frames to object URLs, revoke the prior URL after each frame, reconnect with capped exponential backoff, and stop when the card unmounts.

- [ ] **Step 5: Implement the event center**

Provide camera, type, status, and time filters; a responsive table/card view; snapshot detail; and ack/close actions. Optimistically update status only after preserving a rollback value, and show API error messages in an aria-live region.

- [ ] **Step 6: Run tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_event_queries.py -v

Run: npm run test:run -- src/live src/events

Working directory for npm commands: frontend

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add backend/api backend/db/repositories.py tests/test_event_queries.py frontend/src/live frontend/src/events
git commit -m "feat: add live monitoring and event center"
~~~

---

### Task 6: Administrative Resource Pages

**Files:**
- Create: frontend/src/admin/cameras/CameraPage.tsx
- Create: frontend/src/admin/people/PeoplePage.tsx
- Create: frontend/src/admin/groups/AlarmGroupsPage.tsx
- Create: frontend/src/admin/models/ModelsPage.tsx
- Create: frontend/src/admin/users/UsersPage.tsx
- Create: frontend/src/admin/settings/SiteSettingsPage.tsx
- Create: frontend/src/admin/audit/AuditPage.tsx
- Create: frontend/src/admin/admin.css
- Modify: backend/api/routes.py
- Modify: backend/db/repositories.py
- Create: tests/test_alarm_group_api.py
- Create: frontend/src/admin/AdminPages.test.tsx

**Interfaces:**
- Adds: PATCH/DELETE /api/alarm-groups/{id}
- Consumes: existing protected camera, person, alarm-group, model, user, brand, and audit APIs
- Produces: administrator-only forms with shared validation and confirmation dialogs

- [ ] **Step 1: Add missing alarm-group API tests**

~~~python
def test_admin_updates_and_deletes_alarm_group(admin_client, repository):
    group = seed_alarm_group(repository)
    updated = admin_client.patch(
        "/api/alarm-groups/" + str(group["id"]),
        json={"name": "夜班告警", "person_ids": []},
    )
    assert updated.json()["name"] == "夜班告警"
    assert admin_client.delete("/api/alarm-groups/" + str(group["id"])).status_code == 204
~~~

- [ ] **Step 2: Implement transactional group update/delete**

Update the group and replace members in one transaction. Return 409 for missing person IDs and 404 for missing groups. Reject deletion when a camera references the group, returning a stable resource_in_use error.

- [ ] **Step 3: Write page permission and form tests**

~~~tsx
it("updates branding and refreshes the shell", async () => {
  renderAuthenticatedApp({ role: "admin" }, "/settings");
  await userEvent.clear(await screen.findByLabelText("网站名称"));
  await userEvent.type(screen.getByLabelText("网站名称"), "厂区安全中心");
  await userEvent.click(screen.getByRole("button", { name: "保存品牌设置" }));
  expect(await screen.findByText("厂区安全中心")).toBeInTheDocument();
});
~~~

Cover camera secret-field handling, person/group membership, model activation confirmation, user reset/disable, Logo file validation, and audit pagination.

- [ ] **Step 4: Implement focused resource pages**

Each page owns one query-key namespace, one list, and focused create/edit dialogs. Do not show stored RTSP URLs in full; render masked values and require explicit replacement on edit. Destructive actions use a named confirmation dialog. Model activation displays the selected version and runtime before submission.

- [ ] **Step 5: Implement users, branding, and audit**

User reset returns a temporary password only in the one successful response and prompts the administrator to copy it; the UI never stores it. Brand save calls BrandProvider.refresh. Audit filters include actor, action, result, and time range.

- [ ] **Step 6: Run tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_alarm_group_api.py -v

Run: npm run test:run -- src/admin

Working directory for npm commands: frontend

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add backend/api/routes.py backend/db/repositories.py tests/test_alarm_group_api.py frontend/src/admin
git commit -m "feat: add console administration pages"
~~~

---

### Task 7: Responsive, Accessibility, Build, and Documentation Verification

**Files:**
- Modify: frontend/src/styles/global.css
- Modify: frontend/src/layout/layout.css
- Modify: frontend/src/app/App.tsx
- Modify: README.md
- Modify: .env.example
- Create: frontend/src/app/accessibility.test.tsx

**Interfaces:**
- Consumes all frontend pages from Tasks 1 through 6
- Produces a production frontend/dist served by FastAPI

- [ ] **Step 1: Add keyboard and accessible-name tests**

~~~tsx
it("opens and closes mobile navigation with focus restoration", async () => {
  setViewport(390, 844);
  renderAuthenticatedApp({ role: "admin" }, "/dashboard");
  const trigger = await screen.findByRole("button", { name: "打开导航" });
  await userEvent.click(trigger);
  expect(screen.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await userEvent.keyboard("{Escape}");
  expect(trigger).toHaveFocus();
});
~~~

- [ ] **Step 2: Complete responsive and reduced-motion rules**

At widths below 900 px switch the sidebar to a focus-trapped drawer; below 640 px switch data tables to labeled cards and make form actions full-width. Honor prefers-reduced-motion and maintain a visible 2 px focus ring.

- [ ] **Step 3: Run frontend verification**

Run: npm run typecheck

Run: npm run test:run

Run: npm run build

Working directory: frontend

Expected: all commands PASS with no TypeScript errors.

- [ ] **Step 4: Run backend and same-origin smoke tests**

Run: .venv\Scripts\python.exe -m pytest -v

Run the FastAPI service with frontend/dist present, then verify GET /, GET /dashboard, GET /api/health, login, and an authenticated WebSocket connection.

- [ ] **Step 5: Update operator documentation**

Document Node prerequisite, frontend development proxy, production build, same-origin startup, initial login, page permissions, branding changes, and WebSocket behavior. Keep secrets out of command examples.

- [ ] **Step 6: Commit**

~~~powershell
git add frontend README.md .env.example
git commit -m "docs: finish management console workflow"
~~~
