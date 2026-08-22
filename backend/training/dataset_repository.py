from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.db.models import TrainingDataset, TrainingDatasetImage, TrainingJob


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
        "version": row.version,
        "status": row.status,
        "parent_id": row.parent_id,
        "root_dataset_id": row.root_dataset_id,
        "revision": row.revision,
        "published_at": row.published_at,
        "manifest_digest": row.manifest_digest,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _image_dict(row: TrainingDatasetImage) -> dict[str, Any]:
    return {
        "id": row.id,
        "dataset_id": row.dataset_id,
        "split": row.split,
        "image_key": row.image_key,
        "display_name": row.display_name,
        "width": row.width,
        "height": row.height,
        "review_status": row.review_status,
        "revision": row.revision,
        "content_digest": row.content_digest,
        "validation_code": row.validation_code,
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
                    version=1,
                    status="published",
                    published_at=datetime.now(timezone.utc),
                )
                session.add(row)
                session.flush()
                return _dataset_dict(row)
        except IntegrityError as exc:
            raise TrainingDatasetConflictError("dataset location already exists") from exc

    def create_editing_version(
        self, source_id: int, values: Mapping[str, object]
    ) -> dict[str, Any]:
        try:
            with self._session_factory.begin() as session:
                source = session.get(TrainingDataset, source_id)
                if source is None or source.status != "published":
                    raise TrainingDatasetConflictError("source dataset is not published")
                root_id = source.root_dataset_id or source.id
                maximum = session.scalar(
                    select(func.max(TrainingDataset.version)).where(
                        or_(
                            TrainingDataset.id == root_id,
                            TrainingDataset.root_dataset_id == root_id,
                        )
                    )
                ) or 1
                row = TrainingDataset(
                    name=str(values.get("name", source.name)),
                    root_alias=source.root_alias,
                    directory=str(values["directory"]),
                    train_split=source.train_split,
                    val_split=source.val_split,
                    test_split=source.test_split,
                    classes=source.classes,
                    enabled=False,
                    version=int(maximum) + 1,
                    status="editing",
                    parent_id=source.id,
                    root_dataset_id=root_id,
                    revision=0,
                    created_by=int(values["created_by"]),
                )
                session.add(row)
                session.flush()
                return _dataset_dict(row)
        except IntegrityError as exc:
            raise TrainingDatasetConflictError("dataset version conflict") from exc

    def list_versions(self, root_dataset_id: int) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(TrainingDataset)
                .where(
                    or_(
                        TrainingDataset.id == root_dataset_id,
                        TrainingDataset.root_dataset_id == root_dataset_id,
                    )
                )
                .order_by(TrainingDataset.version, TrainingDataset.id)
            ).all()
            return [_dataset_dict(row) for row in rows]

    def upsert_image_index(
        self, dataset_id: int, rows: Sequence[Mapping[str, object]]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        try:
            with self._session_factory.begin() as session:
                dataset = session.get(TrainingDataset, dataset_id)
                if dataset is None or dataset.status != "editing":
                    raise TrainingDatasetConflictError("dataset is not editable")
                for values in rows:
                    row = session.scalar(
                        select(TrainingDatasetImage).where(
                            TrainingDatasetImage.dataset_id == dataset_id,
                            TrainingDatasetImage.split == str(values["split"]),
                            TrainingDatasetImage.image_key == str(values["image_key"]),
                        )
                    )
                    if row is None:
                        row = TrainingDatasetImage(
                            dataset_id=dataset_id,
                            split=str(values["split"]),
                            image_key=str(values["image_key"]),
                            display_name=str(values["display_name"]),
                            width=int(values["width"]),
                            height=int(values["height"]),
                            review_status=str(values.get("review_status", "pending_annotation")),
                            content_digest=str(values["content_digest"]),
                            validation_code=values.get("validation_code"),
                        )
                        session.add(row)
                        session.flush()
                    results.append(_image_dict(row))
            return results
        except IntegrityError as exc:
            raise TrainingDatasetConflictError("dataset image conflict") from exc

    def list_images(
        self, dataset_id: int, split: str, status: str | None = None,
        page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            criteria = [
                TrainingDatasetImage.dataset_id == dataset_id,
                TrainingDatasetImage.split == split,
            ]
            if status is not None:
                criteria.append(TrainingDatasetImage.review_status == status)
            total = session.scalar(
                select(func.count(TrainingDatasetImage.id)).where(*criteria)
            ) or 0
            rows = session.scalars(
                select(TrainingDatasetImage).where(*criteria)
                .order_by(TrainingDatasetImage.image_key, TrainingDatasetImage.id)
                .offset((page - 1) * page_size).limit(page_size)
            ).all()
            return {
                "items": [_image_dict(row) for row in rows], "total": int(total),
                "page": page, "page_size": page_size,
            }

    def get_image(self, dataset_id: int, image_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(TrainingDatasetImage, image_id)
            if row is None or row.dataset_id != dataset_id:
                return None
            return _image_dict(row)

    def update_image_review(
        self, image_id: int, expected_revision: int, values: Mapping[str, object]
    ) -> dict[str, Any]:
        allowed = {"review_status", "validation_code", "content_digest", "width", "height"}
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            result = session.execute(
                update(TrainingDatasetImage)
                .where(
                    TrainingDatasetImage.id == image_id,
                    TrainingDatasetImage.revision == expected_revision,
                )
                .values(
                    **{key: value for key, value in values.items() if key in allowed},
                    revision=expected_revision + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise TrainingDatasetConflictError("annotation revision conflict")
            row = session.get(TrainingDatasetImage, image_id)
            if row is None:
                raise TrainingDatasetConflictError("annotation image missing")
            return _image_dict(row)

    def delete_editing_version(self, dataset_id: int) -> bool:
        with self._session_factory.begin() as session:
            row = session.get(TrainingDataset, dataset_id)
            if row is None:
                return False
            if row.status != "editing":
                raise TrainingDatasetConflictError("published dataset cannot be deleted")
            session.delete(row)
            return True

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
