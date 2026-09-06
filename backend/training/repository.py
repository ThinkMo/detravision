from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import TrainingDataset, TrainingJob, TrainingScheduler
from training.configuration import normalize_acceptance_config, resolve_training_config


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


def _decode_snapshot(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("training snapshot must be a JSON object")
    return decoded


def _serialize_snapshot(value: Mapping[str, Any], *, name: str) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a JSON-serializable object") from exc


def training_job_dict(
    row: TrainingJob, *, dataset_manifest_digest: str | None = None
) -> dict[str, Any]:
    """Return a JSON-ready training record without server-local paths."""
    return {
        "id": row.id,
        "created_by": row.created_by,
        "executor": row.executor,
        "base_model": row.base_model,
        "dataset_key": row.dataset_key,
        "dataset_classes": _decode_classes(row.dataset_classes),
        "dataset_manifest_digest": dataset_manifest_digest,
        "training_dataset_id": row.training_dataset_id,
        "epochs": row.epochs,
        "imgsz": row.imgsz,
        "batch": row.batch,
        "device": row.device,
        "patience": row.patience,
        "config_schema_version": row.config_schema_version,
        "training_profile": row.training_profile,
        "advanced_config": _decode_snapshot(row.advanced_config),
        "acceptance_config": _decode_snapshot(row.acceptance_config),
        "status": row.status,
        "current_epoch": row.current_epoch,
        "total_epochs": row.total_epochs,
        "pid": row.pid,
        "owner_id": row.owner_id,
        "heartbeat_at": row.heartbeat_at,
        "execution_generation": row.execution_generation,
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
        epochs = int(values["epochs"])
        classes = list(values.get("dataset_classes", []))
        default_advanced = resolve_training_config(
            mode="basic", profile="balanced", overrides=None, epochs=epochs
        )
        default_acceptance = normalize_acceptance_config(
            None,
            classes=classes,
            template=(
                "safety_helmet"
                if {"person", "helmet", "no_helmet"} <= set(classes)
                else None
            ),
        )
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
            epochs=epochs,
            imgsz=int(values["imgsz"]),
            batch=str(values["batch"]),
            device=str(values["device"]),
            patience=int(values["patience"]),
            config_schema_version=int(values.get("config_schema_version", 1)),
            training_profile=str(values.get("training_profile", "legacy")),
            advanced_config=_serialize_snapshot(
                values.get("advanced_config", default_advanced), name="advanced_config"
            ),
            acceptance_config=_serialize_snapshot(
                values.get("acceptance_config", default_acceptance),
                name="acceptance_config",
            ),
            status="queued",
            current_epoch=0,
            total_epochs=epochs,
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

    def delete_terminal(self, job_id: int) -> bool:
        with self._session_factory.begin() as session:
            result = session.execute(
                delete(TrainingJob).where(
                    TrainingJob.id == job_id,
                    TrainingJob.status.in_(TERMINAL_STATUSES),
                )
            )
            return bool(result.rowcount)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(TrainingJob, job_id)
            if row is None:
                return None
            dataset = (
                session.get(TrainingDataset, row.training_dataset_id)
                if row.training_dataset_id is not None
                else None
            )
            return training_job_dict(
                row,
                dataset_manifest_digest=(
                    dataset.manifest_digest if dataset is not None else None
                ),
            )

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
            items = []
            for row in session.scalars(page_statement):
                dataset = (
                    session.get(TrainingDataset, row.training_dataset_id)
                    if row.training_dataset_id is not None
                    else None
                )
                items.append(
                    training_job_dict(
                        row,
                        dataset_manifest_digest=(
                            dataset.manifest_digest if dataset is not None else None
                        ),
                    )
                )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def acquire_lease(self, owner_id: str, lease_seconds: float) -> int | None:
        if not owner_id or lease_seconds <= 0:
            raise ValueError("owner_id and lease_seconds are required")
        now = self._now()
        with self._session_factory.begin() as session:
            self._lock_sqlite_writer(session)
            row = self._locked_scheduler(session)
            if (
                row.owner_id is not None
                and row.owner_id != owner_id
                and row.lease_expires_at is not None
                and row.lease_expires_at > now
            ):
                return None
            if row.owner_id != owner_id:
                row.revision += 1
            row.owner_id = owner_id
            row.heartbeat_at = now
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            session.flush()
            return row.revision

    def renew_lease(self, owner_id: str, revision: int, lease_seconds: float) -> bool:
        now = self._now()
        with self._session_factory.begin() as session:
            changed = session.execute(
                update(TrainingScheduler)
                .where(
                    TrainingScheduler.id == 1,
                    TrainingScheduler.owner_id == owner_id,
                    TrainingScheduler.revision == revision,
                    TrainingScheduler.lease_expires_at >= now,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                )
            )
            return changed.rowcount == 1

    def release_lease(self, owner_id: str, revision: int) -> bool:
        with self._session_factory.begin() as session:
            changed = session.execute(
                update(TrainingScheduler)
                .where(
                    TrainingScheduler.id == 1,
                    TrainingScheduler.owner_id == owner_id,
                    TrainingScheduler.revision == revision,
                )
                .values(owner_id=None, heartbeat_at=None, lease_expires_at=None)
            )
            return changed.rowcount == 1

    def scheduler_state(self) -> str:
        now = self._now()
        with self._session_factory() as session:
            row = session.get(TrainingScheduler, 1)
            if row is None or row.owner_id is None or row.lease_expires_at is None:
                return "unavailable"
            return "healthy" if row.lease_expires_at >= now else "unavailable"

    def claim_next(
        self, owner_id: str | None = None, lease_revision: int | None = None
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._session_factory.begin() as session:
            self._lock_sqlite_writer(session)
            scheduler = self._locked_scheduler(session)
            if owner_id is not None and (
                lease_revision is None
                or scheduler.owner_id != owner_id
                or scheduler.revision != lease_revision
                or scheduler.lease_expires_at is None
                or scheduler.lease_expires_at < now
            ):
                return None
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
                .values(
                    status="running",
                    started_at=now,
                    updated_at=now,
                    owner_id=owner_id,
                    heartbeat_at=now if owner_id is not None else None,
                    execution_generation=TrainingJob.execution_generation + 1,
                )
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
        owner_id: str | None = None,
        execution_generation: int | None = None,
    ) -> dict[str, Any] | None:
        if current_epoch < 0:
            raise ValueError("current_epoch must be nonnegative")
        metrics_json = self._serialize_metrics(metrics)
        values: dict[str, Any] = {
            "current_epoch": current_epoch,
            "updated_at": self._now(),
        }
        if owner_id is not None:
            values["heartbeat_at"] = values["updated_at"]
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
            conditions = [
                TrainingJob.id == job_id,
                TrainingJob.status.in_(allowed_statuses),
                TrainingJob.current_epoch <= current_epoch,
            ]
            if owner_id is not None:
                conditions.extend((
                    TrainingJob.owner_id == owner_id,
                    TrainingJob.execution_generation == execution_generation,
                ))
            changed = session.execute(update(TrainingJob).where(*conditions).values(**values))
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
        owner_id: str | None = None,
        execution_generation: int | None = None,
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
            "owner_id": None,
            "heartbeat_at": None,
        }
        if metrics_json is not None:
            values.update(self._metric_values(metrics, metrics_json))
        allowed_statuses = ("queued",) if status == "cancelled" else ACTIVE_STATUSES
        with self._session_factory.begin() as session:
            conditions = [
                TrainingJob.id == job_id,
                TrainingJob.status.in_(allowed_statuses),
            ]
            if owner_id is not None:
                conditions.extend((
                    TrainingJob.owner_id == owner_id,
                    TrainingJob.execution_generation == execution_generation,
                ))
            changed = session.execute(update(TrainingJob).where(*conditions).values(**values))
            if changed.rowcount != 1:
                existing = session.get(TrainingJob, job_id)
                return training_job_dict(existing) if existing is not None else None
            row = session.get(TrainingJob, job_id)
            assert row is not None
            return training_job_dict(row)

    def recover_expired(
        self, owner_id: str, lease_revision: int, grace_seconds: float
    ) -> int:
        if grace_seconds <= 0:
            raise ValueError("grace_seconds must be positive")
        now = self._now()
        cutoff = now - timedelta(seconds=grace_seconds)
        with self._session_factory.begin() as session:
            self._lock_sqlite_writer(session)
            scheduler = self._locked_scheduler(session)
            if (
                scheduler.owner_id != owner_id
                or scheduler.revision != lease_revision
                or scheduler.lease_expires_at is None
                or scheduler.lease_expires_at < now
            ):
                return 0
            result = session.execute(
                update(TrainingJob)
                .where(
                    TrainingJob.status.in_(ACTIVE_STATUSES),
                    func.coalesce(TrainingJob.heartbeat_at, TrainingJob.updated_at) < cutoff,
                )
                .values(
                    status="interrupted",
                    pid=None,
                    owner_id=None,
                    heartbeat_at=None,
                    finished_at=now,
                    updated_at=now,
                    error_code="training_heartbeat_timeout",
                    error_message="Training heartbeat timed out; resume from the last checkpoint after review.",
                )
            )
            return int(result.rowcount)

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
                "config_schema_version": source.config_schema_version,
                "training_profile": source.training_profile,
                "advanced_config": source.advanced_config,
                "acceptance_config": source.acceptance_config,
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

    @staticmethod
    def _locked_scheduler(session: Session) -> TrainingScheduler:
        statement = select(TrainingScheduler).where(TrainingScheduler.id == 1)
        if session.bind is not None and session.bind.dialect.name == "mysql":
            statement = statement.with_for_update()
        row = session.scalar(statement)
        if row is None:
            raise RuntimeError("training scheduler lock row is missing")
        return row

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
