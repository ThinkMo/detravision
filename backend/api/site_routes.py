from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import StreamingResponse

from backend.audit import audit_service, audited_admin, request_ip
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import require_same_origin
from backend.brand import BrandService
from backend.storage import (
    ImageLimitError,
    ImageValidationError,
    UnsupportedImageError,
    validate_image,
)


MAX_LOGO_BYTES = 10 * 1024 * 1024
MAX_LOGO_PIXELS = 40_000_000
ALLOWED_LOGO_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
LOGO_EXTENSION_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/settings/site",
    tags=["site settings"],
    dependencies=[Depends(require_same_origin)],
)


class SiteNameUpdate(BaseModel):
    site_name: str = Field(min_length=1, max_length=200)

    @field_validator("site_name")
    @classmethod
    def normalize_site_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("site_name must not be blank")
        return normalized


def brand_service(request: Request) -> BrandService:
    return request.app.state.brand_service


def _service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="site settings are temporarily unavailable",
    )


def _storage_unavailable(error: OSError) -> HTTPException:
    logger.error(
        "site_logo_storage_unavailable error_type=%s",
        type(error).__name__,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="site storage is temporarily unavailable",
    )


@router.get("")
def get_site_settings(request: Request) -> dict[str, str | None]:
    try:
        return brand_service(request).get().public_dict()
    except SQLAlchemyError as exc:
        raise _service_unavailable() from exc


@router.patch("")
def update_site_settings(
    payload: SiteNameUpdate,
    request: Request,
    user: AuthenticatedUser = Depends(
        audited_admin("site_settings_update", "site_settings")
    ),
) -> dict[str, str | None]:
    try:
        result = brand_service(request).update_site_name(payload.site_name)
    except SQLAlchemyError as exc:
        raise _service_unavailable() from exc
    audit_service(request).record(
        user.id,
        "site_settings_update",
        "site_settings",
        1,
        "success",
        request_ip(request),
    )
    return result.public_dict()


@router.post("/logo")
def replace_site_logo(
    request: Request,
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(
        audited_admin("site_logo_update", "site_settings")
    ),
) -> dict[str, str | None]:
    declared_type = (file.content_type or "").casefold()
    if declared_type not in ALLOWED_LOGO_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Logo must be PNG, JPEG, or WebP",
        )
    filename = (file.filename or "").replace("\\", "/")
    extension = PurePosixPath(filename).suffix.casefold()
    extension_type = LOGO_EXTENSION_MEDIA_TYPES.get(extension)
    if extension_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Logo filename must end with .png, .jpg, .jpeg, or .webp",
        )
    try:
        image = validate_image(
            file.file,
            max_bytes=MAX_LOGO_BYTES,
            max_pixels=MAX_LOGO_PIXELS,
        )
    except ImageLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except UnsupportedImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if image.media_type != declared_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="declared media type does not match decoded image",
        )
    if image.media_type != extension_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="filename extension does not match decoded image",
        )
    try:
        result = brand_service(request).replace_logo(image)
    except SQLAlchemyError as exc:
        raise _service_unavailable() from exc
    except OSError as exc:
        raise _storage_unavailable(exc) from exc
    audit_service(request).record(
        user.id,
        "site_logo_update",
        "site_settings",
        1,
        "success",
        request_ip(request),
    )
    return result.public_dict()


def _stream_and_close(stream) -> Iterator[bytes]:
    try:
        while chunk := stream.read(64 * 1024):
            yield chunk
    finally:
        stream.close()


@router.get("/logo")
def get_site_logo(request: Request) -> StreamingResponse:
    try:
        stored = brand_service(request).open_logo()
    except (SQLAlchemyError, ValueError) as exc:
        raise _service_unavailable() from exc
    except OSError as exc:
        raise _storage_unavailable(exc) from exc
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logo not found")
    stream, media_type = stored
    return StreamingResponse(
        _stream_and_close(stream),
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )
