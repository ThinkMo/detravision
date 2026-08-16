from __future__ import annotations

import json
from inspect import isawaitable
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from backend.auth import AuthenticatedUser
from backend.auth.dependencies import current_user, require_admin
from backend.db.models import AuditLog


_SENSITIVE_KEY_PARTS = frozenset(
    {
        "cookie",
        "file_path",
        "local_path",
        "password",
        "path",
        "rtsp",
        "secret",
        "session",
        "snapshot",
        "token",
    }
)


def _sensitive_key(key: object) -> bool:
    normalized = str(key).casefold()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sensitive_string(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized.startswith(("rtsp://", "file://")):
        return True
    return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _sensitive_key(key) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and _sensitive_string(value):
        return "[REDACTED]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class AuditService:
    """Persist compact, sanitized audit records in independent transactions."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        actor_id: int | None,
        action: str,
        resource_type: str,
        resource_id: str | int | None,
        outcome: str,
        ip: str | None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        serialized_details = (
            json.dumps(_sanitize(details), ensure_ascii=False, sort_keys=True)
            if details is not None
            else None
        )
        with self._session_factory.begin() as session:
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=(
                        str(resource_id) if resource_id is not None else None
                    ),
                    outcome=outcome,
                    details=serialized_details,
                    ip=ip or None,
                )
            )


def audit_service(request: Request) -> AuditService:
    configured = getattr(request.app.state, "audit_service", None)
    if configured is not None:
        return configured
    return AuditService(request.app.state.session_factory)


def request_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _path_resource_id(request: Request) -> str | None:
    for name in (
        "camera_id",
        "person_id",
        "group_id",
        "model_id",
        "event_id",
        "user_id",
    ):
        value = request.path_params.get(name)
        if value is not None:
            return str(value)
    return None


def audited_admin(action: str, resource_type: str):
    """Require an administrator and audit authenticated role denials."""

    async def dependency(
        request: Request,
        user: AuthenticatedUser = Depends(current_user),
    ) -> AuthenticatedUser:
        try:
            enforce_admin = request.app.dependency_overrides.get(
                require_admin,
                require_admin,
            )
            result = enforce_admin(user)
            return await result if isawaitable(result) else result
        except HTTPException:
            audit_service(request).record(
                user.id,
                action,
                resource_type,
                _path_resource_id(request),
                "rejected",
                request_ip(request),
            )
            raise

    return dependency
