from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from .service import AuthenticatedUser, AuthService


SESSION_COOKIE_NAME = "session"
_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_PASSWORD_CHANGE_ALLOWED_PATHS = frozenset(
    {
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/change-password",
    }
)


def auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def require_same_origin(request: Request) -> None:
    if request.method not in _MUTATING_METHODS:
        return

    request_host = request.url.netloc.casefold()
    for header in ("origin", "referer"):
        supplied = request.headers.get(header)
        if supplied is None:
            continue
        supplied_host = urlsplit(supplied).netloc.casefold()
        if not supplied_host or supplied_host != request_host:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="cross-origin mutation rejected",
            )


def current_user(request: Request) -> AuthenticatedUser:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )

    user = auth_service(request).authenticate(raw_token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    if (
        user.must_change_password
        and request.url.path not in _PASSWORD_CHANGE_ALLOWED_PATHS
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password change required",
        )
    return user


def require_admin(
    user: AuthenticatedUser = Depends(current_user),
) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="administrator role required",
        )
    return user
