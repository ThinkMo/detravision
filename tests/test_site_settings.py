from __future__ import annotations

import asyncio
import io
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from backend.audit import AuditService
from backend.auth import AuthService, hash_password
from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import AuditLog, SiteSettings, User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.main import app


MAX_LOGO_BYTES = 10 * 1024 * 1024
MAX_LOGO_PIXELS = 40_000_000


def image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (3, 2),
) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (19, 95, 87)).save(output, format=image_format)
    return output.getvalue()


def load_component(module: str, name: str):
    """Turn a missing wished-for component into an explicit RED assertion."""
    try:
        return getattr(import_module(module), name)
    except (ImportError, AttributeError) as exc:
        pytest.fail(f"required component {module}.{name} is missing: {exc}")


@pytest.fixture
def site_app(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "site-settings.db").as_posix()
    upload_dir = tmp_path / "uploads"
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    auth_service = AuthService(
        session_factory,
        Settings(
            database_url=database_url,
            admin_initial_password="Admin-password-123",
            cookie_secure=False,
            enable_runtime=False,
            upload_dir=upload_dir,
        ),
    )
    with session_factory.begin() as session:
        session.add_all(
            [
                User(
                    username="administrator",
                    password_hash=hash_password("Admin-password-123"),
                    role="admin",
                    enabled=True,
                    must_change_password=False,
                ),
                User(
                    username="operator",
                    password_hash=hash_password("Operator-password-123"),
                    role="user",
                    enabled=True,
                    must_change_password=False,
                ),
            ]
        )

    app.state.auth_service = auth_service
    app.state.session_factory = session_factory
    app.state.audit_service = AuditService(session_factory)
    app.state.cookie_secure = False

    # The first RED run intentionally has no storage/brand modules or router.
    # Once the modules exist, exercise the real local storage and service.
    try:
        LocalFileStorage = getattr(import_module("backend.storage"), "LocalFileStorage")
        BrandService = getattr(import_module("backend.brand"), "BrandService")
    except (ImportError, AttributeError):
        pass
    else:
        storage = LocalFileStorage(upload_dir)
        app.state.file_storage = storage
        app.state.brand_service = BrandService(session_factory, storage)

    try:
        yield app, auth_service, session_factory, upload_dir
    finally:
        for attribute in ("file_storage", "brand_service"):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)
        engine.dispose()


@contextmanager
def client_for(site_app, role: str | None = None, *, raise_server_exceptions=True):
    api_app, auth_service, _, _ = site_app
    transport = httpx.ASGITransport(
        app=api_app,
        raise_app_exceptions=raise_server_exceptions,
    )
    async_client = httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    )
    if role is not None:
        credentials = {
            "admin": ("administrator", "Admin-password-123"),
            "user": ("operator", "Operator-password-123"),
        }
        username, password = credentials[role]
        async_client.cookies.set(
            "session",
            auth_service.login(username, password, "127.0.0.1").token,
        )

    class Client:
        def request(self, method: str, path: str, **kwargs) -> httpx.Response:
            return asyncio.run(async_client.request(method, path, **kwargs))

        def get(self, path: str, **kwargs) -> httpx.Response:
            return self.request("GET", path, **kwargs)

        def post(self, path: str, **kwargs) -> httpx.Response:
            return self.request("POST", path, **kwargs)

        def patch(self, path: str, **kwargs) -> httpx.Response:
            return self.request("PATCH", path, **kwargs)

    client = Client()
    try:
        yield client
    finally:
        asyncio.run(async_client.aclose())


def stored_files(upload_dir: Path) -> set[Path]:
    return {path for path in upload_dir.rglob("*") if path.is_file()}


def test_public_site_settings_expose_only_brand(site_app):
    """Catches branding becoming authenticated or leaking persistence fields."""
    with client_for(site_app) as client:
        response = client.get("/api/settings/site")
    assert response.status_code == 200
    assert response.json() == {"site_name": "Safety Detect", "logo_url": None}


