from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import BatchImage, BatchJob

from .types import (
    BatchImageView,
    BatchJobPage,
    BatchJobView,
    ClaimedImage,
    CompletedImage,
    ExpiredImage,
    ExpiredJob,
    PendingImage,
)


class BatchImageStateError(RuntimeError):
    """The requested result transition is invalid or the image is missing."""


class BatchRepository:
    """Portable transactional persistence for batch inference work."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        retention_days: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self._session_factory = session_factory
        self._retention = timedelta(days=retention_days)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_job(
        self, user_id: int, images: list[PendingImage]
    ) -> BatchJobView:
        if not images:
            raise ValueError("at least one image is required")
        now = self._now()
        with self._session_factory.begin() as session:
            job = BatchJob(
                user_id=user_id,
                status="queued",
                image_total=len(images),
                success_count=0,
                failure_count=0,
                expires_at=now + self._retention,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.flush()
            session.add_all(
                BatchImage(
                    job_id=job.id,
                    display_name=image.display_name,
                    source_key=image.source_key,
                    status="queued",
                    detections_json="[]",
                    created_at=now,
                    updated_at=now,
                )
                for image in images
            )
            session.flush()
            return self._job_view(job)

    def get_job(
        self, job_id: int, *, user_id: int | None = None
    ) -> BatchJobView | None:
        statement = select(BatchJob).where(BatchJob.id == job_id)
        if user_id is not None:
            statement = statement.where(BatchJob.user_id == user_id)
        with self._session_factory() as session:
            job = session.scalar(statement)
            return self._job_view(job) if job is not None else None

    def list_jobs(
        self,
        *,
        user_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> BatchJobPage:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = select(BatchJob)
        if user_id is not None:
            statement = statement.where(BatchJob.user_id == user_id)
        if status is not None:
            statement = statement.where(BatchJob.status == status)
        if created_from is not None:
            statement = statement.where(BatchJob.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(BatchJob.created_at <= created_to)
        count_statement = select(func.count()).select_from(statement.subquery())
        page_statement = (
            statement.order_by(BatchJob.created_at.desc(), BatchJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        with self._session_factory() as session:
            total = int(session.scalar(count_statement) or 0)
            items = [self._job_view(job) for job in session.scalars(page_statement)]
        return BatchJobPage(items=items, total=total, page=page, page_size=page_size)

    def list_images(
        self, job_id: int, *, user_id: int | None = None
    ) -> list[BatchImageView]:
        statement = select(BatchImage).where(BatchImage.job_id == job_id)
        if user_id is not None:
            statement = statement.join(
                BatchJob, BatchJob.id == BatchImage.job_id
            ).where(BatchJob.user_id == user_id)
        statement = statement.order_by(BatchImage.id)
        with self._session_factory() as session:
            return [self._image_view(image) for image in session.scalars(statement)]

    def claim_next_image(self) -> ClaimedImage | None:
        now = self._now()
        for _attempt in range(2):
            with self._session_factory.begin() as session:
                image_id = session.scalar(
                    select(BatchImage.id)
                    .where(BatchImage.status == "queued")
                    .order_by(BatchImage.created_at, BatchImage.id)
                    .limit(1)
                )
                if image_id is None:
                    return None
                claimed = session.execute(
                    update(BatchImage)
                    .where(
                        BatchImage.id == image_id,
                        BatchImage.status == "queued",
                    )
                    .values(status="processing", updated_at=now)
                )
                if claimed.rowcount != 1:
                    continue
                image = session.get(BatchImage, image_id)
                assert image is not None
                self._recompute_job(session, image.job_id, now)
                return ClaimedImage(
                    id=image.id,
                    job_id=image.job_id,
                    display_name=image.display_name,
                    source_key=image.source_key,
                )
        return None

    def complete_image(self, image_id: int, result: CompletedImage) -> None:
        try:
            detections_json = json.dumps(
                result.detections,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("detections must be JSON serializable") from exc
        now = self._now()
        with self._session_factory.begin() as session:
            job_id = session.scalar(
                select(BatchImage.job_id).where(BatchImage.id == image_id)
            )
            if job_id is None:
                raise BatchImageStateError("batch image must be processing")
            changed = session.execute(
                update(BatchImage)
                .where(
                    BatchImage.id == image_id,
                    BatchImage.status == "processing",
                )
                .values(
                    status="completed",
                    annotated_key=result.annotated_key,
                    model_version_id=result.model_version_id,
                    detections_json=detections_json,
                    error_code=None,
                    error_message=None,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise BatchImageStateError("batch image must be processing")
            self._recompute_job(session, job_id, now)

    def fail_image(self, image_id: int, error_code: str, message: str) -> None:
        if not error_code.strip():
            raise ValueError("error_code must not be empty")
        now = self._now()
        with self._session_factory.begin() as session:
            job_id = session.scalar(
                select(BatchImage.job_id).where(BatchImage.id == image_id)
            )
            if job_id is None:
                raise BatchImageStateError("batch image must be processing")
            changed = session.execute(
                update(BatchImage)
                .where(
                    BatchImage.id == image_id,
                    BatchImage.status == "processing",
                )
                .values(
                    status="failed",
                    annotated_key=None,
                    error_code=error_code,
                    error_message=message,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise BatchImageStateError("batch image must be processing")
            self._recompute_job(session, job_id, now)

    def recover_interrupted(self) -> int:
        now = self._now()
        with self._session_factory.begin() as session:
            recovered = session.execute(
                update(BatchImage)
                .where(BatchImage.status == "processing")
                .values(status="queued", updated_at=now)
            )
            job_ids = session.scalars(
                select(BatchJob.id).where(
                    BatchJob.status.in_(("queued", "processing"))
                )
            ).all()
            for job_id in job_ids:
                self._recompute_job(session, job_id, now)
            return int(recovered.rowcount or 0)

    def list_expired(self, now: datetime) -> list[ExpiredJob]:
        self._require_aware(now)
        with self._session_factory() as session:
            jobs = session.scalars(
                select(BatchJob)
                .where(BatchJob.expires_at <= now)
                .order_by(BatchJob.expires_at, BatchJob.id)
            ).all()
            expired: list[ExpiredJob] = []
            for job in jobs:
                images = session.scalars(
                    select(BatchImage)
                    .where(BatchImage.job_id == job.id)
                    .order_by(BatchImage.id)
                ).all()
                expired.append(
                    ExpiredJob(
                        id=job.id,
                        images=[
                            ExpiredImage(
                                id=image.id,
                                source_key=image.source_key,
                                annotated_key=image.annotated_key,
                            )
                            for image in images
                        ],
                    )
                )
            return expired

    @staticmethod
    def _recompute_job(session: Session, job_id: int, now: datetime) -> None:
        success_count, failure_count, processing_count, queued_count = session.execute(
            select(
                func.sum(case((BatchImage.status == "completed", 1), else_=0)),
                func.sum(case((BatchImage.status == "failed", 1), else_=0)),
                func.sum(case((BatchImage.status == "processing", 1), else_=0)),
                func.sum(case((BatchImage.status == "queued", 1), else_=0)),
            ).where(BatchImage.job_id == job_id)
        ).one()
        success = int(success_count or 0)
        failure = int(failure_count or 0)
        processing = int(processing_count or 0)
        queued = int(queued_count or 0)
        job = session.get(BatchJob, job_id)
        if job is None:
            return
        if processing:
            status = "processing"
        elif queued:
            status = "queued"
        elif success == job.image_total:
            status = "completed"
        elif failure == job.image_total:
            status = "failed"
        else:
            status = "partial_failed"
        job.status = status
        job.success_count = success
        job.failure_count = failure
        job.updated_at = now

    @staticmethod
    def _job_view(job: BatchJob) -> BatchJobView:
        return BatchJobView(
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

    @staticmethod
    def _image_view(image: BatchImage) -> BatchImageView:
        detections = json.loads(image.detections_json)
        if not isinstance(detections, list):
            raise ValueError("stored detections must be a list")
        return BatchImageView(
            id=image.id,
            job_id=image.job_id,
            display_name=image.display_name,
            source_key=image.source_key,
            annotated_key=image.annotated_key,
            status=image.status,
            error_code=image.error_code,
            error_message=image.error_message,
            model_version_id=image.model_version_id,
            detections=detections,
            created_at=image.created_at,
            updated_at=image.updated_at,
        )

    def _now(self) -> datetime:
        now = self._clock()
        self._require_aware(now)
        return now.astimezone(timezone.utc)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
