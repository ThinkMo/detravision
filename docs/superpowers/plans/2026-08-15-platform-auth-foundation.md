# Platform, Authentication, and Brand Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Replace the raw SQLite repository with a SQLite/MySQL-compatible persistence layer and add secure local authentication, role enforcement, auditing, and configurable site branding.

**Architecture:** FastAPI continues to own application startup and API routing, while SQLAlchemy sessions and focused repositories replace direct SQL construction. Authentication uses opaque server-side sessions in an HttpOnly cookie; authorization is enforced by FastAPI dependencies and WebSocket handshake checks. Site branding uses a database singleton plus a local-storage adapter that later plans reuse.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, pwdlib with Argon2, Pillow, PyMySQL, pytest, HTTPX

## Global Constraints

- Preserve existing REST paths and response field names for cameras, persons, alarm groups, events, and model versions.
- Default DATABASE_URL is sqlite:///data/safety_detect.db.
- MySQL uses mysql+pymysql and utf8mb4, requires MySQL 8.0.13 or newer, and does not use database-specific JSON queries.
- camera.roi, camera.detect_classes, detection_event.status, and model_version.runtime remain Text on both SQLite and MySQL; Text defaults use parenthesized default expressions accepted by MySQL 8.0.13 or newer.
- Store all timestamps in UTC and expose ISO 8601 values.
- No public registration endpoint.
- Bootstrap username is admin; production requires ADMIN_INITIAL_PASSWORD, while development may use admin123456.
- Initial and reset passwords set must_change_password=true.
- Session cookies are HttpOnly and SameSite=Lax; production HTTPS also sets Secure.
- Administrators manage configuration; normal users may read monitoring data and acknowledge or close events.
- Do not log passwords, cookies, raw session tokens, RTSP credentials, or local file paths.

---

### Task 1: SQLAlchemy and Alembic Foundation

**Files:**
- Modify: requirements.txt
- Modify: requirements-dev.txt
- Modify: backend/core/config.py
- Create: backend/db/base.py
- Create: backend/db/models.py
- Create: backend/db/session.py
- Create: alembic.ini
- Create: migrations/env.py
- Create: migrations/script.py.mako
- Create: migrations/versions/0001_legacy_baseline.py
- Create: migrations/versions/0002_auth_brand_audit.py
- Create: backend/db/migrate.py
- Test: tests/test_migrations.py

**Interfaces:**
- Produces: create_engine_from_url(url: str) -> Engine
- Produces: create_session_factory(engine: Engine) -> sessionmaker[Session]
- Produces: run_migrations(database_url: str) -> None
- Produces ORM classes Camera, Person, AlarmGroup, DetectionEvent, ModelVersion, User, UserSession, SiteSettings, AuditLog; DetectionEvent retains ack_by for legacy Person references and adds nullable ack_user_id for authenticated User references

- [ ] **Step 1: Write migration tests**

~~~python
def test_fresh_sqlite_upgrade_creates_all_tables(tmp_path):
    url = "sqlite:///" + (tmp_path / "fresh.db").as_posix()
    run_migrations(url)
    names = set(inspect(create_engine(url)).get_table_names())
    assert {"camera", "person", "alarm_group", "detection_event", "model_version"} <= names
    assert {"user", "user_session", "site_settings", "audit_log"} <= names


def test_legacy_sqlite_is_stamped_then_upgraded(legacy_database_url):
    run_migrations(legacy_database_url)
    with create_engine(legacy_database_url).connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0002"
~~~

- [ ] **Step 2: Run the migration tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_migrations.py -v

Expected: FAIL because backend.db.migrate and ORM models do not exist.

- [ ] **Step 3: Add persistence dependencies and database settings**

Add these runtime requirements:

~~~text
sqlalchemy>=2.0,<3
alembic>=1.13,<2
pymysql>=1.1,<2
pwdlib[argon2]>=0.2,<1
python-multipart>=0.0.9,<1
pillow>=10.4,<13
~~~