def test_only_admin_can_update_site_name(site_app):
    """Catches an ordinary user changing global branding."""
    with client_for(site_app, "user") as user_client:
        rejected = user_client.patch(
            "/api/settings/site",
            json={"site_name": "Unauthorized"},
        )
    assert rejected.status_code == 403

    with client_for(site_app, "admin") as admin_client:
        updated = admin_client.patch(
            "/api/settings/site",
            json={"site_name": "工地安全中心"},
        )
    assert updated.status_code == 200
    assert updated.json() == {"site_name": "工地安全中心", "logo_url": None}

    with client_for(site_app) as public_client:
        assert public_client.get("/api/settings/site").json() == updated.json()


def test_only_admin_can_upload_logo(site_app):
    """Catches ordinary users replacing the globally visible Logo."""
    png = image_bytes()
    with client_for(site_app, "user") as client:
        response = client.post(
            "/api/settings/site/logo",
            files={"file": ("logo.png", png, "image/png")},
        )
    assert response.status_code == 403


def test_admin_upload_is_content_validated_randomly_named_and_publicly_served(site_app):
    """Catches trusting a hostile filename or returning a local path instead of a stable URL."""
    _, _, session_factory, upload_dir = site_app
    png = image_bytes()
    with client_for(site_app, "admin") as client:
        response = client.post(
            "/api/settings/site/logo",
            files={"file": ("../../client-logo.png", png, "image/png")},
        )
        assert response.status_code == 200
        assert response.json() == {
            "site_name": "Safety Detect",
            "logo_url": "/api/settings/site/logo",
        }
        downloaded = client.get(response.json()["logo_url"])

    assert downloaded.status_code == 200
    assert downloaded.content == png
    assert downloaded.headers["content-type"] == "image/png"
    with session_factory() as session:
        key = session.get(SiteSettings, 1).logo_key
    assert key is not None
    assert "client-logo" not in key
    assert ".." not in key
    assert key.endswith(".png")
    assert (upload_dir / Path(key)).is_file()


@pytest.mark.parametrize(
    ("filename", "content", "media_type"),
    [
        ("logo", image_bytes("PNG"), "image/png"),
        ("logo.txt", image_bytes("PNG"), "image/png"),
        ("logo.jpg", image_bytes("PNG"), "image/png"),
    ],
)
def test_logo_rejects_missing_unsupported_or_content_mismatched_extension(
    site_app,
    filename: str,
    content: bytes,
    media_type: str,
):
    """Catches accepting a client extension that is absent, unsafe, or forged."""
    _, _, _, upload_dir = site_app
    with client_for(site_app, "admin") as client:
        response = client.post(
            "/api/settings/site/logo",
            files={"file": (filename, content, media_type)},
        )
    assert response.status_code == 415
    assert stored_files(upload_dir) == set()


@pytest.mark.parametrize(
    ("filename", "image_format", "media_type"),
    [
        ("LOGO.PNG", "PNG", "image/png"),
        ("LOGO.JpEg", "JPEG", "image/jpeg"),
        ("LOGO.WeBp", "WEBP", "image/webp"),
    ],
)
def test_logo_extension_validation_is_case_insensitive(
    site_app,
    filename: str,
    image_format: str,
    media_type: str,
):
    """Catches valid uppercase or mixed-case extensions being rejected."""
    with client_for(site_app, "admin") as client:
        response = client.post(
            "/api/settings/site/logo",
            files={"file": (filename, image_bytes(image_format), media_type)},
        )
    assert response.status_code == 200


