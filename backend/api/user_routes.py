from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.auth import (
    AuthenticationError,
    PasswordValidationError,
    hash_password,
)
from backend.auth.dependencies import auth_service, require_admin, require_same_origin
from backend.db.models import User, UserSession
from backend.schemas import (
    TemporaryPasswordResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)


router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_same_origin), Depends(require_admin)],
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
def list_users(request: Request):
    with _session_factory(request)() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        return [_user_response(user) for user in users]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def create_user(payload: UserCreate, request: Request):
    if payload.password.casefold() == payload.username.casefold():
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username already exists",
        ) from exc
    return result


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(user_id: int, payload: UserUpdate, request: Request):
    with _session_factory(request).begin() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="user not found",
            )

        next_role = payload.role if payload.role is not None else user.role
        next_enabled = payload.enabled if payload.enabled is not None else user.enabled
        removes_enabled_admin = (
            user.role == "admin"
            and user.enabled
            and (next_role != "admin" or not next_enabled)
        )
        if removes_enabled_admin:
            other_admins = session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.id != user.id,
                    User.role == "admin",
                    User.enabled.is_(True),
                )
            )
            if not other_admins:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="cannot remove the final enabled administrator",
                )

        user.role = next_role
        user.enabled = next_enabled
        user.updated_at = datetime.now(timezone.utc)
        if payload.enabled is False:
            session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        session.flush()
        return _user_response(user)


@router.post("/{user_id}/reset-password", response_model=TemporaryPasswordResponse)
def reset_password(user_id: int, request: Request):
    temporary_password = secrets.token_urlsafe(12)
    try:
        auth_service(request).reset_password(user_id, temporary_password)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        ) from exc
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"temporary_password": temporary_password}
