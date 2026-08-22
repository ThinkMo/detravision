from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import TrainingJob, TrainingScheduler


ACTIVE_STATUSES = ("running", "evaluating", "stopping")
TERMINAL_STATUSES = ("stopped", "completed", "failed", "interrupted", "cancelled")
METRIC_COLUMNS = (
    "precision",
    "recall",
    "map50",
    "map50_95",
    "no_helmet_precision",
    "no_helmet_recall",
    "accepted",
)


def _decode_metrics(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else None


def _decode_classes(value: str | None) -> list[str]:
    if value is None:
        return []
    decoded = json.loads(value)
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        return []
    return decoded


def training_job_dict(row: TrainingJob) -> dict[str, Any]:
    """Return a JSON-ready training record without server-local paths."""
    return {
        "id": row.id,
        "created_by": row.created_by,
        "executor": row.executor,
        "base_model": row.base_model,
        "dataset_key": row.dataset_key,
        "dataset_classes": _decode_classes(row.dataset_classes),
        "training_dataset_id": row.training_dataset_id,
        "epochs": row.epochs,
        "imgsz": row.imgsz,
        "batch": row.batch,
        "device": row.device,
        "patience": row.patience,
        "status": row.status,
        "current_epoch": row.current_epoch,
        "total_epochs": row.total_epochs,
        "pid": row.pid,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "stop_requested": row.stop_requested,
        "run_key": row.run_key,
        "has_best": row.has_best,
        "has_last": row.has_last,
        "has_metrics": row.has_metrics,
        "precision": row.precision,
        "recall": row.recall,
        "map50": row.map50,
        "map50_95": row.map50_95,
        "no_helmet_precision": row.no_helmet_precision,
        "no_helmet_recall": row.no_helmet_recall,
        "accepted": row.accepted,
        "metrics": _decode_metrics(row.metrics_json),
        "error_code": row.error_code,
        "error_message": row.error_message,
        "idempotency_key": row.idempotency_key,
        "best_sha256": row.best_sha256,
        "resume_from_id": row.resume_from_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class TrainingRepository:
    """Transactional persistence and queue transitions for local training."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_job(self, values: Mapping[str, Any]) -> dict[str, Any]:
        now = self._now()
        idempotency_key = str(values["idempotency_key"])
        job = TrainingJob(
            created_by=int(values["created_by"]),
            executor=str(values["executor"]),
            base_model=str(values["base_model"]),
            dataset_key=str(values["dataset_key"]),
            training_dataset_id=values.get("training_dataset_id"),
            dataset_classes=json.dumps(
                list(values.get("dataset_classes", [])),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            epochs=int(values["epochs"]),
            imgsz=int(values["imgsz"]),
            batch=str(values["batch"]),
            device=str(values["device"]),
            patience=int(values["patience"]),
            status="queued",
            current_epoch=0,
            total_epochs=int(values["epochs"]),
            stop_requested=False,
            run_key=str(values["run_key"]),
            has_best=False,
            has_last=False,
            has_metrics=False,
            idempotency_key=idempotency_key,
            resume_from_id=values.get("resume_from_id"),
            created_at=now,
            updated_at=now,
        )
        return self._insert_idempotent(job, idempotency_key)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(TrainingJob, job_id)
            return training_job_dict(row) if row is not None else None

    def list_jobs(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> dict[str, Any]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = select(TrainingJob)
        if status is not None:
            statement = statement.where(TrainingJob.status == status)
        count_statement = select(func.count()).select_from(statement.subquery())
        page_statement = (
            statement.order_by(TrainingJob.created_at.desc(), TrainingJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        with self._session_factory() as session:
            total = int(session.scalar(count_statement) or 0)
            items = [training_job_dict(row) for row in session.scalars(page_statement)]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def claim_next(self) -> dict[str, Any] | None:
        now = self._now()
        with self._session_factory.begin() as session:
            self._lock_sqlite_writer(session)
            if session.bind is not None and session.bind.dialect.name == "mysql":
                scheduler_id = session.scalar(
                    select(TrainingScheduler.id)
                    .where(TrainingScheduler.id == 1)
                    .with_for_update()
                )
                if scheduler_id is None:
                    raise RuntimeError("training scheduler lock row is missing")
            active_statement = (
                select(TrainingJob.id)
                .where(TrainingJob.status.in_(ACTIVE_STATUSES))
                .limit(1)
            )
            if session.bind is not None and session.bind.dialect.name == "mysql":
                active_statement = active_statement.with_for_update()
            if session.scalar(active_statement) is not None:
                return None
            queued_statement = (
                select(TrainingJob.id)
                .where(TrainingJob.status == "queued")
                .order_by(TrainingJob.created_at, TrainingJob.id)
                .limit(1)
            )
            if session.bind is not None and session.bind.dialect.name == "mysql":
                queued_statement = queued_statement.with_for_update()
            job_id = session.scalar(queued_statement)
            if job_id is None:
                return None
            claimed = session.execute(
                update(TrainingJob)
                .where(TrainingJob.id == job_id, TrainingJob.status == "queued")
                .values(status="running", started_at=now, updated_at=now)
            )
            if claimed.rowcount != 1:
                return None
            row = session.get(TrainingJob, job_id)
            assert row is not None
            return training_job_dict(row)

    def update_progress(
        self,
        job_id: int,
        *,
        current_epoch: int,
        metrics: Mapping[str, Any] | None = None,
        status: str | None = None,
        pid: int | None = None,
    ) -> dict[str, Any] | None:
        if current_epoch < 0:
            raise ValueError("current_epoch must be nonnegative")
        metrics_json = self._serialize_metrics(metrics)
        values: dict[str, Any] = {
            "current_epoch": current_epoch,
            "updated_at": self._now(),
        }
        if metrics_json is not None:
            values.update(self._metric_values(metrics, metrics_json))
        if status is not None:
            if status not in ("running", "evaluating", "stopping"):
                raise ValueError("invalid active training status")
            values["status"] = status
        if pid is not None:
            values["pid"] = pid
        with self._session_factory.begin() as session:
            allowed_statuses = ACTIVE_STATUSES
            if status == "running":
                allowed_statuses = ("running",)
            elif status == "evaluating":
                allowed_statuses = ("running", "evaluating")
            changed = session.execute(
                update(TrainingJob)
                .where(
                    TrainingJob.id == job_id,
                    TrainingJob.status.in_(allowed_statuses),
                    TrainingJob.current_epoch <= current_epoch,
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                existing = session.get(TrainingJob, job_id)
                return training_job_dict(existing) if existing is not None else None
            row = session.get(TrainingJob, job_id)
            assert row is not None
            return training_job_dict(row)

    def request_stop(self, job_id: int) -> dict[str, Any] | None:
        now = self._now()
        with self._session_factory.begin() as session:
            self._lock_sqlite_writer(session)
            statement = select(TrainingJob).where(TrainingJob.id == job_id)
            if session.bind is not None and session.bind.dialect.name == "mysql":
                statement = statement.with_for_update()
            row = session.scalar(statement)
            if row is None:
                return None
            if row.status == "queued":
                row.status = "cancelled"
                row.stop_requested = True
                row.finished_at = now
                row.updated_at = now
            elif row.status in ("running", "evaluating"):
                row.status = "stopping"
                row.stop_requested = True
                row.updated_at = now
            session.flush()
            return training_job_dict(row)

    def mark_terminal(
        self,
        job_id: int,
        *,
        status: str,
        metrics: Mapping[str, Any] | None = None,
        has_best: bool = False,
        has_last: bool = False,
        best_sha256: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal training status")
        metrics_json = self._serialize_metrics(metrics)
        values: dict[str, Any] = {
            "status": status,
            "pid": None,
            "finished_at": self._now(),
            "updated_at": self._now(),
            "has_best": has_best,
            "has_last": has_last,
            "best_sha256": best_sha256,
            "error_code": error_code,
            "error_message": error_message,
        }
        if metrics_json is not None:
            values.update(self._metric_values(metrics, metrics_json))
        allowed_statuses = ("queued",) if status == "cancelled" else ACTIVE_STATUSES
        with self._session_factory.begin() as session:
            changed = session.execute(
                update(TrainingJob)
                .where(
                    TrainingJob.id == job_id,
                    TrainingJob.status.in_(allowed_statuses),
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                existing = session.get(TrainingJob, job_id)
                return training_job_dict(existing) if existing is not None else None
            row = session.get(TrainingJob, job_id)
            assert row is not None
            return training_job_dict(row)

    def recover_interrupted(self) -> int:
        now = self._now()
        with self._session_factory.begin() as session:
            result = session.execute(
                update(TrainingJob)
                .where(TrainingJob.status.in_(ACTIVE_STATUSES))
                .values(
                    status="interrupted",
                    pid=None,
                    finished_at=now,
                    updated_at=now,
                )
            )
            return int(result.rowcount)

    def create_resume(
        self,
        job_id: int,
        *,
        run_key: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        now = self._now()
        duplicate = self._get_by_idempotency_key(idempotency_key)
        if duplicate is not None:
            return duplicate
        with self._session_factory() as session:
            source = session.get(TrainingJob, job_id)
            if source is None:
                raise ValueError("training job does not exist")
            if not source.has_last:
                raise ValueError("last checkpoint is unavailable")
            if source.status not in ("stopped", "interrupted", "failed"):
                raise ValueError("training job cannot be resumed")
            source_values = {
                "created_by": source.created_by,
                "executor": source.executor,
                "base_model": source.base_model,
                "dataset_key": source.dataset_key,
                "training_dataset_id": source.training_dataset_id,
                "dataset_classes": source.dataset_classes,
                "epochs": source.epochs,
                "imgsz": source.imgsz,
                "batch": source.batch,
                "device": source.device,
                "patience": source.patience,
                "total_epochs": source.total_epochs,
                "resume_from_id": source.id,
            }
        resumed = TrainingJob(
            **source_values,
            status="queued",
            current_epoch=0,
            stop_requested=False,
            run_key=run_key,
            has_best=False,
            has_last=False,
            has_metrics=False,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        return self._insert_idempotent(resumed, idempotency_key)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _insert_idempotent(
        self, job: TrainingJob, idempotency_key: str
    ) -> dict[str, Any]:
        try:
            with self._session_factory.begin() as session:
                session.add(job)
                session.flush()
                return training_job_dict(job)
        except IntegrityError:
            duplicate = self._get_by_idempotency_key(idempotency_key)
            if duplicate is None:
                raise
            return duplicate

    def _get_by_idempotency_key(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(TrainingJob).where(
                    TrainingJob.idempotency_key == idempotency_key
                )
            )
            return training_job_dict(row) if row is not None else None

    @staticmethod
    def _serialize_metrics(metrics: Mapping[str, Any] | None) -> str | None:
        if metrics is None:
            return None
        try:
            return json.dumps(
                dict(metrics),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("metrics must be JSON serializable") from exc

    @staticmethod
    def _metric_values(
        metrics: Mapping[str, Any] | None, metrics_json: str
    ) -> dict[str, Any]:
        assert metrics is not None
        values = {name: metrics.get(name) for name in METRIC_COLUMNS}
        values.update(metrics_json=metrics_json, has_metrics=True)
        return values

    @staticmethod
    def _lock_sqlite_writer(session: Session) -> None:
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