def test_replacing_logo_removes_old_file_only_after_new_setting_commits(site_app):
    """Catches successful replacement leaking the superseded Logo file."""
    _, _, session_factory, upload_dir = site_app
    with client_for(site_app, "admin") as client:
        first = client.post(
            "/api/settings/site/logo",
            files={"file": ("first.png", image_bytes(), "image/png")},
        )
        assert first.status_code == 200
        with session_factory() as session:
            first_key = session.get(SiteSettings, 1).logo_key

        second = client.post(
            "/api/settings/site/logo",
            files={"file": ("second.webp", image_bytes("WEBP"), "image/webp")},
        )
        assert second.status_code == 200
        with session_factory() as session:
            second_key = session.get(SiteSettings, 1).logo_key

    assert first_key != second_key
    assert first_key is not None and not (upload_dir / Path(first_key)).exists()
    assert second_key is not None and (upload_dir / Path(second_key)).is_file()
    LogoCleanupQueue = load_component("backend.db.models", "LogoCleanupQueue")
    with session_factory() as session:
        assert session.scalars(select(LogoCleanupQueue)).all() == []


@pytest.mark.parametrize(
    ("filename", "content", "media_type", "expected_status"),
    [
        ("fake.jpg", image_bytes("PNG"), "image/jpeg", 415),
        ("broken.png", b"not an image", "image/png", 400),
        ("notes.txt", b"not an image", "text/plain", 415),
    ],
)
def test_logo_rejects_fake_mime_corrupt_and_unsupported_content(
    site_app,
    filename: str,
    content: bytes,
    media_type: str,
    expected_status: int,
):
    """Catches MIME declarations being trusted without decoding the image."""
    _, _, _, upload_dir = site_app
    with client_for(site_app, "admin") as client:
        response = client.post(
            "/api/settings/site/logo",
            files={"file": (filename, content, media_type)},
        )
    assert response.status_code == expected_status
    assert stored_files(upload_dir) == set()


def test_logo_rejects_more_than_40_million_decoded_pixels(site_app):
    """Catches a compact decompression bomb bypassing the decoded-pixel limit."""
    _, _, _, upload_dir = site_app
    oversized_dimensions = image_bytes(size=(10_000, 4_001))
    assert len(oversized_dimensions) < MAX_LOGO_BYTES
    with client_for(site_app, "admin") as client:
        response = client.post(
            "/api/settings/site/logo",
            files={"file": ("wide.png", oversized_dimensions, "image/png")},
        )
    assert response.status_code == 413
    assert stored_files(upload_dir) == set()


def test_logo_rejects_request_file_larger_than_10_mb(site_app):
    """Catches upload validation reading or persisting beyond the byte limit."""
    _, _, _, upload_dir = site_app
    with client_for(site_app, "admin") as client:
        response = client.post(
            "/api/settings/site/logo",
            files={
                "file": (
                    "oversized.png",
                    b"x" * (MAX_LOGO_BYTES + 1),
                    "image/png",
                )
            },
        )
    assert response.status_code == 413
    assert stored_files(upload_dir) == set()


def test_database_failure_removes_new_logo_and_preserves_old_setting(site_app):
    """Catches a failed settings transaction orphaning its newly written file."""
    _, _, session_factory, upload_dir = site_app
    engine = session_factory.kw["bind"]
    before = stored_files(upload_dir)

    def fail_site_settings_write(
        _connection,
        _cursor,
        statement,
        parameters,
        _context,
        _executemany,
    ) -> None:
        normalized = " ".join(statement.casefold().split())
        if normalized.startswith(("insert into site_settings", "update site_settings")):
            raise OperationalError(statement, parameters, RuntimeError("database offline"))

    event.listen(engine, "before_cursor_execute", fail_site_settings_write)
    try:
        with client_for(site_app, "admin", raise_server_exceptions=False) as client:
            response = client.post(
                "/api/settings/site/logo",
                files={"file": ("new.png", image_bytes(), "image/png")},
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail_site_settings_write)

    assert response.status_code == 503
    assert stored_files(upload_dir) == before
    with session_factory() as session:
        assert session.get(SiteSettings, 1) is None


