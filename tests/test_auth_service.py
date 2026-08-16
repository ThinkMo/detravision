from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.auth import (
    AuthService,
    AuthenticationError,
    LoginRateLimiter,
    LoginRateLimited,
    PasswordValidationError,
    hash_password,
    verify_password,
)
from backend.auth import service as auth_service_module
from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import User, UserSession
from backend.db.session import create_engine_from_url, create_session_factory


@pytest.fixture
def session_factory(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "auth.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def settings_factory():
    def build(**overrides):
        values = {
            "environment": "development",
            "admin_initial_password": None,
            "session_ttl_hours": 12,
        }
        values.update(overrides)
        return replace(Settings(), **values)

    return build


@pytest.fixture
def auth_service(session_factory, settings_factory):
    return AuthService(session_factory, settings_factory())


def bootstrap(service: AuthService) -> None:
    service.bootstrap_admin()


def count_rows(session_factory, model) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_password_hash_verifies_only_the_original_password():
    encoded = hash_password("A-secure-password")

    assert encoded != "A-secure-password"
    assert verify_password("A-secure-password", encoded) is True
    assert verify_password("a-secure-password", encoded) is False


def test_development_bootstrap_creates_forced_change_admin(auth_service, session_factory):
    auth_service.bootstrap_admin()

    user = auth_service.users.by_username("admin")
    assert user is not None
    assert user.role == "admin"
    assert user.must_change_password is True
    assert verify_password("admin123456", user.password_hash)
    assert count_rows(session_factory, User) == 1


def test_bootstrap_is_idempotent(auth_service, session_factory):
    auth_service.bootstrap_admin()
    first = auth_service.users.by_username("admin")

    auth_service.bootstrap_admin()

    second = auth_service.users.by_username("admin")
    assert first is not None and second is not None
    assert second.id == first.id
    assert second.password_hash == first.password_hash
    assert count_rows(session_factory, User) == 1


def test_concurrent_bootstrap_returns_once_without_overwriting_password(
    session_factory,
    settings_factory,
    monkeypatch,
):
    first_password = "First-bootstrap-123"
    second_password = "Second-bootstrap-123"
    services = [
        AuthService(
            session_factory,
            settings_factory(admin_initial_password=first_password),
        ),
        AuthService(
            session_factory,
            settings_factory(admin_initial_password=second_password),
        ),
    ]
    real_hash_password = auth_service_module.hash_password
    both_ready_to_insert = threading.Barrier(2)

    def coordinated_hash(password: str) -> str:
        encoded = real_hash_password(password)
        both_ready_to_insert.wait(timeout=10)
        return encoded

    monkeypatch.setattr(auth_service_module, "hash_password", coordinated_hash)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(service.bootstrap_admin) for service in services]
        for future in futures:
            assert future.result(timeout=10) is None

    user = services[0].users.by_username("admin")
    assert user is not None
    assert count_rows(session_factory, User) == 1
    assert sum(
        verify_password(candidate, user.password_hash)
        for candidate in (first_password, second_password)
    ) == 1


def test_bootstrap_does_not_add_admin_when_any_user_exists(auth_service, session_factory):
    with session_factory.begin() as session:
        session.add(
            User(
                username="existing",
                password_hash=hash_password("Existing-pass-123"),
                role="user",
                enabled=True,
                must_change_password=False,
            )
        )

    auth_service.bootstrap_admin()

    assert auth_service.users.by_username("admin") is None
    assert count_rows(session_factory, User) == 1


def test_production_bootstrap_requires_password(session_factory, settings_factory):
    service = AuthService(
        session_factory,
        settings_factory(environment="production", admin_initial_password=None),
    )

    with pytest.raises(RuntimeError, match="ADMIN_INITIAL_PASSWORD"):
        service.bootstrap_admin()

    assert count_rows(session_factory, User) == 0