Extend Settings with:

~~~python
environment: str = os.getenv("APP_ENV", "development")
database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/safety_detect.db")
session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "12"))
admin_initial_password: str | None = os.getenv("ADMIN_INITIAL_PASSWORD")
upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
cookie_secure: bool = _bool_env("COOKIE_SECURE", False)
~~~

Keep database_path as a temporary compatibility property only until Task 2 removes its callers.

- [ ] **Step 4: Define the declarative models and session helpers**

Use a naming convention so Alembic emits stable constraint names:

~~~python
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
~~~

Map legacy table and column names exactly. Keep roi, detect_classes, status, and runtime as Text columns on every database. Use parenthesized server-default expressions for their defaults so MySQL 8.0.13 or newer accepts the DDL. Expose JSON serialization through repositories rather than dialect-specific JSON operations. Define user roles as string values admin and user.

- [ ] **Step 5: Implement legacy-aware Alembic migration**

run_migrations must:

~~~python
LEGACY_TABLES = {"camera", "person", "alarm_group", "alarm_group_member", "detection_event", "model_version"}

def run_migrations(database_url: str) -> None:
    engine = create_engine_from_url(database_url)
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables and LEGACY_TABLES <= tables:
        validate_legacy_columns(engine)
        command.stamp(alembic_config(database_url), "0001")
    command.upgrade(alembic_config(database_url), "head")
~~~

validate_legacy_columns compares the required legacy column sets and raises RuntimeError with the table and missing columns when the structure is incompatible. For MySQL, run_migrations rejects server versions older than 8.0.13 before applying DDL. Revision 0001 creates the six legacy tables for a fresh database with Text defaults expressed in parentheses; revision 0002 creates user, user_session, site_settings, and audit_log, then adds nullable detection_event.ack_user_id with a foreign key to user.id and an index. Use Alembic batch table alteration for SQLite compatibility.

- [ ] **Step 6: Install dependencies and run migration tests**

Run: .venv\Scripts\python.exe -m pip install -r requirements-dev.txt

Run: .venv\Scripts\python.exe -m pytest tests/test_migrations.py -v

Expected: PASS for both fresh and legacy SQLite databases.

- [ ] **Step 7: Add MySQL migration coverage**

Add a marker-controlled test:

~~~python
@pytest.mark.mysql
def test_mysql_upgrade_and_crud(mysql_url):
    run_migrations(mysql_url)
    factory = create_session_factory(create_engine_from_url(mysql_url))
    with factory.begin() as session:
        session.add(Camera(name="mysql-camera", rtsp_url="rtsp://invalid", enabled=False))
    with factory() as session:
        assert session.scalar(select(Camera).where(Camera.name == "mysql-camera")) is not None
~~~

Run with a disposable database: set TEST_MYSQL_URL=mysql+pymysql://safety:safety@127.0.0.1:3306/safety_test?charset=utf8mb4

Run: .venv\Scripts\python.exe -m pytest tests/test_migrations.py -m mysql -v

Expected: PASS.

- [ ] **Step 8: Commit**

~~~powershell
git add requirements.txt requirements-dev.txt backend/core/config.py backend/db alembic.ini migrations tests/test_migrations.py
git commit -m "feat: add portable database migrations"
~~~

---

### Task 2: ORM Repositories and Existing API Compatibility

**Files:**
- Create: backend/db/repositories.py
- Modify: backend/db/__init__.py
- Modify: backend/api/routes.py
- Modify: backend/pipeline/runtime.py
- Modify: backend/main.py
- Modify: tests/test_database.py
- Create: tests/test_api_compatibility.py
- Delete: backend/db/database.py

**Interfaces:**
- Consumes: create_session_factory and legacy ORM classes from Task 1
- Produces: Repository(session_factory: sessionmaker[Session])
- Produces: repository methods list_cameras, get_camera, create_camera, update_camera, delete_camera, list_events, create_event, list_models, activate_model
- Preserves: JSON-ready dictionaries at existing API and RuntimeManager boundaries

- [ ] **Step 1: Convert repository tests to explicit methods**

~~~python
def test_camera_crud_and_json_round_trip(repository):
    camera = repository.create_camera(
        {
            "name": "gate",
            "rtsp_url": "rtsp://example.invalid/live",
            "enabled": True,
            "sample_fps": 2,
            "roi": [[0, 0], [100, 0], [100, 100]],
            "detect_classes": ["fire"],
            "alarm_group_id": None,
        }
    )
    assert camera["detect_classes"] == ["fire"]
    assert repository.update_camera(camera["id"], {"enabled": False})["enabled"] is False
~~~

Add API compatibility assertions for list/create/update/delete response keys and status codes.

- [ ] **Step 2: Run compatibility tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_database.py tests/test_api_compatibility.py -v

Expected: FAIL because Repository and the dependency overrides do not exist.

- [ ] **Step 3: Implement focused repository methods**

Use small private converters:

~~~python
def camera_dict(row: Camera) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "rtsp_url": row.rtsp_url,
        "enabled": row.enabled,
        "sample_fps": row.sample_fps,
        "roi": json.loads(row.roi),
        "detect_classes": json.loads(row.detect_classes),
        "alarm_group_id": row.alarm_group_id,
        "created_at": utc_iso(row.created_at),
    }
~~~

Create explicit methods per aggregate. Do not retain a public method that accepts arbitrary table or where strings.

- [ ] **Step 4: Replace route and runtime callers**

FastAPI dependency:

~~~python
def repository(request: Request) -> Repository:
    return request.app.state.repository
~~~

RuntimeManager consumes Repository and calls list_enabled_cameras, get_camera, create_event, and get_active_model. Model activation remains atomic in one database transaction.

- [ ] **Step 5: Remove the raw repository and run regression tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_database.py tests/test_api_compatibility.py tests/test_rules.py tests/test_frame_queue.py -v

Expected: PASS with no import of backend.db.database.

- [ ] **Step 6: Commit**

~~~powershell
git add backend tests/test_database.py tests/test_api_compatibility.py
git commit -m "refactor: move backend persistence to SQLAlchemy"
~~~

---

### Task 3: Passwords, Sessions, Bootstrap, and Login Rate Limiting

**Files:**
- Create: backend/auth/security.py
- Create: backend/auth/service.py
- Create: backend/auth/rate_limit.py
- Create: backend/auth/__init__.py
- Create: tests/test_auth_service.py

**Interfaces:**
- Consumes: User and UserSession ORM models, session factory, Settings
- Produces: hash_password(password: str) -> str
- Produces: verify_password(password: str, encoded: str) -> bool
- Produces: AuthService.bootstrap_admin() -> None
- Produces: AuthService.login(username: str, password: str, ip: str) -> LoginResult
- Produces: AuthService.authenticate(raw_token: str) -> AuthenticatedUser | None
- Produces: AuthService.logout(raw_token: str) -> None
- Produces: AuthService.change_password(user_id: int, current: str, replacement: str) -> None

- [ ] **Step 1: Write service tests**

~~~python
def test_development_bootstrap_creates_forced_change_admin(auth_service):
    auth_service.bootstrap_admin()
    user = auth_service.users.by_username("admin")
    assert user.role == "admin"
    assert user.must_change_password is True
    assert verify_password("admin123456", user.password_hash)


def test_production_bootstrap_requires_password(session_factory, settings_factory):
    service = AuthService(session_factory, settings_factory(environment="production", admin_initial_password=None))
    with pytest.raises(RuntimeError, match="ADMIN_INITIAL_PASSWORD"):
        service.bootstrap_admin()


def test_password_reset_revokes_existing_sessions(auth_service):
    result = auth_service.login("admin", "admin123456", "127.0.0.1")
    auth_service.reset_password(result.user.id, "Replacement-123")
    assert auth_service.authenticate(result.token) is None
~~~

- [ ] **Step 2: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_auth_service.py -v

Expected: FAIL because backend.auth does not exist.

- [ ] **Step 3: Implement password and token primitives**

~~~python
password_hash = PasswordHash.recommended()

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)

def issue_session_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest
~~~

Enforce a minimum password length of 10 and reject a replacement equal to the username or the current password.

- [ ] **Step 4: Implement AuthService transaction boundaries**

bootstrap_admin locks through the database transaction, creates only when the user table is empty, and is idempotent. login uses a constant invalid hash when the username is absent to reduce username timing leakage. Sessions store only SHA-256 token digests and UTC expiry timestamps. Disabling a user or changing/resetting a password deletes all of that user's sessions.

- [ ] **Step 5: Implement bounded login limiting**

~~~python
class LoginRateLimiter:
    def allow(self, username: str, ip: str, now: float | None = None) -> bool:
        instant = time.monotonic() if now is None else now
        key = (username.casefold(), ip)
        with self._lock:
            failures = [value for value in self._failures.get(key, []) if instant - value < 900]
            self._failures[key] = failures
            return len(failures) < 5

    def record_failure(self, username: str, ip: str, now: float | None = None) -> None:
        instant = time.monotonic() if now is None else now
        key = (username.casefold(), ip)
        with self._lock:
            self._failures.setdefault(key, []).append(instant)

    def reset(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures.pop((username.casefold(), ip), None)
~~~

Use a thread-safe dictionary keyed by normalized username and IP, allow five failures per 15 minutes, and evict entries after the window.

- [ ] **Step 6: Run tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_auth_service.py -v

Expected: PASS for bootstrap, login, expiry, logout, reset, disabled-user, and limiter cases.

- [ ] **Step 7: Commit**

~~~powershell
git add backend/auth tests/test_auth_service.py
git commit -m "feat: add local account session service"
~~~

---

### Task 4: Authentication API and Request Dependencies

**Files:**
- Create: backend/auth/dependencies.py
- Create: backend/api/auth_routes.py
- Create: backend/api/user_routes.py
- Modify: backend/schemas.py
- Modify: backend/main.py
- Create: tests/test_auth_api.py

**Interfaces:**
- Consumes: AuthService from Task 3
- Produces: current_user(request: Request) -> AuthenticatedUser
- Produces: require_admin(user: AuthenticatedUser = Depends(current_user)) -> AuthenticatedUser
- Produces routes /api/auth/login, /api/auth/logout, /api/auth/me, /api/auth/change-password
- Produces admin routes /api/users and /api/users/{id}

- [ ] **Step 1: Write API tests**

~~~python
def test_login_sets_http_only_cookie(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]


def test_forced_change_blocks_other_api(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
    assert client.get("/api/cameras").status_code == 403
    assert client.post(
        "/api/auth/change-password",
        json={"current_password": "admin123456", "new_password": "A-new-password-123"},
    ).status_code == 204
    assert client.get("/api/cameras").status_code == 200
~~~

Cover missing cookie, expired session, logout, disabled user, ordinary-user 403, admin user creation, password reset, duplicate username, and same-origin rejection.

- [ ] **Step 2: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_auth_api.py -v

Expected: FAIL because auth routes and dependencies are absent.

- [ ] **Step 3: Add request/response schemas and auth dependencies**

~~~python
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=1024)

class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,100}$")
    password: str = Field(min_length=10, max_length=1024)
    role: Literal["admin", "user"] = "user"
~~~

current_user hashes the Cookie token, resolves an enabled user, and returns 401 on failure. A dependency rejects all routes except me, logout, and change-password while must_change_password is true.

- [ ] **Step 4: Implement Cookie and same-origin behavior**

Set the Cookie only after successful login. Clear it on logout even when the session is already absent. For POST, PATCH, PUT, and DELETE, reject a supplied Origin or Referer whose host differs from the request host.

- [ ] **Step 5: Implement user administration**

Only administrators may list/create/update users or reset passwords. Prevent disabling or demoting the final enabled administrator. The reset endpoint generates a random temporary password with secrets.token_urlsafe(12), stores only its hash, returns the plaintext only in that one successful response, sets must_change_password, and revokes all prior sessions. Tests assert that neither the audit row nor application logs contain the temporary password.

- [ ] **Step 6: Run API tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_auth_api.py -v

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add backend/auth backend/api/auth_routes.py backend/api/user_routes.py backend/schemas.py backend/main.py tests/test_auth_api.py
git commit -m "feat: expose authenticated user management"
~~~

---

### Task 5: Protect Existing APIs, WebSockets, and Audit Mutations

**Files:**
- Create: backend/audit/service.py
- Create: backend/audit/__init__.py
- Create: backend/api/audit_routes.py
- Modify: backend/api/routes.py
- Modify: backend/main.py
- Create: tests/test_authorization.py
- Create: tests/test_audit.py

**Interfaces:**
- Consumes: current_user, require_admin, Repository, AuditLog
- Produces: AuditService.record(actor_id, action, resource_type, resource_id, outcome, ip) -> None
- Produces: authenticated websocket helper returning AuthenticatedUser or closing with code 4401

- [ ] **Step 1: Write the permission matrix tests**

~~~python
@pytest.mark.parametrize(
    ("method", "path", "role", "expected"),
    [
        ("GET", "/api/cameras", "user", 200),
        ("POST", "/api/cameras", "user", 403),
        ("GET", "/api/models", "user", 200),
        ("POST", "/api/models", "user", 403),
        ("PATCH", "/api/events/1", "user", 200),
    ],
)
def test_permission_matrix(authenticated_client, method, path, role, expected):
    client = authenticated_client(role)
    response = client.request(method, path, json=request_body(method, path))
    assert response.status_code == expected
~~~

Add WebSocket tests for no Cookie, disabled user, and authenticated live/events connections. Add audit assertions for successful and rejected mutations.

- [ ] **Step 2: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_authorization.py tests/test_audit.py -v

Expected: FAIL because existing routes are public.

- [ ] **Step 3: Apply route dependencies**

All existing REST routes require current_user. POST/PATCH/DELETE for cameras, persons, alarm groups, and models require require_admin. Event PATCH accepts both roles, preserves the legacy ack_by person reference, and writes the authenticated user's ID to ack_user_id when status becomes ack or closed.

- [ ] **Step 4: Protect WebSockets**

Resolve the session Cookie before hub.connect. Close unauthenticated connections with 4401 and forced-password-change connections with 4403. Do not place session tokens in query strings.

- [ ] **Step 5: Record audit events**

Record login success/failure, logout, password change/reset, user changes, camera/person/group/model mutations, model activation, and event status changes. Audit writes use their own short transaction and sanitize details before logging.

- [ ] **Step 6: Run authorization and regression tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_authorization.py tests/test_audit.py tests/test_api_compatibility.py -v

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add backend tests/test_authorization.py tests/test_audit.py
git commit -m "feat: enforce roles and audit protected operations"
~~~

---

### Task 6: Configurable Site Name and Logo Storage

**Files:**
- Create: backend/storage/base.py
- Create: backend/storage/local.py
- Create: backend/storage/images.py
- Create: backend/storage/__init__.py
- Create: backend/brand/service.py
- Create: backend/brand/__init__.py
- Create: backend/api/site_routes.py
- Modify: backend/main.py
- Create: tests/test_site_settings.py

**Interfaces:**
- Produces: FileStorage.save(stream: BinaryIO, namespace: str, suffix: str) -> str
- Produces: FileStorage.open(key: str) -> BinaryIO
- Produces: FileStorage.delete(key: str) -> None
- Produces: validate_image(stream: BinaryIO, max_bytes: int, max_pixels: int) -> ValidatedImage
- Produces: GET/PATCH /api/settings/site and POST /api/settings/site/logo

