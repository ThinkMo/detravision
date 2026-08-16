from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import select

from backend.auth import AuthenticatedUser, AuthService, hash_password, verify_password
from backend.auth.dependencies import current_user, require_admin
from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import AuditLog, User, UserSession
from backend.db.session import create_engine_from_url, create_session_factory
from backend.main import app


@pytest.fixture
def auth_api(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "auth-api.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    auth_service = AuthService(
        session_factory,
        Settings(
            database_url=database_url,
            admin_initial_password="admin123456",
            cookie_secure=False,
            enable_runtime=False,
        ),
    )
    auth_service.bootstrap_admin()
    app.state.auth_service = auth_service
    app.state.session_factory = session_factory
    try:
        yield app, auth_service, session_factory
    finally:
        engine.dispose()


@contextmanager
def api_client(auth_api):
    api_app, _, _ = auth_api

    async def open_client():
        transport = httpx.ASGITransport(app=api_app)
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    client = asyncio.run(open_client())
    try:
        yield client
    finally:
        asyncio.run(client.aclose())


def send(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(client.request(method, path, **kwargs))


def login(client: httpx.AsyncClient, username="admin", password="admin123456"):
    return send(
        client,
        "POST",
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def establish_admin(client: httpx.AsyncClient) -> None:
    assert login(client).status_code == 200
    changed = send(
        client,
        "POST",
        "/api/auth/change-password",
        json={
            "current_password": "admin123456",
            "new_password": "Admin-password-123",
        },
    )
    assert changed.status_code == 204
    assert login(client, password="Admin-password-123").status_code == 200


def test_login_sets_secure_cookie_attributes_only_after_success(auth_api):
    """Catches issuing a readable or cross-site session cookie, or issuing one on failure."""
    with api_client(auth_api) as client:
        rejected = login(client, password="wrong-password")
        assert rejected.status_code == 401
        assert "set-cookie" not in rejected.headers

        response = login(client)
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("session=")
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Path=/" in cookie
        assert "Secure" not in cookie
        assert response.json() == {
            "id": 1,
            "username": "admin",
            "role": "admin",
            "must_change_password": True,
        }


def test_login_sets_secure_cookie_when_configured(auth_api):
    """Catches production cookie configuration failing to add transport security."""
    api_app, _, _ = auth_api
    api_app.state.cookie_secure = True
    try:
        with api_client(auth_api) as client:
            response = login(client)
            assert response.status_code == 200
            assert "Secure" in response.headers["set-cookie"]
    finally:
        api_app.state.cookie_secure = False


def test_authentication_rejects_missing_and_expired_session_cookies(auth_api):
    """Catches accepting absent or expired opaque sessions at the request boundary."""
    _, _, session_factory = auth_api
    with api_client(auth_api) as client:
        assert send(client, "GET", "/api/auth/me").status_code == 401
        assert login(client).status_code == 200
        with session_factory.begin() as session:
            stored = session.scalar(select(UserSession))
            assert stored is not None
            stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        assert send(client, "GET", "/api/auth/me").status_code == 401
        with session_factory() as session:
            assert session.scalar(select(UserSession)) is None


def test_disabled_user_session_is_rejected(auth_api):
    """Catches session authentication overlooking the user's enabled flag."""
    _, auth_service, _ = auth_api
    with api_client(auth_api) as client:
        response = login(client)
        user_id = response.json()["id"]
        auth_service.set_user_enabled(user_id, False)
        assert send(client, "GET", "/api/auth/me").status_code == 401


def test_logout_revokes_session_and_always_clears_cookie(auth_api):
    """Catches logout leaving either the server session or browser cookie usable."""
    with api_client(auth_api) as client:
        assert login(client).status_code == 200
        response = send(client, "POST", "/api/auth/logout")
        assert response.status_code == 204
        assert response.headers["set-cookie"].startswith("session=")
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert "session" not in client.cookies
        assert send(client, "GET", "/api/auth/me").status_code == 401

        absent = send(client, "POST", "/api/auth/logout")
        assert absent.status_code == 204
        assert absent.headers["set-cookie"].startswith("session=")
        assert "Max-Age=0" in absent.headers["set-cookie"]


def test_forced_password_change_allows_only_auth_recovery_routes(auth_api):
    """Catches a forced-change account reaching protected application dependencies."""
    with api_client(auth_api) as client:
        assert login(client).status_code == 200
        assert send(client, "GET", "/api/auth/me").status_code == 200
        assert send(client, "GET", "/api/users").status_code == 403
        changed = send(
            client,
            "POST",
            "/api/auth/change-password",
            json={
                "current_password": "admin123456",
                "new_password": "A-new-password-123",
            },
        )
        assert changed.status_code == 204
        assert send(client, "GET", "/api/auth/me").status_code == 401
        assert login(client, password="A-new-password-123").status_code == 200
        assert send(client, "GET", "/api/users").status_code == 200


@pytest.mark.parametrize(
    ("header_name", "header_value"),
    [
        ("Origin", "https://evil.example"),
        ("Referer", "https://evil.example/form"),
    ],
)
def test_cross_origin_mutations_are_rejected(auth_api, header_name, header_value):
    """Catches a supplied cross-site Origin or Referer bypassing mutation checks."""
    with api_client(auth_api) as client:
        response = send(
            client,
            "POST",
            "/api/auth/login",
            headers={header_name: header_value},
            json={"username": "admin", "password": "admin123456"},
        )
        assert response.status_code == 403
        assert "set-cookie" not in response.headers


def test_same_host_origin_is_accepted(auth_api):
    """Catches same-origin validation rejecting legitimate browser mutations."""
    with api_client(auth_api) as client:
        response = send(
            client,
            "POST",
            "/api/auth/login",
            headers={"Origin": "https://testserver"},
            json={"username": "admin", "password": "admin123456"},
        )
        assert response.status_code == 200


def test_ordinary_user_cannot_manage_users(auth_api):
    """Catches role enforcement allowing a non-administrator into user management."""
    _, _, session_factory = auth_api
    with session_factory.begin() as session:
        session.add(
            User(
                username="operator",
                password_hash=hash_password("Operator-pass-123"),
                role="user",
                enabled=True,
                must_change_password=False,
            )
        )

    with api_client(auth_api) as client:
        assert login(client, "operator", "Operator-pass-123").status_code == 200
        assert send(client, "GET", "/api/users").status_code == 403
        assert send(
            client,
            "POST",
            "/api/users",
            json={"username": "blocked", "password": "Blocked-pass-123"},
        ).status_code == 403


def test_admin_creates_and_lists_forced_change_user_without_password_disclosure(auth_api):
    """Catches user creation losing defaults or exposing the submitted password."""
    with api_client(auth_api) as client:
        establish_admin(client)
        created = send(
            client,
            "POST",
            "/api/users",
            json={
                "username": "viewer.one",
                "password": "Viewer-password-123",
                "role": "user",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["username"] == "viewer.one"
        assert body["role"] == "user"
        assert body["enabled"] is True
        assert body["must_change_password"] is True
        assert "password" not in body

        listed = send(client, "GET", "/api/users")
        assert listed.status_code == 200
        assert [item["username"] for item in listed.json()] == ["admin", "viewer.one"]
        assert all("password" not in item for item in listed.json())


def test_duplicate_username_returns_conflict(auth_api):
    """Catches a uniqueness violation escaping as a server error or duplicate row."""
    with api_client(auth_api) as client:
        establish_admin(client)
        payload = {"username": "duplicate", "password": "Duplicate-pass-123"}
        assert send(client, "POST", "/api/users", json=payload).status_code == 201
        assert send(client, "POST", "/api/users", json=payload).status_code == 409


@pytest.mark.parametrize("update", [{"enabled": False}, {"role": "user"}])
def test_final_enabled_administrator_cannot_be_disabled_or_demoted(auth_api, update):
    """Catches user updates leaving the installation without an enabled administrator."""
    with api_client(auth_api) as client:
        establish_admin(client)
        response = send(client, "PATCH", "/api/users/1", json=update)
        assert response.status_code == 409
        me = send(client, "GET", "/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"


def test_concurrent_updates_preserve_one_enabled_administrator(auth_api):
    """Catches concurrent removals each relying on a stale view of the other admin."""
    api_app, _, session_factory = auth_api
    with api_client(auth_api) as client:
        establish_admin(client)
        created = send(
            client,
            "POST",
            "/api/users",
            json={
                "username": "second.admin",
                "password": "Second-admin-123",
                "role": "admin",
            },
        )
        assert created.status_code == 201
        second_admin_id = created.json()["id"]
        raw_cookie = client.cookies["session"]

    both_authenticated = threading.Barrier(2)

    def synchronized_admin(
        user: AuthenticatedUser = Depends(current_user),
    ) -> AuthenticatedUser:
        both_authenticated.wait(timeout=10)
        return user

    api_app.dependency_overrides[require_admin] = synchronized_admin

    def update(path: str, payload: dict[str, object]) -> int:
        async def request() -> int:
            transport = httpx.ASGITransport(app=api_app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                cookies={"session": raw_cookie},
            ) as concurrent_client:
                response = await concurrent_client.patch(path, json=payload)
                return response.status_code

        return asyncio.run(request())

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(update, "/api/users/1", {"enabled": False}),
                executor.submit(
                    update,
                    f"/api/users/{second_admin_id}",
                    {"role": "user"},
                ),
            ]
            statuses = [future.result(timeout=15) for future in futures]
    finally:
        api_app.dependency_overrides.pop(require_admin, None)

    assert sorted(statuses) == [200, 409]
    with session_factory() as session:
        enabled_admins = session.scalars(
            select(User).where(User.role == "admin", User.enabled.is_(True))
        ).all()
        assert len(enabled_admins) == 1


def test_reset_returns_temporary_password_once_without_persisting_or_logging_it(
    auth_api, caplog
):
    """Catches reset secrets leaking beyond the single successful response."""
    _, auth_service, session_factory = auth_api
    with api_client(auth_api) as client:
        establish_admin(client)
        created = send(
            client,
            "POST",
            "/api/users",
            json={"username": "reset.me", "password": "Original-pass-123"},
        )
        user_id = created.json()["id"]

        with api_client(auth_api) as user_client:
            assert login(user_client, "reset.me", "Original-pass-123").status_code == 200
            caplog.set_level(logging.INFO)
            reset = send(client, "POST", f"/api/users/{user_id}/reset-password")
            assert reset.status_code == 200
            temporary_password = reset.json()["temporary_password"]
            assert len(temporary_password) >= 16
            assert auth_service.authenticate(user_client.cookies["session"]) is None

        with session_factory() as session:
            stored = session.get(User, user_id)
            assert stored is not None
            assert temporary_password not in stored.password_hash
            assert verify_password(temporary_password, stored.password_hash)
            assert stored.must_change_password is True
            audit_details = [row.details or "" for row in session.scalars(select(AuditLog))]
            assert all(temporary_password not in detail for detail in audit_details)
        assert temporary_password not in caplog.text

        listed = send(client, "GET", "/api/users").json()
        assert temporary_password not in str(listed)
        with api_client(auth_api) as fresh_client:
            assert login(fresh_client, "reset.me", temporary_password).status_code == 200
            assert send(fresh_client, "GET", "/api/users").status_code == 403
