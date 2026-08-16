from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.audit import audit_service, request_ip
from backend.auth import (
    AuthenticatedUser,
    AuthenticationError,
    LoginRateLimited,
    PasswordValidationError,
)
from backend.auth.dependencies import (
    SESSION_COOKIE_NAME,
    auth_service,
    current_user,
    require_same_origin,
)
from backend.schemas import CurrentUserResponse, LoginRequest, PasswordChangeRequest


router = APIRouter(
    prefix="/api/auth",
    tags=["authentication"],
    dependencies=[Depends(require_same_origin)],
)


def _user_response(user: AuthenticatedUser) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }


@router.post("/login", response_model=CurrentUserResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    service = auth_service(request)
    client_ip = request_ip(request) or ""
    try:
        result = service.login(payload.username, payload.password, client_ip)
    except LoginRateLimited as exc:
        attempted_user = service.users.by_username(payload.username)
        audit_service(request).record(
            attempted_user.id if attempted_user is not None else None,
            "login",
            "session",
            None,
            "rejected",
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
        ) from exc
    except AuthenticationError as exc:
        attempted_user = service.users.by_username(payload.username)
        audit_service(request).record(
            attempted_user.id if attempted_user is not None else None,
            "login",
            "session",
            None,
            "rejected",
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        ) from exc

    audit_service(request).record(
        result.user.id,
        "login",
        "session",
        result.user.id,
        "success",
        client_ip,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=result.token,
        httponly=True,
        secure=bool(getattr(request.app.state, "cookie_secure", False)),
        samesite="lax",
        path="/",
        expires=result.expires_at,
    )
    return _user_response(result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    user = auth_service(request).authenticate(raw_token) if raw_token else None
    if raw_token:
        auth_service(request).logout(raw_token)
    if user is not None:
        audit_service(request).record(
            user.id,
            "logout",
            "session",
            user.id,
            "success",
            request_ip(request),
        )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=bool(getattr(request.app.state, "cookie_secure", False)),
        samesite="lax",
    )
    return response


@router.get("/me", response_model=CurrentUserResponse)
def me(user: AuthenticatedUser = Depends(current_user)):
    return _user_response(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> Response:
    try:
        auth_service(request).change_password(
            user.id,
            payload.current_password,
            payload.new_password,
        )
    except AuthenticationError as exc:
        audit_service(request).record(
            user.id,
            "password_change",
            "user",
            user.id,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="current password is invalid",
        ) from exc
    except PasswordValidationError as exc:
        audit_service(request).record(
            user.id,
            "password_change",
            "user",
            user.id,
            "rejected",
            request_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    audit_service(request).record(
        user.id,
        "password_change",
        "user",
        user.id,
        "success",
        request_ip(request),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=bool(getattr(request.app.state, "cookie_secure", False)),
        samesite="lax",
    )
    return response