def test_storage_write_failure_returns_stable_503_without_path_leak(
    site_app,
    monkeypatch,
    caplog,
    tmp_path,
):
    """Catches local storage errors escaping as unstable 500 responses or path-bearing logs."""
    api_app, _, _, _ = site_app
    secret_path = str(tmp_path / "private" / "logo.png")

    def fail_save(*_args, **_kwargs):
        raise OSError(28, "disk full", secret_path)

    monkeypatch.setattr(api_app.state.file_storage, "save", fail_save)
    caplog.set_level("ERROR")
    with client_for(site_app, "admin", raise_server_exceptions=False) as client:
        response = client.post(
            "/api/settings/site/logo",
            files={"file": ("logo.png", image_bytes(), "image/png")},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "site storage is temporarily unavailable"}
    assert secret_path not in response.text
    assert secret_path not in caplog.text
    storage_records = [
        record for record in caplog.records
        if "site_logo_storage_unavailable" in record.getMessage()
    ]
    assert len(storage_records) == 1
    assert "error_type=OSError" in storage_records[0].getMessage()
    assert storage_records[0].exc_info is None


def test_failed_old_logo_delete_is_queued_and_retried_exactly_after_restart(
    site_app,
    monkeypatch,
    caplog,
    tmp_path,
):
    """Catches a failed superseded-Logo deletion losing the key needed for recovery."""
    api_app, _, session_factory, upload_dir = site_app
    storage = api_app.state.file_storage
    with client_for(site_app, "admin") as client:
        assert client.post(
            "/api/settings/site/logo",
            files={"file": ("first.png", image_bytes(), "image/png")},
        ).status_code == 200
        with session_factory() as session:
            first_key = session.get(SiteSettings, 1).logo_key

        real_delete = storage.delete
        secret_path = str(tmp_path / "private" / "first.png")
        failures = 0

        def fail_first_old_delete(key: str) -> None:
            nonlocal failures
            if key == first_key and failures == 0:
                failures += 1
                raise OSError(5, "delete failed", secret_path)
            real_delete(key)

        monkeypatch.setattr(storage, "delete", fail_first_old_delete)
        caplog.set_level("ERROR")
        assert client.post(
            "/api/settings/site/logo",
            files={"file": ("second.png", image_bytes(), "image/png")},
        ).status_code == 200

    assert first_key is not None
    assert (upload_dir / Path(first_key)).is_file()
    assert secret_path not in caplog.text
    cleanup_records = [
        record for record in caplog.records
        if "site_logo_cleanup_failed" in record.getMessage()
    ]
    assert len(cleanup_records) == 1
    assert "error_type=OSError" in cleanup_records[0].getMessage()
    assert cleanup_records[0].exc_info is None

    LogoCleanupQueue = load_component("backend.db.models", "LogoCleanupQueue")
    with session_factory() as session:
        pending = session.scalars(select(LogoCleanupQueue)).all()
        assert [row.logo_key for row in pending] == [first_key]

    # These files share the Logo namespace but were never explicitly queued.
    # Cleanup must not infer that they are safe to remove.
    uncommitted_key = storage.save(io.BytesIO(image_bytes()), "site-logo", "png")
    readme = upload_dir / "site-logo" / "README.md"
    temporary = upload_dir / "site-logo" / ".upload-pending.tmp"
    readme.write_text("keep", encoding="utf-8")
    temporary.write_bytes(b"in progress")

    # A new service instance has no in-memory knowledge of first_key. The DB
    # queue must be the only source of keys eligible for recovery.
    service_type = type(api_app.state.brand_service)
    restarted_service = service_type(session_factory, storage)
    assert restarted_service.cleanup_pending_logos() == 1
    assert not (upload_dir / Path(first_key)).exists()
    assert (upload_dir / Path(uncommitted_key)).is_file()
    assert readme.read_text(encoding="utf-8") == "keep"
    assert temporary.read_bytes() == b"in progress"
    with session_factory() as session:
        assert session.scalars(select(LogoCleanupQueue)).all() == []


