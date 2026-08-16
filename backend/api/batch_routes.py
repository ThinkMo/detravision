from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from backend.audit import audit_service, request_ip
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import current_user, require_same_origin
from backend.batch.service import BatchService, BatchServiceError
from backend.schemas import (
    BatchImageResponse,
    BatchJobDetailResponse,
    BatchJobPageResponse,
    BatchJobResponse,
)


router = APIRouter(
    prefix="/api/batch-jobs",
    tags=["batch inference"],
    dependencies=[Depends(require_same_origin)],
)


def batch_service(request: Request) -> BatchService:
    return BatchService(
        request.app.state.batch_repository,
        request.app.state.file_storage,
        request.app.state.detector_provider,
    )


def error_response(error: BatchServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


def job_response(job) -> BatchJobResponse:
    return BatchJobResponse(
        id=job.id,
        user_id=job.user_id,
        status=job.status,
        image_total=job.image_total,
        success_count=job.success_count,
        failure_count=job.failure_count,
        expires_at=job.expires_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def image_response(job_id: int, image) -> BatchImageResponse:
    return BatchImageResponse(
        id=image.id,
        display_name=image.display_name,
        status=image.status,
        error_code=image.error_code,
        error_message=image.error_message,
        model_version_id=image.model_version_id,
        detections=image.detections,
        result_url=(
            f"/api/batch-jobs/{job_id}/images/{image.id}/download"
        ),
        created_at=image.created_at,
        updated_at=image.updated_at,
    )


def detail_response(job, images) -> BatchJobDetailResponse:
    return BatchJobDetailResponse(
        **job_response(job).model_dump(),
        images=[image_response(job.id, image) for image in images],
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=BatchJobResponse,
)
def create_batch_job(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    user: AuthenticatedUser = Depends(current_user),
):
    try:
        job = batch_service(request).create_job(user.id, files)
    except BatchServiceError as exc:
        return error_response(exc)
    audit_service(request).record(
        user.id,
        "batch_job_create",
        "batch_job",
        job.id,
        "success",
        request_ip(request),
        details={"image_count": job.image_total},
    )
    return job_response(job)


@router.get("", response_model=BatchJobPageResponse)
def list_batch_jobs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    user_id: int | None = Query(default=None, ge=1),
    user: AuthenticatedUser = Depends(current_user),
):
    if any(
        value is not None and (value.tzinfo is None or value.utcoffset() is None)
        for value in (created_from, created_to)
    ):
        return error_response(
            BatchServiceError(
                422,
                "invalid_time_filter",
                "batch time filters must include a UTC offset",
            )
        )
    page_result = batch_service(request).list_jobs(
        user,
        page=page,
        page_size=page_size,
        status=status_filter,
        created_from=created_from,
        created_to=created_to,
        requested_user_id=user_id,
    )
    return BatchJobPageResponse(
        items=[job_response(job) for job in page_result.items],
        total=page_result.total,
        page=page_result.page,
        page_size=page_result.page_size,
    )


@router.get("/{job_id}", response_model=BatchJobDetailResponse)
def get_batch_job(
    job_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
):
    try:
        job, images = batch_service(request).get_job(user, job_id)
    except BatchServiceError as exc:
        return error_response(exc)
    return detail_response(job, images)


def _stream_and_close(stream) -> Iterator[bytes]:
    try:
        while chunk := stream.read(64 * 1024):
            yield chunk
    finally:
        stream.close()


@router.get("/{job_id}/images/{image_id}/download")
def download_batch_image(
    job_id: int,
    image_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
):
    try:
        download = batch_service(request).open_result(user, job_id, image_id)
    except BatchServiceError as exc:
        return error_response(exc)
    disposition = f"attachment; filename*=UTF-8''{quote(download.filename, safe='')}"
    return StreamingResponse(
        _stream_and_close(download.stream),
        media_type=download.media_type,
        headers={"Content-Disposition": disposition},
    )


@router.get("/{job_id}/download")
def download_batch_job(
    job_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
):
    service = batch_service(request)
    try:
        job, images = service.get_job(user, job_id)
        manifest = detail_response(job, images).model_dump(mode="json")
        download = service.create_zip(user, job_id, manifest)
    except BatchServiceError as exc:
        return error_response(exc)
    return FileResponse(
        download.path,
        media_type="application/zip",
        filename=download.filename,
        background=BackgroundTask(download.path.unlink, missing_ok=True),
    )
