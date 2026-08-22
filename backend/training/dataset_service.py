from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import shutil
from typing import Any

from backend.core.config import Settings

from .dataset_repository import (
    TrainingDatasetConflictError,
    TrainingDatasetRepository,
)
from .dataset_filesystem import DatasetFilesystem, DatasetFilesystemError


_ERROR_STATUS = {
    "root_not_found": 404,
    "path_invalid": 422,
    "dataset_invalid": 422,
    "dataset_not_found": 404,
    "dataset_conflict": 409,
    "dataset_in_use": 409,
    "dataset_published": 409,
    "dataset_copy_failed": 503,
    "dataset_scan_failed": 422,
}
_MAX_DIRECTORY_RESULTS = 200
_MAX_CLASSES = 100
_MAX_CLASS_LENGTH = 100


class DatasetServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = _ERROR_STATUS[code]


class DatasetService:
    def __init__(
        self,
        settings: Settings,
        repository: TrainingDatasetRepository,
    ) -> None:
        self._roots = dict(settings.training_dataset_roots)
        self._repository = repository
        self._filesystem = DatasetFilesystem(self._roots)

    def list_roots(self) -> list[dict[str, str]]:
        return [{"alias": alias} for alias in self._roots]

    def list_directories(
        self, root_alias: str, relative: str
    ) -> list[dict[str, str]]:
        root = self._root(root_alias)
        normalized = self._relative(relative, allow_empty=True)
        current = self._contained(root, root / normalized)
        if not current.is_dir():
            raise DatasetServiceError("path_invalid")
        entries: list[dict[str, str]] = []
        try:
            children = sorted(current.iterdir(), key=lambda path: path.name.casefold())
        except OSError as exc:
            raise DatasetServiceError("path_invalid") from exc
        for child in children:
            if len(entries) >= _MAX_DIRECTORY_RESULTS:
                break
            try:
                if not child.is_dir():
                    continue
                self._contained(root, child)
            except (OSError, DatasetServiceError):
                continue
            child_relative = child.relative_to(root).as_posix()
            entries.append({"name": child.name, "path": child_relative})
        return entries

    def create(
        self, values: Mapping[str, object], *, created_by: int
    ) -> dict[str, Any]:
        validated = self._validate(values)
        try:
            row = self._repository.create({**validated, "created_by": created_by})
        except TrainingDatasetConflictError as exc:
            raise DatasetServiceError("dataset_conflict") from exc
        return self._public(row)

    def update(
        self, dataset_id: int, values: Mapping[str, object]
    ) -> dict[str, Any]:
        current = self._repository.get(dataset_id)
        if current is None:
            raise DatasetServiceError("dataset_not_found")
        merged = {
            key: current[key]
            for key in (
                "name", "root_alias", "directory", "train_split", "val_split",
                "test_split", "classes",
            )
        }
        merged.update(values)
        validated = self._validate(merged)
        if "enabled" in values:
            if type(values["enabled"]) is not bool:
                raise DatasetServiceError("dataset_invalid")
            validated["enabled"] = values["enabled"]
        try:
            row = self._repository.update(dataset_id, validated)
        except TrainingDatasetConflictError as exc:
            raise DatasetServiceError("dataset_conflict") from exc
        if row is None:
            raise DatasetServiceError("dataset_not_found")
        return self._public(row)

    def disable(self, dataset_id: int) -> dict[str, Any]:
        row = self._repository.disable(dataset_id)
        if row is None:
            raise DatasetServiceError("dataset_not_found")
        return self._public(row)

    def catalog(self) -> list[dict[str, Any]]:
        return [self._public(row) for row in self._repository.list()]

    def clone_version(self, dataset_id: int, *, created_by: int) -> dict[str, Any]:
        source = self._repository.get(dataset_id)
        if source is None:
            raise DatasetServiceError("dataset_not_found")
        if source["status"] != "published":
            raise DatasetServiceError("dataset_published")
        root = self._root(source["root_alias"])
        source_path = self._filesystem.dataset_path(source)
        token = secrets.token_hex(16)
        directory = f"dataset-{token}"
        destination = self._contained(root, root / directory)
        temporary = self._contained(root, root / f".dataset-copy-{token}")
        editing: dict[str, Any] | None = None
        try:
            self._filesystem.clone(source_path, temporary)
            os.replace(temporary, destination)
            candidate = {**source, "directory": directory}
            scanned = self._filesystem.scan(candidate)
            editing = self._repository.create_editing_version(
                dataset_id, {"directory": directory, "created_by": created_by}
            )
            self._repository.upsert_image_index(
                editing["id"], [self._scan_values(item) for item in scanned]
            )
            return self._public(editing)
        except (OSError, DatasetFilesystemError, TrainingDatasetConflictError) as exc:
            if editing is not None:
                try:
                    self._repository.delete_editing_version(editing["id"])
                except TrainingDatasetConflictError:
                    pass
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.rmtree(destination, ignore_errors=True)
            raise DatasetServiceError("dataset_copy_failed") from exc

    def rescan(self, dataset_id: int, split: str | None = None) -> dict[str, int]:
        dataset = self._repository.get(dataset_id)
        if dataset is None:
            raise DatasetServiceError("dataset_not_found")
        if dataset["status"] != "editing":
            raise DatasetServiceError("dataset_published")
        if split is not None and split not in {"train", "val", "test"}:
            raise DatasetServiceError("dataset_invalid")
        try:
            scanned = self._filesystem.scan(dataset, split)
        except DatasetFilesystemError as exc:
            raise DatasetServiceError("dataset_scan_failed") from exc
        splits = (split,) if split else ("train", "val", "test")
        existing = {
            (item["split"], item["image_key"]): item
            for current in splits
            for item in self._repository.list_images(
                dataset_id, current, page=1, page_size=1_000_000
            )["items"]
        }
        found = {(item.split, item.image_key): item for item in scanned}
        discovered = [
            item for key, item in found.items() if key not in existing
        ]
        if discovered:
            self._repository.upsert_image_index(
                dataset_id, [self._scan_values(item) for item in discovered]
            )
        changed = 0
        missing = 0
        for key, current in existing.items():
            observed = found.get(key)
            code: str | None = None
            if observed is None:
                code = "file_missing"
                missing += 1
            elif (
                observed.content_digest != current["content_digest"]
                or observed.width != current["width"]
                or observed.height != current["height"]
            ):
                code = "file_changed"
                changed += 1
            if code != current["validation_code"]:
                self._repository.update_image_review(
                    current["id"], current["revision"], {"validation_code": code}
                )
        return {
            "discovered": len(discovered), "changed": changed, "missing": missing,
            "total": len(found),
        }

    def list_images(
        self, dataset_id: int, *, split: str, status: str | None = None,
        page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        if split not in {"train", "val", "test"}:
            raise DatasetServiceError("dataset_invalid")
        if self._repository.get(dataset_id) is None:
            raise DatasetServiceError("dataset_not_found")
        return self._repository.list_images(dataset_id, split, status, page, page_size)

    @staticmethod
    def _scan_values(item) -> dict[str, object]:
        return {
            "split": item.split,
            "image_key": item.image_key,
            "display_name": item.display_name,
            "width": item.width,
            "height": item.height,
            "review_status": "pending_annotation",
            "content_digest": item.content_digest,
        }

    def resolve(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not key.startswith("dataset:"):
            raise DatasetServiceError("dataset_not_found")
        try:
            dataset_id = int(key.removeprefix("dataset:"))
        except ValueError as exc:
            raise DatasetServiceError("dataset_not_found") from exc
        if dataset_id < 1:
            raise DatasetServiceError("dataset_not_found")
        row = self._repository.get(dataset_id)
        if row is None:
            raise DatasetServiceError("dataset_not_found")
        if not row["enabled"] or row.get("status", "published") != "published":
            raise DatasetServiceError("dataset_invalid")
        if row.get("root_dataset_id") is not None and not self._valid_digest(
            row.get("manifest_digest")
        ):
            raise DatasetServiceError("dataset_invalid")
        validated = self._validate(row)
        root = self._root(validated["root_alias"])
        dataset_path = self._contained(root, root / validated["directory"])
        return {
            "key": key,
            "training_dataset_id": dataset_id,
            "path": dataset_path,
            "train": validated["train_split"],
            "val": validated["val_split"],
            "test": validated["test_split"],
            "classes": list(validated["classes"]),
        }

    def _root(self, alias: object) -> Path:
        if not isinstance(alias, str):
            raise DatasetServiceError("root_not_found")
        try:
            return self._roots[alias]
        except KeyError as exc:
            raise DatasetServiceError("root_not_found") from exc

    @staticmethod
    def _relative(value: object, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str) or "\\" in value:
            raise DatasetServiceError("path_invalid")
        stripped = value.strip()
        if not stripped:
            if allow_empty:
                return "."
            raise DatasetServiceError("path_invalid")
        posix = PurePosixPath(stripped)
        windows = PureWindowsPath(stripped)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or any(part in {"", ".", ".."} for part in posix.parts)
        ):
            raise DatasetServiceError("path_invalid")
        return posix.as_posix()

    @staticmethod
    def _contained(root: Path, path: Path) -> Path:
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise DatasetServiceError("path_invalid") from exc
        return resolved

    def _validate(self, values: Mapping[str, object]) -> dict[str, Any]:
        name = values.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise DatasetServiceError("dataset_invalid")
        classes = values.get("classes")
        if (
            not isinstance(classes, list)
            or not 1 <= len(classes) <= _MAX_CLASSES
            or not all(
                isinstance(item, str)
                and bool(item.strip())
                and len(item.strip()) <= _MAX_CLASS_LENGTH
                for item in classes
            )
        ):
            raise DatasetServiceError("dataset_invalid")
        normalized_classes = [item.strip() for item in classes]
        if len(set(normalized_classes)) != len(normalized_classes):
            raise DatasetServiceError("dataset_invalid")

        root_alias = values.get("root_alias")
        root = self._root(root_alias)
        directory = self._relative(values.get("directory"), allow_empty=True)
        dataset_path = self._contained(root, root / directory)
        if not dataset_path.is_dir():
            raise DatasetServiceError("dataset_invalid")

        splits: dict[str, str] = {}
        for key in ("train_split", "val_split", "test_split"):
            relative = self._relative(values.get(key))
            split_path = self._contained(dataset_path, dataset_path / relative)
            try:
                split_path.relative_to(dataset_path)
            except ValueError as exc:
                raise DatasetServiceError("path_invalid") from exc
            if not split_path.is_dir():
                raise DatasetServiceError("dataset_invalid")
            parts = list(PurePosixPath(relative).parts)
            try:
                image_index = parts.index("images")
            except ValueError as exc:
                raise DatasetServiceError("dataset_invalid") from exc
            parts[image_index] = "labels"
            label_path = self._contained(dataset_path, dataset_path.joinpath(*parts))
            if not label_path.is_dir():
                raise DatasetServiceError("dataset_invalid")
            splits[key] = relative
        return {
            "name": name.strip(),
            "root_alias": str(root_alias),
            "directory": "." if directory == "." else directory,
            **splits,
            "classes": normalized_classes,
        }

    def _public(self, row: Mapping[str, Any]) -> dict[str, Any]:
        available = bool(row["enabled"]) and row.get("status", "published") == "published"
        reason: str | None = None if available else "dataset_disabled"
        if available:
            try:
                self._validate(row)
            except DatasetServiceError:
                available = False
                reason = "dataset_invalid"
        return {
            "id": row["id"],
            "key": f"dataset:{row['id']}",
            "name": row["name"],
            "root_alias": row["root_alias"],
            "directory": row["directory"],
            "train_split": row["train_split"],
            "val_split": row["val_split"],
            "test_split": row["test_split"],
            "classes": list(row["classes"]),
            "enabled": bool(row["enabled"]),
            "available": available,
            "reason": reason,
            "version": row.get("version", 1),
            "status": row.get("status", "published"),
            "parent_id": row.get("parent_id"),
            "root_dataset_id": row.get("root_dataset_id"),
            "revision": row.get("revision", 0),
            "published_at": row.get("published_at"),
            "manifest_digest": row.get("manifest_digest"),
        }

    @staticmethod
    def _valid_digest(value: object) -> bool:
        return (
            isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )
