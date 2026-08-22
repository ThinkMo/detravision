from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


_MODEL_ID = re.compile(r"yolo(?:11|26)[nsmlx]\.pt\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = frozenset(
    {
        "base_model",
        "dataset_yaml",
        "run_dir",
        "model_cache_dir",
        "epochs",
        "imgsz",
        "batch",
        "device",
        "patience",
    }
)
_PROGRESS_KEYS = frozenset({"current_epoch", "total_epochs", "metrics"})
_RESULT_KEYS = frozenset(
    {
        "status",
        "metrics",
        "accepted",
        "best_sha256",
        "error_code",
        "error_message",
    }
)
_METRIC_KEYS = frozenset({"precision", "recall", "map50", "map50_95", "classes"})
_CLASS_METRIC_KEYS = frozenset({"precision", "recall", "map50", "map50_95"})

ERROR_MESSAGES = {
    "cuda_oom": "GPU memory is insufficient for this training configuration.",
    "dataset_invalid": "The configured training dataset is unavailable or invalid.",
    "weights_unavailable": "The selected base-model weights are unavailable.",
    "evaluation_failed": "Held-out test evaluation failed.",
    "training_failed": "Training did not produce a valid model.",
}


def _mapping(payload: object, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _exact_keys(payload: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(payload) != expected:
        raise ValueError(f"{label} has unsupported or missing fields")


def _string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _integer(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _absolute_path(payload: Mapping[str, Any], field: str) -> str:
    value = _string(payload, field)
    if not Path(value).is_absolute():
        raise ValueError(f"{field} must be absolute")
    return value


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _json_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a JSON number")
    return _finite_float(value, field)


@dataclass(frozen=True, slots=True)
class TrainingManifest:
    base_model: str
    dataset_yaml: str
    run_dir: str
    model_cache_dir: str
    epochs: int
    imgsz: int
    batch: str
    device: str
    patience: int

    @classmethod
    def from_json(cls, payload: object) -> "TrainingManifest":
        values = _mapping(payload, "training manifest")
        _exact_keys(values, _MANIFEST_KEYS, "training manifest")
        base_model = _string(values, "base_model")
        if _MODEL_ID.fullmatch(base_model) is None:
            raise ValueError("unsupported base model")
        epochs = _integer(values, "epochs")
        imgsz = _integer(values, "imgsz")
        patience = _integer(values, "patience")
        batch = _string(values, "batch")
        device = _string(values, "device")
        if not 1 <= epochs <= 1000:
            raise ValueError("epochs out of range")
        if imgsz not in {640, 960}:
            raise ValueError("unsupported image size")
        if batch not in {"1", "2", "4", "8", "auto"}:
            raise ValueError("unsupported batch size")
        if device not in {"auto", "cpu", "0"}:
            raise ValueError("unsupported device")
        if not 0 <= patience <= 500:
            raise ValueError("patience out of range")
        return cls(
            base_model=base_model,
            dataset_yaml=_absolute_path(values, "dataset_yaml"),
            run_dir=_absolute_path(values, "run_dir"),
            model_cache_dir=_absolute_path(values, "model_cache_dir"),
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            patience=patience,
        )

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrainingProgress:
    current_epoch: int
    total_epochs: int
    metrics: dict[str, float]

    @classmethod
    def from_json(cls, payload: object) -> "TrainingProgress":
        values = _mapping(payload, "training progress")
        _exact_keys(values, _PROGRESS_KEYS, "training progress")
        current_epoch = _integer(values, "current_epoch")
        total_epochs = _integer(values, "total_epochs")
        if current_epoch < 0 or total_epochs < 1 or current_epoch > total_epochs:
            raise ValueError("invalid training progress")
        raw_metrics = _mapping(values.get("metrics"), "progress metrics")
        metrics = {
            str(name): _json_float(value, f"metrics.{name}")
            for name, value in raw_metrics.items()
        }
        return cls(current_epoch, total_epochs, metrics)

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrainingResult:
    status: str
    metrics: dict[str, Any] | None
    accepted: bool
    best_sha256: str | None
    error_code: str | None
    error_message: str | None

    @classmethod
    def from_json(cls, payload: object) -> "TrainingResult":
        values = _mapping(payload, "training result")
        _exact_keys(values, _RESULT_KEYS, "training result")
        status = _string(values, "status")
        if status not in {"completed", "failed"}:
            raise ValueError("unsupported training result status")
        accepted = values.get("accepted")
        if type(accepted) is not bool:
            raise ValueError("accepted must be boolean")
        raw_metrics = values.get("metrics")
        metrics = _validated_metrics(raw_metrics) if raw_metrics is not None else None
        best_sha256 = values.get("best_sha256")
        if best_sha256 is not None and (
            not isinstance(best_sha256, str)
            or _SHA256.fullmatch(best_sha256) is None
        ):
            raise ValueError("best_sha256 must be a SHA-256 digest")
        error_code = values.get("error_code")
        error_message = values.get("error_message")
        if error_code is not None and not isinstance(error_code, str):
            raise ValueError("error_code must be a string or null")
        if error_message is not None and not isinstance(error_message, str):
            raise ValueError("error_message must be a string or null")
        if status == "completed" and (
            metrics is None
            or best_sha256 is None
            or error_code is not None
            or error_message is not None
        ):
            raise ValueError("incomplete successful result")
        if status == "failed" and (
            metrics is not None
            or accepted
            or best_sha256 is not None
            or not error_code
            or not error_message
        ):
            raise ValueError("invalid failed result")
        if status == "completed" and accepted is not meets_acceptance(metrics):
            raise ValueError("acceptance does not match metrics")
        if status == "failed" and (
            error_code not in ERROR_MESSAGES
            or error_message != ERROR_MESSAGES.get(error_code)
            or len(error_message) > 256
            or "\n" in error_message
            or "\r" in error_message
        ):
            raise ValueError("unsafe training error")
        return cls(
            status=status,
            metrics=metrics,
            accepted=accepted,
            best_sha256=best_sha256,
            error_code=error_code,
            error_message=error_message,
        )

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _optional_metric(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _json_float(value, field)


def _validated_metrics(payload: object) -> dict[str, Any]:
    values = _mapping(payload, "metrics")
    _exact_keys(values, _METRIC_KEYS, "metrics")
    raw_classes = _mapping(values["classes"], "metric classes")
    classes: dict[str, dict[str, float | None]] = {}
    for name, raw_class_metrics in raw_classes.items():
        if not isinstance(name, str) or not name:
            raise ValueError("metric class names must be non-empty strings")
        class_metrics = _mapping(raw_class_metrics, f"class metrics for {name}")
        _exact_keys(class_metrics, _CLASS_METRIC_KEYS, f"class metrics for {name}")
        classes[name] = {
            "precision": _optional_metric(
                class_metrics["precision"], f"{name}.precision"
            ),
            "recall": _optional_metric(class_metrics["recall"], f"{name}.recall"),
            "map50": _json_float(class_metrics["map50"], f"{name}.map50"),
            "map50_95": _json_float(
                class_metrics["map50_95"], f"{name}.map50_95"
            ),
        }
    return {
        "precision": _json_float(values["precision"], "precision"),
        "recall": _json_float(values["recall"], "recall"),
        "map50": _json_float(values["map50"], "map50"),
        "map50_95": _json_float(values["map50_95"], "map50_95"),
        "classes": classes,
    }


def atomic_write_json(path: Path | str, payload: Mapping[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _value_at(values: object, index: int) -> object | None:
    if values is None:
        return None
    try:
        return values[index]
    except (IndexError, KeyError, TypeError):
        return None


def _class_name(names: object, class_id: int) -> str:
    if isinstance(names, Mapping):
        return str(names.get(class_id, class_id))
    if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
        try:
            return str(names[class_id])
        except IndexError:
            pass
    return str(class_id)


def extract_metrics(metrics: object) -> dict[str, Any]:
    box = getattr(metrics, "box")
    overall = {
        "precision": _finite_float(getattr(box, "mp"), "precision"),
        "recall": _finite_float(getattr(box, "mr"), "recall"),
        "map50": _finite_float(getattr(box, "map50"), "map50"),
        "map50_95": _finite_float(getattr(box, "map"), "map50_95"),
    }
    ap50_values = getattr(box, "ap50", None)
    ap_values = getattr(box, "ap", None)
    maps_values = getattr(box, "maps", None)
    precision_values = getattr(box, "p", None)
    recall_values = getattr(box, "r", None)
    if ap50_values is None:
        return {**overall, "classes": {}}
    class_ids = getattr(box, "ap_class_index", None)
    if class_ids is None:
        class_ids = range(len(ap50_values))
    names = getattr(metrics, "names", {})
    classes: dict[str, dict[str, float | None]] = {}
    for result_index, raw_class_id in enumerate(class_ids):
        class_id = int(raw_class_id)
        raw_map50 = _value_at(ap50_values, result_index)
        raw_map50_95 = _value_at(maps_values, class_id)
        if raw_map50_95 is None:
            raw_map50_95 = _value_at(ap_values, result_index)
        if raw_map50 is None or raw_map50_95 is None:
            continue
        raw_precision = _value_at(precision_values, result_index)
        raw_recall = _value_at(recall_values, result_index)
        classes[_class_name(names, class_id)] = {
            "precision": (
                _finite_float(raw_precision, "class precision")
                if raw_precision is not None
                else None
            ),
            "recall": (
                _finite_float(raw_recall, "class recall")
                if raw_recall is not None
                else None
            ),
            "map50": _finite_float(raw_map50, "class map50"),
            "map50_95": _finite_float(raw_map50_95, "class map50_95"),
        }
    return {**overall, "classes": classes}


def meets_acceptance(metrics: Mapping[str, Any]) -> bool:
    try:
        no_helmet = metrics["classes"]["no_helmet"]
        map50 = metrics["map50"]
        precision = no_helmet["precision"]
        recall = no_helmet["recall"]
        if precision is None or recall is None:
            return False
        return (
            _finite_float(map50, "map50") >= 0.85
            and _finite_float(precision, "no_helmet precision") >= 0.85
            and _finite_float(recall, "no_helmet recall") >= 0.90
        )
    except (KeyError, TypeError, ValueError):
        return False