def test_non_development_bootstrap_fails_closed_without_password(session_factory, settings_factory):
    service = AuthService(
        session_factory,
        settings_factory(environment="staging", admin_initial_password=None),
    )

    with pytest.raises(RuntimeError, match="ADMIN_INITIAL_PASSWORD"):
        service.bootstrap_admin()

    assert count_rows(session_factory, User) == 0


def test_production_bootstrap_uses_configured_password(session_factory, settings_factory):
    service = AuthService(
        session_factory,
        settings_factory(
            environment="production",
            admin_initial_password="Configured-123",
        ),
    )

    service.bootstrap_admin()

    user = service.users.by_username("admin")
    assert user is not None
    assert verify_password("Configured-123", user.password_hash)


def test_login_persists_only_token_digest_with_aware_utc_expiry(auth_service, session_factory):
    bootstrap(auth_service)

    before = datetime.now(timezone.utc)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")

    with session_factory() as session:
        stored = session.scalar(select(UserSession))
        assert stored is not None
        assert stored.token_digest == hashlib.sha256(result.token.encode("utf-8")).hexdigest()
        assert stored.token_digest != result.token
        assert stored.expires_at.tzinfo is timezone.utc
        assert before + timedelta(hours=11, minutes=59) < stored.expires_at
        assert stored.expires_at < before + timedelta(hours=12, minutes=1)
    assert result.user.username == "admin"
    assert auth_service.authenticate(result.token) == result.user


@pytest.mark.parametrize("username", ["admin", "missing"])
def test_invalid_credentials_do_not_create_a_session(auth_service, session_factory, username):
    bootstrap(auth_service)

    with pytest.raises(AuthenticationError):
        auth_service.login(username, "definitely-wrong", "127.0.0.1")

    assert count_rows(session_factory, UserSession) == 0


def test_logout_revokes_the_raw_token_and_is_idempotent(auth_service, session_factory):
    bootstrap(auth_service)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")

    auth_service.logout(result.token)
    auth_service.logout(result.token)

    assert auth_service.authenticate(result.token) is None
    assert count_rows(session_factory, UserSession) == 0


def test_expired_session_rejects_the_raw_token(auth_service, session_factory):
    bootstrap(auth_service)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")
    digest = hashlib.sha256(result.token.encode("utf-8")).hexdigest()
    with session_factory.begin() as session:
        stored = session.scalar(select(UserSession).where(UserSession.token_digest == digest))
        assert stored is not None
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert auth_service.authenticate(result.token) is None
    assert count_rows(session_factory, UserSession) == 0


def test_password_reset_revokes_existing_sessions(auth_service):
    bootstrap(auth_service)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")

    auth_service.reset_password(result.user.id, "Replacement-123")

    user = auth_service.users.by_username("admin")
    assert user is not None
    assert verify_password("Replacement-123", user.password_hash)
    assert user.must_change_password is True
    assert auth_service.authenticate(result.token) is None


def test_password_change_requires_current_password_and_preserves_session_on_failure(auth_service):
    bootstrap(auth_service)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")

    with pytest.raises(AuthenticationError):
        auth_service.change_password(result.user.id, "wrong-current", "Replacement-123")

    assert auth_service.authenticate(result.token) == result.user
    user = auth_service.users.by_username("admin")
    assert user is not None
    assert verify_password("admin123456", user.password_hash)


def test_password_change_clears_forced_change_and_revokes_sessions(auth_service):
    bootstrap(auth_service)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")

    auth_service.change_password(result.user.id, "admin123456", "Replacement-123")

    user = auth_service.users.by_username("admin")
    assert user is not None
    assert verify_password("Replacement-123", user.password_hash)
    assert user.must_change_password is False
    assert auth_service.authenticate(result.token) is None
    assert auth_service.login("admin", "Replacement-123", "127.0.0.1").user.id == user.id


@pytest.mark.parametrize("replacement", ["short", "admin", "admin123456"])
def test_password_change_rejects_weak_or_reused_replacements(auth_service, replacement):
    bootstrap(auth_service)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")

    with pytest.raises(PasswordValidationError):
        auth_service.change_password(result.user.id, "admin123456", replacement)

    assert auth_service.authenticate(result.token) == result.user


