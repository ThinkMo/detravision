from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from fastapi import UploadFile

from backend.auth import AuthenticatedUser
from backend.engine import DetectorProvider
from backend.storage import (
    FileStorage,
    ImageLimitError,
    ImageValidationError,
    UnsupportedImageError,
    validate_image,
)

from .repository import BatchRepository
from .types import BatchImageView, BatchJobPage, BatchJobView, PendingImage


MAX_BATCH_IMAGES = 20
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BATCH_BYTES = MAX_BATCH_IMAGES * MAX_IMAGE_BYTES
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
EXTENSION_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

logger = logging.getLogger(__name__)
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


@dataclass(frozen=True, slots=True)
class BatchServiceError(Exception):
    status_code: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ResultDownload:
    stream: BinaryIO
    filename: str
    media_type: str


BatchDownload = ResultDownload


@dataclass(frozen=True, slots=True)
class BatchZipDownload:
    path: Path
    filename: str


class BatchService:
    """Validate uploads and expose owner-scoped batch results without storage keys."""

    def __init__(
        self,
        repository: BatchRepository,
        storage: FileStorage,
        provider: DetectorProvider,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._provider = provider

    def create_job(
        self,
        user_id: int,
        uploads: list[UploadFile],
    ) -> BatchJobView:
        if not self._provider.available:
            raise BatchServiceError(
                409,
                "model_unavailable",
                "no inference model is loaded",
            )
        if not uploads:
            raise BatchServiceError(422, "no_images", "at least one image is required")
        if len(uploads) > MAX_BATCH_IMAGES:
            raise BatchServiceError(
                422,
                "too_many_images",
                f"at most {MAX_BATCH_IMAGES} images are allowed",
            )

        pending: list[PendingImage] = []
        saved_keys: list[str] = []
        total_bytes = 0
        try:
            for ordinal, upload in enumerate(uploads, start=1):
                declared_type = (upload.content_type or "").casefold()
                if declared_type not in ALLOWED_MEDIA_TYPES:
                    raise BatchServiceError(
                        415,
                        "unsupported_media_type",
                        "images must be PNG, JPEG, or WebP",
                    )
                supplied_name = (upload.filename or "").replace("\\", "/")
                extension = PurePosixPath(supplied_name).suffix.casefold()
                extension_type = EXTENSION_MEDIA_TYPES.get(extension)
                if extension_type is None:
                    raise BatchServiceError(
                        415,
                        "unsupported_file_extension",
                        "image filename must end with .png, .jpg, .jpeg, or .webp",
                    )
                if extension_type != declared_type:
                    raise BatchServiceError(
                        415,
                        "media_type_mismatch",
                        "filename extension does not match declared media type",
                    )
                try:
                    image = validate_image(
                        upload.file,
                        max_bytes=MAX_IMAGE_BYTES,
                        max_pixels=MAX_IMAGE_PIXELS,
                    )
                except ImageLimitError as exc:
                    message = str(exc)
                    code = (
                        "pixel_limit_exceeded"
                        if "pixel" in message
                        else "image_too_large"
                    )
                    raise BatchServiceError(413, code, message) from exc
                except UnsupportedImageError as exc:
                    raise BatchServiceError(
                        415,
                        "unsupported_media_type",
                        str(exc),
                    ) from exc
                except ImageValidationError as exc:
                    raise BatchServiceError(400, "invalid_image", str(exc)) from exc

                if image.media_type != declared_type:
                    raise BatchServiceError(
                        415,
                        "media_type_mismatch",
                        "declared media type does not match decoded image",
                    )
                total_bytes += len(image.content)
                if total_bytes > MAX_BATCH_BYTES:
                    raise BatchServiceError(
                        413,
                        "batch_too_large",
                        "combined upload exceeds the request byte limit",
                    )
                display_name = sanitize_display_name(
                    upload.filename,
                    fallback=f"image-{ordinal}.{image.suffix}",
                )
                key = self._storage.save(image.open(), "batch-source", image.suffix)
                saved_keys.append(key)
                pending.append(PendingImage(display_name=display_name, source_key=key))

            try:
                return self._repository.create_job(user_id, pending)
            except Exception as exc:
                raise BatchServiceError(
                    503,
                    "batch_create_failed",
                    "batch job could not be created",
                ) from exc
        except BatchServiceError:
            self._remove_saved(saved_keys)
            raise
        except (OSError, ValueError) as exc:
            self._remove_saved(saved_keys)
            raise BatchServiceError(
                503,
                "batch_storage_failed",
                "uploaded images could not be stored",
            ) from exc

    def list_jobs(
        self,
        user: AuthenticatedUser,
        *,
        page: int,
        page_size: int,
        status: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        requested_user_id: int | None,
    ) -> BatchJobPage:
        owner_id = requested_user_id if user.role == "admin" else user.id
        return self._repository.list_jobs(
            user_id=owner_id,
            page=page,
            page_size=page_size,
            status=status,
            created_from=created_from,
            created_to=created_to,
        )

    def get_job(
        self,
        user: AuthenticatedUser,
        job_id: int,
    ) -> tuple[BatchJobView, list[BatchImageView]]:
        owner_id = None if user.role == "admin" else user.id
        job = self._repository.get_job(job_id, user_id=owner_id)
        if job is None:
            raise BatchServiceError(
                404,
                "job_not_found",
                "batch job was not found",
            )
        images = self._repository.list_images(job_id, user_id=owner_id)
        return job, images

    def open_result(
        self,
        user: AuthenticatedUser,
        job_id: int,
        image_id: int,
    ) -> ResultDownload:
        _job, images = self.get_job(user, job_id)
        image = next((item for item in images if item.id == image_id), None)
        if (
            image is None
            or image.status != "completed"
            or image.annotated_key is None
        ):
            raise BatchServiceError(
                404,
                "result_not_found",
                "image result was not found",
            )
        try:
            stream = self._storage.open(image.annotated_key)
        except (OSError, ValueError) as exc:
            raise BatchServiceError(
                404,
                "result_not_found",
                "image result was not found",
            ) from exc
        stem = Path(image.display_name).stem or f"image-{image.id}"
        filename = sanitize_display_name(
            f"{stem}.jpg",
            fallback=f"image-{image.id}.jpg",
        )
        return ResultDownload(stream=stream, filename=filename, media_type="image/jpeg")

    def create_zip(
        self,
        user: AuthenticatedUser,
        job_id: int,
        manifest: dict[str, object],
    ) -> BatchZipDownload:
        _job, images = self.get_job(user, job_id)
        descriptor, name = tempfile.mkstemp(prefix=f"batch-{job_id}-", suffix=".zip")
        os.close(descriptor)
        path = Path(name)
        try:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "results.json",
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ).encode("utf-8"),
                )
                completed_ordinal = 0
                for image in images:
                    if image.status != "completed" or image.annotated_key is None:
                        continue
                    completed_ordinal += 1
                    stem = Path(image.display_name).stem or f"image-{image.id}"
                    display_name = sanitize_display_name(
                        f"{stem}.jpg",
                        fallback=f"image-{image.id}.jpg",
                    )
                    member_name = f"{completed_ordinal:03d}-{display_name}"
                    with self._storage.open(image.annotated_key) as source:
                        archive.writestr(member_name, source.read())
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise BatchServiceError(
                503,
                "batch_download_failed",
                "batch results could not be prepared",
            ) from exc
        return BatchZipDownload(path=path, filename=f"batch-{job_id}-results.zip")

    def _remove_saved(self, keys: list[str]) -> None:
        for key in reversed(keys):
            try:
                self._storage.delete(key)
            except Exception:
                logger.warning(
                    "batch_upload_cleanup_failed error_code=storage_delete_failed"
                )


def sanitize_display_name(value: str | None, *, fallback: str) -> str:
    normalized = (value or "").replace("\\", "/")
    leaf = PurePosixPath(normalized).name
    safe = _UNSAFE_FILENAME.sub("_", leaf).strip(" .")
    if safe in {"", ".", ".."}:
        safe = fallback
    return safe[:200]
