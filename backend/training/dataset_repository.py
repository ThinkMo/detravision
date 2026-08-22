from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.db.models import TrainingDataset, TrainingJob


class TrainingDatasetConflictError(Exception):
    pass


def _decode_classes(value: str) -> list[str]:
    payload = json.loads(value)
    return [item for item in payload if isinstance(item, str)] if isinstance(payload, list) else []


def _dataset_dict(row: TrainingDataset) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "root_alias": row.root_alias,
        "directory": row.directory,
        "train_split": row.train_split,
        "val_split": row.val_split,
        "test_split": row.test_split,
        "classes": _decode_classes(row.classes),
        "enabled": row.enabled,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class TrainingDatasetRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def list(self) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(TrainingDataset).order_by(TrainingDataset.id)
            ).all()
            return [_dataset_dict(row) for row in rows]

    def get(self, dataset_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(TrainingDataset, dataset_id)
            return None if row is None else _dataset_dict(row)

    def create(self, values: Mapping[str, object]) -> dict[str, Any]:
        try:
            with self._session_factory.begin() as session:
                row = TrainingDataset(
                    name=str(values["name"]),
                    root_alias=str(values["root_alias"]),
                    directory=str(values["directory"]),
                    train_split=str(values["train_split"]),
                    val_split=str(values["val_split"]),
                    test_split=str(values["test_split"]),
                    classes=json.dumps(
                        list(values["classes"]), ensure_ascii=False, separators=(",", ":")
                    ),
                    created_by=int(values["created_by"]),
                )
                session.add(row)
                session.flush()
                return _dataset_dict(row)
        except IntegrityError as exc:
            raise TrainingDatasetConflictError("dataset location already exists") from exc

    def update(
        self, dataset_id: int, values: Mapping[str, object]
    ) -> dict[str, Any] | None:
        allowed = {
            "name", "root_alias", "directory", "train_split", "val_split",
            "test_split", "classes", "enabled",
        }
        try:
            with self._session_factory.begin() as session:
                row = session.get(TrainingDataset, dataset_id)
                if row is None:
                    return None
                for key, value in values.items():
                    if key not in allowed:
                        continue
                    if key == "classes":
                        value = json.dumps(
                            list(value), ensure_ascii=False, separators=(",", ":")
                        )
                    setattr(row, key, value)
                row.updated_at = datetime.now(timezone.utc)
                session.flush()
                return _dataset_dict(row)
        except IntegrityError as exc:
            raise TrainingDatasetConflictError("dataset location already exists") from exc

    def disable(self, dataset_id: int) -> dict[str, Any] | None:
        return self.update(dataset_id, {"enabled": False})

    def is_referenced(self, dataset_id: int) -> bool:
        with self._session_factory() as session:
            count = session.scalar(
                select(func.count(TrainingJob.id)).where(
                    TrainingJob.training_dataset_id == dataset_id
                )
            )
            return bool(count)