- [ ] **Step 1: Write settings and upload tests**

~~~python
def test_public_site_settings_expose_only_brand(client):
    response = client.get("/api/settings/site")
    assert response.json() == {"site_name": "Safety Detect", "logo_url": None}


def test_admin_can_replace_logo_and_old_file_is_removed(admin_client, png_bytes, storage):
    first = admin_client.post("/api/settings/site/logo", files={"file": ("first.png", png_bytes, "image/png")})
    second = admin_client.post("/api/settings/site/logo", files={"file": ("second.png", png_bytes, "image/png")})
    assert first.status_code == second.status_code == 200
    assert not storage.exists(key_from(first.json()["logo_url"]))
~~~

Cover ordinary-user rejection, fake MIME, corrupt image, pixel limit, oversized body, generated storage names, and database failure cleanup.

- [ ] **Step 2: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_site_settings.py -v

Expected: FAIL because storage and brand routes do not exist.

- [ ] **Step 3: Implement local storage and image validation**

LocalFileStorage resolves every key under settings.upload_dir and rejects absolute paths or path traversal. Save through a temporary file followed by os.replace. Pillow must call Image.verify, reopen the image, enforce width times height at or below 40,000,000 pixels, and map decoded formats to png, jpg, or webp suffixes.

- [ ] **Step 4: Implement brand service and routes**

GET is public and returns only site_name and a stable /api/settings/site/logo URL. PATCH and POST logo require an administrator. The service saves the new file, commits the new key, then removes the prior key; transaction failure removes the new file.

- [ ] **Step 5: Run tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_site_settings.py -v

Expected: PASS.

- [ ] **Step 6: Commit**

~~~powershell
git add backend/storage backend/brand backend/api/site_routes.py backend/main.py tests/test_site_settings.py
git commit -m "feat: add configurable site branding"
~~~

---

### Task 7: Foundation Integration, MySQL Verification, and Documentation

**Files:**
- Modify: .env.example
- Modify: README.md
- Modify: tests/conftest.py
- Create: deploy/docker-compose.mysql-test.yml
- Modify: backend/main.py

**Interfaces:**
- Consumes all outputs from Tasks 1 through 6
- Produces a startup path that migrates development databases, bootstraps admin, and initializes services in dependency order

- [ ] **Step 1: Add an application startup integration test**

~~~python
def test_empty_database_startup_bootstraps_and_serves_health(app_factory, tmp_path):
    app = app_factory(database_url="sqlite:///" + (tmp_path / "app.db").as_posix())
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123456"},
        ).status_code == 200
~~~

- [ ] **Step 2: Run the integration test and correct startup ordering**

Run: .venv\Scripts\python.exe -m pytest tests/test_app_startup.py -v

Expected after implementation: PASS with migrations before repository creation and bootstrap before requests are accepted.

- [ ] **Step 3: Document exact configuration**

Add APP_ENV, DATABASE_URL, ADMIN_INITIAL_PASSWORD, SESSION_TTL_HOURS, COOKIE_SECURE, and UPLOAD_DIR to .env.example and README. Document the development password warning, forced change, explicit production migration command, and MySQL connection example.

- [ ] **Step 4: Verify SQLite and full regression**

Run: .venv\Scripts\python.exe -m pytest -v

Expected: all tests PASS.

- [ ] **Step 5: Verify MySQL**

Run: docker compose -f deploy/docker-compose.mysql-test.yml up -d

Run: .venv\Scripts\python.exe -m pytest -m mysql -v

Expected: migration and repository tests PASS against MySQL 8 with utf8mb4.

- [ ] **Step 6: Commit**

~~~powershell
git add .env.example README.md deploy tests/conftest.py tests/test_app_startup.py backend/main.py
git commit -m "docs: complete authenticated platform setup"
~~~
