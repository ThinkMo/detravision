from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
import os
from pathlib import Path
import secrets
import hashlib
import json
from threading import RLock
from sqlalchemy.exc import SQLAlchemyError
from typing import Any, Mapping, Sequence

from .dataset_filesystem import DatasetFilesystem, DatasetFilesystemError
from .dataset_repository import TrainingDatasetConflictError, TrainingDatasetRepository


class AnnotationServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AnnotationBox:
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float


class AnnotationService:
    def __init__(self, repository: TrainingDatasetRepository, filesystem: DatasetFilesystem) -> None:
        self._repository = repository
        self._filesystem = filesystem
        self._mutation_lock = RLock()

    def get_image(self, dataset_id: int, image_id: int) -> dict[str, Any]:
        dataset, image = self._records(dataset_id, image_id)
        label = self._filesystem.resolve_label(dataset, image["split"], image["image_key"])
        return {**image, "boxes": [box.__dict__ for box in self._read(label, len(dataset["classes"]))]}

    def content(self, dataset_id: int, image_id: int) -> tuple[Path, str]:
        dataset, image = self._records(dataset_id, image_id)
        path = self._filesystem.resolve_image(dataset, image["split"], image["image_key"])
        try:
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    checksum.update(chunk)
            digest = checksum.hexdigest()
        except OSError as exc:
            raise AnnotationServiceError("annotation_not_found") from exc
        if digest != image["content_digest"]:
            raise AnnotationServiceError("annotation_conflict")
        media_types = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".bmp": "image/bmp", ".webp": "image/webp",
        }
        try:
            return path, media_types[path.suffix.casefold()]
        except KeyError as exc:
            raise AnnotationServiceError("annotation_invalid") from exc

    def save(
        self, dataset_id: int, image_id: int, revision: int,
        boxes: Sequence[Mapping[str, object]],
    ) -> dict[str, Any]:
        with self._mutation_lock:
            if not boxes:
                raise AnnotationServiceError("annotation_invalid")
            dataset, image = self._records(dataset_id, image_id, editable=True)
            parsed = [self._box(value, len(dataset["classes"])) for value in boxes]
            payload = "".join(
                f"{box.class_id} {box.x_center:.6f} {box.y_center:.6f} {box.width:.6f} {box.height:.6f}\n"
                for box in parsed
            ).encode("utf-8")
            return self._write(dataset, image, revision, payload, "annotated")

    def confirm_empty(self, dataset_id: int, image_id: int, revision: int) -> dict[str, Any]:
        with self._mutation_lock:
            dataset, image = self._records(dataset_id, image_id, editable=True)
            return self._write(dataset, image, revision, b"", "confirmed_empty")

    def remove_images(
        self, dataset_id: int, revision: int, images: Sequence[Mapping[str, object]],
    ) -> dict[str, Any]:
        with self._mutation_lock:
            return self._remove_images(dataset_id, revision, images)

    def _remove_images(self, dataset_id, revision, images):
        if (type(revision) is not int or revision < 0 or not 1 <= len(images) <= 100
            or any(set(item) != {"id", "revision"} or type(item["id"]) is not int
                   or item["id"] <= 0 or type(item["revision"]) is not int
                   or item["revision"] < 0 for item in images)
            or len({item["id"] for item in images}) != len(images)):
            raise AnnotationServiceError("annotation_invalid")
        dataset = self._dataset(dataset_id, editable=True)
        records = []
        for item in images:
            image = self._repository.get_image(dataset_id, item["id"])
            if image is None:
                raise AnnotationServiceError("annotation_not_found")
            if image["revision"] != item["revision"]:
                raise AnnotationServiceError("annotation_conflict")
            records.append(image)
        directory = self._filesystem.dataset_path(dataset)
        archive = directory / ".removed-images" / secrets.token_hex(12)
        paths = []
        try:
            # No caller-supplied paths; reject symlinks/junctions or any dataset escape.
            for candidate in (archive.parent, archive):
                if candidate.resolve() != candidate.absolute() or not candidate.resolve().is_relative_to(directory):
                    raise AnnotationServiceError("annotation_invalid")
            for image in records:
                for kind, resolver in (("image", self._filesystem.resolve_image), ("label", self._filesystem.resolve_label)):
                    source = resolver(dataset, image["split"], image["image_key"])
                    split_parts = list(Path(str(dataset[f"{image['split']}_split"])).parts)
                    key = Path(str(image["image_key"]))
                    if kind == "label":
                        split_parts[split_parts.index("images")] = "labels"
                        key = key.with_suffix(".txt")
                    expected = directory.joinpath(*split_parts) / key
                    if source != expected.absolute():
                        raise AnnotationServiceError("annotation_invalid")
                    if source.resolve() != source.absolute() or not source.resolve().is_relative_to(directory):
                        raise AnnotationServiceError("annotation_invalid")
                    if source.exists() and not source.is_file():
                        raise AnnotationServiceError("annotation_invalid")
                    if kind == "image" and source.exists():
                        digest = hashlib.sha256()
                        with source.open("rb") as stream:
                            while block := stream.read(1024 * 1024):
                                digest.update(block)
                        if digest.hexdigest() != image["content_digest"]:
                            raise AnnotationServiceError("annotation_conflict")
                    paths.append((source, archive / source.relative_to(directory)))
        except DatasetFilesystemError as exc:
            raise AnnotationServiceError("annotation_invalid") from exc
        except OSError as exc:
            raise AnnotationServiceError("annotation_write_failed") from exc
        moved = []
        try:
            with self._repository.removing_images(dataset_id, revision, images) as new_revision:
                archive.mkdir(parents=True)
                (archive / "manifest.json").write_text(json.dumps({
                    "dataset_id": dataset_id, "previous_revision": revision,
                    "dataset_revision": new_revision, "images": records,
                }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                for source, target in paths:
                    if source.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(source, target)
                        moved.append((source, target))
            return {"removed": len(images), "image_ids": [item["id"] for item in images],
                    "dataset_revision": new_revision}
        except (OSError, SQLAlchemyError, TrainingDatasetConflictError) as exc:
            for source, target in reversed(moved):
                os.replace(target, source)
            code = "annotation_conflict" if isinstance(exc, TrainingDatasetConflictError) else "annotation_write_failed"
            raise AnnotationServiceError(code) from exc

    def add_class(self, dataset_id: int, revision: int, name: str) -> dict[str, Any]:
        dataset = self._dataset(dataset_id, editable=True)
        normalized = self._class_name(name)
        if normalized in dataset["classes"]:
            raise AnnotationServiceError("class_invalid")
        return self._update_classes(dataset_id, revision, [*dataset["classes"], normalized])

    def rename_class(
        self, dataset_id: int, revision: int, class_id: int, name: str
    ) -> dict[str, Any]:
        dataset = self._dataset(dataset_id, editable=True)
        if type(class_id) is not int or not 0 <= class_id < len(dataset["classes"]):
            raise AnnotationServiceError("class_invalid")
        normalized = self._class_name(name)
        classes = list(dataset["classes"])
        if normalized in classes and classes[class_id] != normalized:
            raise AnnotationServiceError("class_invalid")
        classes[class_id] = normalized
        return self._update_classes(dataset_id, revision, classes)

    def delete_class(self, dataset_id: int, revision: int, class_id: int) -> dict[str, Any]:
        dataset = self._dataset(dataset_id, editable=True)
        classes = list(dataset["classes"])
        if type(class_id) is not int or not 0 <= class_id < len(classes):
            raise AnnotationServiceError("class_invalid")
        if class_id != len(classes) - 1:
            raise AnnotationServiceError("class_order_locked")
        for split in ("train", "val", "test"):
            page = self._repository.list_images(
                dataset_id, split, page=1, page_size=1_000_000
            )
            for image in page["items"]:
                label = self._filesystem.resolve_label(
                    dataset, split, str(image["image_key"])
                )
                if any(box.class_id == class_id for box in self._read(label, len(classes))):
                    raise AnnotationServiceError("class_in_use")
        return self._update_classes(dataset_id, revision, classes[:-1])

    def _write(
        self, dataset: Mapping[str, object], image: Mapping[str, object],
        revision: int, payload: bytes, status: str,
    ) -> dict[str, Any]:
        label = self._filesystem.resolve_label(dataset, str(image["split"]), str(image["image_key"]))
        label.parent.mkdir(parents=True, exist_ok=True)
        old = label.read_bytes() if label.exists() else None
        temporary = label.with_name(f".label-{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, label)
            try:
                return self._repository.update_image_review(
                    int(image["id"]), revision,
                    {"review_status": status, "validation_code": None},
                )
            except TrainingDatasetConflictError as exc:
                if old is None:
                    label.unlink(missing_ok=True)
                else:
                    restore = label.with_name(f".label-{secrets.token_hex(8)}.tmp")
                    restore.write_bytes(old); os.replace(restore, label)
                raise AnnotationServiceError("annotation_conflict") from exc
        except OSError as exc:
            raise AnnotationServiceError("annotation_write_failed") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _records(self, dataset_id: int, image_id: int, *, editable: bool = False):
        dataset = self._dataset(dataset_id, editable=editable)
        image = self._repository.get_image(dataset_id, image_id)
        if image is None:
            raise AnnotationServiceError("annotation_not_found")
        return dataset, image

    def _dataset(self, dataset_id: int, *, editable: bool = False) -> dict[str, Any]:
        dataset = self._repository.get(dataset_id)
        if dataset is None:
            raise AnnotationServiceError("dataset_not_found")
        if editable and dataset["status"] != "editing":
            raise AnnotationServiceError("dataset_published")
        return dataset

    def _update_classes(self, dataset_id: int, revision: int, classes: list[str]):
        try:
            return self._repository.update_editing_dataset(dataset_id, revision, {"classes": classes})
        except TrainingDatasetConflictError as exc:
            raise AnnotationServiceError("annotation_conflict") from exc

    @staticmethod
    def _class_name(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
            raise AnnotationServiceError("class_invalid")
        return value.strip()

    @staticmethod
    def _box(value: Mapping[str, object], class_count: int) -> AnnotationBox:
        if set(value) != {"class_id", "x_center", "y_center", "width", "height"}:
            raise AnnotationServiceError("annotation_invalid")
        class_id = value["class_id"]
        if type(class_id) is not int or not 0 <= class_id < class_count:
            raise AnnotationServiceError("annotation_invalid")
        numbers = []
        for key in ("x_center", "y_center", "width", "height"):
            item = value[key]
            if isinstance(item, bool) or not isinstance(item, Real):
                raise AnnotationServiceError("annotation_invalid")
            number = float(item)
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise AnnotationServiceError("annotation_invalid")
            numbers.append(number)
        if numbers[2] <= 0 or numbers[3] <= 0:
            raise AnnotationServiceError("annotation_invalid")
        return AnnotationBox(class_id, *numbers)

    def _read(self, path: Path, class_count: int) -> list[AnnotationBox]:
        if not path.is_file():
            return []
        boxes: list[AnnotationBox] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) != 5:
                    raise ValueError
                boxes.append(self._box({
                    "class_id": int(parts[0]), "x_center": float(parts[1]),
                    "y_center": float(parts[2]), "width": float(parts[3]),
                    "height": float(parts[4]),
                }, class_count))
            return boxes
        except (OSError, UnicodeError, ValueError) as exc:
            raise AnnotationServiceError("annotation_invalid") from exc
