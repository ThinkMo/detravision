from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import Settings
from backend.db.models import User, UserSession

from .rate_limit import LoginRateLimiter
from .security import (
    INVALID_PASSWORD_HASH,
    digest_session_token,
    hash_password,
    issue_session_token,
    verify_password,
)


class AuthError(Exception):
    """Base class for authentication service failures."""


class AuthenticationError(AuthError):
    """Credentials or current-password verification failed."""


class LoginRateLimited(AuthError):
    """The username and IP pair exceeded its login failure budget."""


class PasswordValidationError(AuthError):
    """A replacement password violates the local password policy."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: int
    username: str
    role: str
    must_change_password: bool


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: AuthenticatedUser
    token: str
    expires_at: datetime


class UserReader:
    """Small read boundary used by auth consumers and later API services."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def by_username(self, username: str) -> User | None:
        with self._session_factory() as session:
            return session.scalar(select(User).where(User.username == username))

    def by_id(self, user_id: int) -> User | None:
        with self._session_factory() as session:
            return session.get(User, user_id)


class AuthService:
    _BOOTSTRAP_ATTEMPTS = 2
    _MYSQL_TRANSIENT_LOCK_ERRORS = frozenset({1205, 1213})

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        rate_limiter: LoginRateLimiter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self.rate_limiter = rate_limiter or LoginRateLimiter()
        self.users = UserReader(session_factory)

    @staticmethod
    def _authenticated_user(user: User) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=user.id,
            username=user.username,
            role=user.role,
            must_change_password=user.must_change_password,
        )

    def bootstrap_admin(self) -> None:
        last_error: IntegrityError | OperationalError | None = None
        for _attempt in range(self._BOOTSTRAP_ATTEMPTS):
            try:
                self._bootstrap_admin_once()
                return
            except IntegrityError as exc:
                last_error = exc
            except OperationalError as exc:
                if not self._is_mysql_transient_lock_error(exc):
                    raise
                last_error = exc
            if self._bootstrap_winner_exists():
                return
        assert last_error is not None
        raise last_error

    def _bootstrap_winner_exists(self) -> bool:
        with self._session_factory() as session:
            return (
                session.scalar(select(User.id).where(User.username == "admin"))
                is not None
            )

    @classmethod
    def _is_mysql_transient_lock_error(cls, exc: OperationalError) -> bool:
        original_args = getattr(exc.orig, "args", ())
        return bool(
            original_args
            and isinstance(original_args[0], int)
            and original_args[0] in cls._MYSQL_TRANSIENT_LOCK_ERRORS
        )

    def _bootstrap_admin_once(self) -> None:
        with self._session_factory.begin() as session:
            existing_id = session.scalar(select(User.id).order_by(User.id).limit(1))
            if existing_id is not None:
                return

            initial_password = self._settings.admin_initial_password
            if initial_password is None:
                if self._settings.environment.casefold() != "development":
                    raise RuntimeError(
                        "ADMIN_INITIAL_PASSWORD is required outside development"
                    )
                initial_password = "admin123456"
            self._validate_replacement("admin", None, initial_password)
            session.add(
                User(
                    username="admin",
                    password_hash=hash_password(initial_password),
                    role="admin",
                    enabled=True,
                    must_change_password=True,
                )
            )

    def login(self, username: str, password: str, ip: str) -> LoginResult:
        if not self.rate_limiter.allow(username, ip):
            raise LoginRateLimited("too many login attempts")

        with self._session_factory.begin() as session:
            user = session.scalar(select(User).where(User.username == username))
            encoded = user.password_hash if user is not None else INVALID_PASSWORD_HASH
            password_valid = verify_password(password, encoded)
            if user is None or not user.enabled or not password_valid:
                if not self.rate_limiter.record_failure_if_allowed(username, ip):
                    raise LoginRateLimited("too many login attempts")
                raise AuthenticationError("invalid username or password")

            credential_lock = session.execute(
                update(User)
                .where(
                    User.id == user.id,
                    User.password_hash == encoded,
                    User.enabled.is_(True),
                )
                .values(password_hash=encoded)
            )
            if credential_lock.rowcount != 1:
                if not self.rate_limiter.record_failure_if_allowed(username, ip):
                    raise LoginRateLimited("too many login attempts")
                raise AuthenticationError("invalid username or password")

            raw_token, token_digest = issue_session_token()
            expires_at = datetime.now(timezone.utc) + timedelta(
                hours=self._settings.session_ttl_hours
            )
            session.add(
                UserSession(
                    user_id=user.id,
                    token_digest=token_digest,
                    expires_at=expires_at,
                )
            )
            authenticated = self._authenticated_user(user)

        self.rate_limiter.reset(username, ip)
        return LoginResult(user=authenticated, token=raw_token, expires_at=expires_at)

    def authenticate(self, raw_token: str) -> AuthenticatedUser | None:
        token_digest = digest_session_token(raw_token)
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            stored = session.scalar(
                select(UserSession).where(UserSession.token_digest == token_digest)
            )
            if stored is None:
                return None
            if stored.expires_at <= now:
                session.delete(stored)
                return None
            user = session.get(User, stored.user_id)
            if user is None or not user.enabled:
                session.delete(stored)
                return None
            return self._authenticated_user(user)

    def logout(self, raw_token: str) -> None:
        token_digest = digest_session_token(raw_token)
        with self._session_factory.begin() as session:
            session.execute(
                delete(UserSession).where(UserSession.token_digest == token_digest)
            )

    def change_password(self, user_id: int, current: str, replacement: str) -> None:
        with self._session_factory.begin() as session:
            user = session.get(User, user_id)
            if user is None or not verify_password(current, user.password_hash):
                raise AuthenticationError("current password is invalid")
            self._validate_replacement(user.username, user.password_hash, replacement)
            user.password_hash = hash_password(replacement)
            user.must_change_password = False
            user.updated_at = datetime.now(timezone.utc)
            self._revoke_user_sessions(session, user.id)

    def reset_password(self, user_id: int, replacement: str) -> None:
        with self._session_factory.begin() as session:
            user = session.get(User, user_id)
            if user is None:
                raise AuthenticationError("user does not exist")
            self._validate_replacement(user.username, user.password_hash, replacement)
            user.password_hash = hash_password(replacement)
            user.must_change_password = True
            user.updated_at = datetime.now(timezone.utc)
            self._revoke_user_sessions(session, user.id)

    def set_user_enabled(self, user_id: int, enabled: bool) -> None:
        with self._session_factory.begin() as session:
            user = session.get(User, user_id)
            if user is None:
                raise AuthenticationError("user does not exist")
            user.enabled = enabled
            user.updated_at = datetime.now(timezone.utc)
            if not enabled:
                self._revoke_user_sessions(session, user.id)

    @staticmethod
    def _validate_replacement(
        username: str,
        current_hash: str | None,
        replacement: str,
    ) -> None:
        if len(replacement) < 10:
            raise PasswordValidationError("password must be at least 10 characters")
        if replacement.casefold() == username.casefold():
            raise PasswordValidationError("password must not equal the username")
        if current_hash is not None and verify_password(replacement, current_hash):
            raise PasswordValidationError("new password must differ from current password")

    @staticmethod
    def _revoke_user_sessions(session: Session, user_id: int) -> None:
        session.execute(delete(UserSession).where(UserSession.user_id == user_id))
