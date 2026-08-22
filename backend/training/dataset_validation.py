from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any, Literal, Mapping

from .annotation_service import AnnotationService, AnnotationServiceError
from .dataset_filesystem import DatasetFilesystem, DatasetFilesystemError
from .dataset_repository import TrainingDatasetConflictError, TrainingDatasetRepository


class DatasetValidationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    severity: Literal["error", "warning"]
    split: str | None = None
    image_id: int | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class DatasetValidationReport:
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    counts: Mapping[str, object]
    digest: str | None

    def to_json(self) -> dict[str, object]:
        return {
            "errors": [asdict(item) for item in self.errors],
            "warnings": [asdict(item) for item in self.warnings],
            "counts": dict(self.counts),
            "digest": self.digest,
        }


class DatasetValidator:
    def __init__(
        self, repository: TrainingDatasetRepository, filesystem: DatasetFilesystem
    ) -> None:
        self._repository = repository
        self._filesystem = filesystem
        self._annotations = AnnotationService(repository, filesystem)

    def validate(self, dataset_id: int) -> DatasetValidationReport:
        dataset = self._repository.get(dataset_id)
        if dataset is None:
            raise DatasetValidationError("dataset_not_found")
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        try:
            scanned = self._filesystem.scan(dataset)
        except DatasetFilesystemError:
            return DatasetValidationReport(
                [ValidationIssue("dataset_scan_failed", "error", message="Dataset files cannot be scanned.")],
                [], {}, None,
            )
        observed = {(item.split, item.image_key): item for item in scanned}
        indexed: dict[tuple[str, str], dict[str, Any]] = {}
        class_counts = {split: Counter() for split in ("train", "val", "test")}
        manifest_items: list[dict[str, object]] = []
        boxes_by_image: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for split in ("train", "val", "test"):
            page = self._repository.list_images(
                dataset_id, split, page=1, page_size=1_000_000
            )
            for image in page["items"]:
                key = (split, str(image["image_key"]))
                indexed[key] = image
                current = observed.get(key)
                if current is None:
                    errors.append(self._issue("file_missing", "error", image))
                    continue
                if (
                    current.content_digest != image["content_digest"]
                    or current.width != image["width"]
                    or current.height != image["height"]
                ):
                    errors.append(self._issue("file_changed", "error", image))
                if image.get("validation_code"):
                    errors.append(self._issue(str(image["validation_code"]), "error", image))
                if image["review_status"] == "pending_annotation":
                    errors.append(self._issue("annotation_pending", "error", image))
                try:
                    detail = self._annotations.get_image(dataset_id, int(image["id"]))
                    boxes = list(detail["boxes"])
                except AnnotationServiceError:
                    errors.append(self._issue("annotation_invalid", "error", image))
                    continue
                if image["review_status"] == "annotated" and not boxes:
                    errors.append(self._issue("annotation_invalid", "error", image))
                if image["review_status"] == "confirmed_empty" and boxes:
                    errors.append(self._issue("annotation_invalid", "error", image))
                for box in boxes:
                    class_counts[split][int(box["class_id"])] += 1
                boxes_by_image.append((image, boxes))
                try:
                    label = self._filesystem.resolve_label(dataset, split, str(image["image_key"]))
                    label_digest = hashlib.sha256(label.read_bytes()).hexdigest()
                except OSError:
                    errors.append(self._issue("annotation_invalid", "error", image))
                    continue
                manifest_items.append({
                    "split": split, "image_key": image["image_key"],
                    "image_digest": current.content_digest,
                    "label_digest": label_digest,
                    "review_status": image["review_status"],
                })
        for key in sorted(set(observed) - set(indexed)):
            errors.append(ValidationIssue(
                "rescan_required", "error", split=key[0],
                message="Unindexed image requires rescan.",
            ))
        split_image_counts = {
            split: sum(1 for key in indexed if key[0] == split)
            for split in class_counts
        }
        self._semantic_warnings(
            dataset, boxes_by_image, class_counts, split_image_counts, warnings
        )
        counts = {
            "images": split_image_counts,
            "classes": {
                split: {str(class_id): count for class_id, count in sorted(values.items())}
                for split, values in class_counts.items()
            },
        }
        digest = None
        if not errors:
            canonical = json.dumps(
                {
                    "classes": list(dataset["classes"]),
                    "splits": ["train", "val", "test"],
                    "items": sorted(manifest_items, key=lambda item: (str(item["split"]), str(item["image_key"]))),
                },
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
        return DatasetValidationReport(errors, warnings, counts, digest)

    def publish(
        self, dataset_id: int, revision: int, confirm_warnings: bool
    ) -> dict[str, Any]:
        dataset = self._repository.get(dataset_id)
        if dataset is None:
            raise DatasetValidationError("dataset_not_found")
        if dataset["status"] != "editing":
            raise DatasetValidationError("dataset_published")
        report = self.validate(dataset_id)
        if report.errors or report.digest is None:
            raise DatasetValidationError("dataset_validation_failed")
        if report.warnings and not confirm_warnings:
            raise DatasetValidationError("dataset_warnings_unconfirmed")
        directory = self._filesystem.dataset_path(dataset)
        manifest = directory / ".dataset-manifest.json"
        old = manifest.read_bytes() if manifest.is_file() else None
        temporary = directory / f".dataset-manifest-{secrets.token_hex(8)}.tmp"
        payload = json.dumps(
            {"digest": report.digest, "counts": report.counts},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, manifest)
            return self._repository.publish(dataset_id, revision, report.digest)
        except (OSError, TrainingDatasetConflictError) as exc:
            if old is None:
                manifest.unlink(missing_ok=True)
            else:
                manifest.write_bytes(old)
            raise DatasetValidationError("dataset_conflict") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _issue(code: str, severity: Literal["error", "warning"], image: Mapping[str, object]) -> ValidationIssue:
        return ValidationIssue(
            code, severity, split=str(image["split"]), image_id=int(image["id"]),
            message=code.replace("_", " ").capitalize() + ".",
        )

    @staticmethod
    def _semantic_warnings(
        dataset, boxes_by_image, class_counts, split_image_counts, warnings
    ) -> None:
        names = list(dataset["classes"])
        person_id = names.index("person") if "person" in names else None
        head_ids = {names.index(name) for name in ("helmet", "no_helmet") if name in names}
        helmet_id = names.index("helmet") if "helmet" in names else None
        no_helmet_id = names.index("no_helmet") if "no_helmet" in names else None
        for image, boxes in boxes_by_image:
            if person_id is not None:
                persons = [box for box in boxes if box["class_id"] == person_id]
                heads = [box for box in boxes if box["class_id"] in head_ids]
                for person in persons:
                    x1 = person["x_center"] - person["width"] / 2
                    x2 = person["x_center"] + person["width"] / 2
                    y1 = person["y_center"] - person["height"] / 2
                    y2 = y1 + person["height"] * 0.4
                    if not any(x1 <= box["x_center"] <= x2 and y1 <= box["y_center"] <= y2 for box in heads):
                        warnings.append(DatasetValidator._issue("person_head_unlabeled", "warning", image))
            helmets = [box for box in boxes if box["class_id"] == helmet_id]
            bare_heads = [box for box in boxes if box["class_id"] == no_helmet_id]
            if any(
                DatasetValidator._iou(helmet, bare_head) >= 0.3
                for helmet in helmets for bare_head in bare_heads
            ):
                warnings.append(DatasetValidator._issue("helmet_state_overlap", "warning", image))
        for split, counts in class_counts.items():
            if split_image_counts[split] == 0:
                continue
            for class_id in range(len(names)):
                if counts[class_id] < 20:
                    warnings.append(ValidationIssue(
                        "class_sparse", "warning", split=split,
                        message="Class has fewer than 20 instances.",
                    ))
        train_total = sum(class_counts["train"].values())
        if train_total:
            for split in ("val", "test"):
                split_total = sum(class_counts[split].values())
                if not split_total:
                    continue
                for class_id in range(len(names)):
                    train_share = class_counts["train"][class_id] / train_total
                    split_share = class_counts[split][class_id] / split_total
                    if (train_share == 0) != (split_share == 0) or (
                        train_share and split_share
                        and max(train_share, split_share) / min(train_share, split_share) > 4
                    ):
                        warnings.append(ValidationIssue(
                            "class_split_imbalance", "warning", split=split,
                            message="Class distribution differs from training split.",
                        ))

    @staticmethod
    def _iou(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
        def corners(box):
            return (
                box["x_center"] - box["width"] / 2,
                box["y_center"] - box["height"] / 2,
                box["x_center"] + box["width"] / 2,
                box["y_center"] + box["height"] / 2,
            )
        a = corners(first)
        b = corners(second)
        intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
            0.0, min(a[3], b[3]) - max(a[1], b[1])
        )
        union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
        return intersection / union if union > 0 else 0.0
