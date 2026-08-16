from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.audit import audit_service, audited_admin, request_ip
from backend.auth import (
    AuthenticatedUser,
    AuthenticationError,
    FinalAdminError,
    PasswordValidationError,
    hash_password,
)
from backend.auth.dependencies import auth_service, require_admin, require_same_origin
from backend.db.models import User
from backend.schemas import (
    TemporaryPasswordResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)


router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_same_origin)],
)


def _user_response(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "enabled": user.enabled,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def _session_factory(request: Request):
    return request.app.state.session_factory


@router.get("", response_model=list[UserResponse])
def list_users(
    request: Request,
    _user: AuthenticatedUser = Depends(require_admin),
):
    with _session_factory(request)() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        return [_user_response(user) for user in users]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def create_user(
    payload: UserCreate,
    request: Request,
    actor: AuthenticatedUser = Depends(audited_admin("create", "user")),
):
    if payload.password.casefold() == payload.username.casefold():
        audit_service(request).record(
            actor.id,
            "create",
            "user",
            None,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="password must not equal the username",
        )
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        enabled=True,
        must_change_password=True,
    )
    try:
        with _session_factory(request).begin() as session:
            session.add(user)
            session.flush()
            result = _user_response(user)
    except IntegrityError as exc:
        audit_service(request).record(
            actor.id,
            "create",
            "user",
            None,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username already exists",
        ) from exc
    audit_service(request).record(
        actor.id,
        "create",
        "user",
        result["id"],
        "success",
        request_ip(request),
        details={"role": payload.role},
    )
    return result


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    actor: AuthenticatedUser = Depends(audited_admin("update", "user")),
):
    try:
        user = auth_service(request).update_user(
            user_id,
            role=payload.role,
            enabled=payload.enabled,
        )
    except AuthenticationError as exc:
        audit_service(request).record(
            actor.id,
            "update",
            "user",
            user_id,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        ) from exc
    except FinalAdminError as exc:
        audit_service(request).record(
            actor.id,
            "update",
            "user",
            user_id,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot remove the final enabled administrator",
        ) from exc
    audit_service(request).record(
        actor.id,
        "update",
        "user",
        user_id,
        "success",
        request_ip(request),
        details=payload.model_dump(exclude_unset=True),
    )
    return _user_response(user)


@router.post("/{user_id}/reset-password", response_model=TemporaryPasswordResponse)
def reset_password(
    user_id: int,
    request: Request,
    actor: AuthenticatedUser = Depends(audited_admin("password_reset", "user")),
):
    temporary_password = secrets.token_urlsafe(12)
    try:
        auth_service(request).reset_password(user_id, temporary_password)
    except AuthenticationError as exc:
        audit_service(request).record(
            actor.id,
            "password_reset",
            "user",
            user_id,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        ) from exc
    except PasswordValidationError as exc:
        audit_service(request).record(
            actor.id,
            "password_reset",
            "user",
            user_id,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    audit_service(request).record(
        actor.id,
        "password_reset",
        "user",
        user_id,
        "success",
        request_ip(request),
    )
    return {"temporary_password": temporary_password}