def test_disabling_user_revokes_sessions_and_blocks_login(auth_service):
    bootstrap(auth_service)
    result = auth_service.login("admin", "admin123456", "127.0.0.1")

    auth_service.set_user_enabled(result.user.id, False)

    assert auth_service.authenticate(result.token) is None
    with pytest.raises(AuthenticationError):
        auth_service.login("admin", "admin123456", "127.0.0.2")


@pytest.mark.parametrize("mutation", ["change", "reset", "disable"])
def test_inflight_old_password_login_cannot_survive_user_mutation(
    auth_service,
    session_factory,
    monkeypatch,
    mutation,
):
    bootstrap(auth_service)
    user = auth_service.users.by_username("admin")
    assert user is not None
    login_verified = threading.Event()
    mutation_complete = threading.Event()
    real_verify_password = auth_service_module.verify_password

    def coordinated_verify(password: str, encoded: str) -> bool:
        verified = real_verify_password(password, encoded)
        if threading.current_thread().name.startswith("old-password-login") and verified:
            login_verified.set()
            assert mutation_complete.wait(timeout=10)
        return verified

    monkeypatch.setattr(auth_service_module, "verify_password", coordinated_verify)

    def old_password_login():
        try:
            return auth_service.login("admin", "admin123456", "127.0.0.9")
        except AuthenticationError:
            return None

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-password-login") as executor:
        future = executor.submit(old_password_login)
        assert login_verified.wait(timeout=10)
        if mutation == "change":
            auth_service.change_password(user.id, "admin123456", "Replacement-123")
        elif mutation == "reset":
            auth_service.reset_password(user.id, "Replacement-123")
        else:
            auth_service.set_user_enabled(user.id, False)
        mutation_complete.set()
        login_result = future.result(timeout=10)

    if mutation == "disable":
        auth_service.set_user_enabled(user.id, True)

    assert count_rows(session_factory, UserSession) == 0
    if login_result is not None:
        assert auth_service.authenticate(login_result.token) is None


def test_rate_limiter_allows_five_failures_per_normalized_user_and_ip():
    limiter = LoginRateLimiter()

    for index in range(5):
        assert limiter.allow("Admin", "127.0.0.1", now=float(index)) is True
        limiter.record_failure("ADMIN", "127.0.0.1", now=float(index))

    assert limiter.allow("admin", "127.0.0.1", now=5.0) is False
    assert limiter.allow("admin", "127.0.0.2", now=5.0) is True


def test_rate_limiter_evicts_failures_after_fifteen_minutes():
    limiter = LoginRateLimiter()
    for index in range(5):
        limiter.record_failure("admin", "127.0.0.1", now=float(index))

    assert limiter.allow("admin", "127.0.0.1", now=899.0) is False
    assert limiter.allow("admin", "127.0.0.1", now=904.0) is True


def test_rate_limiter_atomically_accepts_only_five_concurrent_failures():
    limiter = LoginRateLimiter()

    with ThreadPoolExecutor(max_workers=20) as executor:
        accepted = list(
            executor.map(
                lambda _: limiter.record_failure_if_allowed(
                    "admin", "127.0.0.1", now=10.0
                ),
                range(20),
            )
        )

    assert accepted.count(True) == 5
    assert accepted.count(False) == 15


def test_successful_login_resets_failure_budget(auth_service):
    bootstrap(auth_service)
    for _ in range(4):
        with pytest.raises(AuthenticationError):
            auth_service.login("admin", "wrong", "127.0.0.1")

    auth_service.login("admin", "admin123456", "127.0.0.1")

    for _ in range(5):
        with pytest.raises(AuthenticationError):
            auth_service.login("admin", "wrong", "127.0.0.1")
    with pytest.raises(LoginRateLimited):
        auth_service.login("admin", "admin123456", "127.0.0.1")