def test_pending_cleanup_never_scans_unqueued_logo_namespace_files(site_app):
    """Catches cleanup treating every non-current namespace file as disposable."""
    api_app, _, _, upload_dir = site_app
    storage = api_app.state.file_storage
    uncommitted_key = storage.save(io.BytesIO(image_bytes()), "site-logo", "png")
    readme = upload_dir / "site-logo" / "README.md"
    temporary = upload_dir / "site-logo" / ".upload-active.tmp"
    readme.write_text("keep", encoding="utf-8")
    temporary.write_bytes(b"active")

    assert api_app.state.brand_service.cleanup_pending_logos() == 0
    assert (upload_dir / Path(uncommitted_key)).is_file()
    assert readme.read_text(encoding="utf-8") == "keep"
    assert temporary.read_bytes() == b"active"


def test_cleanup_rechecks_database_and_keeps_a_key_made_current_by_another_instance(
    site_app,
):
    """Catches a stale cleanup instance deleting a key after another instance makes it current."""
    api_app, _, session_factory, upload_dir = site_app
    storage = api_app.state.file_storage
    LogoCleanupQueue = load_component("backend.db.models", "LogoCleanupQueue")
    queued_key = storage.save(io.BytesIO(image_bytes()), "site-logo", "png")
    with session_factory.begin() as session:
        session.add(LogoCleanupQueue(logo_key=queued_key))

    service_type = type(api_app.state.brand_service)
    stale_service = service_type(session_factory, storage)

    # Simulate a second process switching the pointer after the cleanup service
    # was constructed but before it starts the deletion attempt.
    with session_factory.begin() as session:
        row = session.get(SiteSettings, 1)
        if row is None:
            row = SiteSettings(id=1, site_name="Safety Detect")
            session.add(row)
        row.logo_key = queued_key

    assert stale_service.cleanup_pending_logos() == 0
    assert (upload_dir / Path(queued_key)).is_file()
    with session_factory() as session:
        pending = session.scalars(select(LogoCleanupQueue)).all()
        assert [row.logo_key for row in pending] == [queued_key]


def test_queue_insert_failure_rolls_back_pointer_and_removes_uncommitted_new_logo(
    site_app,
):
    """Catches the Logo pointer committing without its exact old-key cleanup record."""
    api_app, _, session_factory, upload_dir = site_app
    LogoCleanupQueue = load_component("backend.db.models", "LogoCleanupQueue")
    with client_for(site_app, "admin") as client:
        assert client.post(
            "/api/settings/site/logo",
            files={"file": ("first.png", image_bytes(), "image/png")},
        ).status_code == 200
        with session_factory() as session:
            old_key = session.get(SiteSettings, 1).logo_key
        before = stored_files(upload_dir)

        engine = session_factory.kw["bind"]

        def fail_queue_insert(
            _connection,
            _cursor,
            statement,
            parameters,
            _context,
            _executemany,
        ) -> None:
            if "insert into logo_cleanup_queue" in " ".join(statement.casefold().split()):
                raise OperationalError(statement, parameters, RuntimeError("database offline"))

        event.listen(engine, "before_cursor_execute", fail_queue_insert)
        try:
            response = client.post(
                "/api/settings/site/logo",
                files={"file": ("second.png", image_bytes(), "image/png")},
            )
        finally:
            event.remove(engine, "before_cursor_execute", fail_queue_insert)

    assert response.status_code == 503
    assert stored_files(upload_dir) == before
    with session_factory() as session:
        assert session.get(SiteSettings, 1).logo_key == old_key
        assert session.scalars(select(LogoCleanupQueue)).all() == []


def test_brand_changes_are_audited_without_storage_keys(site_app):
    """Catches brand mutations missing audit records or exposing local storage details."""
    _, _, session_factory, _ = site_app
    with client_for(site_app, "admin") as client:
        assert client.patch(
            "/api/settings/site",
            json={"site_name": "New name"},
        ).status_code == 200
        assert client.post(
            "/api/settings/site/logo",
            files={"file": ("logo.png", image_bytes(), "image/png")},
        ).status_code == 200

    with session_factory() as session:
        logs = session.scalars(
            select(AuditLog)
            .where(AuditLog.resource_type == "site_settings")
            .order_by(AuditLog.id)
        ).all()
    assert [(row.action, row.outcome) for row in logs] == [
        ("site_settings_update", "success"),
        ("site_logo_update", "success"),
    ]
    assert all(row.details is None for row in logs)


def test_local_storage_rejects_path_traversal_for_every_operation(tmp_path):
    """Catches any storage operation escaping its configured root."""
    LocalFileStorage = load_component("backend.storage", "LocalFileStorage")
    storage = LocalFileStorage(tmp_path / "root")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"do not touch")

    with pytest.raises(ValueError):
        storage.save(io.BytesIO(b"data"), "../escape", "png")
    with pytest.raises(ValueError):
        storage.open("../outside.txt")
    with pytest.raises(ValueError):
        storage.delete("../outside.txt")

    assert outside.read_bytes() == b"do not touch"


@pytest.mark.parametrize(
    "malicious_path",
    [
        "C:\\private\\logo.png",
        "\\\\server\\share\\logo.png",
        "/var/private/logo.png",
    ],
)
def test_local_storage_rejects_platform_absolute_paths(tmp_path, malicious_path: str):
    """Catches Windows drive, UNC, or POSIX absolute paths escaping storage."""
    LocalFileStorage = load_component("backend.storage", "LocalFileStorage")
    storage = LocalFileStorage(tmp_path / "root")
    with pytest.raises(ValueError):
        storage.save(io.BytesIO(b"data"), malicious_path, "png")
    with pytest.raises(ValueError):
        storage.open(malicious_path)
    with pytest.raises(ValueError):
        storage.delete(malicious_path)


def test_local_storage_rejects_symlink_escape_when_supported(tmp_path):
    """Catches a key resolving through a storage-root symlink to an outside directory."""
    LocalFileStorage = load_component("backend.storage", "LocalFileStorage")
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("keep", encoding="utf-8")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {type(exc).__name__}")

    storage = LocalFileStorage(root)
    with pytest.raises(ValueError):
        storage.open("linked/secret.txt")
    with pytest.raises(ValueError):
        storage.delete("linked/secret.txt")
    with pytest.raises(ValueError):
        storage.save(io.BytesIO(b"data"), "linked", "png")
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"


def test_local_storage_failed_write_leaves_no_partial_final_or_temp_file(tmp_path):
    """Catches direct-to-final writes exposing partial files when a stream fails."""
    LocalFileStorage = load_component("backend.storage", "LocalFileStorage")
    storage_root = tmp_path / "root"
    storage = LocalFileStorage(storage_root)

    class ExplodingStream(io.BytesIO):
        def __init__(self):
            super().__init__(b"partial")
            self.calls = 0

        def read(self, size=-1):
            self.calls += 1
            if self.calls > 1:
                raise OSError("stream interrupted")
            return super().read(3 if size < 0 else min(size, 3))

    with pytest.raises(OSError, match="stream interrupted"):
        storage.save(ExplodingStream(), "logos", "png")

    assert not storage_root.exists() or stored_files(storage_root) == set()


@pytest.mark.parametrize(
    ("image_format", "expected_suffix", "expected_media_type"),
    [
        ("PNG", "png", "image/png"),
        ("JPEG", "jpg", "image/jpeg"),
        ("WEBP", "webp", "image/webp"),
    ],
)
def test_image_validation_maps_decoded_formats(
    image_format: str,
    expected_suffix: str,
    expected_media_type: str,
):
    """Catches decoded image formats using client extensions or unstable aliases."""
    validate_image = load_component("backend.storage", "validate_image")
    validated = validate_image(
        io.BytesIO(image_bytes(image_format)),
        max_bytes=MAX_LOGO_BYTES,
        max_pixels=MAX_LOGO_PIXELS,
    )
    assert validated.suffix == expected_suffix
    assert validated.media_type == expected_media_type
